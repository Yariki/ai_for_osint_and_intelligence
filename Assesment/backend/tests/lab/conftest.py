"""The lab suite: on-demand tests against the Compose lab, through the API of
the running product stack (NFR-TST-2).

They need the lab host: Docker, the lab (deploy/lab), the stack started with
the lab settings, and tcpdump to read captures. They do not run in CI and are
skipped unless NDM_LAB=1.

    cd Assesment/deploy
    docker compose -f lab/compose.lab.yaml up -d --build
    docker compose --env-file .env --env-file lab/lab.env up -d --build
    # create the account once (operator guide, "First-run setup"), then:
    cd ../backend
    NDM_LAB=1 NDM_LAB_USERNAME=analyst NDM_LAB_PASSWORD=... uv run pytest tests/lab

The engine smoke tests need NET_RAW and run in a container; see
test_engine_smoke.py. Captures use tcpdump under sudo if sudo needs no
password, otherwise tcpdump in a container (deploy/lab/capture). Every test
first checks that the lab's services are running.
"""

import contextlib
import functools
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from ndm.core.ports import PortProfile, profile_ports

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
MANIFEST = DEPLOY / "lab" / "manifest.yaml"
FINAL = {"completed", "cancelled", "failed"}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("NDM_LAB") == "1":
        return
    skip = pytest.mark.skip(reason="needs the Compose lab; set NDM_LAB=1 on the lab host")
    for item in items:
        if "tests/lab" in str(item.path):
            item.add_marker(skip)


# --- The stack --------------------------------------------------------------------


def compose(
    *arguments: str, check: bool = True, settings: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """``docker compose`` for the product stack, with the lab settings.

    ``settings`` are variables for this call alone; they take precedence over
    the environment files.
    """
    command = ["docker", "compose", "--env-file", ".env", "--env-file", "lab/lab.env", *arguments]
    return subprocess.run(
        command,
        cwd=DEPLOY,
        check=check,
        capture_output=True,
        text=True,
        env={**os.environ, **(settings or {})},
    )


def orchestrator_egress_address() -> str:
    """The orchestrator's address on the egress network, the only network with
    a way out. Looked up each time: a restart can change it."""
    container = compose("ps", "-q", "orchestrator").stdout.strip()
    inspected = subprocess.run(
        ["docker", "inspect", container], check=True, capture_output=True, text=True
    )
    networks = json.loads(inspected.stdout)[0]["NetworkSettings"]["Networks"]
    [address] = [n["IPAddress"] for name, n in networks.items() if name.endswith("_egress")]
    return str(address)


def wait_healthy(timeout: float = 180) -> float:
    """Wait until every long-running service is healthy. Returns the time it became so."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        states = compose("ps", "--format", "{{.Service}} {{.Health}}", check=False).stdout.split(
            "\n"
        )
        health = dict(line.split(" ", 1) for line in states if " " in line)
        if health and all(state == "healthy" for state in health.values()):
            return time.monotonic()
        time.sleep(2)
    raise AssertionError("the stack did not become healthy")


def conntrack_count() -> int:
    return int(Path("/proc/sys/net/netfilter/nf_conntrack_count").read_text())


# --- The manifest: the expected result ----------------------------------------------


@pytest.fixture(scope="session")
def manifest() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(MANIFEST.read_text())
    return loaded


def expected_open(manifest: dict[str, Any], ports: set[int]) -> dict[str, list[int]]:
    """The open ports a scan of ``ports`` must report, per address."""
    return {
        host["address"]: sorted(set(host["open_ports"]) & ports)
        for host in manifest["hosts"]
        if set(host["open_ports"]) & ports
    }


TOP_1000 = set(profile_ports(PortProfile.TOP_1000))


# --- The API ------------------------------------------------------------------------


class Lab:
    """A logged-in client with the calls the suite needs."""

    def __init__(self, client: httpx.Client, project_id: int) -> None:
        self.client = client
        self.project_id = project_id

    def launch(self, **body: Any) -> int:
        response = self.client.post(f"/api/v1/projects/{self.project_id}/scans", json=body)
        assert response.status_code == 201, response.text
        scan_id: int = response.json()["id"]
        return scan_id

    def scan(self, scan_id: int) -> dict[str, Any]:
        scan: dict[str, Any] = self.client.get(f"/api/v1/scans/{scan_id}").json()
        return scan

    def wait(self, scan_id: int, timeout: float = 900) -> dict[str, Any]:
        """Wait until a scan has ended. Returns it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            scan = self.scan(scan_id)
            if scan["state"] in FINAL:
                return scan
            time.sleep(1)
        raise AssertionError(f"scan {scan_id} did not end; it is {self.scan(scan_id)['state']}")

    def wait_until(self, scan_id: int, done: Any, timeout: float = 300) -> dict[str, Any]:
        """Wait until ``done(scan)`` is true."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            scan = self.scan(scan_id)
            if done(scan):
                return scan
            time.sleep(0.5)
        raise AssertionError(f"scan {scan_id} did not reach the expected point")

    def hosts(self, scan_id: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        while True:
            page = self.client.get(
                f"/api/v1/scans/{scan_id}/hosts", params={"limit": 200, "offset": len(rows)}
            ).json()
            rows += page["items"]
            if len(rows) >= page["total"]:
                return rows

    def host(self, scan_id: int, address: str) -> dict[str, Any]:
        detail: dict[str, Any] = self.client.get(f"/api/v1/scans/{scan_id}/hosts/{address}").json()
        return detail

    def open_ports(self, scan_id: int) -> dict[str, list[int]]:
        """Every open port the scan reported, per address."""
        return {
            row["address"]: [
                port["port"] for port in self.host(scan_id, row["address"])["open_ports"]
            ]
            for row in self.hosts(scan_id)
            if row["open_port_count"]
        }

    def observations(self, scan_id: int) -> dict[str, tuple[bool, list[int], int, int]]:
        """What the scan stored per target, for comparing two runs."""
        stored = {}
        for row in self.hosts(scan_id):
            ports = [p["port"] for p in self.host(scan_id, row["address"])["open_ports"]]
            stored[row["address"]] = (
                row["reachable"],
                ports,
                row["closed_count"],
                row["filtered_count"],
            )
        return stored


LAB_SERVICES = {"router", "targets", "allopen", "hop1", "hop2", "hop3", "far-target", "resolver"}


def lab_services_down() -> set[str]:
    """The long-running lab services that are not running."""
    running = subprocess.run(
        ["docker", "compose", "-f", "lab/compose.lab.yaml", "ps", "--services",
         "--status", "running"],
        cwd=DEPLOY, check=True, capture_output=True, text=True,
    ).stdout.split()  # fmt: skip
    return LAB_SERVICES - set(running)


@pytest.fixture(autouse=True)
def lab_is_up() -> None:
    """Before every lab test: with the lab down, every scan finds nothing, and
    the failures would point anywhere but at the cause.

    Skipped where there is no Docker client: the engine smoke tests run in a
    container (test_engine_smoke.py)."""
    if shutil.which("docker") is None:
        return
    down = lab_services_down()
    assert not down, f"lab services not running: {sorted(down)}; start the lab first"


@pytest.fixture(scope="session")
def lab(manifest: dict[str, Any]) -> Iterator[Lab]:
    url = os.environ.get("NDM_LAB_URL", "https://localhost")
    credentials = {
        "username": os.environ["NDM_LAB_USERNAME"],
        "password": os.environ["NDM_LAB_PASSWORD"],
    }
    # The proxy's certificate comes from its own authority, which this client does not know.
    with httpx.Client(base_url=url, verify=False, timeout=30) as client:
        assert client.post("/api/v1/login", json=credentials).status_code == 200
        config = client.get("/api/v1/config").json()
        assert config["scanner"] and config["scanner"]["alive"], "the scanner is not connected"
        project = client.post("/api/v1/projects", json={"name": f"lab {time.time():.0f}"}).json()
        scope = client.put(
            f"/api/v1/projects/{project['id']}/scope",
            json={"entries": [manifest["scanned_network"]], "authorized": True},
        )
        assert scope.status_code == 200, (
            "the lab scope was rejected: is the stack running with lab/lab.env? " + scope.text
        )
        yield Lab(client, project["id"])


# --- Packet captures -----------------------------------------------------------------


CAPTURE_IMAGE = "ndm-lab-capture"


@contextlib.contextmanager
def capture(path: Path, expression: str) -> Iterator[None]:
    """Capture matching packets on every interface while the block runs.

    With tcpdump under sudo when sudo needs no password; otherwise with tcpdump
    in a container on the host network (deploy/lab/capture), which needs only
    Docker.

    IP packets only: "host" and "net" match ARP too, and the bridges' ARP
    requests are not traffic the product sends.
    """
    expression = f"ip and ({expression})"
    if subprocess.run(["sudo", "-n", "true"], check=False, capture_output=True).returncode == 0:
        with _capture_with_sudo(path, expression):
            yield
    else:
        with _capture_in_a_container(path, expression):
            yield


@contextlib.contextmanager
def _capture_with_sudo(path: Path, expression: str) -> Iterator[None]:
    process = subprocess.Popen(
        ["sudo", "tcpdump", "-i", "any", "-n", "-w", str(path), expression],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    try:
        yield
    finally:
        time.sleep(2)
        subprocess.run(["sudo", "pkill", "-INT", "-f", f"tcpdump.*{path}"], check=False)
        process.wait(timeout=20)


@contextlib.contextmanager
def _capture_in_a_container(path: Path, expression: str) -> Iterator[None]:
    docker = functools.partial(subprocess.run, check=True, capture_output=True, text=True)
    docker(["docker", "build", "-q", "-t", CAPTURE_IMAGE, str(DEPLOY / "lab" / "capture")])
    name = f"ndm-lab-capture-{os.getpid()}-{time.monotonic_ns()}"
    # -Z root keeps it from switching to its own user, which needs no file of
    # ours; even so tcpdump calls setgid and setgroups, hence SETUID and SETGID.
    docker(
        ["docker", "run", "-d", "--name", name, "--network", "host",
         "--cap-drop", "ALL", "--cap-add", "NET_RAW", "--cap-add", "NET_ADMIN",
         "--cap-add", "SETUID", "--cap-add", "SETGID",
         CAPTURE_IMAGE, "-i", "any", "-n", "-Z", "root", "-w", "/tmp/capture.pcap", expression]
    )  # fmt: skip
    time.sleep(2)
    try:
        yield
    finally:
        time.sleep(2)
        subprocess.run(
            ["docker", "kill", "--signal", "INT", name], check=False, capture_output=True
        )
        subprocess.run(["docker", "wait", name], check=False, capture_output=True)
        try:
            # Copied out, the file belongs to the user running the tests.
            docker(["docker", "cp", f"{name}:/tmp/capture.pcap", str(path)])
        finally:
            subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


def packets(path: Path, expression: str) -> list[str]:
    """The captured packets that match, one line each, with a timestamp in seconds."""
    result = subprocess.run(
        ["tcpdump", "-n", "-tt", "-r", str(path), expression],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


# A TCP segment with payload: the IP length minus both header lengths is not zero.
HAS_PAYLOAD = "(ip[2:2] - ((ip[0]&0xf)<<2) - ((tcp[12]&0xf0)>>2)) != 0"


# --- The replayed scan of public addresses (Phase 2) ------------------------------------


@contextlib.contextmanager
def replaying(vantage_point: str = "lab", delay: float = 0.3) -> Iterator[None]:
    """The scanner stopped and the findings-replay harness in its place.

    Nothing is probed while this runs: the harness answers chunks from the
    recorded scan of public addresses (tests/fixtures/replay/public_scan.json).
    The broker is reached on the lab host's loopback address.
    """
    settings = dict(
        line.split("=", 1)
        for line in (DEPLOY / ".env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    broker = f"amqp://ndm:{settings['NDM_BROKER_PASSWORD']}@127.0.0.1:5672/"
    compose("stop", "scanner")
    harness: subprocess.Popen[bytes] | None = None
    # Everything after the stop is inside the try, so that the scanner comes
    # back even if the harness cannot start; otherwise every later test's
    # scans would stay queued.
    try:
        harness = subprocess.Popen(
            [sys.executable, "-m", "tests.tools.replay_findings",
             "--vantage-point", vantage_point, "--delay", str(delay)],
            cwd=Path(__file__).resolve().parents[2],
            env={**os.environ, "NDM_BROKER_URL": broker},
        )  # fmt: skip
        time.sleep(15)  # it says hello; the orchestrator sees a live scanner
        yield
    finally:
        if harness is not None:
            harness.terminate()
            harness.wait(timeout=30)
        compose("up", "-d", "scanner")
        wait_healthy()


@pytest.fixture
def public_project(lab: Lab) -> Iterator[Lab]:
    """A project whose scope is the recorded scan of public addresses."""
    from tests.tools.replay_findings import load

    scope = load().scope
    project = lab.client.post("/api/v1/projects", json={"name": f"replay {time.time():.0f}"}).json()
    saved = lab.client.put(
        f"/api/v1/projects/{project['id']}/scope", json={"entries": scope, "authorized": True}
    )
    assert saved.status_code == 200, saved.text
    yield Lab(lab.client, project["id"])


# --- The router chain (Phase 3) ----------------------------------------------------------


@pytest.fixture
def chain_project(lab: Lab, manifest: dict[str, Any]) -> Iterator[Lab]:
    """A project whose scope is the far network behind the router chain only:
    the hops are outside it."""
    network = manifest["chain"]["scanned_network"]
    project = lab.client.post("/api/v1/projects", json={"name": f"chain {time.time():.0f}"}).json()
    saved = lab.client.put(
        f"/api/v1/projects/{project['id']}/scope", json={"entries": [network], "authorized": True}
    )
    assert saved.status_code == 200, saved.text
    yield Lab(lab.client, project["id"])


def traceroute(lab: Lab, target: str, timeout: float = 180, **body: Any) -> dict[str, Any]:
    """Start a traceroute and wait for its end. Returns the run hop by hop."""
    started = lab.client.post(
        f"/api/v1/projects/{lab.project_id}/traceroutes", json={"target": target, **body}
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run: dict[str, Any] = lab.client.get(f"/api/v1/traceroutes/{run_id}").json()
        if run["state"] in ("completed", "failed"):
            return run
        time.sleep(1)
    raise AssertionError(f"traceroute {run_id} did not end")


def hop_sequence(run: dict[str, Any]) -> list[str | None]:
    """The address that answered each TTL, None for a TTL nobody answered."""
    return [hop["responders"][0]["address"] if hop["responders"] else None for hop in run["hops"]]
