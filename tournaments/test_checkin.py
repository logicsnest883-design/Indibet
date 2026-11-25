from datetime import timedelta

from django.test import TestCase

from matches.models import Match

from .checkin import (
    CheckInError,
    check_in,
    notification_for,
    outcome_for,
    room_payload,
    waiting_notification,
)
from .runner import resolve_expired_checkins, start_due_rounds
from .test_runner import matches_of, round_start, started_tournament


class CheckIn(TestCase):
    def setUp(self):
        self.t, self.users = started_tournament(4)
        start_due_rounds(now=round_start(self.t, 1))
        self.match, self.other_match = matches_of(self.t)
        self.start = round_start(self.t, 1)
        self.soon = self.start + timedelta(seconds=30)  # inside the 3-minute check-in

    def test_the_first_player_is_recorded_and_the_game_waits(self):
        m = check_in(self.match.creator_id, self.match.id, now=self.soon)
        self.assertEqual(m.status, Match.Status.WAITING)
        self.assertEqual(m.present, [self.match.creator_id])

    def test_when_both_players_have_arrived_the_game_starts(self):
        check_in(self.match.creator_id, self.match.id, now=self.soon)
        m = check_in(self.match.opponent_id, self.match.id, now=self.soon)

        self.assertEqual(m.status, Match.Status.ACTIVE)
        self.assertEqual(m.state["turn"], "p1")
        self.assertEqual(len(m.state["board"]), 64)
        self.assertEqual(m.started_at, self.soon)
        self.assertIsNotNone(m.turn_deadline)

    def test_checking_in_twice_changes_nothing(self):
        check_in(self.match.creator_id, self.match.id, now=self.soon)
        m = check_in(self.match.creator_id, self.match.id, now=self.soon)
        self.assertEqual(m.present, [self.match.creator_id])
        self.assertEqual(m.status, Match.Status.WAITING)

    def test_a_stranger_cannot_check_in(self):
        with self.assertRaises(CheckInError):
            check_in(self.other_match.creator_id, self.match.id, now=self.soon)

    def test_check_in_closes_at_the_deadline(self):
        too_late = self.start + timedelta(minutes=3, seconds=1)
        with self.assertRaises(CheckInError):
            check_in(self.match.creator_id, self.match.id, now=too_late)

    def test_a_player_who_arrived_goes_through_when_the_other_never_does(self):
        check_in(self.match.creator_id, self.match.id, now=self.soon)

        resolve_expired_checkins(now=self.start + timedelta(minutes=4))

        self.match.refresh_from_db()
        self.assertEqual(self.match.winner_id, self.match.creator_id)
        self.assertEqual(outcome_for(self.match, self.match.creator_id)[0], "advance")
        self.assertEqual(outcome_for(self.match, self.match.opponent_id)[0], "out")


class Rooms(TestCase):
    def setUp(self):
        self.t, self.users = started_tournament(4)
        start_due_rounds(now=round_start(self.t, 1))
        self.match = matches_of(self.t)[0]
        self.start = round_start(self.t, 1)

    def load(self):
        return Match.objects.select_related("tournament", "creator", "opponent").get(pk=self.match.pk)

    def test_the_room_while_waiting(self):
        now = self.start + timedelta(seconds=30)
        check_in(self.match.creator_id, self.match.id, now=now)
        payload = room_payload(self.load(), self.match.creator_id, now=now)

        self.assertEqual(payload["status"], "waiting")
        self.assertEqual(payload["round"], "Semi-finals")  # 4 players: two matches in round 1
        self.assertTrue(payload["you_here"])
        self.assertFalse(payload["opponent_here"])
        self.assertEqual(payload["seconds_left"], 150)
        self.assertIsNone(payload["outcome"])
        self.assertIsNone(payload["board_url"])

    def test_the_room_sends_players_to_the_board_once_the_game_starts(self):
        now = self.start + timedelta(seconds=30)
        check_in(self.match.creator_id, self.match.id, now=now)
        check_in(self.match.opponent_id, self.match.id, now=now)
        payload = room_payload(self.load(), self.match.creator_id, now=now)
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["board_url"], f"/matches/{self.match.id}/")

    def test_a_ready_banner_while_waiting(self):
        banner = notification_for(self.load(), self.match.creator_id)
        self.assertEqual(banner["kind"], "ready")
        self.assertEqual(banner["url"], f"/tournaments/match/{self.match.id}/")
        self.assertIn("Semi-finals", banner["text"])

    def test_the_banner_for_the_page_load_check(self):
        now = self.start + timedelta(seconds=30)
        banner = waiting_notification(self.match.creator_id, now=now)
        self.assertEqual(banner["kind"], "ready")
        self.assertEqual(banner["seconds_left"], 150)
        self.assertIsNone(waiting_notification(self.match.creator_id, now=self.start + timedelta(minutes=5)))

    def test_nobody_arrived(self):
        resolve_expired_checkins(now=self.start + timedelta(minutes=4))
        match = self.load()
        self.assertEqual(outcome_for(match, match.creator_id)[0], "both_out")
        self.assertEqual(notification_for(match, match.creator_id)["kind"], "both_out")

    def test_results_of_a_played_match(self):
        Match.objects.filter(pk=self.match.pk).update(
            status=Match.Status.FINISHED,
            winner_id=self.match.creator_id,
            started_at=self.start,
        )
        match = self.load()
        self.assertEqual(outcome_for(match, match.creator_id)[0], "won")
        self.assertEqual(outcome_for(match, match.opponent_id)[0], "lost")