import asyncio
import math
from decimal import Decimal, InvalidOperation

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from wallet.models import Wallet
from wallet.services import InsufficientFunds

from .models import Match
from .services import (
    COUNTDOWN_SECONDS,
    MatchError,
    activate_due_matches,
    activate_match,
    cancel_match,
    create_match,
    join_match,
)
from .sweeper import ensure_sweeper

LOBBY_GROUP = "lobby"
OFFLINE_GRACE_SECONDS = 5  # a page reload or short drop does not hide your matches
MAX_STAKE = Decimal("1000000")

ONLINE = {}   # user_id -> number of open lobby connections
PENDING = {}  # user_id -> task that hides their matches if they don't come back
TASKS = set()  # keeps countdown tasks alive


def match_to_dict(match):
    return {
        "id": match.id,
        "game": match.get_game_display(),
        "status": match.status,
        "creator_id": match.creator_id,
        "creator": match.creator.username,
        "opponent_id": match.opponent_id,
        "opponent": match.opponent.username if match.opponent_id else None,
        "stake": f"{match.stake:.2f}",
    }


# ---------- database helpers ----------
@database_sync_to_async
def activate_due_matches_db():
    activate_due_matches()


@database_sync_to_async
def get_visible_matches():
    """Open matches whose creators are online, plus every match that is starting or in progress.
    Tournament matches never show here."""
    qs = (
        Match.objects.filter(
            Q(status=Match.Status.OPEN, creator_id__in=list(ONLINE.keys()))
            | Q(status__in=[Match.Status.STARTING, Match.Status.ACTIVE]),
            tournament__isnull=True,
        )
        .select_related("creator", "opponent")
        .order_by("created_at")
    )
    return [match_to_dict(m) for m in qs]


@database_sync_to_async
def get_open_matches_of(user_id):
    qs = Match.objects.filter(
        status=Match.Status.OPEN, creator_id=user_id
    ).select_related("creator", "opponent")
    return [match_to_dict(m) for m in qs]


@database_sync_to_async
def get_my_countdown(user_id):
    """If the player is in a match that is counting down, return what is left (for page reloads)."""
    m = (
        Match.objects.filter(status=Match.Status.STARTING, tournament__isnull=True)
        .filter(Q(creator_id=user_id) | Q(opponent_id=user_id))
        .select_related("creator", "opponent")
        .first()
    )
    if not m or not m.starts_at:
        return None
    seconds = max(0, math.ceil((m.starts_at - timezone.now()).total_seconds()))
    other = m.opponent.username if m.creator_id == user_id else m.creator.username
    return {
        "match_id": m.id,
        "seconds": seconds,
        "message": f"Your match against {other} is about to begin.",
    }


@database_sync_to_async
def get_balance(user_id):
    wallet, _ = Wallet.objects.get_or_create(user_id=user_id)
    return {"available": f"{wallet.available:.2f}", "held": f"{wallet.held:.2f}"}


@database_sync_to_async
def create_match_db(user_id, game, stake):
    user = get_user_model().objects.get(pk=user_id)
    match = create_match(user, game, stake)
    match = Match.objects.select_related("creator", "opponent").get(pk=match.pk)
    return match_to_dict(match)


@database_sync_to_async
def cancel_match_db(user_id, match_id):
    user = get_user_model().objects.get(pk=user_id)
    cancel_match(user, match_id)


@database_sync_to_async
def join_match_db(user_id, match_id, online_ids):
    user = get_user_model().objects.get(pk=user_id)
    match = join_match(user, match_id, online_ids)
    match = Match.objects.select_related("creator", "opponent").get(pk=match.pk)
    return match_to_dict(match)


@database_sync_to_async
def activate_match_db(match_id):
    match = activate_match(match_id)
    if not match:
        return None
    match = Match.objects.select_related("creator", "opponent").get(pk=match.pk)
    return match_to_dict(match)


# ---------- background jobs ----------
async def hide_matches_later(user_id, channel_layer):
    """If the creator stays offline past the grace period, remove their open matches from every lobby."""
    try:
        await asyncio.sleep(OFFLINE_GRACE_SECONDS)
    except asyncio.CancelledError:
        return

    if ONLINE.get(user_id, 0) > 0:
        return
    ONLINE.pop(user_id, None)
    PENDING.pop(user_id, None)

    for m in await get_open_matches_of(user_id):
        await channel_layer.group_send(
            LOBBY_GROUP,
            {"type": "lobby.event", "payload": {"type": "match_removed", "match_id": m["id"]}},
        )


async def start_match_later(match_id):
    """After the countdown: start the match and send both players to the board."""
    await asyncio.sleep(COUNTDOWN_SECONDS)
    layer = get_channel_layer()

    match = await activate_match_db(match_id)
    if not match:
        return

    await layer.group_send(
        LOBBY_GROUP,
        {"type": "lobby.event", "payload": {"type": "match_updated", "match": match}},
    )
    for uid in (match["creator_id"], match["opponent_id"]):
        await layer.group_send(
            f"user_{uid}",
            {
                "type": "lobby.event",
                "payload": {
                    "type": "match_start",
                    "match_id": match["id"],
                    "url": f"/matches/{match['id']}/",
                },
            },
        )


def schedule_start(match_id):
    task = asyncio.create_task(start_match_later(match_id))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)


# ---------- the lobby consumer ----------
class LobbyConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4401)
            return

        self.user_id = user.id
        self.user_group = f"user_{self.user_id}"
        ensure_sweeper()

        pending = PENDING.pop(self.user_id, None)
        if pending:
            pending.cancel()

        first_connection = self.user_id not in ONLINE
        ONLINE[self.user_id] = ONLINE.get(self.user_id, 0) + 1

        await self.channel_layer.group_add(LOBBY_GROUP, self.channel_name)
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.accept()

        await activate_due_matches_db()
        await self.send_json(
            {
                "type": "init",
                "matches": await get_visible_matches(),
                "balance": await get_balance(self.user_id),
                "countdown": await get_my_countdown(self.user_id),
            }
        )

        # The player just came online, so their open matches appear for everyone.
        if first_connection:
            for m in await get_open_matches_of(self.user_id):
                await self.broadcast({"type": "match_created", "match": m})

    async def disconnect(self, code):
        if not hasattr(self, "user_id"):
            return
        await self.channel_layer.group_discard(LOBBY_GROUP, self.channel_name)
        await self.channel_layer.group_discard(self.user_group, self.channel_name)

        ONLINE[self.user_id] = max(0, ONLINE.get(self.user_id, 1) - 1)
        if ONLINE[self.user_id] == 0:
            PENDING[self.user_id] = asyncio.create_task(
                hide_matches_later(self.user_id, self.channel_layer)
            )

    async def receive_json(self, content):
        action = content.get("action")
        try:
            if action == "create":
                await self.handle_create(content)
            elif action == "cancel":
                await self.handle_cancel(content)
            elif action == "join":
                await self.handle_join(content)
        except InsufficientFunds:
            await self.send_error("You don't have enough balance for that stake.")
        except MatchError as e:
            await self.send_error(str(e))

    # ----- actions -----
    async def handle_create(self, content):
        game = content.get("game")
        if game not in Match.Game.values:
            return await self.send_error("Choose a valid game.")

        try:
            stake = Decimal(str(content.get("stake"))).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return await self.send_error("Enter a valid stake.")
        if not stake.is_finite() or stake < 1 or stake > MAX_STAKE:
            return await self.send_error("Stake must be between K1 and K1,000,000.")

        match = await create_match_db(self.user_id, game, stake)
        await self.send_balance()
        await self.broadcast({"type": "match_created", "match": match})

    async def handle_cancel(self, content):
        try:
            match_id = int(content.get("match_id"))
        except (TypeError, ValueError):
            return
        await cancel_match_db(self.user_id, match_id)
        await self.send_balance()
        await self.broadcast({"type": "match_removed", "match_id": match_id})

    async def handle_join(self, content):
        try:
            match_id = int(content.get("match_id"))
        except (TypeError, ValueError):
            return

        match = await join_match_db(self.user_id, match_id, set(ONLINE.keys()))
        await self.send_balance()

        # Everyone's lobby: this match is no longer joinable
        await self.broadcast({"type": "match_updated", "match": match})

        # Both players: the countdown starts now
        for uid, message in (
            (match["creator_id"], f"{match['opponent']} joined your match!"),
            (match["opponent_id"], f"You joined {match['creator']}'s match."),
        ):
            await self.channel_layer.group_send(
                f"user_{uid}",
                {
                    "type": "lobby.event",
                    "payload": {
                        "type": "countdown",
                        "match_id": match["id"],
                        "seconds": COUNTDOWN_SECONDS,
                        "message": message,
                    },
                },
            )

        schedule_start(match["id"])

    # ----- helpers -----
    async def send_balance(self):
        balance = await get_balance(self.user_id)
        await self.send_json({"type": "balance", **balance})

    async def send_error(self, message):
        await self.send_json({"type": "error", "message": message})

    async def broadcast(self, payload):
        await self.channel_layer.group_send(LOBBY_GROUP, {"type": "lobby.event", "payload": payload})

    async def lobby_event(self, event):
        await self.send_json(event["payload"])