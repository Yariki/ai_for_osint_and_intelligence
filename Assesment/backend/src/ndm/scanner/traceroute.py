"""Traceroute in the scanner (architecture sections 7 and 8.2; FR-TRC-2 to FR-TRC-6).

The probes go to the target only, with a TTL that grows from 1. A router where
the TTL runs out answers with an ICMP time-exceeded message; the target answers
with an echo reply (ICMP), a SYN/ACK or a reset (TCP), or a port-unreachable
message (UDP). A hop address is never a destination (I2).

- ICMP: echo requests on the raw ICMP socket; identifier and sequence mark the probe.
- TCP: SYN segments on the raw TCP socket, from a source port of the scanner's
  range; source port, destination port and sequence number mark the probe.
- UDP: empty datagrams (zero-length payload) from a source port of the
  scanner's range, one destination port per probe.

The probes of one TTL are sent together. When each is answered or has timed
out, the TTL is published as one message (FR-TRC-6), and the next TTL starts.
The run ends at the target or at the maximum number of hops.

Before the first probe the job's target is checked against the scope the job
carries, the scanner's own table of non-public space and its own never-scan
list. A job that fails is rejected and nothing is sent.
"""

import asyncio
import itertools
import secrets
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from ipaddress import IPv4Address

from ndm.core import scope as scope_rules
from ndm.core.messages import HopReply, TracerouteJob, TracerouteMethod
from ndm.scanner.packet import ACK, RST, SYN, TcpReply, build_icmp_echo, build_syn, parse_echo_reply
from ndm.scanner.quoted import (
    PROTOCOL_ICMP,
    PROTOCOL_TCP,
    PROTOCOL_UDP,
    parse_icmp_error,
)
from ndm.scanner.ratelimit import RateLimiter

# Send functions, each with the TTL of the probe. Raw sockets in production.
SendIcmp = Callable[[bytes, IPv4Address, int], None]
SendTcp = Callable[[bytes, IPv4Address, int], None]
SendUdp = Callable[[int, IPv4Address, int, int], None]
"""(source port, target, destination port, TTL): send an empty datagram."""
PublishHop = Callable[[int, list[HopReply]], Awaitable[None]]


def check_job(
    job: TracerouteJob, *, never_scan: Sequence[scope_rules.ScopeEntry], allow_non_public: bool
) -> str | None:
    """Why the job must be rejected, or None if its target may be probed."""
    if not job.scope:
        return "no_scope"
    scope = []
    for start, end in job.scope:
        if start > end:
            return "invalid_scope"
        scope.append(scope_rules.ScopeEntry("range", f"{start}-{end}", start, end))
    target = scope_rules.ScopeEntry("address", str(job.target), job.target, job.target)
    try:
        scope_rules.check_target(
            target, scope=scope, never_scan=never_scan, allow_non_public=allow_non_public
        )
    except scope_rules.ScopeError as error:
        return error.code
    if job.method is not TracerouteMethod.ICMP and job.port is None:
        return "no_port"
    return None


@dataclass
class _Probe:
    ttl: int
    index: int
    target: IPv4Address
    sent_at: float
    reply: HopReply | None = None


@dataclass
class _Run:
    """The probes of one TTL of one run that wait for an answer."""

    probes: dict[tuple[object, ...], _Probe] = field(default_factory=dict)
    reached: bool = False


class Traceroutes:
    """Traceroute probes in flight, for all runs. One instance per scanner process."""

    def __init__(
        self,
        identifier: int,
        send_icmp: SendIcmp,
        send_tcp: SendTcp | None,
        send_udp: SendUdp,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._identifier = identifier & 0xFFFF
        self._send_icmp = send_icmp
        self._send_tcp = send_tcp
        self._send_udp = send_udp
        self._clock = clock
        self._sequences = itertools.cycle(range(0x10000))
        self._pending: dict[tuple[object, ...], tuple[_Probe, _Run]] = {}

    # --- Receiving ---

    def _answer(self, key: tuple[object, ...], address: IPv4Address, kind: str) -> None:
        found = self._pending.pop(key, None)
        if found is None:
            return  # another flow, a late answer, or one already counted
        probe, run = found
        rtt = (self._clock() - probe.sent_at) * 1000
        probe.reply = HopReply(probe=probe.index, address=address, rtt_ms=round(rtt, 3), kind=kind)
        if address == probe.target:
            run.reached = True

    def on_icmp(self, packet: bytes) -> None:
        """Called for every packet of the raw ICMP socket."""
        echo = parse_echo_reply(packet)
        if echo is not None:
            if echo.identifier == self._identifier:
                echo_key: tuple[object, ...] = ("icmp", echo.sequence)
                waiting = self._pending.get(echo_key)
                # Only the target itself answers an echo request with a reply.
                if waiting is not None and waiting[0].target == echo.source:
                    self._answer(echo_key, echo.source, "echo_reply")
            return
        error = parse_icmp_error(packet)
        if error is None:
            return
        if error.protocol == PROTOCOL_ICMP and error.identifier == self._identifier:
            key: tuple[object, ...] = ("icmp", error.sequence)
        elif error.protocol == PROTOCOL_TCP:
            key = ("tcp", error.source_port, error.destination_port, error.sequence)
        elif error.protocol == PROTOCOL_UDP:
            key = ("udp", error.source_port, error.destination_port)
        else:
            return
        found = self._pending.get(key)
        # The quoted probe must have been going to the target of that probe.
        if found is not None and found[0].target == error.destination:
            self._answer(key, error.responder, error.kind)

    def on_tcp(self, reply: TcpReply) -> None:
        """Called for a TCP segment captured on a traceroute's source port."""
        if not reply.flags & (RST | SYN | ACK):
            return
        key = (
            "tcp",
            reply.destination_port,
            reply.source_port,
            (reply.acknowledgement - 1) & 0xFFFFFFFF,
        )
        found = self._pending.get(key)
        if found is not None and found[0].target == reply.source:
            kind = "tcp_reset" if reply.flags & RST else "tcp_syn_ack"
            self._answer(key, reply.source, kind)

    # --- Sending ---

    async def run(
        self,
        job: TracerouteJob,
        *,
        limiter: RateLimiter,
        publish_hop: PublishHop,
        source: IPv4Address | None = None,
        source_port: int | None = None,
        stopped: Callable[[], bool] = lambda: False,
    ) -> tuple[bool, int]:
        """Trace the route to the job's target. Returns (target reached, probes sent).

        ``source`` and ``source_port`` are needed for TCP, ``source_port`` for UDP.
        """
        key = -job.run_id  # a rate bucket of its own, apart from the scans
        limiter.set_scan_rate(key, job.pps)
        sent = 0
        try:
            for ttl in range(1, job.max_hops + 1):
                run = _Run()
                for index in range(job.probes_per_hop):
                    if stopped() or not await limiter.acquire(key, stopped):
                        return run.reached, sent
                    self._send(job, ttl, index, run, source, source_port)
                    sent += 1
                deadline = self._clock() + job.timeout_ms / 1000
                while self._clock() < deadline and any(
                    probe.reply is None for probe in run.probes.values()
                ):
                    await asyncio.sleep(0.01)
                for pending_key in list(run.probes):
                    self._pending.pop(pending_key, None)  # a late answer is ignored
                replies = [
                    probe.reply or HopReply(probe=probe.index)
                    for probe in sorted(run.probes.values(), key=lambda probe: probe.index)
                ]
                await publish_hop(ttl, replies)
                if run.reached:
                    return True, sent
            return False, sent
        finally:
            limiter.drop_scan(key)

    def _send(
        self,
        job: TracerouteJob,
        ttl: int,
        index: int,
        run: _Run,
        source: IPv4Address | None,
        source_port: int | None,
    ) -> None:
        probe = _Probe(ttl=ttl, index=index, target=job.target, sent_at=self._clock())
        if job.method is TracerouteMethod.ICMP:
            sequence = next(self._sequences)
            key: tuple[object, ...] = ("icmp", sequence)
            message = build_icmp_echo(self._identifier, sequence)
            self._register(key, probe, run)
            self._send_icmp(message, job.target, ttl)
        elif job.method is TracerouteMethod.TCP:
            assert source is not None and source_port is not None and job.port is not None
            assert self._send_tcp is not None
            sequence = secrets.randbits(32)
            key = ("tcp", source_port, job.port, sequence)
            segment = build_syn(source, job.target, source_port, job.port, sequence)
            self._register(key, probe, run)
            self._send_tcp(segment, job.target, ttl)
        else:
            assert source_port is not None and job.port is not None
            # One destination port per probe, so that each answer names its probe.
            port = job.port + (ttl - 1) * job.probes_per_hop + index
            key = ("udp", source_port, (port - 1) % 65535 + 1)
            self._register(key, probe, run)
            self._send_udp(source_port, job.target, (port - 1) % 65535 + 1, ttl)

    def _register(self, key: tuple[object, ...], probe: _Probe, run: _Run) -> None:
        run.probes[key] = probe
        self._pending[key] = (probe, run)
