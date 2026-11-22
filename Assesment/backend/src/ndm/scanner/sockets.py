"""The scanner's sockets (architecture sections 8.2 and 8.3).

- Sending: a raw TCP socket. The kernel adds the IP header and routes.
- Receiving: a capture (packet) socket with a kernel packet filter that passes
  only TCP segments addressed to the scanner's source-port range. Under host
  networking the socket would otherwise get a copy of all TCP traffic of the
  host. A capture socket sees packets before the host firewall does, so
  reception does not depend on how the firewall treats untracked packets.
- ICMP: a raw ICMP socket for echo requests and their replies.

The choice of the capture socket for receiving is the architecture's default.
The Phase 0 spike report is to confirm it or change it (check 0.8).

Opening any of these needs the NET_RAW capability. Without it the scanner
reports that it has no SYN and no ICMP capability and works in connect mode.
"""

import ctypes
import socket
import struct
from dataclasses import dataclass
from ipaddress import IPv4Address

# Linux constants that the socket module does not name.
ETH_P_IP = 0x0800
SO_ATTACH_FILTER = 26
SOL_PACKET = 263
PACKET_STATISTICS = 6
PACKET_IGNORE_OUTGOING = 23

Instruction = tuple[int, int, int, int]  # code, jump if true, jump if false, k


def reply_filter(first_port: int, last_port: int) -> list[Instruction]:
    """Classic BPF: pass only unfragmented TCP segments addressed to the port range.

    Offsets count from the IP header, which is where the capture socket starts.
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


def attach_filter(sock: socket.socket, program: list[Instruction]) -> None:
    instructions = b"".join(struct.pack("HBBI", *instruction) for instruction in program)
    buffer = ctypes.create_string_buffer(instructions)
    sock.setsockopt(
        socket.SOL_SOCKET,
        SO_ATTACH_FILTER,
        struct.pack("HP", len(program), ctypes.addressof(buffer)),
    )


def receive_buffer_limit(path: str = "/proc/sys/net/core/rmem_max") -> int | None:
    try:
        with open(path) as file:
            return int(file.read())
    except OSError, ValueError:
        return None


def egress_address(target: IPv4Address) -> IPv4Address:
    """The address the kernel sends from to reach ``target``. Sends nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((str(target), 9))
        return IPv4Address(probe.getsockname()[0])


@dataclass
class RawSockets:
    """The sockets of SYN scanning. One set serves all work in flight."""

    send: socket.socket
    capture: socket.socket
    receive_buffer: int
    _drops: int = 0

    @classmethod
    def open(cls, source_ports: tuple[int, int]) -> RawSockets:
        """Open the send and capture sockets. Raises ``PermissionError`` without NET_RAW."""
        send = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP)
        send.setblocking(False)
        capture = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(ETH_P_IP))
        attach_filter(capture, reply_filter(*source_ports))
        try:
            capture.setsockopt(SOL_PACKET, PACKET_IGNORE_OUTGOING, 1)
        except OSError:
            pass  # older kernel: our own SYNs arrive too and the matcher discards them
        # As large as the host allows, so that a burst of replies is not dropped.
        limit = receive_buffer_limit()
        if limit is not None:
            capture.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, limit)
        capture.setblocking(False)
        return cls(send, capture, capture.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))

    def drops(self) -> int:
        """Packets the kernel has dropped at the capture socket since it was opened.

        The kernel resets its counter on every read, so the total is kept here.
        """
        stats = self.capture.getsockopt(SOL_PACKET, PACKET_STATISTICS, 8)
        _packets, dropped = struct.unpack("II", stats)
        self._drops += dropped
        return self._drops

    def close(self) -> None:
        self.send.close()
        self.capture.close()


def open_icmp_socket() -> socket.socket:
    """A raw ICMP socket. Raises ``PermissionError`` without NET_RAW."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    sock.setblocking(False)
    return sock
