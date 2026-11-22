"""Stateless reply matching for SYN scans (AD5). Pure functions.

The sequence number of a probe is a keyed hash of its target address and port.
A reply to that probe acknowledges the sequence number plus one, so a reply can
be checked against the hash alone: no table of probes in flight is kept, and a
segment that belongs to any other traffic of the host cannot pass as a result.
"""

import enum
import hashlib
from ipaddress import IPv4Address

from ndm.scanner.packet import ACK, RST, SYN, TcpReply


class PortState(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"


def probe_sequence(key: bytes, target: IPv4Address, port: int) -> int:
    """The sequence number of the SYN probe to ``target:port``.

    ``key`` is a secret of the scanner process, so nobody else can predict
    which acknowledgement number a valid reply carries.
    """
    digest = hashlib.blake2b(
        target.packed + port.to_bytes(2, "big"), key=key, digest_size=4
    ).digest()
    return int.from_bytes(digest, "big")


def classify_reply(key: bytes, reply: TcpReply, source_port: int) -> PortState | None:
    """Decide what a received segment says about a probe sent from ``source_port``.

    The reply counts only if it is addressed to that source port and its
    acknowledgement number is the probe's sequence number plus one. Then
    SYN/ACK means open and RST means closed. Everything else is unrelated
    traffic and gives ``None``.
    """
    if reply.destination_port != source_port or not reply.flags & ACK:
        return None
    expected = (probe_sequence(key, reply.source, reply.source_port) + 1) & 0xFFFFFFFF
    if reply.acknowledgement != expected:
        return None
    if reply.flags & RST:
        return PortState.CLOSED
    if reply.flags & SYN:
        return PortState.OPEN
    return None
