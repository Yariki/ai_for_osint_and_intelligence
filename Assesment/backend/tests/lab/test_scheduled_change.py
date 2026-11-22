"""Check 4.13 (AC 9): with the minimum interval lowered for the test, a
schedule runs twice against the lab; a port opened on a target between the
runs produces an alert whose diff shows that port.

The API is restarted with NDM_MIN_SCHEDULE_INTERVAL_SECONDS=60 for the test
and with the lab settings again afterwards. The port is opened by a listener
started in the targets container; it ends by itself after 15 minutes.
"""

import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.lab.conftest import DEPLOY, Lab, compose, wait_healthy

HOST = "10.77.3.10"  # closed ports answer with a reset, so an opened port is seen at once
PORT = 8080
INTERVAL = 60
LISTENER = (
    "import socket, time\n"
    "s = socket.socket()\n"
    "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    f"s.bind(('{HOST}', {PORT}))\n"
    "s.listen(64)\n"
    "s.settimeout(1)\n"
    "end = time.monotonic() + 900\n"
    "while time.monotonic() < end:\n"
    "    try:\n"
    "        s.accept()[0].close()\n"
    "    except OSError:\n"
    "        pass\n"
)


@pytest.fixture
def short_minimum() -> Iterator[None]:
    compose("up", "-d", "api", settings={"NDM_MIN_SCHEDULE_INTERVAL_SECONDS": str(INTERVAL)})
    wait_healthy()
    yield
    compose("up", "-d", "api")
    wait_healthy()


def open_port() -> None:
    subprocess.run(
        ["docker", "compose", "-f", "lab/compose.lab.yaml", "exec", "-d", "targets",
         "python3", "-c", LISTENER],
        cwd=DEPLOY,
        check=True,
    )  # fmt: skip


def runs(lab: Lab, schedule_id: int) -> list[dict[str, Any]]:
    page = lab.client.get(f"/api/v1/schedules/{schedule_id}/runs", params={"limit": 50}).json()
    return list(reversed(page["items"]))  # oldest first


def wait_for_run(
    lab: Lab, schedule_id: int, after: datetime, timeout: float = 600
) -> dict[str, Any]:
    """The first run planned after ``after`` whose scan completed, with its phases."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for run in runs(lab, schedule_id):
            planned = datetime.fromisoformat(run["planned_at"])
            if planned > after and run["outcome"] == "started" and run["scan_state"] == "completed":
                return run
        time.sleep(5)
    raise AssertionError(f"no completed run after {after}: {runs(lab, schedule_id)}")


def test_a_port_opened_between_two_runs_raises_an_alert_with_that_port(
    lab: Lab, short_minimum: None
) -> None:
    created = lab.client.post(
        f"/api/v1/projects/{lab.project_id}/schedules",
        json={
            "name": "Lab change detection",
            "targets": [HOST],
            "parameters": {
                "port_profile": "custom",
                "custom_ports": f"22,80,443,{PORT}",
                "discovery": None,
            },
            "interval_seconds": INTERVAL,
        },
    )
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]
    try:
        first = wait_for_run(lab, schedule_id, after=datetime.min.replace(tzinfo=UTC))
        opened_at = datetime.now(UTC)
        open_port()
        second = wait_for_run(lab, schedule_id, after=opened_at)
    finally:
        lab.client.post(f"/api/v1/schedules/{schedule_id}/disable")

    # The second run's diff against the first: the alert links to it.
    alerts = lab.client.get(
        "/api/v1/alerts", params={"project_id": lab.project_id, "limit": 200}
    ).json()["items"]
    [alert] = [
        a
        for a in alerts
        if a["type"] == "changes_detected" and a["link"].get("scan_id") == second["scan_id"]
    ]
    diff = lab.client.get(f"/api/v1/diffs/{alert['link']['diff_id']}").json()
    assert diff["target_scan_id"] == second["scan_id"]
    assert diff["base_scan_id"] >= first["scan_id"]
    assert {"address": HOST, "kind": "port_opened", "port": PORT} in [
        {key: change[key] for key in ("address", "kind", "port")} for change in diff["changes"]
    ]
    assert alert["read_at"] is None
