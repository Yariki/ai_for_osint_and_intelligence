"""Phase 0 spike scanner. Disposable: it proves a design, it is not the product.

It sends TCP SYN probes from a raw socket at a paced rate, receives the replies
on a capture socket (or, for comparison, on a raw TCP socket), matches them
statelessly, and prints a JSON report. With a lab manifest it also says whether
the result is exact.

What is kept from Phase 0 is imported here, not copied: the packet builder and
the reply matcher of ``ndm.scanner``, and the port lists of ``ndm.core``.

Exit status: 0 result exact (or no manifest given), 1 result differs from the
manifest, 3 no SYN capability.
"""

import argparse
import ctypes
import ipaddress
import json
import os
import random
import secrets
import select
import socket
import struct
import sys
import time
from collections.abc import Iterator
from ipaddress import IPv4Address

from ndm.core.ports import PortProfile, profile_ports
from ndm.scanner.matching import PortState, classify_reply, probe_sequence
from ndm.scanner.packet import build_icmp_echo, build_syn, parse_echo_reply, parse_tcp_reply

# Linux constants that the socket module does not name.
ETH_P_IP = 0x0800
SO_ATTACH_FILTER = 26
SO_RXQ_OVFL = 40
SOL_PACKET = 263
PACKET_STATISTICS = 6
PACKET_IGNORE_OUTGOING = 23

CAPABILITY_NAMES = {12: "NET_ADMIN", 13: "NET_RAW"}

# --- Start-up checks ---------------------------------------------------------


def parse_port_range(text: str) -> tuple[int, int]:
    low, _, high = text.partition("-")
    first, last = int(low), int(high)
    if not 1024 <= first <= last <= 65535:
        raise ValueError(f"invalid source-port range {text!r}")
    return first, last


def ephemeral_range(path: str = "/proc/sys/net/ipv4/ip_local_port_range") -> tuple[int, int]:
    with open(path) as file:
        low, high = file.read().split()
    return int(low), int(high)


def ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def effective_capabilities(path: str = "/proc/self/status") -> list[str]:
    """Names of the effective capabilities of this process (check 0.6)."""
    with open(path) as file:
        for line in file:
            if line.startswith("CapEff:"):
                bits = int(line.split()[1], 16)
                return [
                    CAPABILITY_NAMES.get(bit, f"cap_{bit}") for bit in range(64) if bits >> bit & 1
                ]
    return []


def conntrack_count() -> int | None:
    try:
        with open("/proc/sys/net/netfilter/nf_conntrack_count") as file:
            return int(file.read())
    except OSError:
        return None


# --- Kernel packet filter ----------------------------------------------------


def reply_filter(first_port: int, last_port: int) -> list[tuple[int, int, int, int]]:
    """Classic BPF: pass only unfragmented TCP segments addressed to the port range.

    Offsets count from the IP header, which is where both receive sockets start.
    Each instruction is (code, jump-if-true, jump-if-false, k).
    """
    return [
        (0x30, 0, 0, 9),  # A = IP protocol
        (0x15, 0, 7, 6),  # TCP? else drop
        (0x28, 0, 0, 6),  # A = flags and fragment offset
        (0x45, 5, 0, 0x3FFF),  # a fragment? drop
        (0xB1, 0, 0, 0),  # X = length of the IP header
        (0x48, 0, 0, 2),  # A = TCP destination port
        (0x35, 0, 2, first_port),  # below the range? drop
        (0x25, 1, 0, last_port),  # above the range? drop
        (0x06, 0, 0, 0x40000),  # accept
        (0x06, 0, 0, 0),  # drop
    ]


def attach_filter(sock: socket.socket, program: list[tuple[int, int, int, int]]) -> None:
    instructions = b"".join(struct.pack("HBBI", *instruction) for instruction in program)
    buffer = ctypes.create_string_buffer(instructions)
    sock.setsockopt(
        socket.SOL_SOCKET, SO_ATTACH_FILTER, struct.pack("HP", len(program), ctypes.addressof(buffer))
    )


# --- Sockets -----------------------------------------------------------------


def egress_address(target: IPv4Address) -> IPv4Address:
    """The address the kernel would send from to reach ``target``."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((str(target), 9))
        return IPv4Address(probe.getsockname()[0])


def enlarge_receive_buffer(sock: socket.socket) -> int:
    try:
        with open("/proc/sys/net/core/rmem_max") as file:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(file.read()))
    except OSError:
        pass
    return sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)


def open_capture_socket(ports: tuple[int, int]) -> socket.socket:
    """Receive path A: a packet socket. It sees packets before the host firewall does."""
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(ETH_P_IP))
    attach_filter(sock, reply_filter(*ports))
    try:
        sock.setsockopt(SOL_PACKET, PACKET_IGNORE_OUTGOING, 1)
    except OSError:
        pass  # older kernel; our own SYNs are then discarded by the matcher
    sock.setblocking(False)
    return sock


def open_raw_receive_socket(ports: tuple[int, int]) -> socket.socket:
    """Receive path B: a raw TCP socket. It sees only what the host firewall lets in."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP)
    attach_filter(sock, reply_filter(*ports))
    sock.setsockopt(socket.SOL_SOCKET, SO_RXQ_OVFL, 1)
    sock.setblocking(False)
    return sock


class Receiver:
    """One of the two receive paths, with its count of packets the kernel dropped."""

    def __init__(self, path: str, ports: tuple[int, int]) -> None:
        self.path = path
        self.sock = open_capture_socket(ports) if path == "capture" else open_raw_receive_socket(ports)
        self.buffer_size = enlarge_receive_buffer(self.sock)
        self._raw_drops = 0

    def packets(self) -> Iterator[bytes]:
        """Everything that is waiting, without blocking."""
        while True:
            try:
                if self.path == "capture":
                    yield self.sock.recv(65535)
                else:
                    data, ancillary, _flags, _address = self.sock.recvmsg(65535, 64)
                    for level, kind, value in ancillary:
                        if level == socket.SOL_SOCKET and kind == SO_RXQ_OVFL:
                            self._raw_drops = struct.unpack("I", value[:4])[0]
                    yield data
            except BlockingIOError:
                return

    def drops(self) -> int:
        """Packets the kernel dropped because the receive buffer was full."""
        if self.path == "raw":
            return self._raw_drops
        stats = self.sock.getsockopt(SOL_PACKET, PACKET_STATISTICS, 8)
        _packets, drops = struct.unpack("II", stats)
        return drops


# --- Scan --------------------------------------------------------------------


def probe_order(
    targets: list[IPv4Address], ports: list[int], seed: int
) -> Iterator[tuple[IPv4Address, int]]:
    """Every (target, port) pair once, in an order that is not sorted."""
    order = random.Random(seed)
    ports = ports[:]
    order.shuffle(ports)
    for port in ports:
        hosts = targets[:]
        order.shuffle(hosts)
        for host in hosts:
            yield host, port


def scan(
    targets: list[IPv4Address],
    ports: list[int],
    *,
    rate: int,
    source_port: int,
    receiver: Receiver,
    wait_after: float,
    max_seconds: float | None,
    seed: int,
) -> dict:
    key = secrets.token_bytes(16)
    source = egress_address(targets[0])
    sender = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP)
    destinations = {target: (str(target), 0) for target in targets}

    open_ports: dict[str, list[int]] = {}
    closed = sent = send_errors = received = 0

    def drain() -> None:
        nonlocal closed, received
        for packet in receiver.packets():
            reply = parse_tcp_reply(packet)
            if reply is None:
                continue
            state = classify_reply(key, reply, source_port)
            if state is PortState.OPEN:
                found = open_ports.setdefault(str(reply.source), [])
                if reply.source_port not in found:
                    found.append(reply.source_port)
                received += 1
            elif state is PortState.CLOSED:
                closed += 1
                received += 1

    # Token bucket: tokens arrive at `rate` per second; a burst is at most 10 ms worth.
    capacity = max(1.0, rate / 100)
    tokens = capacity
    pairs = probe_order(targets, ports, seed)
    started = last = time.monotonic()
    cpu_started = time.process_time()
    conntrack_before = conntrack_count()
    sending = True
    stop_at = 0.0

    while True:
        now = time.monotonic()
        if sending:
            tokens = min(capacity, tokens + (now - last) * rate)
            last = now
            while tokens >= 1:
                pair = next(pairs, None)
                if pair is None or (max_seconds is not None and now - started >= max_seconds):
                    sending = False
                    stop_at = time.monotonic() + wait_after
                    break
                target, port = pair
                segment = build_syn(source, target, source_port, port, probe_sequence(key, target, port))
                try:
                    sender.sendto(segment, destinations[target])
                    sent += 1
                except OSError:
                    send_errors += 1
                tokens -= 1
            timeout = (1 - tokens) / rate if sending else 0.0
        else:
            if now >= stop_at:
                break
            timeout = stop_at - now
        if select.select([receiver.sock], [], [], max(0.0, timeout))[0]:
            drain()

    sending_seconds = stop_at - wait_after - started
    drain()
    conntrack_after = conntrack_count()
    return {
        "source_address": str(source),
        "source_port": source_port,
        "receive_path": receiver.path,
        "receive_buffer_bytes": receiver.buffer_size,
        "probes_sent": sent,
        "send_errors": send_errors,
        "sending_seconds": round(sending_seconds, 3),
        "achieved_rate_pps": round(sent / sending_seconds) if sending_seconds > 0 else 0,
        "requested_rate_pps": rate,
        "replies_matched": received,
        "closed_ports": closed,
        "open_ports": {host: sorted(found) for host, found in sorted(open_ports.items())},
        "kernel_receive_drops": receiver.drops(),
        "processor_load": round((time.process_time() - cpu_started) / (time.monotonic() - started), 3),
        "conntrack_count_before": conntrack_before,
        "conntrack_count_after": conntrack_after,
    }


def ping(targets: list[IPv4Address], wait: float) -> list[str]:
    """ICMP echo to every target; the addresses that answered."""
    identifier = os.getpid() & 0xFFFF
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    sock.setblocking(False)
    for sequence, target in enumerate(targets):
        sock.sendto(build_icmp_echo(identifier, sequence & 0xFFFF), (str(target), 0))
        time.sleep(0.001)
    answered = set()
    deadline = time.monotonic() + wait
    while (remaining := deadline - time.monotonic()) > 0:
        if not select.select([sock], [], [], remaining)[0]:
            break
        try:
            reply = parse_echo_reply(sock.recv(65535))
        except BlockingIOError:
            continue
        if reply is not None and reply.identifier == identifier:
            answered.add(reply.source)
    return [str(address) for address in sorted(answered)]


# --- Comparison with the manifest ---------------------------------------------


def compare_with_manifest(report: dict, manifest: dict, probed: set[int]) -> dict:
    """What the scan should have found, given the ports it probed, against what it found."""
    expected = {
        host["address"]: sorted(set(host["open_ports"]) & probed)
        for host in manifest["hosts"]
        if set(host["open_ports"]) & probed
    }
    found = report["open_ports"]
    missed = {h: sorted(set(p) - set(found.get(h, []))) for h, p in expected.items()}
    false = {h: sorted(set(p) - set(expected.get(h, []))) for h, p in found.items()}
    return {
        "expected_open_ports": sum(len(ports) for ports in expected.values()),
        "missed": {host: ports for host, ports in missed.items() if ports},
        "false_open": {host: ports for host, ports in false.items() if ports},
    }


# --- Command line ------------------------------------------------------------


def parse_ports(text: str) -> list[int]:
    if text in ("top-100", "top-1000", "full"):
        return list(profile_ports(PortProfile(text)))
    return [int(port) for port in text.split(",")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--targets", help="CIDR block to scan; default: the manifest's network")
    parser.add_argument("--ports", default="top-1000", help="top-100, top-1000, full, or a list")
    parser.add_argument("--rate", type=int, default=5000, help="packets per second")
    parser.add_argument("--source-ports", default="61000-61999")
    parser.add_argument("--receive", choices=["capture", "raw"], default="capture")
    parser.add_argument("--wait", type=float, default=3.0, help="seconds to wait for late replies")
    parser.add_argument("--max-seconds", type=float, help="stop sending after this long")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--manifest", help="lab manifest to compare the result with")
    parser.add_argument("--ping", action="store_true", help="also send ICMP echo to every target")
    args = parser.parse_args()

    report: dict = {"uid": os.getuid(), "effective_capabilities": effective_capabilities()}

    manifest = None
    if args.manifest:
        import yaml  # only the lab comparison needs it

        with open(args.manifest) as file:
            manifest = yaml.safe_load(file)
    network = args.targets or (manifest or {}).get("scanned_network")
    if not network:
        parser.error("give --targets or --manifest")
    targets = list(ipaddress.ip_network(network))
    ports = parse_ports(args.ports)

    # Start-up guard (check 0.7): a source port inside the kernel's ephemeral
    # range could belong to a real connection of the host, whose packets the
    # scan would then disturb. No SYN scanning in that case.
    source_ports = parse_port_range(args.source_ports)
    local_ports = ephemeral_range()
    report["source_port_range"] = list(source_ports)
    report["ephemeral_port_range"] = list(local_ports)
    if ranges_overlap(source_ports, local_ports):
        report["syn_capability"] = False
        report["reason"] = "the source-port range overlaps net.ipv4.ip_local_port_range"
        print(json.dumps(report, indent=2))
        return 3

    try:
        receiver = Receiver(args.receive, source_ports)
    except PermissionError:
        report["syn_capability"] = False
        report["reason"] = "raw sockets are not permitted (the NET_RAW capability is missing)"
        print(json.dumps(report, indent=2))
        return 3
    report["syn_capability"] = True

    if args.ping:
        report["ping_answered"] = ping(targets, args.wait)

    report.update(
        scan(
            targets,
            ports,
            rate=args.rate,
            source_port=source_ports[0],
            receiver=receiver,
            wait_after=args.wait,
            max_seconds=args.max_seconds,
            seed=args.seed if args.seed is not None else secrets.randbits(32),
        )
    )

    exact = True
    if manifest is not None:
        comparison = compare_with_manifest(report, manifest, set(ports))
        report.update(comparison)
        exact = not comparison["missed"] and not comparison["false_open"]
        report["exact"] = exact
    print(json.dumps(report, indent=2))
    return 0 if exact else 1


if __name__ == "__main__":
    sys.exit(main())
