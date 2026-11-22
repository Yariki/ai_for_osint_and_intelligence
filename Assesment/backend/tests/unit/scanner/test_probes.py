"""The probing logic of the scanner engine, without raw sockets.

SYN and ICMP probes are sent into a simulated network that answers like the
lab targets; connect probes go to real listeners on the loopback address.
"""

import asyncio
import struct
from collections.abc import AsyncIterator
from ipaddress import IPv4Address
from typing import Any

import pytest

from ndm.core.messages import Discovery
from ndm.scanner.connect import ConnectProbes
from ndm.scanner.discovery import discover
from ndm.scanner.icmp import EchoProbes
from ndm.scanner.packet import ACK, RST, SYN, TcpReply
from ndm.scanner.ratelimit import RateLimiter
from ndm.scanner.results import ChunkResults
from ndm.scanner.syn import SynProbes

SCANNER = IPv4Address("192.0.2.1")
SOURCE_PORT = 61017
KEY = b"a secret of the scanner process"

RESET = IPv4Address("198.51.100.10")  # answers; resets closed ports
DROP = IPv4Address("198.51.100.20")  # answers on open ports only
NOBODY = IPv4Address("198.51.100.77")  # no host
OPEN = {RESET: {22, 443}, DROP: {443}}


def limiter(scan_id: int = 1) -> RateLimiter:
    fast = RateLimiter(1_000_000)
    fast.set_scan_rate(scan_id, 1_000_000)
    return fast


class Network:
    """Answers SYN segments as the hosts above would."""

    def __init__(self) -> None:
        self.sent: list[tuple[IPv4Address, int, bytes]] = []
        self.probes: SynProbes | None = None
        self.silent_once: set[tuple[IPv4Address, int]] = set()

    def send(self, segment: bytes, target: IPv4Address) -> None:
        source_port, port, sequence = struct.unpack_from("!HHI", segment)
        self.sent.append((target, port, segment))
        if (target, port) in self.silent_once:
            self.silent_once.discard((target, port))  # this one SYN is lost
            return
        if port in OPEN.get(target, set()):
            flags = SYN | ACK
        elif target == RESET:
            flags = RST | ACK
        else:
            return  # dropped
        reply = TcpReply(target, port, source_port, (sequence + 1) & 0xFFFFFFFF, flags)
        assert self.probes is not None
        asyncio.get_running_loop().call_soon(self.probes.on_reply, reply)


def syn_probes(network: Network, results: ChunkResults, **options: Any) -> SynProbes:
    settings: dict[str, Any] = {"timeout_ms": 60, "retries": 1, "seed": 7, **options}
    probes = SynProbes(
        scan_id=1,
        source=SCANNER,
        source_port=SOURCE_PORT,
        key=KEY,
        send=network.send,
        limiter=limiter(),
        results=results,
        **settings,
    )
    network.probes = probes
    return probes


# --- SYN ---


async def test_syn_scan_reports_open_closed_and_filtered() -> None:
    network, results = Network(), ChunkResults()
    ports = [22, 80, 443, 8080]

    await syn_probes(network, results).run([RESET, DROP, NOBODY], ports)

    found = {finding.address: sorted(finding.open_ports) for finding in results.drain()}
    assert found == {RESET: [22, 443], DROP: [443]}
    assert (results.closed[RESET], results.filtered[RESET]) == (2, 0)
    assert (results.closed[DROP], results.filtered[DROP]) == (0, 3)
    assert (results.closed[NOBODY], results.filtered[NOBODY]) == (0, 4)
    # Every pair was probed; only the unanswered ones were probed a second time.
    assert results.probes_sent == 12 + 7
    assert len({(target, port) for target, port, _ in network.sent}) == 12


async def test_syn_segments_carry_no_payload_and_use_the_chunks_source_port() -> None:
    network, results = Network(), ChunkResults()

    await syn_probes(network, results).run([RESET], [22, 80])

    for _target, _port, segment in network.sent:
        header_length = (segment[12] >> 4) * 4
        assert len(segment) == header_length  # nothing after the TCP header
        assert segment[13] == SYN
        assert struct.unpack_from("!H", segment)[0] == SOURCE_PORT


async def test_open_port_is_recorded_with_evidence() -> None:
    network, results = Network(), ChunkResults()

    await syn_probes(network, results).run([RESET], [443])

    (finding,) = results.drain()
    assert finding.open_ports == [443]
    assert list(finding.evidence) == ["tcp:443"]
    assert finding.evidence["tcp:443"].rtt_ms >= 0
    assert finding.evidence["tcp:443"].at.utcoffset() is not None
    assert results.drain() == []  # drained once


async def test_lost_probe_is_retried_and_then_found() -> None:
    network, results = Network(), ChunkResults()
    network.silent_once = {(RESET, 443)}

    await syn_probes(network, results, retries=1).run([RESET], [443])

    assert [finding.open_ports for finding in results.drain()] == [[443]]
    assert results.probes_sent == 2
    assert results.filtered[RESET] == 0


async def test_without_retries_an_unanswered_probe_is_filtered_at_once() -> None:
    network, results = Network(), ChunkResults()

    await syn_probes(network, results, retries=0).run([NOBODY], [80, 443])

    assert results.probes_sent == 2
    assert results.filtered[NOBODY] == 2


async def test_forged_and_repeated_replies_are_ignored() -> None:
    network, results = Network(), ChunkResults()
    probes = syn_probes(network, results)
    task = asyncio.create_task(probes.run([NOBODY], [443]))
    await asyncio.sleep(0.01)

    # Someone who does not know the key cannot make a port look open...
    probes.on_reply(TcpReply(NOBODY, 443, SOURCE_PORT, 12345, SYN | ACK))
    # ...nor can a reply from an address or port that was not probed.
    probes.on_reply(TcpReply(RESET, 9999, SOURCE_PORT, 1, SYN | ACK))
    await task
    assert results.drain() == []
    assert results.filtered[NOBODY] == 1

    # A reply that arrives twice is counted once.
    network, results = Network(), ChunkResults()
    probes = syn_probes(network, results)
    await probes.run([RESET], [80])
    sequence = struct.unpack_from("!I", network.sent[0][2], 4)[0]
    probes.on_reply(TcpReply(RESET, 80, SOURCE_PORT, sequence + 1, RST | ACK))
    assert results.closed[RESET] == 1


async def test_cancel_stops_sending_and_keeps_what_was_found() -> None:
    network, results = Network(), ChunkResults()
    slow = RateLimiter(200)
    slow.set_scan_rate(1, 200)
    probes = SynProbes(
        scan_id=1, source=SCANNER, source_port=SOURCE_PORT, key=KEY, send=network.send,
        limiter=slow, results=results, timeout_ms=50, retries=0, seed=1,
    )  # fmt: skip
    network.probes = probes
    task = asyncio.create_task(probes.run([RESET], list(range(1, 501))))
    await asyncio.sleep(0.1)

    probes.cancelled = True
    await asyncio.wait_for(task, timeout=2)

    assert 0 < results.probes_sent < 100
    assert results.closed[RESET] > 0


async def test_probe_order_is_shuffled_inside_the_chunk() -> None:
    network, results = Network(), ChunkResults()

    await syn_probes(network, results, retries=0).run([RESET, DROP], list(range(1, 201)))

    order = [(target, port) for target, port, _ in network.sent]
    assert order != sorted(order)
    assert sorted(order) == sorted((t, p) for t in (RESET, DROP) for p in range(1, 201))


# --- Connect ---


@pytest.fixture
async def listener() -> AsyncIterator[tuple[int, list[bytes]]]:
    """A local server that records what each client sends before it disconnects."""
    received: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        received.append(await reader.read())  # until the client closes
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1], received
    server.close()
    await server.wait_closed()


def closed_port() -> int:
    """A loopback port on which nothing listens."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


LOCAL = IPv4Address("127.0.0.1")


def connect_probes(results: ChunkResults, **options: Any) -> ConnectProbes:
    settings: dict[str, Any] = {"timeout_ms": 300, "retries": 1, "seed": 3, **options}
    return ConnectProbes(scan_id=1, limiter=limiter(), results=results, **settings)


async def test_connect_scan_reports_open_and_closed(listener: tuple[int, list[bytes]]) -> None:
    open_port, _ = listener
    refused = closed_port()
    results = ChunkResults()

    await connect_probes(results).run([LOCAL], [open_port, refused])

    (finding,) = results.drain()
    assert finding.open_ports == [open_port]
    assert list(finding.evidence) == [f"tcp:{open_port}"]
    assert (results.closed[LOCAL], results.filtered[LOCAL]) == (1, 0)
    assert results.probes_sent == 2


async def test_connection_is_closed_at_once_with_nothing_sent(
    listener: tuple[int, list[bytes]],
) -> None:
    open_port, received = listener

    await connect_probes(ChunkResults()).run([LOCAL], [open_port])
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.01)

    # The server saw a connection that ended without a single byte (FR-SCN-3).
    assert received == [b""]


async def test_connection_that_gets_no_answer_is_filtered_after_the_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def no_answer(*_args: Any, **_kwargs: Any) -> None:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(10)

    monkeypatch.setattr(asyncio, "open_connection", no_answer)
    results = ChunkResults()

    await connect_probes(results, timeout_ms=30, retries=2).run([LOCAL], [9])

    assert attempts == 3
    assert (results.filtered[LOCAL], results.closed[LOCAL]) == (1, 0)
    assert results.drain() == []


async def test_unreachable_network_counts_as_filtered(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_route(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(113, "No route to host")

    monkeypatch.setattr(asyncio, "open_connection", no_route)
    results = ChunkResults()

    await connect_probes(results, retries=0).run([LOCAL], [9])

    assert results.filtered[LOCAL] == 1


# --- ICMP echo ---


def echo_reply(source: IPv4Address, identifier: int, sequence: int) -> bytes:
    header = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 28, 0, 0, 64, 1, 0, source.packed, SCANNER.packed
    )
    return header + struct.pack("!BBHHH", 0, 0, 0, identifier, sequence)


class Pinged:
    """Answers echo requests for the hosts that are up."""

    def __init__(self, up: set[IPv4Address]) -> None:
        self.up = up
        self.requests: list[tuple[IPv4Address, bytes]] = []
        self.echo: EchoProbes | None = None

    def send(self, message: bytes, target: IPv4Address) -> None:
        self.requests.append((target, message))
        if target in self.up:
            _kind, _code, _checksum, identifier, sequence = struct.unpack("!BBHHH", message)
            assert self.echo is not None
            asyncio.get_running_loop().call_soon(
                self.echo.on_packet, echo_reply(target, identifier, sequence)
            )


async def ping(network: Pinged, targets: list[IPv4Address], **options: Any) -> ChunkResults:
    results = ChunkResults()
    network.echo = EchoProbes(identifier=0xBEEF, send=network.send)
    settings: dict[str, Any] = {"timeout_ms": 50, "retries": 1, **options}
    await network.echo.run(targets, scan_id=1, limiter=limiter(), results=results, **settings)
    return results


async def test_echo_finds_the_hosts_that_answer() -> None:
    network = Pinged(up={RESET})

    results = await ping(network, [RESET, NOBODY])

    (finding,) = results.drain()
    assert (finding.address, list(finding.evidence), finding.open_ports) == (RESET, ["icmp"], [])
    # The silent address was asked twice, the other once.
    assert [target for target, _ in network.requests] == [RESET, NOBODY, NOBODY]
    # An echo request has no data.
    assert all(len(message) == 8 for _, message in network.requests)


async def test_echo_reply_must_match_identifier_sequence_and_source() -> None:
    network = Pinged(up=set())
    results = ChunkResults()
    echo = EchoProbes(identifier=0xBEEF, send=network.send)
    task = asyncio.create_task(
        echo.run([NOBODY], scan_id=1, limiter=limiter(), results=results, timeout_ms=80, retries=0)
    )
    await asyncio.sleep(0.01)
    sequence = struct.unpack("!BBHHH", network.requests[0][1])[4]

    echo.on_packet(echo_reply(NOBODY, 0x1234, sequence))  # another program's ping
    echo.on_packet(echo_reply(NOBODY, 0xBEEF, sequence + 1))  # a sequence never sent
    echo.on_packet(echo_reply(RESET, 0xBEEF, sequence))  # from an address that was not asked
    echo.on_packet(b"garbage")
    await task

    assert results.drain() == []


# --- Discovery ---


async def test_discovery_combines_echo_and_tcp_probes() -> None:
    network, pinged, results = Network(), Pinged(up={RESET}), ChunkResults()
    echo = EchoProbes(identifier=1, send=pinged.send)
    pinged.echo = echo
    tcp = syn_probes(network, results, discovery=True)

    await discover(
        [RESET, DROP, NOBODY],
        Discovery(icmp_echo=True, tcp_ports=[80, 443]),
        scan_id=1, limiter=limiter(), results=results, timeout_ms=50, retries=0, tcp=tcp, echo=echo,
    )  # fmt: skip

    evidence = {finding.address: sorted(finding.evidence) for finding in results.drain()}
    assert evidence == {
        RESET: ["icmp", "tcp:443", "tcp:80"],  # a reset is evidence too
        DROP: ["tcp:443"],  # no ping, found by its open port
    }
    # Discovery counts nothing and reports no open port.
    assert not results.closed and not results.filtered


async def test_discovery_without_the_icmp_capability_uses_tcp_only() -> None:
    network, results = Network(), ChunkResults()

    await discover(
        [RESET, NOBODY],
        Discovery(icmp_echo=True, tcp_ports=[443]),
        scan_id=1, limiter=limiter(), results=results, timeout_ms=50, retries=0,
        tcp=syn_probes(network, results, discovery=True), echo=None,
    )  # fmt: skip

    assert {f.address: list(f.evidence) for f in results.drain()} == {RESET: ["tcp:443"]}


async def test_connect_discovery_takes_a_refused_connection_as_evidence(
    listener: tuple[int, list[bytes]],
) -> None:
    open_port, _ = listener
    refused = closed_port()
    results = ChunkResults()

    await discover(
        [LOCAL],
        Discovery(icmp_echo=False, tcp_ports=[open_port, refused]),
        scan_id=1, limiter=limiter(), results=results, timeout_ms=300, retries=0,
        tcp=connect_probes(results, discovery=True), echo=None,
    )  # fmt: skip

    (finding,) = results.drain()
    assert sorted(finding.evidence) == sorted([f"tcp:{open_port}", f"tcp:{refused}"])
    assert finding.open_ports == []
