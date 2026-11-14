import unittest

from .engine import (
    ENGINE, IllegalMove, P1, P2, P1_MAN, P1_KING, P2_MAN,
    sq_of as sq, state_from_pieces,
)


class CheckersRules(unittest.TestCase):
    def test_start_position_has_7_opening_moves(self):
        moves = ENGINE.legal_moves(ENGINE.initial_state())
        self.assertEqual(len(moves), 7)

    def test_capture_is_optional(self):
        s = state_from_pieces({sq(5, 2): P1_MAN, sq(4, 3): P2_MAN, sq(7, 0): P1_MAN, sq(0, 1): P2_MAN})
        moves = ENGINE.legal_moves(s)
        self.assertIn([sq(5, 2), sq(3, 4)], moves)
        self.assertIn([sq(7, 0), sq(6, 1)], moves)

    def test_man_captures_backward(self):
        s = state_from_pieces({sq(4, 3): P1_MAN, sq(5, 4): P2_MAN})
        self.assertIn([sq(4, 3), sq(6, 5)], ENGINE.legal_moves(s))
        s2 = ENGINE.apply(s, [sq(4, 3), sq(6, 5)])
        self.assertEqual(s2["board"][sq(5, 4)], 0)

    def test_man_cannot_step_backward(self):
        s = state_from_pieces({sq(4, 3): P1_MAN, sq(0, 7): P2_MAN})
        self.assertEqual(
            sorted(ENGINE.legal_moves(s)),
            sorted([[sq(4, 3), sq(3, 2)], [sq(4, 3), sq(3, 4)]]),
        )

    def test_multi_jump_must_be_completed(self):
        s = state_from_pieces({sq(6, 1): P1_MAN, sq(5, 2): P2_MAN, sq(3, 4): P2_MAN, sq(0, 1): P2_MAN})
        moves = ENGINE.legal_moves(s)
        self.assertIn([sq(6, 1), sq(4, 3), sq(2, 5)], moves)
        self.assertNotIn([sq(6, 1), sq(4, 3)], moves)

    def test_crowning_ends_the_jump(self):
        s = state_from_pieces({sq(2, 3): P1_MAN, sq(1, 4): P2_MAN, sq(1, 6): P2_MAN})
        moves = ENGINE.legal_moves(s)
        self.assertIn([sq(2, 3), sq(0, 5)], moves)
        self.assertNotIn([sq(2, 3), sq(0, 5), sq(2, 7)], moves)
        s2 = ENGINE.apply(s, [sq(2, 3), sq(0, 5)])
        self.assertEqual(s2["board"][sq(0, 5)], P1_KING)


class Suspension(unittest.TestCase):
    def test_declining_a_capture_removes_the_capturing_piece(self):
        s = state_from_pieces({sq(5, 2): P1_MAN, sq(4, 3): P2_MAN, sq(7, 0): P1_MAN, sq(0, 1): P2_MAN})
        s2 = ENGINE.apply(s, [sq(7, 0), sq(6, 1)])
        self.assertEqual(s2["board"][sq(5, 2)], 0)
        self.assertEqual(s2["board"][sq(6, 1)], P1_MAN)
        self.assertEqual(s2["suspended"], sq(5, 2))
        self.assertEqual(s2["turn"], P2)

    def test_capturer_that_moves_away_is_removed_where_it_lands(self):
        s = state_from_pieces({sq(5, 2): P1_MAN, sq(4, 3): P2_MAN, sq(7, 6): P1_MAN, sq(0, 1): P2_MAN})
        s2 = ENGINE.apply(s, [sq(5, 2), sq(4, 1)])
        self.assertEqual(s2["board"][sq(4, 1)], 0)
        self.assertEqual(s2["suspended"], sq(4, 1))

    def test_capturing_causes_no_suspension(self):
        s = state_from_pieces({sq(5, 2): P1_MAN, sq(4, 3): P2_MAN, sq(7, 0): P1_MAN, sq(0, 1): P2_MAN})
        s2 = ENGINE.apply(s, [sq(5, 2), sq(3, 4)])
        self.assertIsNone(s2["suspended"])
        self.assertEqual(s2["board"][sq(7, 0)], P1_MAN)

    def test_longest_chain_piece_is_removed(self):
        s = state_from_pieces({
            sq(6, 1): P1_MAN, sq(5, 2): P2_MAN, sq(3, 4): P2_MAN,
            sq(6, 5): P1_MAN, sq(5, 6): P2_MAN,
            sq(7, 2): P1_MAN,
        })
        s2 = ENGINE.apply(s, [sq(7, 2), sq(6, 3)])
        self.assertEqual(s2["board"][sq(6, 1)], 0)
        self.assertEqual(s2["board"][sq(6, 5)], P1_MAN)

    def test_suspension_can_cost_the_game(self):
        s = state_from_pieces({sq(5, 2): P1_MAN, sq(4, 3): P2_MAN, sq(0, 1): P2_MAN})
        s2 = ENGINE.apply(s, [sq(5, 2), sq(4, 1)])
        self.assertEqual(ENGINE.result(s2), P2)


class Results(unittest.TestCase):
    def test_no_legal_moves_loses(self):
        s = state_from_pieces({sq(7, 2): P2_MAN, sq(5, 0): P1_MAN}, turn=P2)
        self.assertEqual(ENGINE.result(s), P1)

    def test_capturing_last_piece_wins(self):
        s = state_from_pieces({sq(5, 2): P1_MAN, sq(4, 3): P2_MAN})
        s2 = ENGINE.apply(s, [sq(5, 2), sq(3, 4)])
        self.assertEqual(ENGINE.result(s2), P1)

    def test_illegal_move_is_rejected(self):
        with self.assertRaises(IllegalMove):
            ENGINE.apply(ENGINE.initial_state(), [sq(5, 0), sq(2, 3)])







class FlyingKings(unittest.TestCase):
    def test_king_slides_across_the_diagonal(self):
        s = state_from_pieces({sq(7, 0): P1_KING, sq(0, 1): P2_MAN})
        self.assertEqual(len(ENGINE.legal_moves(s)), 7)

    def test_king_captures_from_a_distance(self):
        s = state_from_pieces({sq(7, 0): P1_KING, sq(3, 4): P2_MAN})
        moves = ENGINE.legal_moves(s)
        for land in (sq(2, 5), sq(1, 6), sq(0, 7)):
            self.assertIn([sq(7, 0), land], moves)
        s2 = ENGINE.apply(s, [sq(7, 0), sq(1, 6)])
        self.assertEqual(s2["board"][sq(3, 4)], 0)
        self.assertEqual(s2["board"][sq(1, 6)], P1_KING)

    def test_king_multi_jump_must_continue(self):
        s = state_from_pieces({sq(7, 0): P1_KING, sq(5, 2): P2_MAN, sq(3, 2): P2_MAN})
        moves = ENGINE.legal_moves(s)
        self.assertIn([sq(7, 0), sq(4, 3), sq(2, 1)], moves)
        self.assertNotIn([sq(7, 0), sq(4, 3)], moves)