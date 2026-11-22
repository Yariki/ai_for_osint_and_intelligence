"""Check 3.8 (I2, FR-TRC-5): no packet has an out-of-scope hop address as its
destination, during traceroutes by each method and during a following scan
of the target. Hops are seen only through their answers.
"""

from pathlib import Path
from typing import Any

from tests.lab.conftest import Lab, capture, packets, traceroute


def test_hops_are_never_a_destination(
    chain_project: Lab, manifest: dict[str, Any], tmp_path: Path
) -> None:
    chain = manifest["chain"]
    target = chain["hosts"][0]["address"]
    hops = [hop for hop in chain["hops"] if hop is not None]
    # Every address of the chain's routers, on every segment, not only the
    # one that answers: hop2 is silent and has two addresses.
    routers = sorted({*hops, "10.77.22.2", "10.77.22.3", "10.77.23.2", "10.77.24.3"})
    to_a_router = " or ".join(f"dst host {address}" for address in routers)
    pcap = tmp_path / "hops.pcap"

    with capture(pcap, f"({to_a_router}) or dst host {target}"):
        for method in ("icmp", "tcp", "udp"):
            run = traceroute(
                chain_project, target, method=method, port=443 if method == "tcp" else None
            )
            assert run["state"] == "completed"
        scan_id = chain_project.launch(targets=[target], port_profile="top-100")
        assert chain_project.wait(scan_id)["state"] == "completed"

    # The capture saw the probes to the target...
    assert len(packets(pcap, f"dst host {target}")) > 100
    # ...and no packet sent to a router. Their ICMP answers come from them, not to them.
    to_routers = packets(pcap, f"({to_a_router}) and not icmp")
    icmp_to_routers = packets(
        pcap, f"({to_a_router}) and icmp and not icmp[icmptype] == icmp-unreach"
    )
    assert to_routers == [], "\n".join(to_routers[:10])
    assert icmp_to_routers == [], "\n".join(icmp_to_routers[:10])
