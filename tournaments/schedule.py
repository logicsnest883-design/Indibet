"""
Round schedule (pure Python, no Django inside).

Rules
- Round 1 starts at the tournament's start time.
- Every next round starts `interval_minutes` after the one before it.
- A round can only start inside the daily play window: from `daily_open` up to (not including)
  `daily_close`. A time outside the window moves to the next opening time, so play pauses
  overnight and carries on the next morning.
"""
from datetime import datetime, timedelta


def _into_window(moment, daily_open, daily_close, tz):
    """Move a moment forward to the nearest time that is inside the daily play window."""
    local = moment.astimezone(tz)
    t = local.time()
    if daily_open <= t < daily_close:
        return local
    day = local.date()
    if t >= daily_close:
        day += timedelta(days=1)  # too late today: wait for tomorrow's opening
    return datetime.combine(day, daily_open, tzinfo=tz)


def round_start_times(first_start, rounds, interval_minutes, daily_open, daily_close, tz):
    """The start time of every round, as timezone-aware datetimes in `tz`."""
    starts = []
    current = first_start
    for _ in range(rounds):
        current = _into_window(current, daily_open, daily_close, tz)
        starts.append(current)
        current = current + timedelta(minutes=interval_minutes)
    return starts


def round_name(matches_in_round):
    """Final, Semi-finals, Quarter-finals, Round of 16, Round of 32..."""
    names = {1: "Final", 2: "Semi-finals", 4: "Quarter-finals"}
    return names.get(matches_in_round, f"Round of {matches_in_round * 2}")