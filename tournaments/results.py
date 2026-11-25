"""
Records the result of played tournament games in the bracket.

Every few seconds the timer calls record_finished_matches(). For each tournament game that has
finished and has not been used yet:
  - a win sends the winner to the next round (and ends the tournament after the final),
  - a draw is replayed with the colours swapped, up to MAX_REPLAYS times, and if the game is still
    drawn after that, a coin toss decides who goes through.

Replays cost nothing: the match fee was paid once, for the match.
"""
import logging
import random
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from matches.models import Match

from .bracket import BracketError, record_result
from .models import Tournament
from .runner import _save_bracket

logger = logging.getLogger(__name__)

MAX_REPLAYS = 2  # a drawn match is replayed up to twice, then a coin toss decides it


def _create_replay(tournament, drawn, now):
    """A new game for the same place in the bracket, with the colours swapped."""
    return Match.objects.create(
        game=tournament.game,
        creator_id=drawn.opponent_id,  # swapped: the old second player now moves first
        opponent_id=drawn.creator_id,
        stake=Decimal("0.00"),
        status=Match.Status.WAITING,
        tournament=tournament,
        bracket_round=drawn.bracket_round,
        bracket_number=drawn.bracket_number,
        checkin_deadline=now + timedelta(minutes=tournament.checkin_minutes),
    )


def _mark_recorded(match, extra_fields=()):
    match.result_recorded = True
    match.save(update_fields=["result_recorded", *extra_fields])


def _record_win(tournament, match, winner_id):
    try:
        bracket = record_result(
            tournament.bracket, match.bracket_round, match.bracket_number, winner_id, how="played"
        )
    except BracketError:
        logger.exception("Could not record match %s in tournament %s", match.pk, tournament.pk)
        return False
    _save_bracket(tournament, bracket)
    return True


@transaction.atomic
def _record_one(match_id, now):
    """Use one finished game's result. Returns {'replay': new match id or None}, or None if nothing was done."""
    tournament_id = Match.objects.filter(pk=match_id).values_list("tournament_id", flat=True).first()
    if tournament_id is None:
        return None
    tournament = Tournament.objects.select_for_update().get(pk=tournament_id)  # tournament first, then match
    match = Match.objects.select_for_update().get(pk=match_id)
    if match.result_recorded or match.status != Match.Status.FINISHED:
        return None

    if match.winner_id:
        _record_win(tournament, match, match.winner_id)
        _mark_recorded(match)
        return {"replay": None}

    # A draw: how many games have been played for this place in the bracket?
    games = Match.objects.filter(
        tournament=tournament,
        bracket_round=match.bracket_round,
        bracket_number=match.bracket_number,
        started_at__isnull=False,
    ).count()

    if games > MAX_REPLAYS:
        winner_id = random.SystemRandom().choice([match.creator_id, match.opponent_id])
        state = dict(match.state or {})
        state["coin_toss_winner"] = winner_id
        match.state = state
        _record_win(tournament, match, winner_id)
        _mark_recorded(match, extra_fields=("state",))
        return {"replay": None}

    replay = _create_replay(tournament, match, now)
    _mark_recorded(match)
    return {"replay": replay.id}


def record_finished_matches(now=None):
    """Use the result of every finished tournament game that has not been used yet.
    Returns {'recorded': [match ids], 'replays': [new match ids]}."""
    now = now or timezone.now()
    ids = list(
        Match.objects.filter(
            tournament__isnull=False,
            status=Match.Status.FINISHED,
            result_recorded=False,
            started_at__isnull=False,  # games that were really played (walkovers are settled by the runner)
        ).values_list("id", flat=True)
    )
    recorded, replays = [], []
    for match_id in ids:
        outcome = _record_one(match_id, now)
        if outcome is None:
            continue
        recorded.append(match_id)
        if outcome["replay"]:
            replays.append(outcome["replay"])
    return {"recorded": recorded, "replays": replays}