"""Basis of check 1B.3: a reply is accepted only with the expected acknowledgement
number and source-port mapping; unrelated segments are ignored."""

from ipaddress import IPv4Address

import pytest

from ndm.scanner.matching import PortState, classify_reply, probe_sequence
from ndm.scanner.packet import ACK, FIN, RST, SYN, TcpReply

KEY = b"a secret of the scanner process"
TARGET = IPv4Address("198.51.100.2")
PORT = 443
SOURCE_PORT = 61017  # the source port of the chunk in flight


def reply(
    flags: int,
    *,
    acknowledgement: int | None = None,
    source: IPv4Address = TARGET,
    source_port: int = PORT,
    destination_port: int = SOURCE_PORT,
) -> TcpReply:
    """A reply to the probe sent to TARGET:PORT; correct unless a field is overridden."""
    if acknowledgement is None:
        acknowledgement = (probe_sequence(KEY, TARGET, PORT) + 1) & 0xFFFFFFFF
    return TcpReply(source, source_port, destination_port, acknowledgement, flags)


# --- The sequence number ---


def test_sequence_is_a_32_bit_number_that_is_stable_for_a_target() -> None:
    sequence = probe_sequence(KEY, TARGET, PORT)

    assert 0 <= sequence <= 0xFFFFFFFF
    assert probe_sequence(KEY, TARGET, PORT) == sequence


def test_sequence_depends_on_address_port_and_key() -> None:
    sequences = {
        probe_sequence(KEY, TARGET, PORT),
        probe_sequence(KEY, TARGET, PORT + 1),
        probe_sequence(KEY, IPv4Address("198.51.100.3"), PORT),
        probe_sequence(b"another key", TARGET, PORT),
    }

    assert len(sequences) == 4


def test_sequences_are_spread_over_the_whole_range() -> None:
    sequences = [probe_sequence(KEY, TARGET, port) for port in range(1, 2001)]

    assert len(set(sequences)) == 2000
    assert min(sequences) < 2**28 and max(sequences) > 2**32 - 2**28


# --- Valid replies ---


def test_syn_ack_means_open() -> None:
    assert classify_reply(KEY, reply(SYN | ACK), SOURCE_PORT) is PortState.OPEN


def test_rst_means_closed() -> None:
    assert classify_reply(KEY, reply(RST | ACK), SOURCE_PORT) is PortState.CLOSED


def test_acknowledgement_wraps_around(monkeypatch: pytest.MonkeyPatch) -> None:
    # A probe whose sequence number is the largest one is acknowledged with 0.
    monkeypatch.setattr("ndm.scanner.matching.probe_sequence", lambda *_: 0xFFFFFFFF)

    assert classify_reply(KEY, reply(SYN | ACK, acknowledgement=0), SOURCE_PORT) is PortState.OPEN
    assert classify_reply(KEY, reply(SYN | ACK, acknowledgement=2**32), SOURCE_PORT) is None


# --- Segments that must be ignored ---


@pytest.mark.parametrize("offset", [0, 2, -1, 1000])
def test_wrong_acknowledgement_number_is_ignored(offset: int) -> None:
    sequence = probe_sequence(KEY, TARGET, PORT)
    wrong = (sequence + offset) & 0xFFFFFFFF

    assert classify_reply(KEY, reply(SYN | ACK, acknowledgement=wrong), SOURCE_PORT) is None
    assert classify_reply(KEY, reply(RST | ACK, acknowledgement=wrong), SOURCE_PORT) is None


def test_reply_to_another_source_port_is_ignored() -> None:
    # A correct acknowledgement number, but addressed to a port that is not this chunk's.
    for flags in (SYN | ACK, RST | ACK):
        assert (
            classify_reply(KEY, reply(flags, destination_port=SOURCE_PORT + 1), SOURCE_PORT) is None
        )
        assert classify_reply(KEY, reply(flags, destination_port=54321), SOURCE_PORT) is None


def test_reply_from_another_address_or_port_is_ignored() -> None:
    # The acknowledgement number belongs to TARGET:PORT; from anywhere else it is wrong.
    other_host = reply(SYN | ACK, source=IPv4Address("198.51.100.3"))
    other_port = reply(SYN | ACK, source_port=8443)

    assert classify_reply(KEY, other_host, SOURCE_PORT) is None
    assert classify_reply(KEY, other_port, SOURCE_PORT) is None


def test_reply_computed_with_another_key_is_ignored() -> None:
    forged = reply(SYN | ACK, acknowledgement=(probe_sequence(b"guess", TARGET, PORT) + 1))

    assert classify_reply(KEY, forged, SOURCE_PORT) is None


@pytest.mark.parametrize(
    "flags",
    [
        ACK,  # data or keep-alive of an existing connection
        SYN,  # an incoming connection attempt
        RST,  # a reset that acknowledges nothing cannot be tied to a probe
        FIN | ACK,
        0,
    ],
)
def test_other_flag_combinations_are_ignored(flags: int) -> None:
    assert classify_reply(KEY, reply(flags), SOURCE_PORT) is None


def test_ordinary_traffic_of_the_host_is_ignored() -> None:
    # An established connection from the scan target to some local service.
    for acknowledgement in (0, 1, 0x7FFFFFFF, 0xFFFFFFFF):
        segment = TcpReply(TARGET, 51000, 22, acknowledgement, ACK)
        assert classify_reply(KEY, segment, SOURCE_PORT) is None
