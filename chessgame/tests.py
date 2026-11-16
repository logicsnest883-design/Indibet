import json
import random
import unittest

import chess

from .engine import ENGINE, IllegalMove, P1, P2, state_from_fen


def play(state, *moves):
    for m in moves:
        state = ENGINE.apply(state, m)
    return state


class ChessRules(unittest.TestCase):
    def test_start_position(self):
        s = ENGINE.initial_state()
        self.assertEqual(len(ENGINE.legal_moves(s)), 20)
        self.assertEqual(s["turn"], P1)
        self.assertIsNone(ENGINE.result(s))

    def test_move_changes_turn(self):
        s = play(ENGINE.initial_state(), "e2e4")
        self.assertEqual(s["turn"], P2)
        self.assertEqual(s["last_move"], "e2e4")
        self.assertEqual(len(s["moves"]), 1)

    def test_illegal_moves_rejected(self):
        s = ENGINE.initial_state()
        for bad in ("e2e5", "e7e5", "e1e2", "zz", "", None, "0000"):
            with self.assertRaises(IllegalMove):
                ENGINE.apply(s, bad)

    def test_fools_mate(self):
        s = play(ENGINE.initial_state(), "f2f3", "e7e5", "g2g4", "d8h4")
        self.assertEqual(ENGINE.result(s), P2)
        self.assertEqual(ENGINE.reason(s), "checkmate")

    def test_scholars_mate(self):
        s = play(ENGINE.initial_state(), "e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7")
        self.assertEqual(ENGINE.result(s), P1)

    def test_stalemate_is_a_draw(self):
        s = state_from_fen("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
        self.assertEqual(ENGINE.legal_moves(s), [])
        self.assertEqual(ENGINE.result(s), "draw")
        self.assertEqual(ENGINE.reason(s), "stalemate")

    def test_insufficient_material_is_a_draw(self):
        s = state_from_fen("8/8/8/4k3/8/8/4K3/8 w - - 0 1")
        self.assertEqual(ENGINE.result(s), "draw")

    def test_promotion_needs_a_piece(self):
        s = state_from_fen("7k/4P3/8/8/8/8/8/K7 w - - 0 1")
        moves = ENGINE.legal_moves(s)
        for piece in "qrbn":
            self.assertIn("e7e8" + piece, moves)
        self.assertNotIn("e7e8", moves)
        with self.assertRaises(IllegalMove):
            ENGINE.apply(s, "e7e8")
        s2 = ENGINE.apply(s, "e7e8q")
        self.assertEqual(ENGINE._board(s2).piece_at(chess.E8).symbol(), "Q")

    def test_castling(self):
        s = state_from_fen("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
        moves = ENGINE.legal_moves(s)
        self.assertIn("e1g1", moves)
        self.assertIn("e1c1", moves)
        s2 = ENGINE.apply(s, "e1g1")
        self.assertEqual(s2["fen"].split()[0], "r3k2r/8/8/8/8/8/8/R4RK1")

    def test_en_passant(self):
        s = play(ENGINE.initial_state(), "e2e4", "a7a6", "e4e5", "d7d5")
        self.assertIn("e5d6", ENGINE.legal_moves(s))
        s2 = ENGINE.apply(s, "e5d6")
        board = ENGINE._board(s2)
        self.assertIsNone(board.piece_at(chess.D5))  # the captured pawn is gone
        self.assertEqual(board.piece_at(chess.D6).symbol(), "P")

    def test_threefold_repetition_is_a_draw(self):
        s = ENGINE.initial_state()
        cycle = ["g1f3", "g8f6", "f3g1", "f6g8"]
        for i, move in enumerate(cycle * 2):
            self.assertIsNone(ENGINE.result(s), f"ended early at ply {i}")
            s = ENGINE.apply(s, move)
        self.assertEqual(ENGINE.result(s), "draw")
        self.assertEqual(ENGINE.reason(s), "repetition")

    def test_state_is_json_safe(self):
        s = play(ENGINE.initial_state(), "e2e4", "e7e5")
        self.assertEqual(json.loads(json.dumps(s)), s)

    def test_random_games_never_crash(self):
        rng = random.Random(7)
        for _ in range(10):
            s = ENGINE.initial_state()
            for _ply in range(120):
                if ENGINE.result(s):
                    break
                s = ENGINE.apply(s, rng.choice(ENGINE.legal_moves(s)))