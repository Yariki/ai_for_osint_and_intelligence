"""ICMP echo: host discovery by ping, on a raw ICMP socket.

An echo request carries no data. A reply is matched on the identifier the
scanner chose and on the sequence number, which stands for one target of one
chunk. The worker's receive loop reads the socket and calls ``on_packet``.
"""

import asyncio
import itertools
import time
from collections.abc import Callable, Sequence
from ipaddress import IPv4Address

from ndm.scanner.packet import build_icmp_echo, parse_echo_reply
from ndm.scanner.ratelimit import RateLimiter
from ndm.scanner.results import ChunkResults

# Sends one ICMP message to an address. Raw-socket sending in production.
Send = Callable[[bytes, IPv4Address], None]


class EchoProbes:
    """Echo requests in flight, for all chunks. One instance per scanner process."""

    def __init__(self, identifier: int, send: Send) -> None:
        self._identifier = identifier & 0xFFFF
        self._send = send
        self._sequences = itertools.cycle(range(0x10000))
        # sequence -> (target, results of its chunk, time sent)
        self._pending: dict[int, tuple[IPv4Address, ChunkResults, float]] = {}

    def on_packet(self, packet: bytes) -> None:
        """Called by the receive loop for every packet of the ICMP socket."""
        reply = parse_echo_reply(packet)
        if reply is None or reply.identifier != self._identifier:
            return
        pending = self._pending.get(reply.sequence)
        # The reply must come from the address the request went to.
        if pending is None or pending[0] != reply.source:
            return
        target, results, sent_at = self._pending.pop(reply.sequence)
        results.reachable(target, "icmp", (time.monotonic() - sent_at) * 1000)

    async def run(
        self,
        targets: Sequence[IPv4Address],
        *,
        scan_id: int,
        limiter: RateLimiter,
        results: ChunkResults,
        timeout_ms: int,
        retries: int,
    ) -> None:
        """Ping every target, again if it does not answer, and wait for late replies."""
        waiting = list(targets)
        for _attempt in range(1 + retries):
            if not waiting:
                break
            sequences: dict[IPv4Address, int] = {}
            for target in waiting:
                await limiter.acquire(scan_id)
                sequence = next(self._sequences)
                sequences[target] = sequence
                self._pending[sequence] = (target, results, time.monotonic())
                self._send(build_icmp_echo(self._identifier, sequence), target)
                results.probes_sent += 1

            deadline = time.monotonic() + timeout_ms / 1000
            while time.monotonic() < deadline and any(
                s in self._pending for s in sequences.values()
            ):
                await asyncio.sleep(0.02)
            # Forget what was not answered; a late reply to it is ignored.
            waiting = [t for t, s in sequences.items() if self._pending.pop(s, None) is not None]
