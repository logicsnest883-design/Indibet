"""
Chess engine. The rules come from the python-chess library, so castling, en passant,
promotion, check, checkmate and stalemate are all handled by well-tested code.

p1 = white (the match creator, moves first), p2 = black (the joiner).

State (plain JSON-safe dict):
    start_fen   the position the game started from
    moves       every move played so far, as text like "e2e4" (promotions like "e7e8q")
    fen         the current position
    turn        "p1" or "p2"
    last_move   the last move played, or None
    check       True if the player to move is in check

A move is a text like "e2e4". Promotions must include the piece: "e7e8q", "e7e8r", "e7e8b", "e7e8n".
"""
import chess

P1, P2 = "p1", "p2"


class IllegalMove(ValueError):
    pass


def _color_name(color):
    return P1 if color == chess.WHITE else P2


def _make_state(board, start_fen, moves, last_move):
    return {
        "start_fen": start_fen,
        "moves": moves,
        "fen": board.fen(),
        "turn": _color_name(board.turn),
        "last_move": last_move,
        "check": board.is_check(),
    }


def state_from_fen(fen):
    """Start from any position. Used for tests and puzzles."""
    board = chess.Board(fen)
    return _make_state(board, fen, [], None)


class ChessEngine:
    name = "chess"

    def _board(self, state):
        """Rebuild the game from its start so repetitions are counted correctly."""
        board = chess.Board(state["start_fen"])
        for uci in state["moves"]:
            board.push_uci(uci)
        return board

    def _outcome(self, board):
        """Returns (result, reason). result is 'p1', 'p2', 'draw' or None while the game goes on."""
        outcome = board.outcome()  # checkmate, stalemate, insufficient material, 75-move, fivefold
        if outcome:
            result = "draw" if outcome.winner is None else _color_name(outcome.winner)
            return result, outcome.termination.name.lower()
        if board.is_repetition(3):
            return "draw", "repetition"
        if board.is_fifty_moves():
            return "draw", "fifty_moves"
        return None, None

    # --- public methods ---
    def initial_state(self):
        board = chess.Board()
        return _make_state(board, board.fen(), [], None)

    def legal_moves(self, state):
        return [m.uci() for m in self._board(state).legal_moves]

    def apply(self, state, move):
        board = self._board(state)
        try:
            parsed = chess.Move.from_uci(str(move))
        except ValueError:
            raise IllegalMove(f"Illegal move: {move}")
        if parsed not in board.legal_moves:
            raise IllegalMove(f"Illegal move: {move}")

        board.push(parsed)
        return _make_state(board, state["start_fen"], state["moves"] + [parsed.uci()], parsed.uci())

    def result(self, state):
        """None while the game is on, otherwise 'p1', 'p2' or 'draw'."""
        return self._outcome(self._board(state))[0]

    def reason(self, state):
        """Why the game ended: 'checkmate', 'stalemate', 'repetition', ... or None."""
        return self._outcome(self._board(state))[1]


ENGINE = ChessEngine()