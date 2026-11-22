"""Check 1B.12 (AC 3): no packet to a target carries application data, in SYN
and in connect mode, and connect-mode connections close as soon as they are
established."""

from pathlib import Path
from typing import Any

import pytest

from tests.lab.conftest import HAS_PAYLOAD, Lab, capture, packets

SYN_ACK = "tcp[tcpflags] & (tcp-syn|tcp-ack) == (tcp-syn|tcp-ack)"


@pytest.mark.parametrize("technique", ["syn", "connect"])
def test_no_packet_to_a_target_carries_payload(
    lab: Lab, manifest: dict[str, Any], tmp_path: Path, technique: str
) -> None:
    network = manifest["scanned_network"]
    pcap = tmp_path / f"{technique}.pcap"

    with capture(pcap, f"net {network}"):
        scan_id = lab.launch(targets=[network], port_profile="top-100", technique=technique)
        scan = lab.wait(scan_id)

    assert scan["state"] == "completed" and scan["technique_used"] == technique
    to_targets = packets(pcap, f"dst net {network}")
    assert len(to_targets) > 100, "the capture did not see the scan"
    # No TCP segment sent to a target has a payload, and nothing but TCP and ICMP was sent.
    assert packets(pcap, f"dst net {network} and tcp and {HAS_PAYLOAD}") == []
    assert packets(pcap, f"dst net {network} and not tcp and not icmp") == []
    # ICMP echo requests carry no data either: 20 bytes of IP header and 8 of ICMP.
    assert packets(pcap, f"dst net {network} and icmp and ip[2:2] > 28") == []


def test_every_half_open_connection_is_torn_down(
    lab: Lab, manifest: dict[str, Any], tmp_path: Path
) -> None:
    """SYN mode: the host answers each SYN/ACK with a reset (architecture 8.5)."""
    network = manifest["scanned_network"]
    pcap = tmp_path / "teardown.pcap"

    with capture(pcap, f"tcp and net {network}"):
        lab.wait(lab.launch(targets=[network], port_profile="top-100", technique="syn"))

    syn_acks = packets(pcap, f"src net {network} and {SYN_ACK}")
    resets = packets(pcap, f"dst net {network} and tcp[tcpflags] & tcp-rst != 0")
    assert syn_acks, "no open port answered"
    assert len(resets) >= len(syn_acks)


def test_connect_mode_closes_each_connection_as_soon_as_it_is_established(
    lab: Lab, manifest: dict[str, Any], tmp_path: Path
) -> None:
    network = manifest["scanned_network"]
    pcap = tmp_path / "connect.pcap"

    with capture(pcap, f"tcp and net {network}"):
        lab.wait(lab.launch(targets=[network], port_profile="top-100", technique="connect"))

    established = packets(pcap, f"src net {network} and {SYN_ACK}")
    closed_by_us = packets(pcap, f"dst net {network} and tcp[tcpflags] & (tcp-fin|tcp-rst) != 0")
    assert established, "no connection was established"
    # Each established connection was closed from our side, and none carried data either way.
    assert len(closed_by_us) >= len(established)
    assert packets(pcap, f"tcp and net {network} and {HAS_PAYLOAD}") == []
