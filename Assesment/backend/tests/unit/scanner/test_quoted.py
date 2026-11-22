"""Reading ICMP errors, and matching them to traceroute probes (TASK-050)."""

import asyncio
import struct
from ipaddress import IPv4Address
from typing import Any

import pytest

from ndm.core.messages import HopReply, TracerouteJob, TracerouteMethod
from ndm.scanner.packet import ACK, RST, SYN, TcpReply, build_icmp_echo, build_syn
from ndm.scanner.quoted import (
    PROTOCOL_ICMP,
    PROTOCOL_TCP,
    PROTOCOL_UDP,
    TIME_EXCEEDED,
    UNREACHABLE,
    icmp_error,
    ip_packet,
    parse_icmp_error,
    udp_header,
)
from ndm.scanner.ratelimit import RateLimiter
from ndm.scanner.traceroute import Traceroutes

SCANNER = IPv4Address("192.0.2.10")
TARGET = IPv4Address("198.51.100.7")
ROUTER = IPv4Address("203.0.113.1")


def ip(address: str) -> IPv4Address:
    return IPv4Address(address)


# --- Parsing ---


def test_time_exceeded_for_an_icmp_echo() -> None:
    probe = ip_packet(SCANNER, TARGET, PROTOCOL_ICMP, build_icmp_echo(0x1234, 77))

    error = parse_icmp_error(icmp_error(ROUTER, SCANNER, TIME_EXCEEDED, 0, probe))

    assert error is not None
    assert (error.responder, error.kind, error.code) == (ROUTER, "time_exceeded", 0)
    assert (error.destination, error.protocol) == (TARGET, PROTOCOL_ICMP)
    assert (error.identifier, error.sequence) == (0x1234, 77)


def test_time_exceeded_for_a_tcp_syn() -> None:
    segment = build_syn(SCANNER, TARGET, 61000, 443, 0xDEADBEEF)
    probe = ip_packet(SCANNER, TARGET, PROTOCOL_TCP, segment)

    error = parse_icmp_error(icmp_error(ROUTER, SCANNER, TIME_EXCEEDED, 0, probe))

    assert error is not None
    assert (error.protocol, error.source_port, error.destination_port, error.sequence) == (
        PROTOCOL_TCP,
        61000,
        443,
        0xDEADBEEF,
    )


def test_port_unreachable_for_a_udp_datagram() -> None:
    probe = ip_packet(SCANNER, TARGET, PROTOCOL_UDP, udp_header(61001, 33440))

    error = parse_icmp_error(icmp_error(TARGET, SCANNER, UNREACHABLE, 3, probe))

    assert error is not None
    assert (error.responder, error.kind, error.code) == (TARGET, "unreachable", 3)
    assert (error.protocol, error.source_port, error.destination_port) == (
        PROTOCOL_UDP,
        61001,
        33440,
    )


@pytest.mark.parametrize(
    "packet",
    [
        b"",
        b"\x45" + b"\x00" * 10,
        # An echo reply is not an error.
        ip_packet(TARGET, SCANNER, PROTOCOL_ICMP, struct.pack("!BBHHH", 0, 0, 0, 1, 1)),
        # A redirect (type 5) is not an answer to a probe.
        ip_packet(ROUTER, SCANNER, PROTOCOL_ICMP, struct.pack("!BBHI", 5, 0, 0, 0) + b"\x00" * 28),
        # An error that quotes less than the 8 bytes needed.
        ip_packet(
            ROUTER,
            SCANNER,
            PROTOCOL_ICMP,
            struct.pack("!BBHI", 11, 0, 0, 0) + b"\x45" + b"\x00" * 19,
        ),
        # Not ICMP at all.
        ip_packet(ROUTER, SCANNER, PROTOCOL_TCP, b"\x00" * 20),
    ],
)
def test_anything_else_is_not_an_error(packet: bytes) -> None:
    assert parse_icmp_error(packet) is None


# --- A simulated network, for the traceroute of the scanner ---


class Network:
    """Answers probes as routers and a target would. ``path`` is the routers in
    front of the target; None is a silent router."""

    def __init__(self, path: list[str | None], answer: bool = True) -> None:
        self.path = [ip(hop) if hop else None for hop in path]
        self.answer = answer
        self.traces: Traceroutes | None = None
        self.sent: list[tuple[str, IPv4Address, int, bytes]] = []

    def _deliver(self, packet: bytes | TcpReply) -> None:
        traces = self.traces
        assert traces is not None
        loop = asyncio.get_running_loop()
        if isinstance(packet, TcpReply):
            loop.call_soon(traces.on_tcp, packet)
        else:
            loop.call_soon(traces.on_icmp, packet)

    def _router(self, ttl: int) -> IPv4Address | None | bool:
        """The router where a probe with this TTL ends, None if it is silent,
        True if the probe reaches the target."""
        return True if ttl > len(self.path) else self.path[ttl - 1]

    def icmp(self, message: bytes, target: IPv4Address, ttl: int) -> None:
        self.sent.append(("icmp", target, ttl, message))
        probe = ip_packet(SCANNER, target, PROTOCOL_ICMP, message)
        where = self._router(ttl)
        if where is True:
            if self.answer:
                identifier, sequence = struct.unpack_from("!HH", message, 4)
                reply = struct.pack("!BBHHH", 0, 0, 0, identifier, sequence)
                self._deliver(ip_packet(target, SCANNER, PROTOCOL_ICMP, reply))
        elif isinstance(where, IPv4Address):
            self._deliver(icmp_error(where, SCANNER, TIME_EXCEEDED, 0, probe))

    def tcp(self, segment: bytes, target: IPv4Address, ttl: int) -> None:
        self.sent.append(("tcp", target, ttl, segment))
        probe = ip_packet(SCANNER, target, PROTOCOL_TCP, segment)
        where = self._router(ttl)
        if where is True:
            if self.answer:
                source_port, port, sequence = struct.unpack_from("!HHI", segment)
                self._deliver(TcpReply(target, port, source_port, sequence + 1, SYN | ACK))
        elif isinstance(where, IPv4Address):
            self._deliver(icmp_error(where, SCANNER, TIME_EXCEEDED, 0, probe))

    def udp(self, source_port: int, target: IPv4Address, port: int, ttl: int) -> None:
        # What goes on the wire: an 8-byte header and nothing after it.
        datagram = udp_header(source_port, port)
        self.sent.append(("udp", target, ttl, datagram))
        probe = ip_packet(SCANNER, target, PROTOCOL_UDP, datagram)
        where = self._router(ttl)
        if where is True:
            if self.answer:
                self._deliver(icmp_error(target, SCANNER, UNREACHABLE, 3, probe))
        elif isinstance(where, IPv4Address):
            self._deliver(icmp_error(where, SCANNER, TIME_EXCEEDED, 0, probe))


def job(method: TracerouteMethod, **changes: Any) -> TracerouteJob:
    port = {TracerouteMethod.ICMP: None, TracerouteMethod.TCP: 443, TracerouteMethod.UDP: 33434}
    values: dict[str, Any] = {
        "run_id": 5,
        "target": TARGET,
        "method": method,
        "port": port[method],
        "max_hops": 10,
        "probes_per_hop": 2,
        "timeout_ms": 100,
        "pps": 10_000,
        "attestation_id": 1,
        "scope": [(TARGET, TARGET)],
    }
    return TracerouteJob(**(values | changes))


async def trace(
    network: Network, traced: TracerouteJob
) -> tuple[bool, int, list[tuple[int, list[HopReply]]]]:
    traces = Traceroutes(0x4242, network.icmp, network.tcp, network.udp)
    network.traces = traces
    hops: list[tuple[int, list[HopReply]]] = []

    async def publish(ttl: int, replies: list[HopReply]) -> None:
        hops.append((ttl, replies))

    reached, sent = await traces.run(
        traced, limiter=RateLimiter(10_000), publish_hop=publish, source=SCANNER, source_port=61000
    )
    return reached, sent, hops


def summary(
    hops: list[tuple[int, list[HopReply]]],
) -> list[tuple[int, list[tuple[str | None, str | None]]]]:
    return [
        (ttl, [(str(r.address) if r.address else None, r.kind) for r in replies])
        for ttl, replies in hops
    ]


PATH = ["10.0.0.1", None, "203.0.113.1"]


@pytest.mark.parametrize(
    ("method", "at_target"),
    [
        (TracerouteMethod.ICMP, "echo_reply"),
        (TracerouteMethod.TCP, "tcp_syn_ack"),
        (TracerouteMethod.UDP, "unreachable"),
    ],
)
async def test_each_method_reports_every_hop_the_silent_one_and_the_target(
    method: TracerouteMethod, at_target: str
) -> None:
    network = Network(PATH)

    reached, sent, hops = await trace(network, job(method))

    assert (reached, sent) == (True, 8)
    assert summary(hops) == [
        (1, [("10.0.0.1", "time_exceeded")] * 2),
        (2, [(None, None)] * 2),  # the silent router: a hop without an answer
        (3, [("203.0.113.1", "time_exceeded")] * 2),
        (4, [(str(TARGET), at_target)] * 2),
    ]
    # Each probe of a hop is reported once, in probe order, with its own time.
    for _, replies in hops:
        assert [r.probe for r in replies] == [0, 1]
        assert all(r.rtt_ms is None or r.rtt_ms >= 0 for r in replies)


@pytest.mark.parametrize("method", list(TracerouteMethod))
async def test_only_the_target_is_ever_a_destination_and_no_probe_carries_payload(
    method: TracerouteMethod,
) -> None:
    network = Network(PATH)

    await trace(network, job(method))

    assert {destination for _, destination, _, _ in network.sent} == {TARGET}
    assert [ttl for _, _, ttl, _ in network.sent] == [1, 1, 2, 2, 3, 3, 4, 4]
    for kind, _, _, data in network.sent:
        if kind == "icmp":
            assert len(data) == 8  # an echo request with no data
        elif kind == "tcp":
            data_offset = (data[12] >> 4) * 4
            assert len(data) == data_offset  # the header, and nothing after it
        else:
            assert struct.unpack_from("!H", data, 4)[0] == 8  # UDP length: header only


async def test_a_target_that_does_not_answer_is_traced_to_the_maximum_number_of_hops() -> None:
    network = Network(PATH, answer=False)

    reached, sent, hops = await trace(network, job(TracerouteMethod.ICMP, max_hops=6))

    assert (reached, sent) == (False, 12)
    assert [ttl for ttl, _ in hops] == [1, 2, 3, 4, 5, 6]
    assert summary(hops)[3:] == [(ttl, [(None, None)] * 2) for ttl in (4, 5, 6)]


async def test_an_error_that_quotes_another_flow_is_ignored() -> None:
    traces = Traceroutes(0x4242, lambda *a: None, lambda *a: None, lambda *a: None)
    hops: list[tuple[int, list[HopReply]]] = []

    async def publish(ttl: int, replies: list[HopReply]) -> None:
        hops.append((ttl, replies))

    async def strangers() -> None:
        await asyncio.sleep(0.01)
        others = [
            # Another program's ping: another identifier.
            ip_packet(SCANNER, TARGET, PROTOCOL_ICMP, build_icmp_echo(0x9999, 0)),
            # Our identifier and sequence, but to another destination.
            ip_packet(SCANNER, ip("198.51.100.99"), PROTOCOL_ICMP, build_icmp_echo(0x4242, 0)),
            # Another TCP flow, and a UDP flow nobody sent.
            ip_packet(SCANNER, TARGET, PROTOCOL_TCP, build_syn(SCANNER, TARGET, 50000, 80, 1)),
            ip_packet(SCANNER, TARGET, PROTOCOL_UDP, udp_header(50001, 33434)),
        ]
        for packet in others:
            traces.on_icmp(icmp_error(ROUTER, SCANNER, TIME_EXCEEDED, 0, packet))
        # An echo reply from an address that is not the target.
        traces.on_icmp(
            ip_packet(ROUTER, SCANNER, PROTOCOL_ICMP, struct.pack("!BBHHH", 0, 0, 0, 0x4242, 0))
        )
        # A reset to a port of ours, from somewhere else.
        traces.on_tcp(TcpReply(ROUTER, 443, 61000, 1, RST))

    (
        _,
        _,
    ) = await asyncio.gather(
        traces.run(
            job(TracerouteMethod.ICMP, max_hops=1, probes_per_hop=1),
            limiter=RateLimiter(10_000),
            publish_hop=publish,
        ),
        strangers(),
    )

    assert summary(hops) == [(1, [(None, None)])]


async def test_a_late_answer_after_the_timeout_is_ignored() -> None:
    late: list[bytes] = []

    def send(message: bytes, target: IPv4Address, ttl: int) -> None:
        late.append(ip_packet(SCANNER, target, PROTOCOL_ICMP, message))

    traces = Traceroutes(0x4242, send, None, lambda *a: None)
    hops: list[tuple[int, list[HopReply]]] = []

    async def publish(ttl: int, replies: list[HopReply]) -> None:
        hops.append((ttl, replies))
        # The router's answer arrives after the TTL was given up.
        traces.on_icmp(icmp_error(ROUTER, SCANNER, TIME_EXCEEDED, 0, late[0]))

    await traces.run(
        job(TracerouteMethod.ICMP, max_hops=2, probes_per_hop=1),
        limiter=RateLimiter(10_000),
        publish_hop=publish,
    )

    assert summary(hops) == [(1, [(None, None)]), (2, [(None, None)])]


async def test_every_probe_takes_a_token_of_the_run_and_of_the_global_rate() -> None:
    limiter = RateLimiter(10_000)
    taken: list[int] = []
    real = limiter.acquire

    async def counted(key: int, stop: Any = None) -> bool:
        taken.append(key)
        return await real(key, stop)

    limiter.acquire = counted  # type: ignore[method-assign,assignment]
    network = Network(PATH)
    traces = Traceroutes(0x4242, network.icmp, network.tcp, network.udp)
    network.traces = traces

    async def publish(ttl: int, replies: list[HopReply]) -> None:
        pass

    await traces.run(job(TracerouteMethod.ICMP), limiter=limiter, publish_hop=publish)

    # One per probe, from the run's own bucket (keyed apart from every scan)...
    assert taken == [-5] * 8
    # ...which is gone afterwards; the global bucket is the limiter's own.
    with pytest.raises(KeyError):
        limiter.try_take(-5)
