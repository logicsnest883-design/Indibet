import asyncio
import logging

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from django.utils import timezone

from checkers.services import forfeit_on_timeout as checkers_timeout
from chessgame.services import forfeit_on_timeout as chess_timeout
from tournaments.checkin import notification_for
from tournaments.results import record_finished_matches
from tournaments.runner import tick as tournament_tick

from .models import Match

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 2

# Each game says how a timeout is settled
TIMEOUT_HANDLERS = {
    "checkers": checkers_timeout,
    "chess": chess_timeout,
}

_task = None


def expire_overdue_matches():
    """Forfeit every active match whose current player ran out of time."""
    overdue = list(
        Match.objects.filter(status=Match.Status.ACTIVE, turn_deadline__lt=timezone.now())
        .values_list("id", "game")
    )
    expired = []
    for match_id, game in overdue:
        handler = TIMEOUT_HANDLERS.get(game)
        if handler and handler(match_id):
            expired.append((match_id, game))
    return expired


@database_sync_to_async
def _expire_overdue():
    return expire_overdue_matches()


@database_sync_to_async
def _tournament_tick():
    """Use the results of finished games first, then do whatever the clock says is due."""
    results = record_finished_matches()
    result = tournament_tick()
    return {
        "resolved": results["recorded"] + result["resolved"],
        "created": results["replays"] + result["created"],
    }


@database_sync_to_async
def _notifications_for(match_ids):
    """(user_id, banner) pairs: what each player should be told about these tournament matches."""
    found = []
    matches = Match.objects.filter(pk__in=match_ids).select_related("tournament", "creator", "opponent")
    for match in matches:
        for user_id in (match.creator_id, match.opponent_id):
            banner = notification_for(match, user_id)
            if banner:
                found.append((user_id, banner))
    # "your match is ready" must come last, so it is the one a player is left looking at
    found.sort(key=lambda pair: pair[1]["kind"] == "ready")
    return found


async def _loop():
    layer = get_channel_layer()
    while True:
        try:
            for match_id, game in await _expire_overdue():
                # Tell both players' boards to refresh, and clear the match from every lobby
                await layer.group_send(f"{game}_{match_id}", {"type": "game.refresh"})
                await layer.group_send(
                    "lobby",
                    {
                        "type": "lobby.event",
                        "payload": {"type": "match_removed", "match_id": match_id},
                    },
                )
        except Exception:
            logger.exception("Timer check failed")

        try:
            result = await _tournament_tick()
            changed = result["created"] + result["resolved"]
            if changed:
                for user_id, banner in await _notifications_for(changed):
                    await layer.group_send(
                        f"user_{user_id}", {"type": "lobby.event", "payload": banner}
                    )
                for match_id in changed:
                    await layer.group_send(f"tmatch_{match_id}", {"type": "tmatch.refresh"})
        except Exception:
            logger.exception("Tournament check failed")

        await asyncio.sleep(CHECK_EVERY_SECONDS)


def ensure_sweeper():
    """Start the timer checker once per server process."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())