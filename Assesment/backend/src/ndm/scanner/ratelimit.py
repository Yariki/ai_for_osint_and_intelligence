"""Rate limiting: one token bucket per scan and one global bucket (FR-SCN-6).

Every probe takes a token from both. The global bucket enforces the operator's
ceiling across all running scans; a scan's own bucket enforces the rate chosen
for that scan.

The guarantee of a bucket with limit L: **over any window of one second or
longer, it grants at most L tokens per second of the window.**

A bucket has to allow small bursts, because the sender wakes up every few
milliseconds and not once per packet. It pays for the burst out of its refill
rate. With a capacity of C tokens it refills at L - C per second, so in a
window of T seconds it grants at most C + (L - C) * T, which is at most L * T
for every T of one second or more. C is 0.5 % of L and at least 2 tokens, so
at 5,000 packets per second a burst is 25 packets and the refill is 99.5 % of
the limit.

(Below 8 tokens per second a capacity of 2 would leave too little refill. The
window of the guarantee is then longer than a second: 8 / L seconds.)
"""

import asyncio
import time
from collections.abc import Callable

BURST_FRACTION = 0.005
MIN_CAPACITY = 2.0
# With a stop condition, the longest sleep between two looks at it.
MAX_SLEEP = 0.05


class TokenBucket:
    def __init__(self, limit: float, clock: Callable[[], float] = time.monotonic) -> None:
        if limit <= 0:
            raise ValueError("the limit must be positive")
        self.limit = limit
        self._capacity = max(MIN_CAPACITY, limit * BURST_FRACTION)
        # The window over which the guarantee holds: one second, or longer for
        # very low limits, so that at least three quarters of the limit refill.
        window = max(1.0, 4 * self._capacity / limit)
        self._refill = limit - self._capacity / window
        self._clock = clock
        # Starts empty: nothing is granted that was not earned since creation.
        self._tokens = 0.0
        self._updated = clock()

    def _fill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._refill)
        self._updated = now

    def wait(self) -> float:
        """Seconds until a token is available; 0 if one is available now."""
        self._fill()
        # A hair below one counts as one: a sleep for exactly the time asked for
        # must not leave the caller a rounding error short.
        return 0.0 if self._tokens >= 1 - 1e-9 else (1 - self._tokens) / self._refill

    def take(self) -> None:
        """Take one token. Call only after ``wait()`` returned 0."""
        self._tokens -= 1


class RateLimiter:
    """The global bucket and the buckets of the scans in flight."""

    def __init__(self, global_limit: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.global_limit = global_limit
        self._clock = clock
        self._global = TokenBucket(global_limit, clock)
        self._scans: dict[int, TokenBucket] = {}

    def set_scan_rate(self, scan_id: int, pps: float) -> float:
        """Set the rate of a scan and return the rate in effect.

        The rate that arrives in a chunk is clamped to the operator ceiling of
        the scanner's own environment (FR-CFG-7): the scanner does not take the
        orchestrator's word for it.
        """
        rate = min(pps, self.global_limit)
        bucket = self._scans.get(scan_id)
        if bucket is None or bucket.limit != rate:
            self._scans[scan_id] = TokenBucket(rate, self._clock)
        return rate

    def drop_scan(self, scan_id: int) -> None:
        self._scans.pop(scan_id, None)

    def try_take(self, scan_id: int) -> float:
        """Take one token from the scan's bucket and from the global bucket.

        Returns 0 if both gave one, otherwise the time to wait, and then takes
        from neither.
        """
        scan = self._scans[scan_id]
        wait = max(scan.wait(), self._global.wait())
        if wait == 0.0:
            scan.take()
            self._global.take()
        return wait

    async def acquire(self, scan_id: int, stop: Callable[[], bool] | None = None) -> bool:
        """Wait until a probe of this scan may be sent.

        Returns True with a token taken. If ``stop`` is given and becomes true
        while waiting, returns False with no token taken: a cancelled chunk
        must not keep queuing for its turn.
        """
        while (wait := self.try_take(scan_id)) > 0:
            if stop is not None and stop():
                return False
            await asyncio.sleep(min(wait, MAX_SLEEP) if stop is not None else wait)
        return True
