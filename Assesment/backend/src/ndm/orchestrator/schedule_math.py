"""When a schedule runs (FR-SCH-1, FR-SCH-4, FR-SCH-6). Pure functions.

A schedule recurs by an interval, or by a cron expression read in a time zone.

- **Interval:** the runs are the anchor plus whole intervals. An interval is
  a duration, so daylight-saving changes do not move it.
- **Cron:** the runs are the times the expression names, on the wall clock of
  the time zone. A time that does not exist on the day clocks go forward is
  moved to the next one that does.
- **The next run** is always the first occurrence strictly after the current
  time. After downtime that is one run, not one per missed occurrence: missed
  runs are not replayed (FR-SCH-4).
- **The minimum interval** (FR-SCH-6): an interval is compared directly; for a
  cron expression the gaps between its upcoming occurrences are checked.
"""

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronsim import CronSim, CronSimError

# How far ahead the gaps of a cron expression are checked.
CHECK_OCCURRENCES = 2000
CHECK_HORIZON = timedelta(days=2 * 366)


class ScheduleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Recurrence:
    """An interval in seconds, or a cron expression with its time zone."""

    interval_seconds: int | None = None
    cron: str | None = None
    time_zone: str = "UTC"


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ScheduleError("invalid_time_zone", f"{name!r} is not a known time zone.") from error


def _cron(expression: str, start: datetime, time_zone: str) -> CronSim:
    try:
        return CronSim(expression, start.astimezone(zone(time_zone)))
    except CronSimError as error:
        raise ScheduleError(
            "invalid_cron", f"The cron expression is not valid: {error}."
        ) from error


def next_run(recurrence: Recurrence, now: datetime, anchor: datetime | None = None) -> datetime:
    """The first occurrence strictly after ``now``, in UTC.

    ``anchor`` is where the occurrences of an interval are counted from; by
    default ``now``, which makes the first run one interval away.
    """
    if (recurrence.interval_seconds is None) == (recurrence.cron is None):
        raise ScheduleError("no_recurrence", "Give an interval or a cron expression, not both.")
    if recurrence.interval_seconds is not None:
        if recurrence.interval_seconds <= 0:
            raise ScheduleError("invalid_interval", "The interval must be positive.")
        # In UTC: arithmetic on a datetime in a named time zone is wall-clock arithmetic.
        step = timedelta(seconds=recurrence.interval_seconds)
        current = now.astimezone(UTC)
        start = (anchor or now).astimezone(UTC)
        if start > current:
            return start
        passed = (current - start) // step
        return start + (passed + 1) * step
    assert recurrence.cron is not None
    return next(_cron(recurrence.cron, now, recurrence.time_zone)).astimezone(UTC)


def shortest_gap(recurrence: Recurrence, now: datetime) -> timedelta:
    """The shortest time between two runs, over the coming occurrences."""
    if recurrence.interval_seconds is not None:
        return timedelta(seconds=recurrence.interval_seconds)
    assert recurrence.cron is not None
    times = _cron(recurrence.cron, now, recurrence.time_zone)
    occurrences = []
    for occurrence in itertools.islice(times, CHECK_OCCURRENCES):
        occurrences.append(occurrence.astimezone(UTC))
        if occurrence - now > CHECK_HORIZON:
            break
    if len(occurrences) < 2:
        return CHECK_HORIZON
    return min(later - earlier for earlier, later in itertools.pairwise(occurrences))


def check(recurrence: Recurrence, now: datetime, minimum_seconds: int) -> None:
    """Raise ``ScheduleError`` unless the schedule is valid and keeps the minimum interval."""
    zone(recurrence.time_zone)
    next_run(recurrence, now)  # the recurrence can be evaluated at all
    gap = shortest_gap(recurrence, now)
    if gap < timedelta(seconds=minimum_seconds):
        minimum = _words(timedelta(seconds=minimum_seconds))
        raise ScheduleError(
            "below_minimum_interval",
            f"Runs would be {_words(gap)} apart; the minimum is {minimum}.",
        )


def _words(span: timedelta) -> str:
    seconds = int(span.total_seconds())
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds} seconds"
