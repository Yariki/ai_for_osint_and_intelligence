"""Check 3.10, at the event stream (FR-TRC-6): hop events arrive one TTL at a
time, before the run ends, and the stored run holds method, parameters and
vantage point (FR-TRC-7).
"""

import json
import threading
import time
from typing import Any

import httpx

from tests.lab.conftest import Lab


def listen(lab: Lab, events: list[tuple[float, dict[str, Any]]], stop: threading.Event) -> None:
    cookies = dict(lab.client.cookies)
    with httpx.Client(base_url=str(lab.client.base_url), verify=False, timeout=None) as client:
        with client.stream("GET", "/api/v1/events", cookies=cookies) as response:
            for line in response.iter_lines():
                if stop.is_set():
                    return
                if line.startswith("data: "):
                    event = json.loads(line.removeprefix("data: "))
                    if event["type"] == "traceroute_hop":
                        events.append((time.time(), event))


def test_hops_arrive_one_ttl_at_a_time_before_the_run_ends(
    chain_project: Lab, manifest: dict[str, Any]
) -> None:
    target = manifest["chain"]["hosts"][0]["address"]
    events: list[tuple[float, dict[str, Any]]] = []
    stop = threading.Event()
    listener = threading.Thread(target=listen, args=(chain_project, events, stop), daemon=True)
    listener.start()
    time.sleep(1)

    # A slow timeout, so that the TTLs are clearly apart in time.
    started = chain_project.client.post(
        f"/api/v1/projects/{chain_project.project_id}/traceroutes",
        json={"target": target, "method": "icmp", "probes_per_hop": 2, "timeout_ms": 2000},
    ).json()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = chain_project.client.get(f"/api/v1/traceroutes/{started['id']}").json()
        if run["state"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    time.sleep(2)
    stop.set()

    ours = [(at, e) for at, e in events if e["run_id"] == started["id"]]
    hops = [(at, e) for at, e in ours if e["ttl"] is not None]
    ends = [(at, e) for at, e in ours if e["ttl"] is None and e["state"] == "completed"]
    expected = len(manifest["chain"]["hops"]) + 1
    assert [e["ttl"] for _, e in hops] == list(range(1, expected + 1))
    assert len(ends) == 1
    # Each hop arrived before the run ended, and they came apart in time, not at once.
    assert all(at < ends[0][0] for at, _ in hops)
    assert hops[-1][0] - hops[0][0] > 1.0
    # The silent hop's event says that nobody answered.
    silent = [e for _, e in hops if e["responders"] == []]
    assert len(silent) == 1 and silent[0]["lost"] == 2

    assert (run["method"], run["vantage_point"]) == ("icmp", "lab")
    assert run["parameters"] == {
        "port": None,
        "max_hops": 30,
        "probes_per_hop": 2,
        "timeout_ms": 2000,
    }
    assert run["created_at"] and run["dispatched_at"] and run["finished_at"]
