"""Connect probing: the fallback when the scanner has no raw-socket capability.

A full TCP connection is opened with the operating system's own stack.
Connected means open, refused means closed, a timeout means filtered. The
connection is closed as soon as it is established: nothing is sent and nothing
is read (FR-SCN-3).
"""

import asyncio
import random
import time
from collections.abc import Sequence
from ipaddress import IPv4Address

from ndm.scanner.ratelimit import RateLimiter
from ndm.scanner.results import ChunkResults

# Connections that may be open or opening at the same time.
CONCURRENCY = 256


class ConnectProbes:
    """The connect probes of one chunk."""

    def __init__(
        self,
        *,
        scan_id: int,
        limiter: RateLimiter,
        results: ChunkResults,
        timeout_ms: int,
        retries: int,
        seed: int,
        discovery: bool = False,
    ) -> None:
        self.scan_id = scan_id
        self._limiter = limiter
        self._results = results
        self._timeout = timeout_ms / 1000
        self._retries = retries
        self._order = random.Random(seed)
        self._discovery = discovery
        self._slots = asyncio.Semaphore(CONCURRENCY)
        self.cancelled = False

    async def _probe(self, target: IPv4Address, port: int) -> None:
        for _attempt in range(1 + self._retries):
            if not await self._limiter.acquire(self.scan_id, stop=lambda: self.cancelled):
                return
            self._results.probes_sent += 1
            started = time.monotonic()
            try:
                async with asyncio.timeout(self._timeout):
                    _reader, writer = await asyncio.open_connection(str(target), port)
            except ConnectionRefusedError:
                # A reset: the host is there and the port is closed.
                rtt_ms = (time.monotonic() - started) * 1000
                if self._discovery:
                    self._results.reachable(target, f"tcp:{port}", rtt_ms)
                else:
                    self._results.closed[target] += 1
                return
            except TimeoutError, OSError:
                continue  # no answer, or no route: try again, then filtered
            rtt_ms = (time.monotonic() - started) * 1000
            # Established. Close at once; nothing was written and nothing is read.
            writer.close()
            if self._discovery:
                self._results.reachable(target, f"tcp:{port}", rtt_ms)
            else:
                self._results.open_port(target, port, rtt_ms)
            return
        if not self._discovery:
            self._results.filtered[target] += 1

    async def run(self, targets: Sequence[IPv4Address], ports: Sequence[int]) -> None:
        """Probe every target on every port. Returns when all probes are done or cancelled."""
        probes = [(target, port) for target in targets for port in ports]
        self._order.shuffle(probes)

        async def limited(target: IPv4Address, port: int) -> None:
            async with self._slots:
                await self._probe(target, port)

        await asyncio.gather(*(limited(target, port) for target, port in probes))
