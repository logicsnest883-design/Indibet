import json
import random
import unittest

from .bracket import (
    BYE,
    BracketError,
    bracket_size,
    build_bracket,
    champion,
    current_round,
    describe,
    is_finished,
    placings,
    ready_matches,
    record_double_no_show,
    record_result,
    seed_order,
)


class NoShuffle:
    """Keeps players in the order given, so a test knows exactly who is seed 1, 2, 3..."""

    def shuffle(self, items):
        pass


def build(n):
    return build_bracket(list(range(1, n + 1)), rng=NoShuffle())


def round1_players(bracket):
    return [p for m in bracket["rounds"][0] for p in (m["p1"], m["p2"]) if p != BYE]


class BracketBasics(unittest.TestCase):
    def test_bracket_sizes(self):
        for players, size in ((2, 2), (3, 4), (4, 4), (5, 8), (8, 8), (9, 16), (79, 128), (128, 128), (129, 256)):
            self.assertEqual(bracket_size(players), size)

    def test_a_bracket_needs_two_different_players(self):
        for bad in ([], [1], [1, 1, 2]):
            with self.assertRaises(BracketError):
                build_bracket(bad)

    def test_seed_order(self):
        self.assertEqual(seed_order(2), [1, 2])
        self.assertEqual(seed_order(4), [1, 4, 2, 3])
        self.assertEqual(seed_order(8), [1, 8, 4, 5, 2, 7, 3, 6])


class Draw(unittest.TestCase):
    def test_79_players_in_a_128_bracket(self):
        b = build_bracket(list(range(1, 80)), rng=random.Random(5))
        self.assertEqual(b["size"], 128)
        self.assertEqual(len(b["rounds"]), 7)

        round1 = b["rounds"][0]
        byes = [m for m in round1 if BYE in (m["p1"], m["p2"])]
        self.assertEqual(len(byes), 49)
        self.assertEqual(len(round1) - len(byes), 15)
        self.assertTrue(all(m["winner"] is not None and m["how"] == "bye" for m in byes))

        self.assertEqual(sorted(round1_players(b)), list(range(1, 80)))  # everyone, exactly once

        waiting = [p for m in b["rounds"][1] for p in (m["p1"], m["p2"]) if p is not None]
        self.assertEqual(len(waiting), 49)  # the bye players are already waiting in round 2

    def test_every_size_works(self):
        for n in range(2, 130):
            b = build_bracket(list(range(1, n + 1)), rng=random.Random(n))
            size = bracket_size(n)
            round1 = b["rounds"][0]
            self.assertEqual(len(round1), size // 2)
            self.assertEqual(sorted(round1_players(b)), list(range(1, n + 1)))
            double_byes = [m for m in round1 if m["p1"] == BYE and m["p2"] == BYE]
            self.assertEqual(double_byes, [], f"two byes in one match with {n} players")
            byes = [m for m in round1 if BYE in (m["p1"], m["p2"])]
            self.assertEqual(len(byes), size - n)
            self.assertTrue(all(m["winner"] is not None for m in byes))

    def test_byes_are_spread_out(self):
        b = build(5)  # 8 places, 3 byes for seeds 1, 2 and 3
        r1 = b["rounds"][0]
        self.assertEqual([(m["p1"], m["p2"]) for m in r1], [(1, BYE), (4, 5), (2, BYE), (3, BYE)])
        r2 = b["rounds"][1]
        self.assertEqual((r2[0]["p1"], r2[0]["p2"]), (1, None))  # waits for the winner of 4 v 5
        self.assertEqual((r2[1]["p1"], r2[1]["p2"]), (2, 3))     # both came through byes
        self.assertEqual([(m["round"], m["number"]) for m in ready_matches(b)], [(1, 1), (2, 1)])

    def test_power_of_two_has_no_byes(self):
        b = build(16)
        self.assertFalse([m for m in b["rounds"][0] if BYE in (m["p1"], m["p2"])])
        self.assertTrue(all(m["p1"] is None and m["p2"] is None for m in b["rounds"][1]))

    def test_the_default_random_draw_works(self):
        b = build_bracket([10, 20, 30, 40, 50])
        self.assertEqual(sorted(round1_players(b)), [10, 20, 30, 40, 50])


class PlayingIt(unittest.TestCase):
    def test_top_seeds_only_meet_in_the_final(self):
        b = build(8)
        while True:
            ready = ready_matches(b)
            if not ready:
                break
            m = ready[0]
            b = record_result(b, m["round"], m["number"], min(m["p1"], m["p2"]))

        final = b["rounds"][-1][0]
        self.assertEqual({final["p1"], final["p2"]}, {1, 2})
        p = placings(b)
        self.assertEqual(p["champion"], 1)
        self.assertEqual(p["runner_up"], 2)
        self.assertEqual(sorted(p["semifinalists"]), [3, 4])
        self.assertEqual(sorted(p["quarterfinalists"]), [5, 6, 7, 8])

    def test_full_run_with_5_players(self):
        b = build(5)
        b = record_result(b, 1, 1, 4)  # 4 beats 5
        b = record_result(b, 2, 0, 1)  # 1 beats 4
        b = record_result(b, 2, 1, 3)  # 3 beats 2
        b = record_result(b, 3, 0, 3)  # the final: 3 beats 1
        self.assertTrue(is_finished(b))
        p = placings(b)
        self.assertEqual(p["champion"], 3)
        self.assertEqual(p["runner_up"], 1)
        self.assertEqual(sorted(p["semifinalists"]), [2, 4])
        self.assertEqual(p["quarterfinalists"], [5])  # players who went through byes did not lose a match

    def test_walkover_loser_gets_no_placing(self):
        b = record_result(build(5), 1, 1, 4, how="walkover")  # 5 did not show up
        self.assertEqual(placings(b)["quarterfinalists"], [])
        self.assertEqual(b["rounds"][1][0]["p2"], 4)  # 4 goes through

    def test_double_no_show_gives_the_opponent_a_bye(self):
        b = record_double_no_show(build(5), 1, 1)  # neither 4 nor 5 showed up
        r2_first = b["rounds"][1][0]
        self.assertEqual((r2_first["p1"], r2_first["p2"]), (1, BYE))
        self.assertEqual((r2_first["winner"], r2_first["how"]), (1, "bye"))  # 1 goes through without playing
        self.assertEqual(b["rounds"][2][0]["p1"], 1)
        self.assertEqual(placings(b)["quarterfinalists"], [])

    def test_everyone_absent_means_no_champion(self):
        b = build(4)
        b = record_double_no_show(b, 1, 0)
        b = record_double_no_show(b, 1, 1)
        self.assertTrue(is_finished(b))
        self.assertIsNone(champion(b))

    def test_current_round_and_finished(self):
        b = build(5)
        self.assertEqual(current_round(b), 1)
        b = record_result(b, 1, 1, 4)
        self.assertEqual(current_round(b), 2)
        b = record_result(b, 2, 0, 1)
        b = record_result(b, 2, 1, 3)
        self.assertEqual(current_round(b), 3)
        self.assertFalse(is_finished(b))
        b = record_result(b, 3, 0, 3)
        self.assertIsNone(current_round(b))
        self.assertTrue(is_finished(b))


class Safety(unittest.TestCase):
    def test_bad_results_are_refused(self):
        b = build(5)
        bad_calls = [
            lambda: record_result(b, 1, 0, 1),             # a bye match is already decided
            lambda: record_result(b, 2, 0, 1),             # still waiting for its second player
            lambda: record_result(b, 1, 1, 99),            # the winner is not in this match
            lambda: record_result(b, 9, 0, 1),             # no such round
            lambda: record_result(b, 0, 0, 1),             # no such round
            lambda: record_result(b, 1, -1, 1),            # no such match
            lambda: record_result(b, 1, 1, 4, how="cheating"),
            lambda: record_double_no_show(b, 2, 0),        # not ready either
        ]
        for call in bad_calls:
            with self.assertRaises(BracketError):
                call()
        decided = record_result(b, 1, 1, 4)
        with self.assertRaises(BracketError):
            record_result(decided, 1, 1, 4)  # cannot record twice

    def test_original_is_not_changed(self):
        b = build(5)
        before = json.dumps(b, sort_keys=True)
        record_result(b, 1, 1, 4)
        record_double_no_show(b, 1, 1)
        self.assertEqual(json.dumps(b, sort_keys=True), before)

    def test_bracket_is_json_safe(self):
        b = record_result(build(5), 1, 1, 4)
        self.assertEqual(json.loads(json.dumps(b)), b)

    def test_describe(self):
        text = describe(build_bracket(list(range(1, 80)), rng=random.Random(1)))
        self.assertIn("Bracket of 128 for 79 players", text)
        self.assertIn("49 byes", text)
        self.assertIn("15 real matches", text)