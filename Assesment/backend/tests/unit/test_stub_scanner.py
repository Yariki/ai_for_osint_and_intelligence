"""The stub scanner times a message's replies when it sends the message."""

from datetime import UTC, datetime
from ipaddress import IPv4Address

from ndm.core.messages import ChunkDone, Evidence, Findings, HostFinding
from tests.tools.stub_scanner import stamped

BUILT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
SENT = datetime(2026, 1, 1, 12, 0, 2, tzinfo=UTC)


def test_every_reply_of_a_findings_message_gets_the_time_it_is_sent() -> None:
    findings = Findings(
        scan_id=1,
        chunk_seq=3,
        hosts=[
            HostFinding(
                address=IPv4Address("8.8.8.8"),
                evidence={
                    "icmp": Evidence(at=BUILT, rtt_ms=1.5),
                    "tcp:53": Evidence(at=BUILT, rtt_ms=2.5),
                },
                open_ports=[53],
            ),
            HostFinding(
                address=IPv4Address("8.8.8.2"),
                evidence={"tcp:443": Evidence(at=BUILT, rtt_ms=2.5)},
            ),
        ],
    )

    result = stamped(findings, SENT)

    assert isinstance(result, Findings)
    assert [{probe: item.at for probe, item in host.evidence.items()} for host in result.hosts] == [
        {"icmp": SENT, "tcp:53": SENT},
        {"tcp:443": SENT},
    ]
    # Everything else is as it was.
    assert result.hosts[0].evidence["tcp:53"].rtt_ms == 2.5
    assert [host.open_ports for host in result.hosts] == [[53], []]
    assert (result.scan_id, result.chunk_seq) == (1, 3)
    # The message it came from is unchanged.
    assert findings.hosts[0].evidence["icmp"].at == BUILT


def test_other_messages_are_sent_as_they_are() -> None:
    done = ChunkDone(scan_id=1, chunk_seq=3, probes_sent=10, receive_drops=0, counts=[])

    assert stamped(done, SENT) is done
