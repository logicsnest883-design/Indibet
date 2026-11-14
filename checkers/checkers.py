from django.db import transaction
from django.utils import timezone

from matches.models import Match
from wallet.services import settle_draw, settle_match

from .engine import ENGINE, P1, P2, IllegalMove


class MoveError(Exception):
    pass


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

    try:
        new_state = ENGINE.apply(state, path)
    except (IllegalMove, TypeError, ValueError):
        raise MoveError("That move is not allowed.")

    match.state = new_state
    result = ENGINE.result(new_state)
    if result:
        _finish(match, result)

    match.save(update_fields=["state", "status", "winner", "finished_at"])
    return match


@transaction.atomic
def resign(user_id, match_id):
    match = _lock_active_match(match_id)

    color = player_color(match, user_id)
    if color is None:
        raise MoveError("You are not a player in this match.")

    _finish(match, P2 if color == P1 else P1)
    match.save(update_fields=["status", "winner", "finished_at"])
    return match