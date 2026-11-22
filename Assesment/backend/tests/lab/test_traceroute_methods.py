"""Check 3.7 (FR-TRC-2, FR-TRC-3): ICMP, TCP and UDP each report the lab's
known hop sequence with per-probe times; the silent router is recorded as a
hop without an answer; UDP probes carry no payload.
"""

from pathlib import Path
from typing import Any

import pytest

from tests.lab.conftest import Lab, capture, hop_sequence, packets, traceroute

UDP_WITH_PAYLOAD = "udp and udp[4:2] != 8"  # the UDP length counts the 8-byte header only


def expected(manifest: dict[str, Any]) -> list[str | None]:
    """The hops of the manifest, then the target itself."""
    chain = manifest["chain"]
    return [*chain["hops"], chain["hosts"][0]["address"]]


@pytest.mark.parametrize(("method", "port"), [("icmp", None), ("tcp", 443), ("udp", None)])
def test_each_method_reports_the_known_hop_sequence(
    chain_project: Lab, manifest: dict[str, Any], method: str, port: int | None
) -> None:
    target = manifest["chain"]["hosts"][0]["address"]
    body: dict[str, Any] = {"method": method, "probes_per_hop": 3, "timeout_ms": 1000}
    if port is not None:
        body["port"] = port

    run = traceroute(chain_project, target, **body)

    assert (run["state"], run["reached"]) == ("completed", True)
    assert hop_sequence(run) == expected(manifest)
    for hop in run["hops"]:
        assert len(hop["probes"]) == 3
        if hop["silent"]:
            # The silent router: a hop recorded as not answering, all probes lost.
            assert (hop["responders"], hop["lost"]) == ([], 3)
        else:
            times = hop["responders"][0]["rtt_ms"]
            assert len(times) == 3 and all(t is None or t > 0 for t in times)
            assert any(t is not None for t in times)
    # The stored run holds how it was made (FR-TRC-7).
    assert run["method"] == method
    assert run["parameters"]["probes_per_hop"] == 3
    assert run["vantage_point"] == "lab"


def test_tcp_without_a_port_uses_an_open_port_of_the_host(
    chain_project: Lab, manifest: dict[str, Any]
) -> None:
    target = manifest["chain"]["hosts"][0]["address"]
    scan_id = chain_project.launch(targets=[target], port_profile="top-100")
    assert chain_project.wait(scan_id)["state"] == "completed"

    run = traceroute(chain_project, target, method="tcp")

    assert run["parameters"]["port"] == min(manifest["chain"]["hosts"][0]["open_ports"])
    assert run["reached"] is True


def test_udp_probes_carry_no_payload(
    chain_project: Lab, manifest: dict[str, Any], tmp_path: Path
) -> None:
    target = manifest["chain"]["hosts"][0]["address"]
    pcap = tmp_path / "udp.pcap"

    with capture(pcap, f"udp and dst host {target}"):
        run = traceroute(chain_project, target, method="udp")

    assert run["state"] == "completed"
    sent = packets(pcap, f"udp and dst host {target}")
    assert len(sent) >= 3 * len(run["hops"]) - 3  # every TTL's probes were captured
    assert packets(pcap, f"dst host {target} and {UDP_WITH_PAYLOAD}") == []


def test_private_hops_are_listed_and_marked_unlocatable(
    chain_project: Lab, manifest: dict[str, Any]
) -> None:
    target = manifest["chain"]["hosts"][0]["address"]

    run = traceroute(chain_project, target)

    responders = [r for hop in run["hops"] for r in hop["responders"]]
    assert responders and all(r["unlocatable"] for r in responders)
    assert all(r["latitude"] is None for r in responders)
