from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from matches.models import Match
from matches.sweeper import ensure_sweeper

from .checkin import CheckInError, check_in, room_payload


class NotifyConsumer(AsyncJsonWebsocketConsumer):
    """Delivers banners like 'your match is ready' to a logged-in player on any page."""

    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        ensure_sweeper()  # keeps the tournament timer running whenever someone is on the site
        self.group = f"user_{user.id}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def lobby_event(self, event):
        payload = event["payload"]
        if payload.get("type") == "notify":  # ignore the lobby's own messages
            await self.send_json(payload)


def _load(match_id):
    return Match.objects.select_related("tournament", "creator", "opponent").get(
        pk=match_id, tournament__isnull=False
    )


@database_sync_to_async
def load_room(user_id, match_id, do_check_in):
    """The room's current state for this player (checking them in first if asked), or None."""
    try:
        match = _load(match_id)
    except Match.DoesNotExist:
        return None
    if user_id not in (match.creator_id, match.opponent_id):
        return None

    if do_check_in:
        try:
            check_in(user_id, match_id)
        except CheckInError:
            pass  # for example: check-in is already closed; we still show what is going on
        match = _load(match_id)
    return room_payload(match, user_id)


class TMatchConsumer(AsyncJsonWebsocketConsumer):
    """The check-in room of one tournament match."""

    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4401)
            return

        ensure_sweeper()
        self.user_id = user.id
        self.match_id = self.scope["url_route"]["kwargs"]["match_id"]

        payload = await load_room(self.user_id, self.match_id, do_check_in=True)
        if payload is None:
            await self.close(code=4403)  # not your match
            return

        self.group = f"tmatch_{self.match_id}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_json(payload)

        if payload["status"] == "active":
            # The game has started: make sure the other player's room finds out too
            await self.channel_layer.group_send(self.group, {"type": "tmatch.refresh"})

    async def disconnect(self, code):
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def tmatch_refresh(self, event):
        payload = await load_room(self.user_id, self.match_id, do_check_in=False)
        if payload:
            await self.send_json(payload)