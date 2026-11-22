"""Check 1B.16: a full-range scan of 16 hosts, about one million probes in
about 100 chunks, completes with exact results; its chunk rows are gone after
completion and the totals remain."""

from typing import Any

from tests.lab.conftest import Lab, compose, expected_open

ALL_PORTS = set(range(1, 65536))


def chunk_rows(scan_id: int) -> int:
    """Rows left in scan_chunk, read in the database itself."""
    result = compose(
        "exec", "-T", "postgres", "sh", "-c",
        f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At '
        f'-c "SELECT count(*) FROM scan_chunk WHERE scan_id = {int(scan_id)}"',
    )  # fmt: skip
    return int(result.stdout.strip())


def test_full_range_scan_of_16_hosts(lab: Lab, manifest: dict[str, Any]) -> None:
    # Sixteen addresses that hold several of the lab hosts, one with a port
    # that only a full-range scan can find (65535).
    first = manifest["scanned_network"].split("/")[0].rsplit(".", 1)[0]
    block = f"{first}.32/28"  # .32 to .47, with the host at .40
    limits = lab.client.get("/api/v1/config").json()["limits"]

    scan_id = lab.launch(
        targets=[block],
        port_profile="full",
        scan_all_targets=True,
        pps=limits["pps"]["max"],
        retries=0,
    )
    running = lab.wait_until(scan_id, lambda scan: scan["chunks_total"] > 10)
    assert chunk_rows(scan_id) == running["chunks_total"]
    scan = lab.wait(scan_id, timeout=3600)

    assert (scan["state"], scan["partial"], scan["receive_drops"]) == ("completed", False, 0)
    assert scan["port_count"] == 65535

    # Exact results, over every port.
    in_block = {
        address: ports
        for address, ports in expected_open(manifest, ALL_PORTS).items()
        if 32 <= int(address.rsplit(".", 1)[1]) <= 47
    }
    assert in_block, "the lab manifest has no host in the scanned block"
    assert lab.open_ports(scan_id) == in_block
    assert any(65535 in ports for ports in in_block.values())

    # About one million probes in about 100 chunks: every port of every address,
    # with no retry, and the discovery probes, which run even when all targets
    # are port-scanned (they are the evidence of reachability, FR-SCN-1).
    discovery = scan["parameters"]["discovery"]
    per_address = 65535 + int(discovery["icmp_echo"]) + len(discovery["tcp_ports"])
    assert scan["probes_sent"] == 16 * per_address
    assert 90 <= scan["chunks_total"] <= 130
    assert scan["chunks_done"] == scan["chunks_total"]
    # The chunk rows are gone; the totals above stayed on the scan.
    assert chunk_rows(scan_id) == 0
