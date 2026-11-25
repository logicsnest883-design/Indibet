"""
Runs tournaments by itself. `tick()` is called every couple of seconds by the background timer.

Each tick it
  1. starts tournaments whose registration has closed (draws the bracket, or cancels and refunds),
  2. settles matches whose check-in time has run out (walkover, or both players out),
  3. starts the next round when its time has come: creates that round's matches and charges the
     match fees.

Rules
- Everybody pays the match fee when their match is created. It is never refunded, even when the
  match ends by walkover.
- A player who cannot pay the fee counts as absent.
- A round starts at its scheduled time, but only inside the daily play window and only after the
  round before it has been decided.
"""
import logging
import random
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from matches.models import Match
from wallet.services import InsufficientFunds, charge_match_fee

from .bracket import (
    BracketError,
    current_round,
    is_finished,
    ready_matches,
    record_double_no_show,
    record_result,
)
from .models import Tournament
from .services import TournamentError, start_tournament

logger = logging.getLogger(__name__)


def _in_play_window(tournament, moment):
    local = timezone.localtime(moment).time()
    return tournament.daily_open <= local < tournament.daily_close


def _save_bracket(tournament, bracket):
    tournament.bracket = bracket
    fields = ["bracket"]
    if is_finished(bracket):
        tournament.status = Tournament.Status.FINISHED
        fields.append("status")
    tournament.save(update_fields=fields)


def _resolve_absence(tournament, match, absent_ids):
    """Record a walkover (one player absent) or a double no-show (both absent) for a match
    that will not be played. Returns True if the match was settled."""
    players = [match.creator_id, match.opponent_id]
    present = [p for p in players if p not in absent_ids]
    if len(present) == 2:
        return False

    try:
        if len(present) == 1:
            bracket = record_result(
                tournament.bracket, match.bracket_round, match.bracket_number, present[0], how="walkover"
            )
            match.status = Match.Status.FINISHED
            match.winner_id = present[0]
        else:
            bracket = record_double_no_show(tournament.bracket, match.bracket_round, match.bracket_number)
            match.status = Match.Status.CANCELLED
    except BracketError:
        logger.exception("Could not settle match %s in tournament %s", match.pk, tournament.pk)
        match.status = Match.Status.CANCELLED
        match.finished_at = timezone.now()
        match.save(update_fields=["status", "finished_at"])
        return False

    match.finished_at = timezone.now()
    match.result_recorded = True
    match.save(update_fields=["status", "winner", "finished_at", "result_recorded"])
    _save_bracket(tournament, bracket)
    return True


# ---------- 1. starting tournaments ----------
def start_due_tournaments(now=None):
    """Start every tournament whose registration has closed. Returns [(id, 'started' or 'cancelled')]."""
    now = now or timezone.now()
    ids = list(
        Tournament.objects.filter(
            status=Tournament.Status.REGISTRATION, registration_closes_at__lte=now
        ).values_list("id", flat=True)
    )
    outcomes = []
    for tournament_id in ids:
        try:
            outcomes.append((tournament_id, start_tournament(tournament_id)))
        except TournamentError:
            continue
    return outcomes


# ---------- 2. missed check-ins ----------
@transaction.atomic
def _resolve_one(match_id):
    tournament_id = Match.objects.filter(pk=match_id).values_list("tournament_id", flat=True).first()
    if tournament_id is None:
        return False
    tournament = Tournament.objects.select_for_update().get(pk=tournament_id)  # tournament first, then match
    match = Match.objects.select_for_update().get(pk=match_id)
    if match.status != Match.Status.WAITING:
        return False

    arrived = set(match.present)
    absent = {p for p in (match.creator_id, match.opponent_id) if p not in arrived}
    if not absent:
        return False  # both arrived, so the game starts
    return _resolve_absence(tournament, match, absent)


def resolve_expired_checkins(now=None):
    """Settle tournament matches whose check-in time has run out. Returns the ids of settled matches."""
    now = now or timezone.now()
    ids = list(
        Match.objects.filter(
            status=Match.Status.WAITING, tournament__isnull=False, checkin_deadline__lte=now
        ).values_list("id", flat=True)
    )
    return [match_id for match_id in ids if _resolve_one(match_id)]


# ---------- 3. starting rounds ----------
def _create_match(tournament, slot, now):
    """Create the real match for a bracket slot and charge both players the match fee."""
    players = [slot["p1"], slot["p2"]]
    random.SystemRandom().shuffle(players)  # a fair draw of who plays first

    match = Match.objects.create(
        game=tournament.game,
        creator_id=players[0],
        opponent_id=players[1],
        stake=Decimal("0.00"),
        status=Match.Status.WAITING,
        tournament=tournament,
        bracket_round=slot["round"],
        bracket_number=slot["number"],
        checkin_deadline=now + timedelta(minutes=tournament.checkin_minutes),
    )

    unpaid = []
    for user_id in players:
        try:
            charge_match_fee(user_id, tournament.match_fee, match.id)
        except InsufficientFunds:
            unpaid.append(user_id)

    if unpaid:
        match.unpaid = unpaid
        match.save(update_fields=["unpaid"])
        _resolve_absence(tournament, match, set(unpaid))  # a player who cannot pay counts as absent
    return match


@transaction.atomic
def _start_next_round(tournament_id, now):
    tournament = Tournament.objects.select_for_update().get(pk=tournament_id)
    if tournament.status != Tournament.Status.RUNNING:
        return []

    rnd = tournament.rounds.select_for_update().filter(started_at__isnull=True).first()
    if rnd is None or now < rnd.starts_at:
        return []
    if not _in_play_window(tournament, now):
        return []  # play pauses overnight

    bracket = tournament.bracket
    waiting_for = current_round(bracket)
    if waiting_for is not None and waiting_for < rnd.number:
        return []  # the round before is still being played

    created = [_create_match(tournament, slot, now).id for slot in ready_matches(bracket, round_number=rnd.number)]
    rnd.started_at = now
    rnd.save(update_fields=["started_at"])
    return created


def start_due_rounds(now=None):
    """Start the next round of every running tournament whose time has come. Returns the new match ids."""
    now = now or timezone.now()
    created = []
    for tournament_id in list(
        Tournament.objects.filter(status=Tournament.Status.RUNNING).values_list("id", flat=True)
    ):
        created += _start_next_round(tournament_id, now)
    return created


def tick(now=None):
    """Everything that has to happen by the clock."""
    now = now or timezone.now()
    start_due_tournaments(now)
    resolved = resolve_expired_checkins(now)
    created = start_due_rounds(now)
    return {"resolved": resolved, "created": created}