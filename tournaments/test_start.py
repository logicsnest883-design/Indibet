from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .models import Tournament
from .services import TournamentError, cancel_tournament, enrol, start_tournament
from .tests import balance, create_tournament, make, set_balance

User = get_user_model()


def enrol_players(tournament, count):
    users = []
    for i in range(count):
        user = User.objects.create_user(f"player{tournament.pk}_{i}", password="x")
        set_balance(user, "100")
        enrol(user, tournament.id)
        users.append(user)
    return users


class Starting(TestCase):
    def test_start_draws_the_bracket_and_schedules_the_rounds(self):
        t = create_tournament(min_players=4, max_players=16, prize_per_player=Decimal("2"))
        enrol_players(t, 5)

        self.assertEqual(start_tournament(t.id, force=True), "started")

        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.RUNNING)
        self.assertEqual(t.final_players, 5)
        self.assertEqual(t.final_prize_pool, Decimal("10.00"))
        self.assertEqual(t.bracket["entrants"], 5)
        self.assertEqual(t.bracket["size"], 8)

        rounds = list(t.rounds.all())
        self.assertEqual(len(rounds), 3)
        starts = [r.starts_at for r in rounds]
        self.assertEqual(starts, sorted(starts))
        for r in rounds:
            local = timezone.localtime(r.starts_at).time()
            self.assertTrue(time(8, 0) <= local < time(20, 0))

    def test_too_few_players_cancels_and_refunds(self):
        t = create_tournament(min_players=8, entry_fee=Decimal("10"))
        users = enrol_players(t, 3)
        self.assertEqual(balance(users[0]), Decimal("90.00"))

        self.assertEqual(start_tournament(t.id, force=True), "cancelled")

        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.CANCELLED)
        self.assertEqual(t.rounds.count(), 0)
        for user in users:
            self.assertEqual(balance(user), Decimal("100.00"))

    def test_registration_must_be_closed_unless_forced(self):
        t = create_tournament(min_players=2)
        enrol_players(t, 2)
        with self.assertRaises(TournamentError):
            start_tournament(t.id)  # registration is still open
        self.assertEqual(start_tournament(t.id, force=True), "started")

    def test_closed_registration_starts_without_force(self):
        t = create_tournament(min_players=2)
        enrol_players(t, 2)
        Tournament.objects.filter(pk=t.pk).update(
            registration_closes_at=timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(start_tournament(t.id), "started")

    def test_only_a_tournament_in_registration_can_start(self):
        draft = create_tournament(status=Tournament.Status.DRAFT)
        with self.assertRaises(TournamentError):
            start_tournament(draft.id, force=True)

        t = create_tournament(min_players=2)
        enrol_players(t, 2)
        start_tournament(t.id, force=True)
        with self.assertRaises(TournamentError):
            start_tournament(t.id, force=True)  # already started
        self.assertEqual(t.rounds.count(), 1)  # and no second draw happened

    def test_cancelling_refunds_entry_fees(self):
        t = create_tournament(entry_fee=Decimal("10"))
        users = enrol_players(t, 2)

        cancel_tournament(t.id)

        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.CANCELLED)
        for user in users:
            self.assertEqual(balance(user), Decimal("100.00"))
        with self.assertRaises(TournamentError):
            cancel_tournament(t.id)  # cannot refund twice

    def test_the_prize_pool_is_locked_when_the_tournament_starts(self):
        t = create_tournament(min_players=2, prize_per_player=Decimal("2"))
        enrol_players(t, 4)
        start_tournament(t.id, force=True)

        t.refresh_from_db()
        t.prize_per_player = Decimal("10")  # changed after the start
        self.assertEqual(t.current_prize_pool(4), Decimal("8.00"))  # the locked amount
        self.assertEqual(t.prize_pool(4), Decimal("40.00"))

    def test_cannot_be_marked_running_without_a_bracket(self):
        with self.assertRaises(ValidationError):
            make(status="running").clean()
        t = make(status="running")
        t.bracket = {"size": 2, "entrants": 2, "rounds": []}
        t.clean()