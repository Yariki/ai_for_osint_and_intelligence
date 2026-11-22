import struct
import subprocess
import sys
from ipaddress import IPv4Address

import pytest

from ndm.scanner.packet import (
    ACK,
    RST,
    SYN,
    EchoReply,
    TcpReply,
    build_icmp_echo,
    build_syn,
    checksum,
    parse_echo_reply,
    parse_tcp_reply,
)

SOURCE = IPv4Address("192.0.2.1")
TARGET = IPv4Address("198.51.100.2")


def ones_complement_sum_is_all_ones(data: bytes) -> bool:
    """How a receiver verifies a checksum: the sum over everything is 0xFFFF.

    Computed with plain integer arithmetic, not with the function under test.
    """
    if len(data) % 2:
        data += b"\x00"
    total = sum(int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2))
    return total % 0xFFFF == 0 and total != 0


def ipv4(source: IPv4Address, protocol: int, payload: bytes, *, fragment: int = 0) -> bytes:
    """An IPv4 packet around ``payload``, as a capture socket delivers it."""
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(payload),
        0,
        fragment,
        64,
        protocol,
        0,
        source.packed,
        SOURCE.packed,
    )
    return header + payload


def tcp(source_port: int, destination_port: int, acknowledgement: int, flags: int) -> bytes:
    return struct.pack(
        "!HHIIBBHHH", source_port, destination_port, 7, acknowledgement, 0x50, flags, 1024, 0, 0
    )


# --- Checksum: known reference vectors ---


def test_checksum_of_the_rfc_1071_example() -> None:
    # RFC 1071, section 3: these words sum to 0xDDF2.
    assert checksum(bytes.fromhex("0001f203f4f5f6f7")) == 0x220D


def test_checksum_of_a_known_ipv4_header() -> None:
    # A widely published example header; its checksum field holds 0xB861.
    header = bytes.fromhex("450000730000400040110000c0a80001c0a800c7")

    assert checksum(header) == 0xB861
    assert checksum(bytes.fromhex("45000073000040004011b861c0a80001c0a800c7")) == 0


def test_checksum_pads_an_odd_length_with_a_zero_byte() -> None:
    assert checksum(b"\x01\x02\x03") == checksum(b"\x01\x02\x03\x00") == 0xFBFD


def test_checksum_folds_carries() -> None:
    assert checksum(b"\xff\xff" * 3) == 0
    assert checksum(b"") == 0xFFFF


# --- SYN segment ---


def test_syn_segment_matches_a_hand_computed_vector() -> None:
    segment = build_syn(SOURCE, TARGET, source_port=61000, port=443, sequence=0x01020304)

    # ports, sequence, acknowledgement 0, offset 6 words, SYN, window 1024,
    # checksum, urgent 0, option MSS 1460.
    assert segment.hex() == "ee4801bb010203040000000060020400b3e50000020405b4"


def test_syn_segment_carries_no_payload() -> None:
    segment = build_syn(SOURCE, TARGET, 61000, 443, 1)

    header_length = (segment[12] >> 4) * 4
    assert header_length == len(segment) == 24
    assert segment[13] == SYN  # SYN and no other flag


@pytest.mark.parametrize(
    ("source_port", "port", "sequence"),
    [(61000, 1, 0), (61999, 65535, 0xFFFFFFFF), (61500, 443, 0x80000000), (61001, 22, 12345)],
)
def test_syn_checksum_verifies_as_a_receiver_would(
    source_port: int, port: int, sequence: int
) -> None:
    segment = build_syn(SOURCE, TARGET, source_port, port, sequence)

    pseudo_header = SOURCE.packed + TARGET.packed + bytes([0, 6]) + len(segment).to_bytes(2, "big")
    assert ones_complement_sum_is_all_ones(pseudo_header + segment)
    assert struct.unpack_from("!HHI", segment) == (source_port, port, sequence)


def test_syn_checksum_depends_on_the_addresses() -> None:
    one = build_syn(SOURCE, TARGET, 61000, 443, 1)
    other = build_syn(SOURCE, IPv4Address("198.51.100.3"), 61000, 443, 1)

    assert one[16:18] != other[16:18]


# --- ICMP echo request ---


def test_echo_request_matches_a_hand_computed_vector() -> None:
    # type 8, code 0, identifier 1, sequence 1: the words sum to 0x0802.
    assert build_icmp_echo(identifier=1, sequence=1).hex() == "0800f7fd00010001"


def test_echo_request_has_no_data_and_a_valid_checksum() -> None:
    message = build_icmp_echo(identifier=0xBEEF, sequence=513)

    assert len(message) == 8
    assert ones_complement_sum_is_all_ones(message)


# --- Parsing replies ---


def test_tcp_reply_is_parsed() -> None:
    packet = ipv4(TARGET, 6, tcp(443, 61000, 0x01020305, SYN | ACK))

    assert parse_tcp_reply(packet) == TcpReply(
        source=TARGET,
        source_port=443,
        destination_port=61000,
        acknowledgement=0x01020305,
        flags=SYN | ACK,
    )


def test_tcp_reply_behind_ip_options_is_parsed() -> None:
    plain = ipv4(TARGET, 6, tcp(443, 61000, 9, RST | ACK))
    with_options = bytes([0x46]) + plain[1:20] + b"\x01\x01\x01\x01" + plain[20:]

    assert parse_tcp_reply(with_options) == parse_tcp_reply(plain)


@pytest.mark.parametrize(
    "packet",
    [
        b"",
        b"\x45" + b"\x00" * 10,  # truncated IP header
        ipv4(TARGET, 17, tcp(443, 61000, 1, SYN | ACK)),  # UDP, not TCP
        ipv4(TARGET, 6, tcp(443, 61000, 1, SYN | ACK)[:19]),  # truncated TCP header
        ipv4(TARGET, 6, tcp(443, 61000, 1, SYN | ACK), fragment=0x2000),  # first fragment
        ipv4(TARGET, 6, tcp(443, 61000, 1, SYN | ACK), fragment=0x0010),  # later fragment
        b"\x65" + ipv4(TARGET, 6, tcp(443, 61000, 1, SYN | ACK))[1:],  # IP version 6
        b"\x44" + ipv4(TARGET, 6, tcp(443, 61000, 1, SYN | ACK))[1:],  # header length below 20
    ],
)
def test_anything_else_is_not_a_tcp_reply(packet: bytes) -> None:
    assert parse_tcp_reply(packet) is None


def test_dont_fragment_flag_is_not_a_fragment() -> None:
    packet = ipv4(TARGET, 6, tcp(443, 61000, 1, SYN | ACK), fragment=0x4000)

    assert parse_tcp_reply(packet) is not None


def test_echo_reply_is_parsed() -> None:
    reply = struct.pack("!BBHHH", 0, 0, 0, 0xBEEF, 513)

    assert parse_echo_reply(ipv4(TARGET, 1, reply)) == EchoReply(TARGET, 0xBEEF, 513)


@pytest.mark.parametrize(
    "message",
    [
        struct.pack("!BBHHH", 8, 0, 0, 1, 1),  # an echo request, not a reply
        struct.pack("!BBHHH", 3, 1, 0, 0, 0),  # destination unreachable
        struct.pack("!BBHHH", 0, 0, 0, 1, 1)[:7],  # truncated
    ],
)
def test_anything_else_is_not_an_echo_reply(message: bytes) -> None:
    assert parse_echo_reply(ipv4(TARGET, 1, message)) is None
    assert parse_echo_reply(ipv4(TARGET, 6, struct.pack("!BBHHH", 0, 0, 0, 1, 1))) is None


# --- C-3 ---


def test_scanner_code_does_not_load_the_database_or_the_api() -> None:
    script = (
        "import sys, ndm.scanner.packet, ndm.scanner.matching\n"
        "bad = [m for m in sys.modules if m.startswith(('ndm.db', 'ndm.api', 'sqlalchemy',"
        " 'fastapi'))]\n"
        "sys.exit(1 if bad else 0)\n"
    )

    assert subprocess.run([sys.executable, "-c", script], check=False).returncode == 0
