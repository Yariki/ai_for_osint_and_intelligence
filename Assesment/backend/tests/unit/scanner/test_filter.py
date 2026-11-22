"""The kernel packet filter of the capture socket.

It runs in the kernel, where a test cannot watch it, so the program is run
here by a small interpreter of the classic BPF instructions it uses.
"""

import struct
from ipaddress import IPv4Address

import pytest

from ndm.scanner.packet import ACK, SYN, parse_tcp_reply
from ndm.scanner.sockets import Instruction, reply_filter

FIRST, LAST = 61000, 61999
SCANNER = IPv4Address("192.0.2.1")
TARGET = IPv4Address("198.51.100.2")


def run(program: list[Instruction], packet: bytes) -> int:
    """Run a classic BPF program on a packet. Returns what the kernel would:
    0 to drop the packet, anything else to pass it."""
    accumulator = index = 0
    position = 0

    def load(offset: int, size: int) -> int:
        if offset + size > len(packet):
            raise IndexError
        return int.from_bytes(packet[offset : offset + size], "big")

    try:
        while True:
            code, if_true, if_false, k = program[position]
            position += 1
            if code == 0x30:  # load byte
                accumulator = load(k, 1)
            elif code == 0x28:  # load half word
                accumulator = load(k, 2)
            elif code == 0x48:  # load half word at index + k
                accumulator = load(index + k, 2)
            elif code == 0xB1:  # index = IP header length
                index = (load(k, 1) & 0x0F) * 4
            elif code == 0x15:  # jump if equal
                position += if_true if accumulator == k else if_false
            elif code == 0x45:  # jump if any of these bits is set
                position += if_true if accumulator & k else if_false
            elif code == 0x35:  # jump if greater or equal
                position += if_true if accumulator >= k else if_false
            elif code == 0x25:  # jump if greater
                position += if_true if accumulator > k else if_false
            elif code == 0x06:  # return
                return k
            else:
                raise AssertionError(f"instruction {code:#x} is not known to this interpreter")
    except IndexError:
        return 0  # the kernel drops a packet when a load reaches past its end


def ipv4(protocol: int, payload: bytes, *, fragment: int = 0, options: bytes = b"") -> bytes:
    header_words = 5 + len(options) // 4
    return (
        struct.pack(
            "!BBHHHBBH4s4s",
            0x40 | header_words,
            0,
            20 + len(options) + len(payload),
            0,
            fragment,
            64,
            protocol,
            0,
            TARGET.packed,
            SCANNER.packed,
        )
        + options
        + payload
    )


def tcp(destination_port: int, source_port: int = 443) -> bytes:
    return struct.pack(
        "!HHIIBBHHH", source_port, destination_port, 1, 2, 0x50, SYN | ACK, 1024, 0, 0
    )


PASSED = {
    "first port of the range": ipv4(6, tcp(FIRST)),
    "last port of the range": ipv4(6, tcp(LAST)),
    "a port inside the range": ipv4(6, tcp(61500)),
    "behind IP options": ipv4(6, tcp(FIRST), options=b"\x01" * 8),
    "with the don't-fragment flag": ipv4(6, tcp(FIRST), fragment=0x4000),
    "from any source port": ipv4(6, tcp(61234, source_port=1)),
}

DROPPED = {
    "one below the range": ipv4(6, tcp(FIRST - 1)),
    "one above the range": ipv4(6, tcp(LAST + 1)),
    "ssh traffic of the host": ipv4(6, tcp(22)),
    "an ephemeral port of the host": ipv4(6, tcp(45000)),
    "source port in the range, destination not": ipv4(6, tcp(80, source_port=FIRST)),
    "UDP to the range": ipv4(17, tcp(FIRST)),
    "ICMP": ipv4(1, tcp(FIRST)),
    "other port behind IP options": ipv4(6, tcp(80), options=b"\x01" * 4),
    "first fragment": ipv4(6, tcp(FIRST), fragment=0x2000),
    "later fragment": ipv4(6, tcp(FIRST), fragment=0x0005),
    "truncated TCP header": ipv4(6, tcp(FIRST))[:21],
    "truncated IP header": ipv4(6, tcp(FIRST))[:8],
    "empty": b"",
}


@pytest.mark.parametrize("name", PASSED)
def test_tcp_segment_addressed_to_the_source_port_range_passes(name: str) -> None:
    assert run(reply_filter(FIRST, LAST), PASSED[name]) != 0


@pytest.mark.parametrize("name", DROPPED)
def test_everything_else_is_dropped(name: str) -> None:
    assert run(reply_filter(FIRST, LAST), DROPPED[name]) == 0


@pytest.mark.parametrize("name", [*PASSED, *DROPPED])
def test_filter_and_parser_agree(name: str) -> None:
    """What the filter passes is exactly what the reply parser can read and
    that is addressed to the range. Nothing the scanner needs is dropped."""
    packet = {**PASSED, **DROPPED}[name]
    reply = parse_tcp_reply(packet)
    wanted = reply is not None and FIRST <= reply.destination_port <= LAST

    assert (run(reply_filter(FIRST, LAST), packet) != 0) == wanted


def test_range_of_one_port() -> None:
    program = reply_filter(61000, 61000)

    assert run(program, ipv4(6, tcp(61000))) != 0
    assert run(program, ipv4(6, tcp(60999))) == 0
    assert run(program, ipv4(6, tcp(61001))) == 0


def test_program_fits_the_kernel_structure() -> None:
    program = reply_filter(FIRST, LAST)

    # struct sock_filter: 16-bit code, two 8-bit jumps, 32-bit k.
    encoded = b"".join(struct.pack("HBBI", *instruction) for instruction in program)
    assert len(encoded) == 8 * len(program)
    # Every jump lands inside the program.
    jumps = {0x15, 0x45, 0x35, 0x25}
    for position, (code, if_true, if_false, _k) in enumerate(program):
        if code in jumps:
            assert position + 1 + max(if_true, if_false) < len(program)
    # It ends in the two returns.
    assert [instruction[0] for instruction in program[-2:]] == [0x06, 0x06]
