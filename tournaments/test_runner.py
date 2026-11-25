from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Q
from django.test import TestCase
from django.utils import timezone

from matches.models import Match
from matches.services import is_busy
from wallet.models import Wallet

from .bracket import champion
from .models import Tournament
from .runner import resolve_expired_checkins, start_due_rounds, start_due_tournaments, tick
from .services import start_tournament
from .test_start import enrol_players
from .tests import balance, create_tournament, set_balance


def started_tournament(players=4, **kwargs):
    """A tournament that has been drawn and is waiting for round 1."""
    t = create_tournament(min_players=2, **kwargs)
    users = enrol_players(t, players)
    start_tournament(t.id, force=True)
    t.refresh_from_db()
    return t, users


def round_start(t, number):
    return t.rounds.get(number=number).starts_at


def matches_of(t):
    return list(Match.objects.filter(tournament=t).order_by("bracket_round", "bracket_number"))


class RoundStart(TestCase):
    def test_a_round_creates_its_matches_and_charges_the_fees(self):
        t, users = started_tournament(4)

        created = start_due_rounds(now=round_start(t, 1))

        self.assertEqual(len(created), 2)
        matches = matches_of(t)
        self.assertEqual([m.status for m in matches], [Match.Status.WAITING] * 2)
        self.assertEqual({m.bracket_number for m in matches}, {0, 1})
        for m in matches:
            self.assertEqual(m.stake, Decimal("0.00"))
            self.assertEqual(m.game, "checkers")
            self.assertEqual(m.checkin_deadline, round_start(t, 1) + timedelta(minutes=3))
        for user in users:
            self.assertEqual(balance(user), Decimal("99.00"))  # the K1 match fee
        self.assertIsNotNone(t.rounds.get(number=1).started_at)
        self.assertEqual(start_due_rounds(now=round_start(t, 1)), [])  # nothing is created twice

    def test_a_round_does_not_start_early(self):
        t, _ = started_tournament(4)
        self.assertEqual(start_due_rounds(now=round_start(t, 1) - timedelta(minutes=1)), [])
        self.assertEqual(Match.objects.filter(tournament=t).count(), 0)

    def test_a_round_waits_for_the_daily_window(self):
        t, _ = started_tournament(4)
        evening = timezone.make_aware(datetime(2030, 1, 10, 21, 0))
        self.assertEqual(start_due_rounds(now=evening), [])

    def test_no_match_fee_when_matches_are_free(self):
        t, users = started_tournament(4, match_fee=Decimal("0"))
        start_due_rounds(now=round_start(t, 1))
        for user in users:
            self.assertEqual(balance(user), Decimal("100.00"))

    def test_players_in_a_waiting_match_are_busy(self):
        t, users = started_tournament(4)
        start_due_rounds(now=round_start(t, 1))
        self.assertTrue(all(is_busy(u.id) for u in users))


class NoShows(TestCase):
    def setUp(self):
        self.t, self.users = started_tournament(4)
        start_due_rounds(now=round_start(self.t, 1))
        self.matches = matches_of(self.t)
        self.late = round_start(self.t, 1) + timedelta(minutes=4)  # after the 3-minute check-in

    def test_nobody_arrives_so_everyone_is_out(self):
        resolved = resolve_expired_checkins(now=self.late)

        self.assertEqual(sorted(resolved), sorted(m.id for m in self.matches))
        for m in matches_of(self.t):
            self.assertEqual(m.status, Match.Status.CANCELLED)
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, Tournament.Status.FINISHED)
        self.assertIsNone(champion(self.t.bracket))

    def test_one_player_arrives_and_goes_through(self):
        a, b = self.matches
        Match.objects.filter(pk=a.pk).update(present=[a.creator_id])
        Match.objects.filter(pk=b.pk).update(present=[b.opponent_id])

        resolve_expired_checkins(now=self.late)

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.status, a.winner_id), (Match.Status.FINISHED, a.creator_id))
        self.assertEqual((b.status, b.winner_id), (Match.Status.FINISHED, b.opponent_id))
        self.t.refresh_from_db()
        final = self.t.bracket["rounds"][1][0]
        self.assertEqual({final["p1"], final["p2"]}, {a.creator_id, b.opponent_id})
        for user in self.users:
            self.assertEqual(balance(user), Decimal("99.00"))  # the match fee is never refunded

    def test_a_match_with_both_players_present_is_left_alone(self):
        a, b = self.matches
        Match.objects.filter(pk=a.pk).update(present=[a.creator_id, a.opponent_id])

        resolved = resolve_expired_checkins(now=self.late)

        self.assertEqual(resolved, [b.id])
        a.refresh_from_db()
        self.assertEqual(a.status, Match.Status.WAITING)

    def test_nothing_is_settled_before_the_check_in_time_is_over(self):
        early = round_start(self.t, 1) + timedelta(minutes=2)
        self.assertEqual(resolve_expired_checkins(now=early), [])

    def test_the_next_round_waits_for_the_round_before(self):
        self.assertEqual(start_due_rounds(now=round_start(self.t, 2)), [])  # round 1 is still being played

        a, b = self.matches
        Match.objects.filter(pk=a.pk).update(present=[a.creator_id])
        Match.objects.filter(pk=b.pk).update(present=[b.creator_id])
        resolve_expired_checkins(now=self.late)

        created = start_due_rounds(now=round_start(self.t, 2))
        self.assertEqual(len(created), 1)  # the final
        final = Match.objects.get(pk=created[0])
        self.assertEqual(final.bracket_round, 2)
        self.assertEqual({final.creator_id, final.opponent_id}, {a.creator_id, b.creator_id})


class CannotPay(TestCase):
    def test_a_player_who_cannot_pay_the_match_fee_is_out(self):
        t, users = started_tournament(4)
        broke = users[0]
        set_balance(broke, "0")

        start_due_rounds(now=round_start(t, 1))

        match = Match.objects.filter(tournament=t).filter(Q(creator=broke) | Q(opponent=broke)).get()
        other_id = match.opponent_id if match.creator_id == broke.id else match.creator_id
        self.assertEqual(match.unpaid, [broke.id])
        self.assertEqual((match.status, match.winner_id), (Match.Status.FINISHED, other_id))
        self.assertEqual(balance(broke), Decimal("0.00"))
        self.assertEqual(Wallet.objects.get(user_id=other_id).available, Decimal("99.00"))  # still paid K1


class AutoStart(TestCase):
    def close_registration(self, t):
        Tournament.objects.filter(pk=t.pk).update(
            registration_closes_at=timezone.now() - timedelta(minutes=1)
        )

    def test_a_tournament_starts_when_registration_closes(self):
        t = create_tournament(min_players=2)
        enrol_players(t, 3)
        self.assertEqual(start_due_tournaments(), [])  # registration is still open

        self.close_registration(t)

        self.assertEqual(start_due_tournaments(), [(t.id, "started")])
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.RUNNING)

    def test_too_few_players_cancels_when_registration_closes(self):
        t = create_tournament(min_players=8, entry_fee=Decimal("10"))
        users = enrol_players(t, 3)
        self.close_registration(t)

        self.assertEqual(start_due_tournaments(), [(t.id, "cancelled")])
        for user in users:
            self.assertEqual(balance(user), Decimal("100.00"))

    def test_tick_starts_the_tournament_and_then_its_rounds(self):
        t = create_tournament(min_players=2)
        enrol_players(t, 2)
        self.close_registration(t)

        tick()  # starts the tournament (round 1 is in 2030, so nothing else happens yet)
        t.refresh_from_db()
        self.assertEqual(t.status, Tournament.Status.RUNNING)

        result = tick(now=t.rounds.get(number=1).starts_at)
        self.assertEqual(len(result["created"]), 1)