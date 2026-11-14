"""
Zambian checkers engine (pure Python, no Django inside).

Rules
- 8x8 board, 12 pieces each, dark squares only. P1 (bottom) moves first.
- Men step diagonally FORWARD one square, but capture in ALL four directions (front and behind).
- Kings move and capture in any direction. One square at a time by default;
  use CheckersEngine(flying_kings=True) for long-range kings.
- Captures are NOT mandatory.
- Once a capture is started it must be completed (keep jumping while a jump exists).
  A man that reaches the far row is crowned, and its move ends there.
- SUSPENSION: if a player makes a non-capturing move while one of their pieces could have captured,
  a piece that could have captured is removed after their move, then it is the opponent's turn.
  If several pieces could have captured, the one with the longest capture chain is removed
  (tie: lowest square number). If the piece that moved was the capturer, it is removed where it landed.
- You lose when you have no pieces or no legal move on your turn.
- Draw: 80 plies with no capture, suspension or man move, or the same position 3 times.

Board: list of 64 numbers, index = row * 8 + col. Row 0 is the top (P2's side), row 7 the bottom (P1's side).
    0 empty, 1 P1 man, 2 P1 king, -1 P2 man, -2 P2 king

A move is the FULL path of squares a piece travels: [from, land1, land2, ...]
"""

P1, P2 = "p1", "p2"
EMPTY, P1_MAN, P1_KING, P2_MAN, P2_KING = 0, 1, 2, -1, -2
CAPTURED = 99  # internal marker for a piece already jumped in the current chain

DRAW_PLY_LIMIT = 80
REPETITION_LIMIT = 3

ALL_DIRS = ((-1, -1), (-1, 1), (1, -1), (1, 1))
_SYMBOLS = {0: ".", 1: "o", 2: "O", -1: "x", -2: "X"}


class IllegalMove(ValueError):
    pass


# ---------- helpers ----------
def rc(sq):
    return divmod(sq, 8)


def sq_of(r, c):
    return r * 8 + c


def _inside(r, c):
    return 0 <= r < 8 and 0 <= c < 8


def _owner(piece):
    if piece in (P1_MAN, P1_KING):
        return P1
    if piece in (P2_MAN, P2_KING):
        return P2
    return None


def _is_enemy(other, piece):
    return abs(other) in (1, 2) and (other > 0) != (piece > 0)


def _forward_dirs(piece):
    return ((-1, -1), (-1, 1)) if piece > 0 else ((1, -1), (1, 1))


def _king_row(piece):
    return 0 if piece > 0 else 7


def _between(a, b):
    """Squares strictly between a and b along their diagonal."""
    (ra, ca), (rb, cb) = rc(a), rc(b)
    dr, dc = (rb > ra) - (rb < ra), (cb > ca) - (cb < ca)
    r, c, out = ra + dr, ca + dc, []
    while (r, c) != (rb, cb):
        out.append(sq_of(r, c))
        r, c = r + dr, c + dc
    return out


def _key(board, turn):
    return turn + ":" + "".join(_SYMBOLS[p] for p in board)


def _make_state(board, turn):
    return {
        "board": board,
        "turn": turn,
        "ply_clock": 0,
        "positions": {_key(board, turn): 1},
        "last_move": None,
        "suspended": None,
    }


def state_from_pieces(pieces, turn=P1):
    """Build a state from {square: piece}. Used for tests."""
    board = [EMPTY] * 64
    for sq, piece in pieces.items():
        board[sq] = piece
    return _make_state(board, turn)


# ---------- the engine ----------
class CheckersEngine:
    name = "checkers"

    def __init__(self, flying_kings=True):
        self.flying_kings = flying_kings

    def _steps(self, board, sq, piece):
        r, c = rc(sq)
        is_king = abs(piece) == 2
        dirs = ALL_DIRS if is_king else _forward_dirs(piece)
        slide = is_king and self.flying_kings
        out = []
        for dr, dc in dirs:
            nr, nc = r + dr, c + dc
            while _inside(nr, nc) and board[sq_of(nr, nc)] == EMPTY:
                out.append([sq, sq_of(nr, nc)])
                if not slide:
                    break
                nr, nc = nr + dr, nc + dc
        return out

    def _jumps(self, board, sq, piece):
        r, c = rc(sq)
        slide = abs(piece) == 2 and self.flying_kings
        out = []
        for dr, dc in ALL_DIRS:
            nr, nc = r + dr, c + dc
            if slide:
                while _inside(nr, nc) and board[sq_of(nr, nc)] == EMPTY:
                    nr, nc = nr + dr, nc + dc
            if not _inside(nr, nc) or not _is_enemy(board[sq_of(nr, nc)], piece):
                continue
            over = sq_of(nr, nc)
            lr, lc = nr + dr, nc + dc
            while _inside(lr, lc) and board[sq_of(lr, lc)] == EMPTY:
                out.append((over, sq_of(lr, lc)))
                if not slide:
                    break
                lr, lc = lr + dr, lc + dc
        return out

    def _capture_paths(self, board, sq, piece, path):
        results = []
        for over, land in self._jumps(board, sq, piece):
            b = board[:]
            b[sq] = EMPTY
            b[over] = CAPTURED
            crowned = abs(piece) == 1 and rc(land)[0] == _king_row(piece)
            new_piece = piece * 2 if crowned else piece
            b[land] = new_piece
            new_path = path + [land]
            if crowned:
                results.append(new_path)
            else:
                deeper = self._capture_paths(b, land, new_piece, new_path)
                results.extend(deeper if deeper else [new_path])
        return results

    def _side_captures(self, board, turn):
        paths = []
        for sq, piece in enumerate(board):
            if _owner(piece) == turn:
                paths.extend(self._capture_paths(board, sq, piece, [sq]))
        return paths

    # --- public methods ---
    def initial_state(self):
        board = [EMPTY] * 64
        for r in range(8):
            for c in range(8):
                if (r + c) % 2 == 1:
                    if r <= 2:
                        board[sq_of(r, c)] = P2_MAN
                    elif r >= 5:
                        board[sq_of(r, c)] = P1_MAN
        return _make_state(board, P1)

    def legal_moves(self, state):
        board, turn = state["board"], state["turn"]
        captures, steps = [], []
        for sq, piece in enumerate(board):
            if _owner(piece) != turn:
                continue
            captures.extend(self._capture_paths(board, sq, piece, [sq]))
            steps.extend(self._steps(board, sq, piece))
        return captures + steps

    def capturing_squares(self, state):
        """Squares of the current player's pieces that can capture right now."""
        return sorted({p[0] for p in self._side_captures(state["board"], state["turn"])})

    def apply(self, state, move):
        move = [int(s) for s in move]
        if move not in self.legal_moves(state):
            raise IllegalMove(f"Illegal move: {move}")

        turn = state["turn"]
        board = state["board"][:]
        start, end = move[0], move[-1]
        piece = board[start]
        was_man = abs(piece) == 1

        capture_paths = self._side_captures(state["board"], turn)

        board[start] = EMPTY
        captured = 0
        for a, b in zip(move, move[1:]):
            for s in _between(a, b):
                if board[s] != EMPTY:
                    board[s] = EMPTY
                    captured += 1
        if was_man and rc(end)[0] == _king_row(piece):
            piece *= 2
        board[end] = piece

        # Suspension: declined a capture, so a capturing piece is removed.
        suspended = None
        if captured == 0 and capture_paths:
            longest = {}
            for p in capture_paths:
                longest[p[0]] = max(longest.get(p[0], 0), len(p) - 1)
            offender = min(longest, key=lambda s: (-longest[s], s))
            suspended = end if offender == start else offender
            board[suspended] = EMPTY

        next_turn = P2 if turn == P1 else P1
        positions = dict(state["positions"])
        key = _key(board, next_turn)
        positions[key] = positions.get(key, 0) + 1

        reset = captured > 0 or was_man or suspended is not None
        return {
            "board": board,
            "turn": next_turn,
            "ply_clock": 0 if reset else state["ply_clock"] + 1,
            "positions": positions,
            "last_move": move,
            "suspended": suspended,
        }

    def result(self, state):
        """None while the game is on, otherwise 'p1', 'p2' or 'draw'."""
        board = state["board"]
        if not any(_owner(p) == P1 for p in board):
            return P2
        if not any(_owner(p) == P2 for p in board):
            return P1
        if not self.legal_moves(state):
            return P2 if state["turn"] == P1 else P1
        if state["ply_clock"] >= DRAW_PLY_LIMIT:
            return "draw"
        if state["positions"].get(_key(board, state["turn"]), 0) >= REPETITION_LIMIT:
            return "draw"
        return None


ENGINE = CheckersEngine()
FLYING_ENGINE = CheckersEngine(flying_kings=True)