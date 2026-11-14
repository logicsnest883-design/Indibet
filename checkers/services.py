from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from matches.models import Match
from wallet.services import settle_draw, settle_match

from .engine import ENGINE, P1, P2, IllegalMove


class MoveError(Exception):
    pass


def _move_seconds():
    return getattr(settings, "MOVE_TIME_SECONDS", 60)


def player_color(match, user_id):
    """The match creator plays p1 (moves first), the joiner plays p2."""
    if user_id == match.creator_id:
        return P1
    if user_id == match.opponent_id:
        return P2
    return None


def _finish(match, result):
    """Mark the match finished and pay out. `result` is 'p1', 'p2' or 'draw'."""
    match.status = Match.Status.FINISHED
    match.finished_at = timezone.now()
    match.turn_deadline = None

    if result == "draw":
        settle_draw(match.id, match.creator_id, match.opponent_id, match.stake)
        return

    if result == P1:
        winner_id, loser_id = match.creator_id, match.opponent_id
    else:
        winner_id, loser_id = match.opponent_id, match.creator_id
    match.winner_id = winner_id
    settle_match(match.id, winner_id, loser_id, match.stake)


def _lock_active_match(match_id):
    try:
        match = Match.objects.select_for_update().get(pk=match_id, game=Match.Game.CHECKERS)
    except Match.DoesNotExist:
        raise MoveError("Match not found.")
    if match.status != Match.Status.ACTIVE:
        raise MoveError("This match is not in progress.")
    return match


@transaction.atomic
def make_move(user_id, match_id, path):
    match = _lock_active_match(match_id)

    color = player_color(match, user_id)
    if color is None:
        raise MoveError("You are not a player in this match.")

    state = match.state
    if state["turn"] != color:
        raise MoveError("It is not your turn.")
    if match.turn_deadline and timezone.now() > match.turn_deadline:
        raise MoveError("Your time is up.")

    try:
        new_state = ENGINE.apply(state, path)
    except (IllegalMove, TypeError, ValueError):
        raise MoveError("That move is not allowed.")

    match.state = new_state
    result = ENGINE.result(new_state)
    if result:
        _finish(match, result)
    else:
        match.turn_deadline = timezone.now() + timedelta(seconds=_move_seconds())

    match.save(update_fields=["state", "status", "winner", "finished_at", "turn_deadline"])
    return match


@transaction.atomic
def resign(user_id, match_id):
    match = _lock_active_match(match_id)

    color = player_color(match, user_id)
    if color is None:
        raise MoveError("You are not a player in this match.")

    _finish(match, P2 if color == P1 else P1)
    match.save(update_fields=["status", "winner", "finished_at", "turn_deadline"])
    return match


@transaction.atomic
def forfeit_on_timeout(match_id):
    """The player whose turn it is ran out of time and loses. Returns True if that happened."""
    match = (
        Match.objects.select_for_update()
        .filter(pk=match_id, game=Match.Game.CHECKERS, status=Match.Status.ACTIVE)
        .first()
    )
    if not match or not match.turn_deadline or match.turn_deadline > timezone.now():
        return False

    loser = match.state["turn"]
    _finish(match, P2 if loser == P1 else P1)
    match.save(update_fields=["status", "winner", "finished_at", "turn_deadline"])
    return True