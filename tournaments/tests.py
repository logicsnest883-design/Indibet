from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from wallet.models import Wallet
from wallet.services import InsufficientFunds

from .models import Tournament
from .services import EnrolmentError, enrol, withdraw

User = get_user_model()


def make(**kwargs):
    start = timezone.make_aware(datetime(2030, 1, 10, 10, 0))  # 10:00 Lusaka time
    data = dict(
        name="Test cup",
        game="checkers",
        registration_closes_at=start - timedelta(hours=2),
        starts_at=start,
    )
    data.update(kwargs)
    return Tournament(**data)


class Prizes(SimpleTestCase):
    def test_pool_grows_with_players(self):
        t = make(prize_per_player=Decimal("2"))
        self.assertEqual(t.prize_pool(8), Decimal("16.00"))
        self.assertEqual(t.prize_pool(64), Decimal("128.00"))

    def test_pool_stops_at_the_cap(self):
        t = make(prize_per_player=Decimal("2"), prize_cap=Decimal("100"))
        self.assertEqual(t.prize_pool(32), Decimal("64.00"))
        self.assertEqual(t.prize_pool(64), Decimal("100.00"))

    def test_no_prize_by_default(self):
        self.assertEqual(make().prize_pool(50), Decimal("0.00"))

    def test_prize_table_lists_each_place(self):
        t = make(prize_per_player=Decimal("10"))
        rows = {r["label"]: r["each"] for r in t.prize_table(10)}  # pool K100
        self.assertEqual(rows["Champion"], Decimal("40.00"))
        self.assertEqual(rows["Runner-up"], Decimal("20.00"))
        self.assertEqual(rows["Each semi-finalist"], Decimal("10.00"))
        self.assertEqual(rows["Each quarter-finalist"], Decimal("5.00"))


class FixedPrizes(SimpleTestCase):
    def test_fixed_pool_does_not_depend_on_players(self):
        t = make(prize_mode="fixed", fixed_prize_pool=Decimal("5000"))
        for players in (0, 1, 8, 128):
            self.assertEqual(t.prize_pool(players), Decimal("5000.00"))

    def test_fixed_pool_ignores_the_growing_settings(self):
        t = make(
            prize_mode="fixed",
            fixed_prize_pool=Decimal("5000"),
            prize_per_player=Decimal("2"),
            prize_cap=Decimal("10"),
            entry_fee=Decimal("10"),
            platform_cut_percent=30,
        )
        b = t.prize_breakdown(10)
        self.assertEqual(b["pool"], Decimal("5000.00"))
        self.assertEqual(b["entry_fees"], Decimal("100.00"))
        self.assertEqual(b["platform_keeps"], Decimal("100.00"))  # IndiBet keeps every entry fee
        self.assertEqual(b["from_entries"], Decimal("0.00"))

    def test_prize_table_splits_the_fixed_pool(self):
        t = make(prize_mode="fixed", fixed_prize_pool=Decimal("5000"))
        rows = {r["label"]: r["each"] for r in t.prize_table(0)}
        self.assertEqual(rows["Champion"], Decimal("2000.00"))
        self.assertEqual(rows["Runner-up"], Decimal("1000.00"))
        self.assertEqual(rows["Each semi-finalist"], Decimal("500.00"))
        self.assertEqual(rows["Each quarter-finalist"], Decimal("250.00"))

    def test_fixed_mode_needs_an_amount(self):
        with self.assertRaises(ValidationError):
            make(prize_mode="fixed", fixed_prize_pool=Decimal("0")).clean()

    def test_fixed_mode_with_an_amount_is_valid(self):
        make(prize_mode="fixed", fixed_prize_pool=Decimal("5000")).clean()


class PlatformCut(SimpleTestCase):
    def test_platform_keeps_its_cut_of_entry_fees(self):
        t = make(entry_fee=Decimal("10"), platform_cut_percent=70)
        b = t.prize_breakdown(64)
        self.assertEqual(b["entry_fees"], Decimal("640.00"))
        self.assertEqual(b["platform_keeps"], Decimal("448.00"))
        self.assertEqual(b["from_entries"], Decimal("192.00"))
        self.assertEqual(b["pool"], Decimal("192.00"))

    def test_indibets_bonus_is_added_on_top(self):
        t = make(entry_fee=Decimal("20"), platform_cut_percent=50, prize_per_player=Decimal("2"))
        b = t.prize_breakdown(8)
        self.assertEqual(b["bonus"], Decimal("16.00"))
        self.assertEqual(b["pool"], Decimal("96.00"))  # K80 from entries + K16 bonus

    def test_cap_only_limits_indibets_own_bonus(self):
        t = make(
            entry_fee=Decimal("10"),
            platform_cut_percent=50,
            prize_per_player=Decimal("5"),
            prize_cap=Decimal("100"),
        )
        b = t.prize_breakdown(64)
        self.assertEqual(b["bonus"], Decimal("100.00"))
        self.assertEqual(b["pool"], Decimal("420.00"))  # K320 from entries + K100 bonus

    def test_free_entry_means_no_cut(self):
        b = make(platform_cut_percent=70).prize_breakdown(64)
        self.assertEqual(b["platform_keeps"], Decimal("0.00"))

    def test_a_100_percent_cut_leaves_nothing_from_entries(self):
        b = make(entry_fee=Decimal("10"), platform_cut_percent=100).prize_breakdown(10)
        self.assertEqual(b["from_entries"], Decimal("0.00"))
        self.assertEqual(b["pool"], Decimal("0.00"))


class Referrals(SimpleTestCase):
    def test_referrals_are_waived_at_the_user_target(self):
        t = make(required_referrals=2, waive_referrals_at_users=10000)
        self.assertEqual(t.referrals_needed(9999), 2)
        self.assertEqual(t.referrals_needed(10000), 0)
        self.assertEqual(t.referrals_needed(25000), 0)

    def test_blank_target_means_never_waived(self):
        t = make(required_referrals=2, waive_referrals_at_users=None)
        self.assertEqual(t.referrals_needed(1000000), 2)

    def test_no_requirement(self):
        self.assertEqual(make(required_referrals=0).referrals_needed(5), 0)


class Checks(SimpleTestCase):
    def test_default_settings_are_valid(self):
        make().clean()

    def test_final_only_prizes_are_valid(self):
        make(share_champion=70, share_runner_up=30, share_semifinalist=0, share_quarterfinalist=0).clean()

    def test_shares_must_add_up_to_100(self):
        with self.assertRaises(ValidationError):
            make(share_champion=50).clean()

    def test_registration_must_close_before_the_start(self):
        start = timezone.make_aware(datetime(2030, 1, 10, 10, 0))
        with self.assertRaises(ValidationError):
            make(registration_closes_at=start + timedelta(hours=1)).clean()

    def test_start_must_be_inside_the_daily_window(self):
        late = timezone.make_aware(datetime(2030, 1, 10, 22, 0))
        with self.assertRaises(ValidationError):
            make(starts_at=late, registration_closes_at=late - timedelta(hours=3)).clean()

    def test_opening_time_must_be_before_closing_time(self):
        with self.assertRaises(ValidationError):
            make(daily_open=time(20, 0), daily_close=time(8, 0)).clean()


def create_tournament(**kwargs):
    kwargs.setdefault("status", Tournament.Status.REGISTRATION)
    t = make(**kwargs)
    t.save()
    return t


def set_balance(user, amount):
    Wallet.objects.filter(user=user).update(available=Decimal(amount))


def balance(user):
    return Wallet.objects.get(user=user).available


class Enrolment(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="x")
        set_balance(self.alice, "100")

    def test_free_tournament(self):
        t = create_tournament()
        enrol(self.alice, t.id)
        self.assertEqual(t.entries.count(), 1)

    def test_referrals_are_required(self):
        t = create_tournament(required_referrals=2)
        with self.assertRaises(EnrolmentError):
            enrol(self.alice, t.id)
        for i in range(2):
            User.objects.create_user(f"friend{i}", password="x", referred_by=self.alice)
        enrol(self.alice, t.id)
        self.assertEqual(t.entries.count(), 1)

    def test_referrals_are_waived_when_the_platform_is_big_enough(self):
        t = create_tournament(required_referrals=2, waive_referrals_at_users=2)
        User.objects.create_user("bob", password="x")  # now 2 users in total
        enrol(self.alice, t.id)
        self.assertEqual(t.entries.count(), 1)

    def test_entry_fee_is_charged(self):
        t = create_tournament(entry_fee=Decimal("10"))
        enrol(self.alice, t.id)
        self.assertEqual(balance(self.alice), Decimal("90.00"))

    def test_not_enough_money_means_no_entry(self):
        set_balance(self.alice, "5")
        t = create_tournament(entry_fee=Decimal("10"))
        with self.assertRaises(InsufficientFunds):
            enrol(self.alice, t.id)
        self.assertEqual(t.entries.count(), 0)
        self.assertEqual(balance(self.alice), Decimal("5.00"))

    def test_cannot_enrol_twice(self):
        t = create_tournament(entry_fee=Decimal("10"))
        enrol(self.alice, t.id)
        with self.assertRaises(EnrolmentError):
            enrol(self.alice, t.id)
        self.assertEqual(balance(self.alice), Decimal("90.00"))

    def test_full_tournament_refuses_more_players(self):
        t = create_tournament(min_players=2, max_players=2)
        for name in ("p1", "p2"):
            enrol(User.objects.create_user(name, password="x"), t.id)
        with self.assertRaises(EnrolmentError):
            enrol(self.alice, t.id)

    def test_closed_registration_refuses(self):
        draft = create_tournament(status=Tournament.Status.DRAFT)
        with self.assertRaises(EnrolmentError):
            enrol(self.alice, draft.id)
        past = create_tournament(registration_closes_at=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(EnrolmentError):
            enrol(self.alice, past.id)

    def test_leaving_refunds_the_entry_fee(self):
        t = create_tournament(entry_fee=Decimal("10"))
        enrol(self.alice, t.id)
        withdraw(self.alice, t.id)
        self.assertEqual(t.entries.count(), 0)
        self.assertEqual(balance(self.alice), Decimal("100.00"))

    def test_enrolling_again_after_leaving_charges_again(self):
        t = create_tournament(entry_fee=Decimal("10"))
        enrol(self.alice, t.id)
        withdraw(self.alice, t.id)
        enrol(self.alice, t.id)
        self.assertEqual(balance(self.alice), Decimal("90.00"))
        self.assertEqual(t.entries.count(), 1)

    def test_cannot_leave_after_registration_closes(self):
        t = create_tournament(entry_fee=Decimal("10"))
        enrol(self.alice, t.id)
        Tournament.objects.filter(pk=t.pk).update(status=Tournament.Status.RUNNING)
        with self.assertRaises(EnrolmentError):
            withdraw(self.alice, t.id)