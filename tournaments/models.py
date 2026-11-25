from datetime import time
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from matches.models import Match


class Tournament(models.Model):
    """A tournament you create in the admin."""

    class Format(models.TextChoices):
        KNOCKOUT = "knockout", "Knockout (elimination)"
        # League tables are added later

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft (hidden from players)"
        REGISTRATION = "registration", "Registration open"
        RUNNING = "running", "Running"
        FINISHED = "finished", "Finished"
        CANCELLED = "cancelled", "Cancelled"

    class PrizeMode(models.TextChoices):
        GROWING = "growing", "Grows with the number of players"
        FIXED = "fixed", "Fixed amount"

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    game = models.CharField(max_length=20, choices=Match.Game.choices)
    format = models.CharField(max_length=20, choices=Format.choices, default=Format.KNOCKOUT)
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.DRAFT, db_index=True
    )

    # ----- players -----
    min_players = models.PositiveIntegerField(
        default=8,
        validators=[MinValueValidator(2)],
        help_text="If fewer players than this enrol, the tournament is cancelled.",
    )
    max_players = models.PositiveIntegerField(
        default=128, validators=[MinValueValidator(2)], help_text="Enrolment closes when this many have joined."
    )

    # ----- timing (times you type in the admin are Lusaka time) -----
    registration_closes_at = models.DateTimeField()
    starts_at = models.DateTimeField(help_text="When round 1 starts. It must fall inside the daily play window.")
    round_interval_minutes = models.PositiveIntegerField(
        default=120,
        validators=[MinValueValidator(10)],
        help_text="Minutes between the start of one round and the next (60 = hourly, 120 = every two hours).",
    )
    daily_open = models.TimeField(default=time(8, 0), help_text="Play starts each day at this time.")
    daily_close = models.TimeField(
        default=time(20, 0),
        help_text="No round starts after this. Unfinished rounds continue the next day at the opening time.",
    )
    checkin_minutes = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
        help_text="Minutes players have to open their match before they count as absent.",
    )

    # ----- costs -----
    entry_fee = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Charged once when a player enrols. 0 = free to enter. With a growing prize, IndiBet keeps "
        "the platform cut of the entry fees and the rest goes into the prize pool. With a fixed prize, "
        "IndiBet keeps all of it.",
    )
    match_fee = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Charged to each player when each of their matches is created. IndiBet keeps all of it. "
        "0 = free matches.",
    )

    # ----- referrals -----
    required_referrals = models.PositiveSmallIntegerField(
        default=0, help_text="People a player must have referred to enrol. 0 = no referrals needed."
    )
    waive_referrals_at_users = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=10000,
        help_text="Once the platform has this many users, nobody needs referrals any more. Leave empty to never waive.",
    )

    # ----- prizes -----
    prize_mode = models.CharField(
        max_length=10,
        choices=PrizeMode.choices,
        default=PrizeMode.GROWING,
        help_text="Fixed amount: the prize is the amount below and never changes. "
        "Grows with the number of players: the prize depends on how many enrol.",
    )
    fixed_prize_pool = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="USED ONLY FOR A FIXED PRIZE. The total prize money, paid by IndiBet, "
        "no matter how many players enrol.",
    )
    platform_cut_percent = models.PositiveSmallIntegerField(
        default=50,
        validators=[MaxValueValidator(100)],
        help_text="USED ONLY FOR A GROWING PRIZE. % of the ENTRY FEES that IndiBet keeps. "
        "The rest goes into the prize pool. No effect when entry is free.",
    )
    prize_per_player = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="USED ONLY FOR A GROWING PRIZE. IndiBet adds this much to the prize pool for every "
        "player enrolled (paid by IndiBet, not by the players).",
    )
    prize_cap = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="USED ONLY FOR A GROWING PRIZE. The most IndiBet will add from its own pocket "
        "(the amount above). Leave empty for no maximum.",
    )
    share_champion = models.PositiveSmallIntegerField(
        default=40, validators=[MaxValueValidator(100)], help_text="% of the pool for the winner."
    )
    share_runner_up = models.PositiveSmallIntegerField(
        default=20, validators=[MaxValueValidator(100)], help_text="% of the pool for the player who loses the final."
    )
    share_semifinalist = models.PositiveSmallIntegerField(
        default=10,
        validators=[MaxValueValidator(100)],
        help_text="% of the pool for EACH of the 2 players who lose in the semi-finals (round of 4). 0 = no prize.",
    )
    share_quarterfinalist = models.PositiveSmallIntegerField(
        default=5,
        validators=[MaxValueValidator(100)],
        help_text="% of the pool for EACH of the 4 players who lose in the quarter-finals (round of 8). 0 = no prize.",
    )

    # ----- filled in by the system when the tournament starts -----
    bracket = models.JSONField(null=True, blank=True, editable=False)
    final_players = models.PositiveIntegerField(null=True, blank=True, editable=False)
    final_prize_pool = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, editable=False
    )
    drawn_at = models.DateTimeField(null=True, blank=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_at"]

    def __str__(self):
        return self.name

    # ----- rules used by the rest of the tournament code -----
    def registration_is_open(self):
        return self.status == self.Status.REGISTRATION and timezone.now() < self.registration_closes_at

    def prize_breakdown(self, enrolled):
        """Where the prize pool comes from for this many enrolled players."""
        cents = Decimal("0.01")
        entry_fees = self.entry_fee * enrolled  # paid by the players

        if self.prize_mode == self.PrizeMode.FIXED:
            fixed = self.fixed_prize_pool.quantize(cents)  # paid by IndiBet, whatever the player count
            return {
                "entry_fees": entry_fees.quantize(cents),
                "platform_keeps": entry_fees.quantize(cents),  # all entry fees stay with IndiBet
                "from_entries": Decimal("0.00"),
                "bonus": fixed,
                "pool": fixed,
            }

        bonus = self.prize_per_player * enrolled  # paid by IndiBet
        if self.prize_cap is not None:
            bonus = min(bonus, self.prize_cap)

        platform_keeps = (entry_fees * self.platform_cut_percent / 100).quantize(cents)
        from_entries = entry_fees - platform_keeps

        return {
            "entry_fees": entry_fees.quantize(cents),
            "platform_keeps": platform_keeps,
            "from_entries": from_entries.quantize(cents),
            "bonus": bonus.quantize(cents),
            "pool": (bonus + from_entries).quantize(cents),
        }

    def prize_pool(self, enrolled):
        """The total prize money for this many enrolled players."""
        return self.prize_breakdown(enrolled)["pool"]

    def current_prize_pool(self, enrolled):
        """The prize pool players see: locked once the tournament has started, otherwise it follows enrolment."""
        if self.final_prize_pool is not None:
            return self.final_prize_pool
        return self.prize_pool(enrolled)

    def prize_table(self, enrolled):
        """What each finishing place earns at the current prize pool."""
        pool = self.current_prize_pool(enrolled)
        rows = []
        for label, count, percent in (
            ("Champion", 1, self.share_champion),
            ("Runner-up", 1, self.share_runner_up),
            ("Each semi-finalist", 2, self.share_semifinalist),
            ("Each quarter-finalist", 4, self.share_quarterfinalist),
        ):
            if percent:
                rows.append(
                    {
                        "label": label,
                        "count": count,
                        "percent": percent,
                        "each": (pool * percent / 100).quantize(Decimal("0.01")),
                    }
                )
        return rows

    def referrals_needed(self, total_users):
        """How many referrals a player needs right now. Waived once the platform is big enough."""
        if self.waive_referrals_at_users is not None and total_users >= self.waive_referrals_at_users:
            return 0
        return self.required_referrals

    # ----- checks when saving in the admin -----
    def clean(self):
        errors = {}

        if self.registration_closes_at and self.starts_at and self.registration_closes_at > self.starts_at:
            errors["registration_closes_at"] = "Registration must close before the tournament starts."

        if self.min_players and self.max_players and self.min_players > self.max_players:
            errors["max_players"] = "Maximum players can't be lower than the minimum."

        if self.daily_open and self.daily_close:
            if self.daily_open >= self.daily_close:
                errors["daily_close"] = "The daily closing time must be after the opening time."
            elif self.starts_at:
                start_time = timezone.localtime(self.starts_at).time()
                if not (self.daily_open <= start_time < self.daily_close):
                    errors["starts_at"] = "Round 1 must start inside the daily play window."

        if self.prize_mode == self.PrizeMode.FIXED and not self.fixed_prize_pool:
            errors["fixed_prize_pool"] = (
                "Enter the fixed prize amount, or switch the prize mode to "
                "'Grows with the number of players'."
            )

        if self.status in (self.Status.RUNNING, self.Status.FINISHED) and not self.bracket:
            errors["status"] = (
                "A tournament can only be running or finished after its bracket is drawn. "
                "Use the action 'Close registration, draw the bracket and start' instead."
            )

        total = (
            (self.share_champion or 0)
            + (self.share_runner_up or 0)
            + 2 * (self.share_semifinalist or 0)
            + 4 * (self.share_quarterfinalist or 0)
        )
        if total != 100:
            errors["share_champion"] = (
                "Prize shares must add up to 100%: champion + runner-up + 2 x semi-finalist "
                f"+ 4 x quarter-finalist. They add up to {total}%."
            )

        if errors:
            raise ValidationError(errors)


class TournamentEntry(models.Model):
    """A player who has enrolled in a tournament."""

    tournament = models.ForeignKey(Tournament, on_delete=models.PROTECT, related_name="entries")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tournament_entries"
    )
    fee_paid = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["enrolled_at"]
        verbose_name_plural = "tournament entries"
        constraints = [
            models.UniqueConstraint(fields=["tournament", "user"], name="one_entry_per_player"),
        ]

    def __str__(self):
        return f"{self.user} in {self.tournament}"


class TournamentRound(models.Model):
    """When each round of a started tournament begins."""

    tournament = models.ForeignKey(Tournament, on_delete=models.PROTECT, related_name="rounds")
    number = models.PositiveSmallIntegerField()
    starts_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["tournament", "number"], name="one_row_per_round"),
        ]

    def __str__(self):
        return f"{self.tournament} - round {self.number}"