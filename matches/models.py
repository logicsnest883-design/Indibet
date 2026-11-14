from django.conf import settings
from django.db import models


class Match(models.Model):
    """One game between two players. Shared by every game (checkers now, chess later)."""

    class Game(models.TextChoices):
        CHECKERS = "checkers", "Checkers"
        # CHESS = "chess", "Chess"   <- added later

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        STARTING = "starting", "Starting"
        ACTIVE = "active", "In progress"
        FINISHED = "finished", "Finished"
        CANCELLED = "cancelled", "Cancelled"

    game = models.CharField(max_length=20, choices=Game.choices, default=Game.CHECKERS)
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_matches"
    )
    opponent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="joined_matches",
    )
    stake = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    state = models.JSONField(default=dict, blank=True)  # the board and game state live here
    winner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="won_matches",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    starts_at = models.DateTimeField(null=True, blank=True)  # end of the countdown
    started_at = models.DateTimeField(null=True, blank=True)
    turn_deadline = models.DateTimeField(null=True, blank=True)  # when the current player's time runs out
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.pk} {self.get_game_display()} K{self.stake} ({self.status})"