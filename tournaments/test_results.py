from datetime import timedelta
from decimal import Decimal

from django.test import TestCase

from matches.models import Match

from .bracket import champion, placings
from .bracket_data import bracket_json
from .checkin import check_in, outcome_for
from .models import Tournament
from .results import record_finished_matches
from .runner import resolve_expired_checkins, start_due_rounds
from .test_runner import matches_of, round_start, started_tournament
from .tests import balance


def play_out(match, winner_id=None, now=None):
    """Both players arrive, then the game ends: a win for winner_id, or a draw if it is None."""
    check_in(match.creator_id, match.id, now=now)
    check_in(match.opponent_id, match.id, now=now)
    Match.objects.filter(pk=match.pk).update(
        status=Match.Status.FINISHED, winner_id=winner_id, finished_at=now
    )


def reload(match):
    return Match.objects.get(pk=match.pk)


class Results(TestCase):
    def setUp(self):
        self.t, self.users = started_tournament(4)
        start_due_rounds(now=round_start(self.t, 1))
        self.a, self.b = matches_of(self.t)
        self.start = round_start(self.t, 1)
        self.played_at = self.start + timedelta(seconds=30)
        self.recorded_at = self.start + timedelta(minutes=1)

    def test_the_winner_goes_through(self):
        play_out(self.a, self.a.creator_id, now=self.played_at)

        out = record_finished_matches(now=self.recorded_at)

        self.assertEqual(out["recorded"], [self.a.id])
        self.assertEqual(out["replays"], [])
        self.t.refresh_from_db()
        slot = "p1" if self.a.bracket_number % 2 == 0 else "p2"
        self.assertEqual(self.t.bracket["rounds"][1][0][slot], self.a.creator_id)
        self.assertTrue(reload(self.a).result_recorded)
        self.assertEqual(record_finished_matches(now=self.recorded_at)["recorded"], [])  # only once

    def test_games_still_being_played_are_left_alone(self):
        check_in(self.a.creator_id, self.a.id, now=self.played_at)
        check_in(self.a.opponent_id, self.a.id, now=self.played_at)  # the game is now in progress
        self.assertEqual(record_finished_matches(now=self.recorded_at)["recorded"], [])

    def test_the_final_ends_the_tournament(self):
        play_out(self.a, self.a.creator_id, now=self.played_at)
        play_out(self.b, self.b.creator_id, now=self.played_at)
        record_finished_matches(now=self.recorded_at)

        created = start_due_rounds(now=round_start(self.t, 2))
        self.assertEqual(len(created), 1)
        final = Match.objects.get(pk=created[0])
        final_at = round_start(self.t, 2) + timedelta(seconds=30)
        play_out(final, final.creator_id, now=final_at)
        record_finished_matches(now=final_at + timedelta(seconds=30))

        self.t.refresh_from_db()
        self.assertEqual(self.t.status, Tournament.Status.FINISHED)
        self.assertEqual(champion(self.t.bracket), final.creator_id)
        p = placings(self.t.bracket)
        self.assertEqual(p["runner_up"], final.opponent_id)
        self.assertEqual(sorted(p["semifinalists"]), sorted([self.a.opponent_id, self.b.opponent_id]))
        self.assertIn("champion", outcome_for(reload(final), final.creator_id)[1])

    def test_a_draw_is_replayed_with_the_colours_swapped(self):
        play_out(self.a, None, now=self.played_at)

        out = record_finished_matches(now=self.recorded_at)

        self.assertEqual(len(out["replays"]), 1)
        replay = Match.objects.get(pk=out["replays"][0])
        self.assertEqual((replay.creator_id, replay.opponent_id), (self.a.opponent_id, self.a.creator_id))
        self.assertEqual(replay.status, Match.Status.WAITING)
        self.assertEqual((replay.bracket_round, replay.bracket_number), (1, self.a.bracket_number))
        self.assertEqual(replay.stake, Decimal("0.00"))
        self.assertEqual(replay.checkin_deadline, self.recorded_at + timedelta(minutes=3))
        for user in self.users:
            self.assertEqual(balance(user), Decimal("99.00"))  # replays cost nothing more
        self.t.refresh_from_db()
        self.assertIsNone(self.t.bracket["rounds"][0][self.a.bracket_number]["winner"])  # still undecided
        self.assertEqual(outcome_for(reload(self.a), self.a.creator_id)[0], "draw")

    def test_three_draws_end_with_a_coin_toss(self):
        play_out(self.a, None, now=self.played_at)
        first = record_finished_matches(now=self.recorded_at)
        replay1 = Match.objects.get(pk=first["replays"][0])

        play_out(replay1, None, now=self.recorded_at + timedelta(seconds=30))
        second_time = self.recorded_at + timedelta(minutes=1)
        second = record_finished_matches(now=second_time)
        replay2 = Match.objects.get(pk=second["replays"][0])

        play_out(replay2, None, now=second_time + timedelta(seconds=30))
        third = record_finished_matches(now=second_time + timedelta(minutes=1))

        self.assertEqual(third["replays"], [])  # no fourth game
        last = reload(replay2)
        toss = last.state["coin_toss_winner"]
        self.assertIn(toss, {self.a.creator_id, self.a.opponent_id})
        self.t.refresh_from_db()
        self.assertEqual(self.t.bracket["rounds"][0][self.a.bracket_number]["winner"], toss)
        loser = self.a.opponent_id if toss == self.a.creator_id else self.a.creator_id
        self.assertEqual(outcome_for(last, toss)[0], "advance")
        self.assertEqual(outcome_for(last, loser)[0], "out")

    def test_a_walkover_is_not_recorded_a_second_time(self):
        Match.objects.filter(pk=self.a.pk).update(present=[self.a.creator_id])
        resolve_expired_checkins(now=self.start + timedelta(minutes=4))
        self.assertEqual(record_finished_matches(now=self.start + timedelta(minutes=5))["recorded"], [])

    def test_the_bracket_page_data(self):
        play_out(self.a, self.a.creator_id, now=self.played_at)
        record_finished_matches(now=self.recorded_at)
        self.t.refresh_from_db()

        data = bracket_json(self.t)

        self.assertEqual(data["status"], "running")
        self.assertEqual([r["label"] for r in data["rounds"]], ["Semi-finals", "Final"])
        first = data["rounds"][0]["matches"][self.a.bracket_number]
        self.assertEqual(first["winner"], self.a.creator_id)
        self.assertEqual({first["p1"]["id"], first["p2"]["id"]}, {self.a.creator_id, self.a.opponent_id})
        self.assertTrue(first["p1"]["name"].startswith("player"))
        final = data["rounds"][1]["matches"][0]
        self.assertEqual([final["p1"], final["p2"]].count(None), 1)  # the other finalist is still to be decided
        self.assertIsNone(data["champion"])