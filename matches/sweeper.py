import asyncio
import logging

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from django.utils import timezone

from checkers.services import forfeit_on_timeout as checkers_timeout

from .models import Match

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 2

# Each game says how a timeout is settled (chess will be added here later)
TIMEOUT_HANDLERS = {
    "checkers": checkers_timeout,
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
        await asyncio.sleep(CHECK_EVERY_SECONDS)


def ensure_sweeper():
    """Start the timer checker once per server process."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())