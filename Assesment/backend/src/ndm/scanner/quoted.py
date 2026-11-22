"""ICMP errors and the original header they quote (architecture section 8.2).

A router that drops a probe because its TTL ran out sends an ICMP
"time exceeded" message; a target that has nothing on a UDP port sends
"destination unreachable". Both quote the IP header of the dropped packet and
the first 8 bytes of what it carried. Those 8 bytes identify the probe:

- ICMP echo: type, code, checksum, identifier, sequence;
- TCP: source port, destination port, sequence number;
- UDP: source port, destination port, length, checksum.

Pure functions, standard library only. The builders at the end make such
packets, for tests.
"""

import struct
from dataclasses import dataclass
from ipaddress import IPv4Address

from ndm.scanner.packet import checksum

PROTOCOL_ICMP = 1
PROTOCOL_TCP = 6
PROTOCOL_UDP = 17

TIME_EXCEEDED = 11
UNREACHABLE = 3
KINDS = {TIME_EXCEEDED: "time_exceeded", UNREACHABLE: "unreachable"}

_IP_HEADER = struct.Struct("!BBHHHBBH4s4s")


@dataclass(frozen=True)
class IcmpError:
    """An ICMP error message, with what it says about the probe it answers."""

    responder: IPv4Address
    """Who sent the error: the router, or the target itself."""
    kind: str
    code: int
    destination: IPv4Address
    """Where the quoted probe was going."""
    protocol: int
    source_port: int | None = None
    destination_port: int | None = None
    sequence: int | None = None
    """TCP sequence number, or ICMP echo sequence."""
    identifier: int | None = None
    """ICMP echo identifier."""


def _header(packet: bytes) -> tuple[int, int] | None:
    """Length of the IPv4 header at the start of ``packet`` and its protocol, or None."""
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return None
    length = (packet[0] & 0x0F) * 4
    if length < 20 or len(packet) < length:
        return None
    return length, packet[9]


def parse_icmp_error(packet: bytes) -> IcmpError | None:
    """Parse an IPv4 packet that holds an ICMP time-exceeded or unreachable
    message. None for anything else, or anything too short to be sure of."""
    outer = _header(packet)
    if outer is None or outer[1] != PROTOCOL_ICMP:
        return None
    outer_length = outer[0]
    message = packet[outer_length:]
    if len(message) < 8 or message[0] not in KINDS:
        return None
    quoted = message[8:]
    inner = _header(quoted)
    if inner is None:
        return None
    inner_length, protocol = inner
    first = quoted[inner_length : inner_length + 8]
    if len(first) < 8:
        return None
    error = {
        "responder": IPv4Address(packet[12:16]),
        "kind": KINDS[message[0]],
        "code": message[1],
        "destination": IPv4Address(quoted[16:20]),
        "protocol": protocol,
    }
    if protocol == PROTOCOL_ICMP:
        identifier, sequence = struct.unpack_from("!HH", first, 4)
        return IcmpError(**error, identifier=identifier, sequence=sequence)  # type: ignore[arg-type]
    if protocol == PROTOCOL_TCP:
        source_port, destination_port, sequence = struct.unpack_from("!HHI", first)
        return IcmpError(
            **error,  # type: ignore[arg-type]
            source_port=source_port,
            destination_port=destination_port,
            sequence=sequence,
        )
    if protocol == PROTOCOL_UDP:
        source_port, destination_port = struct.unpack_from("!HH", first)
        return IcmpError(**error, source_port=source_port, destination_port=destination_port)  # type: ignore[arg-type]
    return IcmpError(**error)  # type: ignore[arg-type]


# --- Builders, for tests -----------------------------------------------------------


def ip_header(
    source: IPv4Address, destination: IPv4Address, protocol: int, payload: int, ttl: int = 64
) -> bytes:
    """An IPv4 header without options, with a valid checksum."""

    def pack(header_checksum: int) -> bytes:
        return _IP_HEADER.pack(
            0x45,
            0,
            20 + payload,
            0,
            0,
            ttl,
            protocol,
            header_checksum,
            source.packed,
            destination.packed,
        )

    return pack(checksum(pack(0)))


def ip_packet(
    source: IPv4Address, destination: IPv4Address, protocol: int, payload: bytes
) -> bytes:
    return ip_header(source, destination, protocol, len(payload)) + payload


def udp_header(source_port: int, destination_port: int) -> bytes:
    """A UDP header of a datagram with no payload; the checksum is left at 0."""
    return struct.pack("!HHHH", source_port, destination_port, 8, 0)


def icmp_error(
    responder: IPv4Address, receiver: IPv4Address, kind: int, code: int, probe: bytes
) -> bytes:
    """The ICMP error that ``responder`` sends to ``receiver`` about ``probe``,
    a whole IP packet of which the header and 8 bytes are quoted."""
    quoted = probe[: (probe[0] & 0x0F) * 4 + 8]
    header = struct.pack("!BBHI", kind, code, 0, 0)
    body = quoted
    with_checksum = struct.pack("!BBHI", kind, code, checksum(header + body), 0)
    return ip_packet(responder, receiver, PROTOCOL_ICMP, with_checksum + body)
