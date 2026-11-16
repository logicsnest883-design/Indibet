from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from matches.models import Match
from matches.services import activate_match, create_match, join_match
from wallet.models import Wallet

from .services import MoveError, forfeit_on_timeout, make_move, resign


class ChessPayouts(TestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user("alice", password="x")  # white, creates the match
        self.bob = User.objects.create_user("bob", password="x")      # black, joins
        for user in (self.alice, self.bob):
            Wallet.objects.update_or_create(
                user=user, defaults={"available": Decimal("100.00"), "held": Decimal("0.00")}
            )
        match = create_match(self.alice, "chess", Decimal("50"))
        join_match(self.bob, match.id)
        self.match = activate_match(match.id)

    def wallet(self, user):
        return Wallet.objects.get(user=user)

    def test_stakes_and_fees_are_taken(self):
        # K50 stake + K2 fee (stakes above K19 pay K2) each
        for user in (self.alice, self.bob):
            self.assertEqual(self.wallet(user).available, Decimal("48.00"))
            self.assertEqual(self.wallet(user).held, Decimal("50.00"))

    def test_checkmate_pays_the_winner_the_full_pot(self):
        make_move(self.alice.id, self.match.id, "f2f3")
        make_move(self.bob.id, self.match.id, "e7e5")
        make_move(self.alice.id, self.match.id, "g2g4")
        make_move(self.bob.id, self.match.id, "d8h4")  # fool's mate: black wins

        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.Status.FINISHED)
        self.assertEqual(self.match.winner_id, self.bob.id)
        self.assertEqual(self.wallet(self.bob).available, Decimal("148.00"))
        self.assertEqual(self.wallet(self.bob).held, Decimal("0.00"))
        self.assertEqual(self.wallet(self.alice).available, Decimal("48.00"))
        self.assertEqual(self.wallet(self.alice).held, Decimal("0.00"))

    def test_resign_pays_the_opponent(self):
        resign(self.alice.id, self.match.id)
        self.match.refresh_from_db()
        self.assertEqual(self.match.winner_id, self.bob.id)
        self.assertEqual(self.wallet(self.bob).available, Decimal("148.00"))

    def test_timeout_forfeits_the_player_on_turn(self):
        Match.objects.filter(pk=self.match.id).update(
            turn_deadline=timezone.now() - timedelta(seconds=1)
        )
        self.assertTrue(forfeit_on_timeout(self.match.id))  # white (alice) was on turn
        self.match.refresh_from_db()
        self.assertEqual(self.match.winner_id, self.bob.id)
        self.assertEqual(self.wallet(self.bob).available, Decimal("148.00"))

    def test_wrong_turn_and_illegal_moves_are_refused(self):
        with self.assertRaises(MoveError):
            make_move(self.bob.id, self.match.id, "e7e5")      # black moving first
        with self.assertRaises(MoveError):
            make_move(self.alice.id, self.match.id, "e2e5")    # illegal move