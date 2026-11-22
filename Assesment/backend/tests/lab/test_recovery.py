"""Checks 1B.13 and 1B.14 (AC 4): a scan survives the loss of the scanner, of
the orchestrator and of the whole stack, and completes by itself with the
observations of an uninterrupted run."""

import time
from collections.abc import Callable
from typing import Any

import pytest

from tests.lab.conftest import Lab, compose, wait_healthy

# Slow enough to be interrupted in the middle: 256,000 probes take about 2 minutes.
SLOW = {"port_profile": "top-1000", "pps": 2000, "scan_all_targets": True}


@pytest.fixture(scope="module")
def uninterrupted(lab: Lab, manifest: dict[str, Any]) -> dict[str, Any]:
    """What the scan stores when nothing goes wrong."""
    scan_id = lab.launch(targets=[manifest["scanned_network"]], **SLOW)
    scan = lab.wait(scan_id)
    assert scan["state"] == "completed"
    return {"observations": lab.observations(scan_id), "probes_sent": scan["probes_sent"]}


def interrupted_scan(
    lab: Lab, manifest: dict[str, Any], interrupt: Callable[[], None]
) -> tuple[int, dict[str, Any]]:
    """Start the slow scan, interrupt it when it is about a third done, and
    wait for its end. No analyst action is taken after the interruption."""
    scan_id = lab.launch(targets=[manifest["scanned_network"]], **SLOW)
    lab.wait_until(
        scan_id,
        lambda scan: scan["chunks_total"] > 1 and scan["chunks_done"] >= scan["chunks_total"] / 3,
    )
    interrupt()
    return scan_id, lab.wait(scan_id)


def test_killing_the_scanner_ends_in_a_completed_scan(
    lab: Lab, manifest: dict[str, Any], uninterrupted: dict[str, Any]
) -> None:
    """Check 1B.13."""

    def kill_and_restart_the_scanner() -> None:
        compose("kill", "scanner")
        time.sleep(5)
        compose("up", "-d", "scanner")

    scan_id, scan = interrupted_scan(lab, manifest, kill_and_restart_the_scanner)

    assert (scan["state"], scan["partial"]) == ("completed", False)
    # No duplicate and no missing observation.
    assert lab.observations(scan_id) == uninterrupted["observations"]
    # At most one chunk was probed twice: about 10,000 probes more than the plain run.
    extra = scan["probes_sent"] - uninterrupted["probes_sent"]
    assert 0 <= extra <= 11_000, f"{extra} probes more than the uninterrupted run"


def test_restarting_the_whole_stack_gives_the_same_result(
    lab: Lab, manifest: dict[str, Any], uninterrupted: dict[str, Any]
) -> None:
    """Check 1B.14, first part. The session lives in the database, so the
    client is still logged in after the restart."""

    def restart_everything() -> None:
        compose("restart")
        wait_healthy()

    scan_id, scan = interrupted_scan(lab, manifest, restart_everything)

    assert (scan["state"], scan["partial"]) == ("completed", False)
    assert lab.observations(scan_id) == uninterrupted["observations"]


def test_first_new_finding_arrives_within_two_minutes_of_a_restart(
    lab: Lab, manifest: dict[str, Any]
) -> None:
    """Check 1B.14, the 2-minute limit, measured directly."""
    scan_id = lab.launch(targets=[manifest["scanned_network"]], **SLOW)
    lab.wait_until(scan_id, lambda scan: scan["chunks_done"] >= 2)

    compose("restart")
    healthy_at = wait_healthy()
    done_at_restart = lab.scan(scan_id)["chunks_done"]
    lab.wait_until(scan_id, lambda scan: scan["chunks_done"] > done_at_restart, timeout=120)

    assert time.monotonic() - healthy_at <= 120
    lab.wait(scan_id)


def test_killing_only_the_orchestrator_gives_the_same_result(
    lab: Lab, manifest: dict[str, Any], uninterrupted: dict[str, Any]
) -> None:
    """Check 1B.14, last sentence."""

    def kill_and_restart_the_orchestrator() -> None:
        compose("kill", "orchestrator")
        time.sleep(5)
        compose("up", "-d", "orchestrator")

    scan_id, scan = interrupted_scan(lab, manifest, kill_and_restart_the_orchestrator)

    assert (scan["state"], scan["partial"]) == ("completed", False)
    assert lab.observations(scan_id) == uninterrupted["observations"]
