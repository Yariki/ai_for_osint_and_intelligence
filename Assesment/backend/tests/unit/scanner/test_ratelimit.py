import asyncio
import time
from collections import Counter

import pytest

from ndm.scanner.ratelimit import RateLimiter, TokenBucket


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def run(
    limiter: RateLimiter, scans: list[int], seconds: float, step: float = 0.001
) -> list[tuple[float, int]]:
    """Scans that take every token they can get, as senders do. Returns (time, scan) per grant."""
    clock = limiter._clock
    assert isinstance(clock, Clock)
    start = clock.now
    grants: list[tuple[float, int]] = []
    turn = 0
    while clock.now - start < seconds:
        # Each scan sends as much as it is allowed; they take turns to go first.
        for offset in range(len(scans)):
            scan = scans[(turn + offset) % len(scans)]
            while limiter.try_take(scan) == 0:
                grants.append((clock.now - start, scan))
        turn += 1
        clock.now += step
    return grants


def most_in_any_window(times: list[float], window: float) -> int:
    """The largest number of grants inside any window of the given length."""
    most = first = 0
    for last, moment in enumerate(times):
        while moment - times[first] >= window:
            first += 1
        most = max(most, last - first + 1)
    return most


# --- The guarantee: at or below the limit over any window ---


@pytest.mark.parametrize("limit", [5000, 1000, 200])
def test_tokens_granted_over_any_window_stay_at_or_below_the_limit(limit: int) -> None:
    limiter = RateLimiter(limit, Clock())
    limiter.set_scan_rate(1, limit)

    times = [moment for moment, _ in run(limiter, [1], seconds=12)]

    for window in (1.0, 2.5, 10.0):
        assert most_in_any_window(times, window) <= limit * window
    # And it does not waste the allowance: at least 98 % of the limit is used.
    assert len(times) >= 0.98 * limit * 12


def test_bucket_starts_empty_and_allows_only_a_small_burst() -> None:
    clock = Clock()
    bucket = TokenBucket(5000, clock)
    assert bucket.wait() > 0

    # After a long pause it holds 5 ms worth of tokens, not the pause's worth.
    clock.now += 60
    burst = 0
    while bucket.wait() == 0:
        bucket.take()
        burst += 1
    assert burst == 25


def test_wait_says_when_the_next_token_comes() -> None:
    clock = Clock()
    bucket = TokenBucket(100, clock)

    wait = bucket.wait()
    assert 0 < wait <= 0.011
    assert wait == pytest.approx(1 / 98)
    clock.now += wait
    assert bucket.wait() == 0


@pytest.mark.parametrize("limit", [0, -5])
def test_limit_must_be_positive(limit: int) -> None:
    with pytest.raises(ValueError):
        TokenBucket(limit)


# --- Scans share the global bucket ---


def test_two_scans_that_share_the_global_bucket_both_advance() -> None:
    limiter = RateLimiter(5000, Clock())
    for scan in (1, 2):
        limiter.set_scan_rate(scan, 5000)  # each asks for the whole ceiling

    grants = run(limiter, [1, 2], seconds=10)

    per_scan = Counter(scan for _, scan in grants)
    assert per_scan[1] > 0.4 * len(grants) and per_scan[2] > 0.4 * len(grants)
    # Together they stay at or below the global ceiling over any window.
    times = [moment for moment, _ in grants]
    assert most_in_any_window(times, 1.0) <= 5000
    assert most_in_any_window(times, 10.0) <= 50_000


def test_three_scans_and_a_small_one_all_advance() -> None:
    limiter = RateLimiter(5000, Clock())
    for scan, rate in ((1, 5000), (2, 5000), (3, 5000), (4, 200)):
        limiter.set_scan_rate(scan, rate)

    grants = run(limiter, [1, 2, 3, 4], seconds=10)

    per_scan = Counter(scan for _, scan in grants)
    assert all(per_scan[scan] > 0.2 * len(grants) for scan in (1, 2, 3))
    assert 0 < per_scan[4] <= 200 * 10
    assert most_in_any_window([moment for moment, _ in grants], 10.0) <= 50_000


def test_scan_does_not_exceed_its_own_rate_below_the_ceiling() -> None:
    limiter = RateLimiter(5000, Clock())
    limiter.set_scan_rate(1, 1000)

    times = [moment for moment, _ in run(limiter, [1], seconds=10)]

    assert most_in_any_window(times, 1.0) <= 1000
    assert 9800 <= len(times) <= 10_000


def test_rate_from_a_chunk_is_clamped_to_the_scanners_own_ceiling() -> None:
    limiter = RateLimiter(5000, Clock())

    assert limiter.set_scan_rate(1, 50_000) == 5000
    assert limiter.set_scan_rate(2, 800) == 800

    times = [moment for moment, _ in run(limiter, [1], seconds=3)]
    assert most_in_any_window(times, 1.0) <= 5000


def test_changing_the_rate_of_a_scan_takes_effect() -> None:
    limiter = RateLimiter(5000, Clock())
    limiter.set_scan_rate(1, 1000)
    limiter.set_scan_rate(1, 100)

    assert len(run(limiter, [1], seconds=10)) <= 1000


def test_dropped_scan_is_forgotten() -> None:
    limiter = RateLimiter(5000, Clock())
    limiter.set_scan_rate(1, 1000)

    limiter.drop_scan(1)
    limiter.drop_scan(1)  # twice is fine

    with pytest.raises(KeyError):
        limiter.try_take(1)


# --- Waiting ---


async def test_acquire_waits_for_a_token() -> None:
    limiter = RateLimiter(200)
    limiter.set_scan_rate(1, 200)

    started = time.monotonic()
    for _ in range(20):
        await limiter.acquire(1)
    elapsed = time.monotonic() - started

    # 20 tokens at 200 per second take about a tenth of a second.
    assert 0.08 <= elapsed < 1.0


async def test_waiting_does_not_block_other_work() -> None:
    limiter = RateLimiter(50)
    limiter.set_scan_rate(1, 50)
    ticks = 0

    async def other_work() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    task = asyncio.create_task(other_work())
    for _ in range(5):
        await limiter.acquire(1)
    task.cancel()

    assert ticks >= 5
