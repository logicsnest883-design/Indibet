from decimal import Decimal

from django.db import transaction

from .models import Transaction, Wallet

FEE_SMALL = Decimal("1.00")
FEE_LARGE = Decimal("2.00")
FEE_THRESHOLD = Decimal("19")


class InsufficientFunds(Exception):
    pass


def match_fee(stake):
    """K1 for stakes up to K19, K2 for stakes above K19. Charged to each player."""
    return FEE_LARGE if Decimal(stake) > FEE_THRESHOLD else FEE_SMALL


def _lock_wallets(user_ids):
    """Lock wallets in a fixed order so two payouts can never deadlock."""
    wallets = (
        Wallet.objects.select_for_update()
        .filter(user_id__in=user_ids)
        .order_by("user_id")
    )
    return {w.user_id: w for w in wallets}


def _log(wallet, kind, amount, reference, match_id=None):
    return Transaction.objects.create(
        wallet=wallet,
        kind=kind,
        amount=amount,
        reference=reference,
        match_id=match_id,
        available_after=wallet.available,
        held_after=wallet.held,
    )


@transaction.atomic
def hold_stake(user_id, amount, match_id):
    """Lock the stake and charge the fee. The player needs stake + fee available."""
    amount = Decimal(amount)
    fee = match_fee(amount)
    reference = f"hold:{match_id}:{user_id}"
    if Transaction.objects.filter(reference=reference).exists():
        return  # already done

    Wallet.objects.get_or_create(user_id=user_id)
    wallet = _lock_wallets([user_id])[user_id]
    if wallet.available < amount + fee:
        raise InsufficientFunds("Not enough balance.")

    wallet.available -= amount + fee
    wallet.held += amount
    wallet.save(update_fields=["available", "held", "updated_at"])
    _log(wallet, Transaction.Kind.STAKE_HOLD, amount, reference, match_id)
    _log(wallet, Transaction.Kind.FEE, fee, f"fee:{match_id}:{user_id}", match_id)


@transaction.atomic
def refund_stake(user_id, amount, match_id, refund_fee=False):
    """Give the locked stake back (and the fee too, if refund_fee is True)."""
    amount = Decimal(amount)
    reference = f"refund:{match_id}:{user_id}"
    if Transaction.objects.filter(reference=reference).exists():
        return  # already done

    fee_back = match_fee(amount) if refund_fee else Decimal("0.00")

    wallet = _lock_wallets([user_id])[user_id]
    wallet.held -= amount
    wallet.available += amount + fee_back
    wallet.save(update_fields=["available", "held", "updated_at"])
    _log(wallet, Transaction.Kind.STAKE_REFUND, amount, reference, match_id)
    if fee_back > 0:
        _log(wallet, Transaction.Kind.STAKE_REFUND, fee_back, f"feerefund:{match_id}:{user_id}", match_id)


@transaction.atomic
def settle_match(match_id, winner_id, loser_id, stake):
    """The winner receives the full pot (both stakes). Fees were already charged at staking."""
    reference = f"payout:{match_id}"
    if Transaction.objects.filter(reference=reference).exists():
        return  # already paid

    stake = Decimal(stake)
    wallets = _lock_wallets([winner_id, loser_id])
    winner, loser = wallets[winner_id], wallets[loser_id]

    loser.held -= stake
    loser.save(update_fields=["held", "updated_at"])
    _log(loser, Transaction.Kind.STAKE_LOSS, stake, f"loss:{match_id}:{loser_id}", match_id)

    winner.held -= stake
    winner.available += stake * 2
    winner.save(update_fields=["available", "held", "updated_at"])
    _log(winner, Transaction.Kind.PAYOUT, stake * 2, reference, match_id)


@transaction.atomic
def settle_draw(match_id, user_a, user_b, stake):
    """Draw: both players get their stake back."""
    if Transaction.objects.filter(reference__startswith=f"draw:{match_id}:").exists():
        return  # already refunded

    stake = Decimal(stake)
    wallets = _lock_wallets([user_a, user_b])
    for uid in (user_a, user_b):
        wallet = wallets[uid]
        wallet.held -= stake
        wallet.available += stake
        wallet.save(update_fields=["available", "held", "updated_at"])
        _log(wallet, Transaction.Kind.STAKE_REFUND, stake, f"draw:{match_id}:{uid}", match_id)