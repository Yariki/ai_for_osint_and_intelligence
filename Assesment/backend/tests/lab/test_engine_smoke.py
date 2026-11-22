"""Lab smoke test of the scanner engine: a SYN scan and a connect scan of one
lab host each report the manifest's open ports.

Needs the Compose lab (deploy/lab) and, for the SYN part, the NET_RAW
capability. It does not run in CI. On the lab host, from Assesment/, in the
backend image with NET_RAW and nothing else:

    docker run --rm --network host --user 0 --cap-drop ALL --cap-add NET_RAW \
        -v "$PWD:/work:ro" -w /work/backend -e NDM_LAB=1 \
        -e PYTHONPATH=/work/backend/src:/work/backend/.venv/lib/python3.14/site-packages \
        -e PYTHONDONTWRITEBYTECODE=1 --entrypoint python ndm-backend:1.0.0-rc.1 \
        -m pytest -p no:cacheprovider tests/lab/test_engine_smoke.py
"""

import asyncio
import os
import secrets
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import pytest
import yaml

from ndm.core.ports import PortProfile, profile_ports
from ndm.scanner.connect import ConnectProbes
from ndm.scanner.packet import parse_tcp_reply
from ndm.scanner.ratelimit import RateLimiter
from ndm.scanner.results import ChunkResults
from ndm.scanner.sockets import RawSockets, egress_address
from ndm.scanner.syn import SynProbes

pytestmark = pytest.mark.skipif(
    os.environ.get("NDM_LAB") != "1", reason="needs the Compose lab; set NDM_LAB=1 on the lab host"
)

MANIFEST = Path(__file__).resolve().parents[3] / "deploy" / "lab" / "manifest.yaml"
SOURCE_PORTS = (61000, 61999)
PORTS = list(profile_ports(PortProfile.TOP_1000))


def lab_host(closed_ports: str) -> dict[str, Any]:
    """The first host of the manifest with open ports and the given closed-port behaviour."""
    manifest = yaml.safe_load(MANIFEST.read_text())
    return next(
        host
        for host in manifest["hosts"]
        if host["closed_ports"] == closed_ports and host["open_ports"]
    )


def limiter() -> RateLimiter:
    rate = RateLimiter(5000)
    rate.set_scan_rate(1, 1000)
    return rate


@pytest.mark.parametrize("closed_ports", ["reset", "drop"])
async def test_syn_scan_reports_the_manifests_open_ports(closed_ports: str) -> None:
    host = lab_host(closed_ports)
    target = IPv4Address(host["address"])
    expected = sorted(set(host["open_ports"]) & set(PORTS))
    try:
        sockets = RawSockets.open(SOURCE_PORTS)
    except PermissionError:
        pytest.skip("the SYN part needs NET_RAW: run it in the image, as the module docstring says")
    results = ChunkResults()

    def send(segment: bytes, address: IPv4Address) -> None:
        sockets.send.sendto(segment, (str(address), 0))

    probes = SynProbes(
        scan_id=1,
        source=egress_address(target),
        source_port=SOURCE_PORTS[0],
        key=secrets.token_bytes(16),
        send=send,
        limiter=limiter(),
        results=results,
        timeout_ms=1500,
        retries=1,
        seed=1,
    )

    def receive() -> None:
        while True:
            try:
                packet = sockets.capture.recv(65535)
            except BlockingIOError:
                return
            reply = parse_tcp_reply(packet)
            if reply is not None:
                probes.on_reply(reply)

    loop = asyncio.get_running_loop()
    loop.add_reader(sockets.capture, receive)
    try:
        await probes.run([target], PORTS)
    finally:
        loop.remove_reader(sockets.capture)
        drops = sockets.drops()
        sockets.close()

    found = sorted(port for finding in results.drain() for port in finding.open_ports)
    assert found == expected
    assert drops == 0
    not_open = len(PORTS) - len(expected)
    if closed_ports == "reset":
        assert (results.closed[target], results.filtered[target]) == (not_open, 0)
    else:
        assert (results.closed[target], results.filtered[target]) == (0, not_open)


@pytest.mark.parametrize("closed_ports", ["reset", "drop"])
async def test_connect_scan_reports_the_manifests_open_ports(closed_ports: str) -> None:
    host = lab_host(closed_ports)
    target = IPv4Address(host["address"])
    expected = sorted(set(host["open_ports"]) & set(PORTS))
    results = ChunkResults()
    probes = ConnectProbes(
        scan_id=1, limiter=limiter(), results=results, timeout_ms=1500, retries=1, seed=1
    )

    await probes.run([target], PORTS)

    found = sorted(port for finding in results.drain() for port in finding.open_ports)
    assert found == expected
    assert results.closed[target] + results.filtered[target] == len(PORTS) - len(expected)
