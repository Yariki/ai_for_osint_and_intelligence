"""Check 2.10 (FR-MAP-7): while a scan runs, every host is drawn within 2
seconds of its finding's timestamp, with and without an active filter.

The scan is the replayed scan of public addresses: lab targets are private
and no dataset knows them. "Drawn" means: in the map points of the scan, on
the map or in the list of hosts without a location, as the browser loads
them. Without a filter the browser takes the host from the event; with a
filter it asks the map-points endpoint again, debounced by 500 ms. Both are
measured here at the API, as the browser would see them.
"""

import json
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from tests.lab.conftest import Lab, replaying

LIMIT_SECONDS = 2.0
DEBOUNCE_SECONDS = 0.5
POLL_SECONDS = 0.1


def first_reply(lab: Lab, scan_id: int, address: str, probe: str | None = None) -> float:
    """The time of the host's first reply, or of its reply to one probe."""
    evidence = lab.host(scan_id, address)["evidence"]
    return min(
        datetime.fromisoformat(item["at"]).astimezone(UTC).timestamp()
        for item in evidence
        if probe is None or item["probe"] == probe
    )


def stream(lab: Lab, seen: dict[str, float], scan: dict[str, int], stop: threading.Event) -> None:
    """Note when each host of the scan first arrives in an event, with or without coordinates."""
    cookies = dict(lab.client.cookies)
    with httpx.Client(base_url=str(lab.client.base_url), verify=False, timeout=None) as client:
        with client.stream("GET", "/api/v1/events", cookies=cookies) as response:
            for line in response.iter_lines():
                if stop.is_set():
                    return
                if not line.startswith("data: "):
                    continue
                event: dict[str, Any] = json.loads(line.removeprefix("data: "))
                if event["type"] == "hosts_changed" and event["scan_id"] == scan.get("id"):
                    for host in event["hosts"]:
                        if host["reachable"]:
                            seen.setdefault(host["address"], time.time())


def poll(lab: Lab, scan_id: int, params: dict[str, Any], seen: dict[str, float]) -> None:
    """What a filtered map shows, polled, with the time each host first appears."""
    body = lab.client.get(f"/api/v1/scans/{scan_id}/map-points", params=params).json()
    now = time.time()
    for host in [*body["located"], *body["unlocated"]]:
        seen.setdefault(host["address"], now)


def test_hosts_are_drawn_within_two_seconds_without_a_filter(public_project: Lab) -> None:
    from tests.tools.replay_findings import load

    seen: dict[str, float] = {}
    scan: dict[str, int] = {}
    stop = threading.Event()
    listener = threading.Thread(target=stream, args=(public_project, seen, scan, stop), daemon=True)

    with replaying():
        listener.start()
        time.sleep(1)
        scan["id"] = public_project.launch(targets=load().scope, port_profile="top-100")
        result = public_project.wait(scan["id"])
        stop.set()

    assert result["state"] == "completed"
    reachable = {row["address"] for row in public_project.hosts(scan["id"]) if row["reachable"]}
    assert reachable and set(seen) == reachable
    for address in reachable:
        delay = seen[address] - first_reply(public_project, scan["id"], address)
        assert -0.5 < delay <= LIMIT_SECONDS, f"{address} was drawn {delay:.2f} s after its finding"


def test_hosts_are_drawn_within_two_seconds_with_a_filter(public_project: Lab) -> None:
    """With a filter the client refetches; 500 ms of that budget go to the debounce.

    The filter is on port 53, which is not a discovery port: a host matches it
    once the port scan records the port open, and the reply stored for the
    port is the port scan's own. (For a discovery port, 80 or 443, the stored
    reply is the earlier discovery one, FR-SCN-1.)
    """
    from tests.tools.replay_findings import load

    port = 53
    params = {"reachable": "true", "open_port": port}
    seen: dict[str, float] = {}

    with replaying():
        scan_id = public_project.launch(targets=load().scope, port_profile="top-100")
        while public_project.scan(scan_id)["state"] not in ("completed", "cancelled", "failed"):
            poll(public_project, scan_id, params, seen)
            time.sleep(POLL_SECONDS)
        poll(public_project, scan_id, params, seen)

    expected = {row["address"] for row in public_project.hosts(scan_id) if row["open_port_count"]}
    expected = {
        address for address in expected
        if port in [p["port"] for p in public_project.host(scan_id, address)["open_ports"]]
    }  # fmt: skip
    assert expected and set(seen) == expected
    for address in expected:
        delay = seen[address] - first_reply(public_project, scan_id, address, f"tcp:{port}")
        # The server's answer is ready this early; the browser adds its debounce.
        assert delay + DEBOUNCE_SECONDS <= LIMIT_SECONDS, f"{address}: {delay:.2f} s"
