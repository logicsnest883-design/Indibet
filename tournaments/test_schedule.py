import unittest
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .schedule import round_name, round_start_times

LUSAKA = ZoneInfo("Africa/Lusaka")
OPEN, CLOSE = time(8, 0), time(20, 0)


def at(day, hour, minute=0):
    """A Lusaka time on January `day`, 2030."""
    return datetime(2030, 1, day, hour, minute, tzinfo=LUSAKA)


def schedule(first, rounds, interval):
    return round_start_times(first, rounds, interval, OPEN, CLOSE, LUSAKA)


class RoundTimes(unittest.TestCase):
    def test_two_hour_rounds_pause_overnight(self):
        # a 128 bracket has 7 rounds
        self.assertEqual(
            schedule(at(10, 8), 7, 120),
            [at(10, 8), at(10, 10), at(10, 12), at(10, 14), at(10, 16), at(10, 18), at(11, 8)],
        )

    def test_hourly_rounds_fit_in_one_day(self):
        self.assertEqual(
            schedule(at(10, 8), 7, 60),
            [at(10, h) for h in (8, 9, 10, 11, 12, 13, 14)],
        )

    def test_a_start_before_opening_waits_for_opening(self):
        self.assertEqual(schedule(at(10, 6), 2, 120), [at(10, 8), at(10, 10)])

    def test_a_start_after_closing_moves_to_the_next_morning(self):
        self.assertEqual(schedule(at(10, 21), 2, 120), [at(11, 8), at(11, 10)])

    def test_nothing_starts_at_or_after_closing_time(self):
        self.assertEqual(
            schedule(at(10, 19), 3, 60),
            [at(10, 19), at(11, 8), at(11, 9)],  # 20:00 is closing time, so round 2 waits
        )

    def test_four_hour_gaps_over_several_days(self):
        self.assertEqual(
            schedule(at(10, 8), 7, 240),
            [at(10, 8), at(10, 12), at(10, 16), at(11, 8), at(11, 12), at(11, 16), at(12, 8)],
        )

    def test_times_given_in_utc_are_read_in_lusaka_time(self):
        utc_start = datetime(2030, 1, 10, 6, 0, tzinfo=timezone.utc)  # 08:00 in Lusaka (UTC+2)
        self.assertEqual(schedule(utc_start, 2, 120), [at(10, 8), at(10, 10)])

    def test_round_names(self):
        self.assertEqual(round_name(1), "Final")
        self.assertEqual(round_name(2), "Semi-finals")
        self.assertEqual(round_name(4), "Quarter-finals")
        self.assertEqual(round_name(8), "Round of 16")
        self.assertEqual(round_name(64), "Round of 128")