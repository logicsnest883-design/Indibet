from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from wallet.services import charge_entry_fee, refund_entry_fee

from .bracket import build_bracket
from .models import Tournament, TournamentEntry, TournamentRound
from .schedule import round_start_times


class TournamentError(Exception):
    pass


class EnrolmentError(TournamentError):
    pass


def _lock_tournament(tournament_id):
    try:
        return Tournament.objects.select_for_update().get(pk=tournament_id)
    except Tournament.DoesNotExist:
        raise EnrolmentError("That tournament does not exist.")


# ---------- enrolling ----------
@transaction.atomic
def enrol(user, tournament_id):
    """Enrol a player: checks the rules, charges the entry fee, records the entry.
    If anything fails, nothing is charged."""
    tournament = _lock_tournament(tournament_id)

    if not tournament.registration_is_open():
        raise EnrolmentError("Registration is not open for this tournament.")
    if tournament.entries.filter(user=user).exists():
        raise EnrolmentError("You are already enrolled.")
    if tournament.entries.count() >= tournament.max_players:
        raise EnrolmentError("This tournament is full.")

    needed = tournament.referrals_needed(get_user_model().objects.count())
    have = user.referrals.count()
    if have < needed:
        raise EnrolmentError(f"You need {needed} referrals to enrol. You have {have}.")

    if tournament.entry_fee > 0:
        charge_entry_fee(user.id, tournament.entry_fee, tournament.id)  # raises InsufficientFunds

    return TournamentEntry.objects.create(
        tournament=tournament, user=user, fee_paid=tournament.entry_fee
    )


@transaction.atomic
def withdraw(user, tournament_id):
    """Leave a tournament while registration is still open. The entry fee is refunded."""
    tournament = _lock_tournament(tournament_id)

    if not tournament.registration_is_open():
        raise EnrolmentError("You can only leave while registration is open.")

    entry = tournament.entries.filter(user=user).first()
    if not entry:
        raise EnrolmentError("You are not enrolled in this tournament.")

    if entry.fee_paid > 0:
        refund_entry_fee(user.id, entry.fee_paid, tournament.id)
    entry.delete()


# ---------- starting and cancelling ----------
def _cancel_and_refund(tournament, entries):
    for entry in entries:
        if entry.fee_paid > 0:
            refund_entry_fee(entry.user_id, entry.fee_paid, tournament.id)
    tournament.status = Tournament.Status.CANCELLED
    tournament.save(update_fields=["status"])


@transaction.atomic
def cancel_tournament(tournament_id):
    """Cancel a tournament before the draw and refund every entry fee."""
    tournament = _lock_tournament(tournament_id)
    if tournament.status not in (Tournament.Status.DRAFT, Tournament.Status.REGISTRATION):
        raise TournamentError("Only a tournament that has not started can be cancelled here.")
    _cancel_and_refund(tournament, list(tournament.entries.all()))


@transaction.atomic
def start_tournament(tournament_id, force=False):
    """Close registration, then either cancel (too few players) or draw the bracket and
    schedule the rounds. Returns 'cancelled' or 'started'.

    Normally this runs once the registration closing time has passed.
    force=True starts it right away (used from the admin)."""
    tournament = _lock_tournament(tournament_id)

    if tournament.status != Tournament.Status.REGISTRATION:
        raise TournamentError("This tournament is not in the registration stage.")

    now = timezone.now()
    if not force and now < tournament.registration_closes_at:
        raise TournamentError("Registration is still open.")

    entries = list(tournament.entries.all())
    if len(entries) < max(tournament.min_players, 2):
        _cancel_and_refund(tournament, entries)
        return "cancelled"

    bracket = build_bracket([entry.user_id for entry in entries])

    starts = round_start_times(
        first_start=max(tournament.starts_at, now),
        rounds=len(bracket["rounds"]),
        interval_minutes=tournament.round_interval_minutes,
        daily_open=tournament.daily_open,
        daily_close=tournament.daily_close,
        tz=timezone.get_current_timezone(),
    )
    TournamentRound.objects.bulk_create(
        [
            TournamentRound(tournament=tournament, number=number, starts_at=starts_at)
            for number, starts_at in enumerate(starts, start=1)
        ]
    )

    tournament.bracket = bracket
    tournament.final_players = len(entries)
    tournament.final_prize_pool = tournament.prize_pool(len(entries))  # the prize is locked from now on
    tournament.drawn_at = now
    tournament.status = Tournament.Status.RUNNING
    tournament.save(
        update_fields=["bracket", "final_players", "final_prize_pool", "drawn_at", "status"]
    )
    return "started"