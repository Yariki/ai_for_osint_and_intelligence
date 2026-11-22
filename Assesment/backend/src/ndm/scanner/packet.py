"""Building probe packets and parsing replies. Pure functions, standard library only.

Probes carry no payload (FR-SCN-3). A TCP SYN is sent through a raw TCP socket,
where the kernel adds the IP header, so only the TCP header is built here. Its
checksum still covers the two IP addresses, which is why they are parameters.
"""

import struct
from dataclasses import dataclass
from ipaddress import IPv4Address

FIN = 0x01
SYN = 0x02
RST = 0x04
ACK = 0x10

_PROTOCOL_ICMP = 1
_PROTOCOL_TCP = 6
_ICMP_ECHO_REQUEST = 8
_ICMP_ECHO_REPLY = 0

# Source port, destination port, sequence, acknowledgement, data offset, flags,
# window, checksum, urgent pointer, then one option: maximum segment size.
# The option is part of the header, not payload; some hosts ignore a SYN without it.
_SYN_HEADER = struct.Struct("!HHIIBBHHHBBH")
_SYN_DATA_OFFSET = (_SYN_HEADER.size // 4) << 4  # header length in 32-bit words
_SYN_WINDOW = 1024
_MSS_OPTION = (2, 4, 1460)  # kind, length, value

_ICMP_ECHO = struct.Struct("!BBHHH")  # type, code, checksum, identifier, sequence
_TCP_PSEUDO_HEADER = struct.Struct("!4s4sBBH")  # source, destination, zero, protocol, length


def checksum(data: bytes) -> int:
    """The Internet checksum (RFC 1071): the complement of the one's-complement sum."""
    if len(data) % 2:
        data += b"\x00"
    total: int = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def build_syn(
    source: IPv4Address, destination: IPv4Address, source_port: int, port: int, sequence: int
) -> bytes:
    """A TCP SYN segment with a valid checksum and no payload."""

    def header(tcp_checksum: int) -> bytes:
        return _SYN_HEADER.pack(
            source_port,
            port,
            sequence,
            0,  # acknowledgement
            _SYN_DATA_OFFSET,
            SYN,
            _SYN_WINDOW,
            tcp_checksum,
            0,  # urgent pointer
            *_MSS_OPTION,
        )

    pseudo_header = _TCP_PSEUDO_HEADER.pack(
        source.packed, destination.packed, 0, _PROTOCOL_TCP, _SYN_HEADER.size
    )
    return header(checksum(pseudo_header + header(0)))


def build_icmp_echo(identifier: int, sequence: int) -> bytes:
    """An ICMP echo request with a valid checksum and no data."""
    without_checksum = _ICMP_ECHO.pack(_ICMP_ECHO_REQUEST, 0, 0, identifier, sequence)
    return _ICMP_ECHO.pack(_ICMP_ECHO_REQUEST, 0, checksum(without_checksum), identifier, sequence)


@dataclass(frozen=True)
class TcpReply:
    """What reply matching needs from a received TCP segment."""

    source: IPv4Address
    source_port: int
    destination_port: int
    acknowledgement: int
    flags: int


@dataclass(frozen=True)
class EchoReply:
    source: IPv4Address
    identifier: int
    sequence: int


def _ip_payload(packet: bytes, protocol: int) -> tuple[IPv4Address, bytes] | None:
    """Source address and payload of an IPv4 packet of ``protocol``, or None.

    None for anything that cannot be read with certainty: another IP version
    or protocol, a truncated packet, or a fragment other than a whole packet.
    """
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return None
    header_length = (packet[0] & 0x0F) * 4
    if header_length < 20 or len(packet) < header_length or packet[9] != protocol:
        return None
    fragment = struct.unpack_from("!H", packet, 6)[0]
    if fragment & 0x3FFF:  # "more fragments" flag or a non-zero offset
        return None
    return IPv4Address(packet[12:16]), packet[header_length:]


def parse_tcp_reply(packet: bytes) -> TcpReply | None:
    """Parse an IPv4 packet that holds a TCP segment. None if it is anything else."""
    parsed = _ip_payload(packet, _PROTOCOL_TCP)
    if parsed is None or len(parsed[1]) < 20:
        return None
    source, segment = parsed
    source_port, destination_port, _sequence, acknowledgement, _offset, flags = struct.unpack_from(
        "!HHIIBB", segment
    )
    return TcpReply(source, source_port, destination_port, acknowledgement, flags)


def parse_echo_reply(packet: bytes) -> EchoReply | None:
    """Parse an IPv4 packet that holds an ICMP echo reply. None if it is anything else."""
    parsed = _ip_payload(packet, _PROTOCOL_ICMP)
    if parsed is None or len(parsed[1]) < _ICMP_ECHO.size:
        return None
    source, message = parsed
    kind, code, _checksum, identifier, sequence = _ICMP_ECHO.unpack_from(message)
    if kind != _ICMP_ECHO_REPLY or code != 0:
        return None
    return EchoReply(source, identifier, sequence)
