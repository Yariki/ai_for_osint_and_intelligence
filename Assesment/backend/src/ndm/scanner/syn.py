"""SYN probing of one chunk (architecture section 8.2).

Each chunk in flight has its own source port. The worker's receive loop reads
the capture socket, finds the chunk by the destination port of a reply and
hands the reply to ``SynProbes.on_reply``. A reply counts only if it
acknowledges the keyed hash of the target (``ndm.scanner.matching``).

SYN/ACK means open, RST means closed, silence after the retries means
filtered. A probe carries no payload. The host's kernel answers a SYN/ACK
with a reset, which closes the half-open connection at the target.
"""

import asyncio
import random
import time
from collections.abc import Callable, Sequence
from ipaddress import IPv4Address

from ndm.scanner.matching import PortState, classify_reply, probe_sequence
from ndm.scanner.packet import TcpReply, build_syn
from ndm.scanner.ratelimit import RateLimiter
from ndm.scanner.results import ChunkResults

# Sends one TCP segment to an address. Raw-socket sending in production.
Send = Callable[[bytes, IPv4Address], None]


class SynProbes:
    """The SYN probes of one chunk: sending, matching, retrying, counting."""

    def __init__(
        self,
        *,
        scan_id: int,
        source: IPv4Address,
        source_port: int,
        key: bytes,
        send: Send,
        limiter: RateLimiter,
        results: ChunkResults,
        timeout_ms: int,
        retries: int,
        seed: int,
        discovery: bool = False,
    ) -> None:
        self.scan_id = scan_id
        self.source_port = source_port
        self._source = source
        self._key = key
        self._send = send
        self._limiter = limiter
        self._results = results
        self._timeout = timeout_ms / 1000
        self._retries = retries
        self._order = random.Random(seed)
        # In discovery any reply shows that the host is there, and nothing is counted.
        self._discovery = discovery
        # Probes without a reply yet: (address, port) -> time the last SYN was sent.
        self._pending: dict[tuple[IPv4Address, int], float] = {}
        self.cancelled = False

    def on_reply(self, reply: TcpReply) -> None:
        """Called by the receive loop for a segment addressed to this chunk's source port."""
        state = classify_reply(self._key, reply, self.source_port)
        if state is None:
            return
        sent_at = self._pending.pop((reply.source, reply.source_port), None)
        if sent_at is None:
            return  # answered before; a retransmitted reply
        rtt_ms = (time.monotonic() - sent_at) * 1000
        if self._discovery:
            self._results.reachable(reply.source, f"tcp:{reply.source_port}", rtt_ms)
        elif state is PortState.OPEN:
            self._results.open_port(reply.source, reply.source_port, rtt_ms)
        else:
            self._results.closed[reply.source] += 1

    async def run(self, targets: Sequence[IPv4Address], ports: Sequence[int]) -> None:
        """Probe every target on every port, then wait for late replies.

        Returns when every probe is answered, timed out after its retries, or
        the chunk was cancelled. What stays unanswered is counted as filtered.
        """
        # Shuffled again inside the chunk (FR-SCN-10).
        probes = [(target, port) for target in targets for port in ports]
        self._order.shuffle(probes)

        for _attempt in range(1 + self._retries):
            if not probes or self.cancelled:
                break
            for target, port in probes:
                if not await self._limiter.acquire(self.scan_id, stop=lambda: self.cancelled):
                    break
                sequence = probe_sequence(self._key, target, port)
                self._pending[(target, port)] = time.monotonic()
                self._send(
                    build_syn(self._source, target, self.source_port, port, sequence), target
                )
                self._results.probes_sent += 1
            await self._wait_for_replies()
            # Only what is still unanswered is probed again.
            probes = [probe for probe in probes if probe in self._pending]

        if not self._discovery:
            for target, _port in self._pending:
                self._results.filtered[target] += 1
        self._pending.clear()

    async def _wait_for_replies(self) -> None:
        deadline = time.monotonic() + self._timeout
        while self._pending and not self.cancelled and time.monotonic() < deadline:
            await asyncio.sleep(min(0.02, self._timeout))
