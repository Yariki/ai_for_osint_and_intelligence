"""Check 4.4: the next run is strictly after the current time for intervals and
cron expressions, across time zones and daylight-saving changes; intervals and
cron expressions below the minimum are rejected."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ndm.orchestrator.schedule_math import (
    Recurrence,
    ScheduleError,
    check,
    next_run,
    shortest_gap,
)

HOUR = 3600
DAY = 24 * HOUR
NOW = datetime(2026, 11, 17, 10, 15, 30, tzinfo=UTC)


def at(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    *,
    tz: str = "UTC",
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo(tz))


# --- Intervals ---


def test_first_run_of_an_interval_is_one_interval_away() -> None:
    assert next_run(Recurrence(interval_seconds=HOUR), NOW) == NOW + timedelta(hours=1)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # Exactly at an occurrence: strictly after means the next one.
        (at(2026, 11, 17, 6), at(2026, 11, 17, 12)),
        (at(2026, 11, 17, 6, 0, 1), at(2026, 11, 17, 12)),
        (at(2026, 11, 17, 11, 59, 59), at(2026, 11, 17, 12)),
        # Days of downtime: one run, the next occurrence after now, not every missed one.
        (at(2026, 11, 21, 3, 7), at(2026, 11, 21, 6)),
    ],
)
def test_interval_runs_are_counted_from_the_anchor(now: datetime, expected: datetime) -> None:
    anchor = at(2026, 11, 17, 0)

    assert next_run(Recurrence(interval_seconds=6 * HOUR), now, anchor) == expected


def test_an_anchor_in_the_future_is_the_first_run() -> None:
    anchor = NOW + timedelta(days=2)

    assert next_run(Recurrence(interval_seconds=DAY), NOW, anchor) == anchor


def test_an_interval_is_a_duration_and_ignores_daylight_saving() -> None:
    # 24 hours from noon before the European clock change is 13:00 after it, on the wall clock.
    anchor = at(2026, 3, 28, 12, tz="Europe/Kyiv")
    now = at(2026, 3, 28, 12, 30, tz="Europe/Kyiv")

    run = next_run(Recurrence(interval_seconds=DAY), now, anchor)

    assert run - anchor == timedelta(days=1)
    assert run.astimezone(ZoneInfo("Europe/Kyiv")).hour == 13


# --- Cron expressions with a time zone ---


@pytest.mark.parametrize(
    ("cron", "tz", "now", "expected"),
    [
        ("0 2 * * *", "UTC", at(2026, 11, 17, 1, 59), at(2026, 11, 17, 2)),
        ("0 2 * * *", "UTC", at(2026, 11, 17, 2), at(2026, 11, 18, 2)),  # strictly after
        # 02:00 in Kyiv is midnight UTC in winter.
        ("0 2 * * *", "Europe/Kyiv", at(2026, 11, 17, 0, 30), at(2026, 11, 18, 0)),
        ("0 2 * * *", "America/New_York", at(2026, 11, 17, 6, 59), at(2026, 11, 17, 7)),
        ("30 9 * * 1", "Asia/Tokyo", at(2026, 11, 17, 0), at(2026, 11, 23, 0, 30)),  # Monday
    ],
)
def test_next_cron_run_is_strictly_after_now_in_the_time_zone(
    cron: str, tz: str, now: datetime, expected: datetime
) -> None:
    run = next_run(Recurrence(cron=cron, time_zone=tz), now)

    assert run == expected
    assert run > now and run.tzinfo == UTC


def test_cron_on_the_day_clocks_go_forward() -> None:
    """03:30 does not exist in Kyiv on 2026-03-29; the run moves to the next time that does."""
    now = at(2026, 3, 29, 2, 0, tz="Europe/Kyiv")

    run = next_run(Recurrence(cron="30 3 * * *", time_zone="Europe/Kyiv"), now)

    assert run > now
    assert run.astimezone(ZoneInfo("Europe/Kyiv")).date() == now.date()
    assert run == at(2026, 3, 29, 4, 0, tz="Europe/Kyiv")


def test_cron_on_the_day_clocks_go_back_runs_once() -> None:
    """03:30 happens twice in Kyiv on 2026-10-25; the schedule runs once that day."""
    tz = "Europe/Kyiv"
    recurrence = Recurrence(cron="30 3 * * *", time_zone=tz)
    now = at(2026, 10, 25, 1, 0, tz=tz)

    first = next_run(recurrence, now)
    second = next_run(recurrence, first)

    assert first > now
    assert second.astimezone(ZoneInfo(tz)).date() == datetime(2026, 10, 26).date()


@pytest.mark.parametrize("tz", ["UTC", "Europe/Kyiv", "America/New_York", "Australia/Lord_Howe"])
def test_a_year_of_next_runs_only_ever_moves_forward(tz: str) -> None:
    for recurrence in (
        Recurrence(cron="15 2 * * *", time_zone=tz),
        Recurrence(interval_seconds=7 * HOUR),
    ):
        now = at(2026, 1, 1, tz=tz)
        anchor = now
        for _ in range(400):
            run = next_run(recurrence, now, anchor)
            assert run > now
            now = run


# --- The minimum interval (FR-SCH-6) ---


@pytest.mark.parametrize(
    ("recurrence", "allowed"),
    [
        (Recurrence(interval_seconds=HOUR), True),
        (Recurrence(interval_seconds=HOUR - 1), False),
        (Recurrence(cron="0 * * * *"), True),
        (Recurrence(cron="*/30 * * * *"), False),
        (Recurrence(cron="0 2 * * *", time_zone="Europe/Kyiv"), True),
        # Mostly a day apart, but twice a day half an hour apart.
        (Recurrence(cron="0,30 9 * * *"), False),
        # Hourly, except near the end of the month.
        (Recurrence(cron="0 * 1-27 * *"), True),
        (Recurrence(cron="0 0 1 1 *"), True),
    ],
)
def test_runs_closer_than_the_minimum_are_rejected(recurrence: Recurrence, allowed: bool) -> None:
    if allowed:
        check(recurrence, NOW, HOUR)
    else:
        with pytest.raises(ScheduleError, match="minimum is 1 hour") as raised:
            check(recurrence, NOW, HOUR)
        assert raised.value.code == "below_minimum_interval"


def test_the_shortest_gap_of_a_cron_expression() -> None:
    assert shortest_gap(Recurrence(cron="0,30 9 * * *"), NOW) == timedelta(minutes=30)
    assert shortest_gap(Recurrence(cron="0 6,18 * * *"), NOW) == timedelta(hours=12)


@pytest.mark.parametrize(
    ("recurrence", "code"),
    [
        (Recurrence(cron="61 * * * *"), "invalid_cron"),
        (Recurrence(cron="every hour"), "invalid_cron"),
        (Recurrence(cron="0 2 * * *", time_zone="Mars/Olympus"), "invalid_time_zone"),
        (Recurrence(interval_seconds=0), "invalid_interval"),
        (Recurrence(), "no_recurrence"),
        (Recurrence(interval_seconds=HOUR, cron="0 * * * *"), "no_recurrence"),
    ],
)
def test_a_recurrence_that_is_not_valid_is_rejected(recurrence: Recurrence, code: str) -> None:
    with pytest.raises(ScheduleError) as raised:
        check(recurrence, NOW, 60)

    assert raised.value.code == code
