from datetime import timedelta

from django.db import transaction
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from checkers.engine import ENGINE as CHECKERS_ENGINE
from wallet.services import hold_stake, refund_stake

from .models import Match

COUNTDOWN_SECONDS = 10

# How each game creates its starting board (chess will be added here later)
INITIAL_STATES = {
    "checkers": CHECKERS_ENGINE.initial_state,
}


class MatchError(Exception):
    pass


def is_busy(user_id):
    """True if the player is in a match that is starting or in progress."""
    return (
        Match.objects.filter(status__in=[Match.Status.STARTING, Match.Status.ACTIVE])
        .filter(Q(creator_id=user_id) | Q(opponent_id=user_id))
        .exists()
    )


@transaction.atomic
def create_match(user, game, stake):
    """Create an open match and lock the creator's stake.
    If the player can't afford it, everything is undone."""
    if is_busy(user.id):
        raise MatchError("Finish your current match first.")
    match = Match.objects.create(creator=user, game=game, stake=stake)
    hold_stake(user.id, stake, match.id)
    return match


@transaction.atomic
def cancel_match(user, match_id):
    """Cancel your own open match and get the stake back."""
    try:
        match = Match.objects.select_for_update().get(pk=match_id)
    except Match.DoesNotExist:
        raise MatchError("That match does not exist.")

    if match.creator_id != user.id:
        raise MatchError("This is not your match.")
    if match.status != Match.Status.OPEN:
        raise MatchError("This match can no longer be cancelled.")

    match.status = Match.Status.CANCELLED
    match.save(update_fields=["status"])
    refund_stake(match.creator_id, match.stake, match.id, refund_fee=True)


@transaction.atomic
def join_match(user, match_id, online_ids=None):
    """Join an open match: lock the joiner's stake and start the countdown."""
    try:
        match = Match.objects.select_for_update().get(pk=match_id)
    except Match.DoesNotExist:
        raise MatchError("That match does not exist.")

    if match.status != Match.Status.OPEN:
        raise MatchError("This match is no longer available.")
    if match.creator_id == user.id:
        raise MatchError("You can't join your own match.")
    if online_ids is not None and match.creator_id not in online_ids:
        raise MatchError("That player is offline right now.")
    if is_busy(user.id):
        raise MatchError("You are already in a match.")
    if is_busy(match.creator_id):
        raise MatchError("That player is busy in another match.")

    hold_stake(user.id, match.stake, match.id)  # raises InsufficientFunds if too poor

    match.opponent = user
    match.status = Match.Status.STARTING
    match.starts_at = timezone.now() + timedelta(seconds=COUNTDOWN_SECONDS)
    match.save(update_fields=["opponent", "status", "starts_at"])
    return match


@transaction.atomic
def activate_match(match_id):
    """The countdown is over: the match is now in progress with a fresh board."""
    try:
        match = Match.objects.select_for_update().get(pk=match_id)
    except Match.DoesNotExist:
        return None
    if match.status != Match.Status.STARTING:
        return None
    factory = INITIAL_STATES.get(match.game)
    match.state = factory() if factory else {}
    match.status = Match.Status.ACTIVE
    match.started_at = timezone.now()
    match.turn_deadline = timezone.now() + timedelta(
        seconds=getattr(settings, "MOVE_TIME_SECONDS", 60)
    )
    match.save(update_fields=["state", "status", "started_at", "turn_deadline"])
    return match


def activate_due_matches():
    """Safety net: start matches whose countdown ended long ago (e.g. the server restarted)."""
    cutoff = timezone.now() - timedelta(seconds=5)
    due_ids = list(
        Match.objects.filter(status=Match.Status.STARTING, starts_at__lte=cutoff)
        .values_list("id", flat=True)
    )
    for match_id in due_ids:
        activate_match(match_id)