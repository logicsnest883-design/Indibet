"""
Check-in for tournament matches.

When a round starts, each match waits in a "room". A player is present once they open the room.
When both players are present the game starts. If the check-in time runs out first, the timer
(tournaments/runner.py) settles the match as a walkover or a double no-show.
"""
import math
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from matches.models import Match
from matches.services import INITIAL_STATES

from .schedule import round_name


class CheckInError(Exception):
    pass


def round_label(match):
    """Final, Semi-finals, Quarter-finals, Round of 16..."""
    bracket = match.tournament.bracket
    return round_name(len(bracket["rounds"][match.bracket_round - 1]))


def _is_final(match):
    return match.bracket_round == len(match.tournament.bracket["rounds"])


def _activate(match, now):
    factory = INITIAL_STATES.get(match.game)
    match.state = factory() if factory else {}
    match.status = Match.Status.ACTIVE
    match.started_at = now
    match.turn_deadline = now + timedelta(seconds=getattr(settings, "MOVE_TIME_SECONDS", 60))


@transaction.atomic
def check_in(user_id, match_id, now=None):
    """The player has opened the room. When both players have, the game starts."""
    now = now or timezone.now()
    try:
        match = Match.objects.select_for_update().get(pk=match_id, tournament__isnull=False)
    except Match.DoesNotExist:
        raise CheckInError("That match does not exist.")

    if user_id not in (match.creator_id, match.opponent_id):
        raise CheckInError("This is not your match.")
    if match.status != Match.Status.WAITING:
        return match  # it has already started or finished
    if match.checkin_deadline and now > match.checkin_deadline:
        raise CheckInError("Check-in is closed.")

    present = list(match.present)
    if user_id not in present:
        present.append(user_id)
    match.present = present

    if set(present) == {match.creator_id, match.opponent_id}:
        _activate(match, now)
        match.save(update_fields=["present", "state", "status", "started_at", "turn_deadline"])
    else:
        match.save(update_fields=["present"])
    return match


def outcome_for(match, user_id):
    """What happened to this player in this match: (code, text), or (None, '') while it is still open."""
    if match.status in (Match.Status.WAITING, Match.Status.ACTIVE):
        return None, ""

    if match.status == Match.Status.CANCELLED:
        if len(match.unpaid) == 2:
            return "both_out", "Neither player could pay the match fee, so you are both out."
        return "both_out", "Neither player arrived in time, so you are both out."

    final = _is_final(match)

    if match.winner_id is None:
        toss = (match.state or {}).get("coin_toss_winner")
        if toss is None:
            return "draw", "Draw. You play again with the colours swapped."
        if toss == user_id:
            ending = "You are the champion!" if final else "You go through."
            return "advance", f"Drawn three times, so a coin toss decided it. It went your way. {ending}"
        ending = "You finish second." if final else "You are out."
        return "out", f"Drawn three times, so a coin toss decided it. It went against you. {ending}"

    won = match.winner_id == user_id
    if match.started_at is None:  # nobody played: it was settled as a walkover
        if user_id in match.unpaid:
            return "out", "You could not pay the match fee, so you are out."
        if match.unpaid:
            return "advance", "Your opponent could not pay the match fee. You go through."
        if won:
            return "advance", "Your opponent did not arrive in time. You go through."
        return "out", "You did not arrive in time, so you are out."

    if won:
        if final:
            return "won", "You won the final. You are the champion!"
        return "won", "You won this match. You go through to the next round."
    if final:
        return "lost", "You lost the final. You finish second."
    return "lost", "You lost this match. You are out."


def room_payload(match, user_id, now=None):
    """Everything the check-in room shows."""
    now = now or timezone.now()
    other = match.opponent if user_id == match.creator_id else match.creator
    present = set(match.present)

    seconds_left = None
    if match.status == Match.Status.WAITING and match.checkin_deadline:
        seconds_left = max(0, math.ceil((match.checkin_deadline - now).total_seconds()))

    code, text = outcome_for(match, user_id)
    return {
        "type": "state",
        "status": match.status,
        "tournament": match.tournament.name,
        "round": round_label(match),
        "opponent": other.username,
        "you_here": user_id in present,
        "opponent_here": other.id in present,
        "seconds_left": seconds_left,
        "outcome": code,
        "message": text,
        "board_url": f"/matches/{match.id}/" if match.status == Match.Status.ACTIVE else None,
        "tournament_url": f"/tournaments/{match.tournament_id}/",
    }


def notification_for(match, user_id):
    """The banner to show a player about this match, or None."""
    name = match.tournament.name
    if match.status == Match.Status.WAITING:
        minutes = match.tournament.checkin_minutes
        return {
            "type": "notify",
            "kind": "ready",
            "text": f"Your {round_label(match)} match in {name} is ready. Open it within {minutes} minutes.",
            "url": f"/tournaments/match/{match.id}/",
        }

    code, text = outcome_for(match, user_id)
    if code is None:
        return None
    return {
        "type": "notify",
        "kind": code,
        "text": f"{name}: {text}",
        "url": f"/tournaments/{match.tournament_id}/",
    }


def waiting_notification(user_id, now=None):
    """The 'your match is ready' banner for a player whose match is waiting right now, or None."""
    now = now or timezone.now()
    match = (
        Match.objects.filter(
            status=Match.Status.WAITING, tournament__isnull=False, checkin_deadline__gt=now
        )
        .filter(Q(creator_id=user_id) | Q(opponent_id=user_id))
        .select_related("tournament", "creator", "opponent")
        .first()
    )
    if match is None:
        return None
    payload = notification_for(match, user_id)
    payload["seconds_left"] = max(0, math.ceil((match.checkin_deadline - now).total_seconds()))
    return payload