import math

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

from matches.models import Match
from matches.sweeper import ensure_sweeper

from .engine import ENGINE
from .services import MoveError, make_move, player_color, resign


def build_payload(match):
    """What both players are told about the match."""
    state = match.state
    result = None
    reason = None
    if match.status == Match.Status.FINISHED:
        if match.winner_id is None:
            result = "draw"
        else:
            result = "p1" if match.winner_id == match.creator_id else "p2"
        reason = ENGINE.reason(state)  # 'checkmate', 'stalemate', ... or None (resign / timeout)

    seconds_left = None
    if match.status == Match.Status.ACTIVE and match.turn_deadline:
        seconds_left = max(0, math.ceil((match.turn_deadline - timezone.now()).total_seconds()))

    return {
        "type": "state",
        "status": match.status,
        "fen": state["fen"],
        "turn": state["turn"],
        "last_move": state.get("last_move"),
        "check": state.get("check", False),
        "result": result,
        "reason": reason,
        "seconds_left": seconds_left,
        "players": {"p1": match.creator.username, "p2": match.opponent.username},
        "stake": f"{match.stake:.2f}",
    }


def _fresh(match_id):
    return Match.objects.select_related("creator", "opponent").get(pk=match_id)


@database_sync_to_async
def load_match(user_id, match_id):
    try:
        match = Match.objects.select_related("creator", "opponent").get(
            pk=match_id, game=Match.Game.CHESS
        )
    except Match.DoesNotExist:
        return None
    color = player_color(match, user_id)
    if color is None or match.status not in (Match.Status.ACTIVE, Match.Status.FINISHED):
        return None
    return color, build_payload(match)


@database_sync_to_async
def move_db(user_id, match_id, move):
    match = make_move(user_id, match_id, move)
    return build_payload(_fresh(match.pk))


@database_sync_to_async
def resign_db(user_id, match_id):
    match = resign(user_id, match_id)
    return build_payload(_fresh(match.pk))


class ChessConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4401)
            return

        self.user_id = user.id
        ensure_sweeper()
        self.match_id = self.scope["url_route"]["kwargs"]["match_id"]

        loaded = await load_match(self.user_id, self.match_id)
        if not loaded:
            await self.close(code=4403)  # not your match
            return

        self.color, payload = loaded
        self.group = f"chess_{self.match_id}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_state(payload)

    async def disconnect(self, code):
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content):
        action = content.get("action")
        try:
            if action == "move":
                move = content.get("move")
                if not (isinstance(move, str) and 4 <= len(move) <= 5):
                    return await self.send_error("Invalid move.")
                payload = await move_db(self.user_id, self.match_id, move)
                await self.publish(payload)
            elif action == "resign":
                payload = await resign_db(self.user_id, self.match_id)
                await self.publish(payload)
        except MoveError as e:
            await self.send_error(str(e))
            loaded = await load_match(self.user_id, self.match_id)
            if loaded:
                await self.send_state(loaded[1])  # put the player's screen back in sync

    # ----- helpers -----
    async def publish(self, payload):
        """Tell both players, and take a finished match off everyone's lobby."""
        await self.channel_layer.group_send(self.group, {"type": "game.state", "payload": payload})
        if payload["status"] == "finished":
            await self.channel_layer.group_send(
                "lobby",
                {
                    "type": "lobby.event",
                    "payload": {"type": "match_removed", "match_id": self.match_id},
                },
            )

    async def send_state(self, payload):
        """Each player also gets their own colour and, on their turn, their legal moves."""
        data = dict(payload)
        data["you"] = self.color
        data["legal_moves"] = []
        if payload["status"] == "active" and payload["turn"] == self.color:
            data["legal_moves"] = ENGINE.legal_moves({"start_fen": payload["fen"], "moves": []})
        await self.send_json(data)

    async def send_error(self, message):
        await self.send_json({"type": "error", "message": message})

    async def game_state(self, event):
        await self.send_state(event["payload"])

    async def game_refresh(self, event):
        """Sent when something happened outside a move (like a timeout): reload and resend."""
        loaded = await load_match(self.user_id, self.match_id)
        if loaded:
            await self.send_state(loaded[1])