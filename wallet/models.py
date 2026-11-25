from decimal import Decimal

from django.conf import settings
from django.db import models


class Wallet(models.Model):
    """One wallet per user.
    available = money the user can use or withdraw.
    held = money locked in a match stake or a pending withdrawal.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="wallet",
    )
    available = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    held = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(available__gte=0),
                name="wallet_available_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(held__gte=0),
                name="wallet_held_gte_0",
            ),
        ]

    def __str__(self):
        return f"{self.user} | available {self.available} | held {self.held}"


class Transaction(models.Model):
    """A permanent record of every money movement. Never edited or deleted."""

    class Kind(models.TextChoices):
        DEPOSIT = "deposit", "Deposit"
        STAKE_HOLD = "stake_hold", "Stake held"
        STAKE_REFUND = "stake_refund", "Stake refunded"
        STAKE_LOSS = "stake_loss", "Stake lost"
        PAYOUT = "payout", "Winnings"
        FEE = "fee", "Platform fee"
        WITHDRAW_HOLD = "withdraw_hold", "Withdrawal requested"
        TOURNAMENT_ENTRY = "tournament_entry", "Tournament entry"
        TOURNAMENT_REFUND = "tournament_refund", "Tournament entry refunded"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COMPLETED)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=100, unique=True)
    provider_ref = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    match_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    available_after = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    held_after = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} {self.amount} ({self.reference})"