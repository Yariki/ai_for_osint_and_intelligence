"""The scan API: create, estimate, list, detail, cancel, delete; ceilings and capabilities."""

import io
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import CancelRequested, ScanCreated
from ndm.db.models import AuditEvent, Scan, ScanState, VantagePoint
from tests.integration.api.conftest import audit_actions


class RecordingPublisher:
    """Stands in for the command publisher and keeps what was published."""

    def __init__(self) -> None:
        self.commands: list[Any] = []

    async def publish(self, command: Any) -> bool:
        self.commands.append(command)
        return True

    async def close(self) -> None:
        pass


@pytest.fixture
def published(app: FastAPI) -> list[Any]:
    publisher = RecordingPublisher()
    app.state.commands = publisher
    return publisher.commands


@pytest.fixture
async def project_id(client: AsyncClient) -> int:
    """A project with the scope 8.8.8.0/24."""
    created: int = (await client.post("/api/v1/projects", json={"name": "Edge"})).json()["id"]
    saved = await client.put(
        f"/api/v1/projects/{created}/scope", json={"entries": ["8.8.8.0/24"], "authorized": True}
    )
    assert saved.status_code == 200
    return created


async def scanner(db: AsyncSession, *, syn: bool = True, alive: bool = True) -> None:
    """The scanner has reported its capabilities."""
    db.add(
        VantagePoint(
            name="default",
            capabilities={"syn": syn, "icmp": True},
            last_heartbeat_at=datetime.now(UTC) if alive else datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    await db.commit()


async def launch(client: AsyncClient, project_id: int, **body: Any) -> Any:
    return await client.post(
        f"/api/v1/projects/{project_id}/scans", json={"targets": ["8.8.8.0/30"], **body}
    )


# --- Create ---


async def test_created_scan_is_queued_and_references_the_current_attestation(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    scope = (await client.get(f"/api/v1/projects/{project_id}/scope")).json()

    response = await launch(client, project_id, targets=["8.8.8.4", "8.8.8.0/30", "8.8.8.2"])

    assert response.status_code == 201
    scan = response.json()
    assert (scan["state"], scan["phase"], scan["partial"]) == ("queued", None, False)
    assert scan["attestation_id"] == scope["attestation_id"]
    # Expanded, each address once, in order.
    assert scan["targets"] == ["8.8.8.0", "8.8.8.1", "8.8.8.2", "8.8.8.3", "8.8.8.4"]
    assert (scan["port_profile"], scan["port_count"]) == ("top-1000", 1000)
    assert (scan["technique_requested"], scan["technique_used"]) == ("automatic", None)
    assert scan["parameters"] == {
        "discovery": {"icmp_echo": True, "tcp_ports": [80, 443]},
        "scan_all_targets": False,
        "pps": 1000,
        "timeout_ms": 1500,
        "retries": 1,
        "hosts_in_parallel": 64,
        "port_profile": "top-1000",
    }

    stored = await db_session.get_one(Scan, scan["id"])
    assert stored.state is ScanState.QUEUED
    assert stored.seed > 0
    assert sum((span.upper or 0) - (span.lower or 0) for span in stored.probed_ports) == 1000

    entry = await db_session.scalar(select(AuditEvent).where(AuditEvent.action == "scan.launch"))
    assert entry is not None
    assert (entry.actor, entry.object_type, entry.object_id) == ("analyst", "scan", str(scan["id"]))
    assert entry.details["targets"] == 5


async def test_a_whole_24_is_accepted_and_one_address_more_is_not(
    client: AsyncClient, project_id: int
) -> None:
    whole = await launch(client, project_id, targets=["8.8.8.0/24"])
    assert whole.status_code == 201
    assert len(whole.json()["targets"]) == 256  # .0 and .255 included (FR-CFG-1)

    # Overlapping entries count each address once.
    overlapping = await launch(client, project_id, targets=["8.8.8.0/24", "8.8.8.0/25", "8.8.8.7"])
    assert len(overlapping.json()["targets"]) == 256

    for targets in (["8.8.8.0/23"], ["8.8.8.0/24", "8.8.9.0"], ["8.0.0.0/8"]):
        too_many = await launch(client, project_id, targets=targets)
        assert too_many.status_code == 422
        assert too_many.json()["error"]["code"] == "too_many_targets"


@pytest.mark.parametrize(
    ("ports", "count"),
    [("443", 1), ("22,80,8000-8100", 103), ("1-65535", 65535), ("80,80,79-81", 3)],
)
async def test_custom_port_list(
    client: AsyncClient, project_id: int, ports: str, count: int
) -> None:
    response = await launch(client, project_id, port_profile="custom", custom_ports=ports)

    assert response.status_code == 201
    assert (response.json()["port_profile"], response.json()["port_count"]) == ("custom", count)


@pytest.mark.parametrize(
    "body",
    [
        {"port_profile": "custom"},  # no list
        {"port_profile": "top-100", "custom_ports": "80"},  # a list without the custom profile
        {"port_profile": "custom", "custom_ports": "80, 443"},
        {"port_profile": "custom", "custom_ports": "0"},
        {"port_profile": "custom", "custom_ports": "65536"},
        {"port_profile": "custom", "custom_ports": "100-90"},
        {"port_profile": "custom", "custom_ports": "080"},
        {"port_profile": "custom", "custom_ports": "http"},
        {"port_profile": "top-10"},
        {"technique": "udp"},
        {"targets": []},
        # Above the fixed limits of the tunable parameters.
        {"timeout_ms": 10_001},
        {"timeout_ms": 99},
        {"retries": 4},
        {"hosts_in_parallel": 0},
        {"hosts_in_parallel": 257},
        {"pps": 0},
        # Discovery with no method at all (FR-CFG-2).
        {"discovery": {"icmp_echo": False, "tcp_ports": []}},
    ],
)
async def test_invalid_configuration_is_rejected(
    client: AsyncClient, project_id: int, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    response = await launch(client, project_id, **body)

    assert response.status_code == 422, response.text
    assert await db_session.scalar(select(Scan)) is None


@pytest.mark.parametrize("extra_env", [{"NDM_GLOBAL_RATE_PPS": "2000"}])
async def test_rate_above_the_operator_ceiling_is_rejected(
    client: AsyncClient, project_id: int
) -> None:
    assert (await launch(client, project_id, pps=2000)).status_code == 201

    above = await launch(client, project_id, pps=2001)

    assert above.status_code == 422
    (detail,) = above.json()["error"]["details"]
    assert (detail["field"], detail["code"]) == ("pps", "above_ceiling")


async def test_discovery_can_be_skipped(client: AsyncClient, project_id: int) -> None:
    response = await launch(client, project_id, discovery=None)

    assert response.status_code == 201
    assert response.json()["parameters"]["discovery"] is None


async def test_scan_in_an_archived_project_is_rejected(
    client: AsyncClient, project_id: int
) -> None:
    await client.post(f"/api/v1/projects/{project_id}/archive")

    response = await launch(client, project_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_archived"


async def test_scan_in_an_unknown_project_is_not_found(client: AsyncClient) -> None:
    assert (await launch(client, 999)).status_code == 404


async def test_a_created_scan_is_logged_with_its_identifier(
    client: AsyncClient, project_id: int, json_log: io.StringIO
) -> None:
    """Check 5.8: the API's line about a scan carries its scan_id (NFR-OPS-2)."""
    scan_id = (await launch(client, project_id)).json()["id"]

    lines = [json.loads(line) for line in json_log.getvalue().splitlines()]
    (line,) = [line for line in lines if line["event"] == "scan_created"]
    assert (line["scan_id"], line["project_id"]) == (scan_id, project_id)


# --- Commands to the orchestrator ---


async def test_command_carries_the_identifier_only_and_follows_the_commit(
    client: AsyncClient, project_id: int, published: list[Any], db_session: AsyncSession
) -> None:
    scan_id = (await launch(client, project_id)).json()["id"]

    (command,) = published
    assert isinstance(command, ScanCreated)
    assert command.model_dump(exclude={"message_id"}) == {
        "schema_version": 1,
        "scan_id": scan_id,
        "type": "scan_created",
    }
    # Nothing is published for a scan that was not created.
    await launch(client, project_id, targets=["9.9.9.9"])
    assert len(published) == 1


@pytest.mark.parametrize("extra_env", [{"NDM_BROKER_URL": "amqp://guest:guest@127.0.0.1:1/"}])
async def test_scan_is_created_when_the_command_cannot_be_published(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    # No broker listens on that port. The orchestrator's tick finds the scan anyway.
    response = await launch(client, project_id)

    assert response.status_code == 201
    stored = await db_session.get_one(Scan, response.json()["id"])
    assert stored.state is ScanState.QUEUED


# --- Estimate (FR-CFG-9) ---


async def test_estimate(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/scans/estimate",
        json={"targets": ["8.8.8.0/24"], "port_profile": "top-1000", "pps": 1000, "retries": 1},
    )

    assert response.status_code == 200
    assert response.json() == {
        "hosts": 256,
        "ports": 1000,
        "probes": 256_000,
        "max_probes": 512_000,
        "seconds": 256.0,
        "max_seconds": 512.0,
    }


async def test_estimate_follows_the_parameters(client: AsyncClient) -> None:
    async def probes(**body: Any) -> tuple[int, float]:
        response = await client.post(
            "/api/v1/scans/estimate", json={"targets": ["8.8.8.0/24"], "retries": 0, **body}
        )
        return response.json()["probes"], response.json()["seconds"]

    assert await probes(port_profile="top-100", pps=1000) == (25_600, 25.6)
    assert await probes(port_profile="full", pps=5000) == (16_776_960, 3355.392)
    assert await probes(port_profile="custom", custom_ports="1-10", pps=100) == (2_560, 25.6)
    assert await probes(port_profile="top-100", pps=1000, targets=["8.8.8.8"]) == (100, 0.1)

    too_many = await client.post("/api/v1/scans/estimate", json={"targets": ["8.8.8.0/23"]})
    assert too_many.status_code == 422


# --- List and detail (FR-HIS-1) ---


async def test_list_is_newest_first_with_summary_counts(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    ids = [(await launch(client, project_id)).json()["id"] for _ in range(3)]

    listed = (await client.get(f"/api/v1/projects/{project_id}/scans", params={"limit": 2})).json()

    assert (listed["total"], [item["id"] for item in listed["items"]]) == (3, ids[:0:-1])
    first = listed["items"][0]
    assert (first["hosts_reachable"], first["open_ports"], first["chunks_total"]) == (0, 0, 0)
    assert (await client.get(f"/api/v1/scans/{ids[0]}")).json()["id"] == ids[0]
    assert (await client.get("/api/v1/scans/999")).status_code == 404
    assert (await client.get("/api/v1/projects/999/scans")).status_code == 404


# --- Cancel (FR-SCN-5) ---


async def test_cancel_marks_the_scan_and_is_audited_once(
    client: AsyncClient, project_id: int, published: list[Any], db_session: AsyncSession
) -> None:
    scan_id = (await launch(client, project_id)).json()["id"]

    first = await client.post(f"/api/v1/scans/{scan_id}/cancel")
    second = await client.post(f"/api/v1/scans/{scan_id}/cancel")

    assert (first.status_code, second.status_code) == (200, 200)
    # The API only asks; the orchestrator ends the scan and keeps its results.
    assert (first.json()["state"], first.json()["cancel_requested"]) == ("queued", True)
    assert (await audit_actions(db_session)).count("scan.cancel") == 1
    assert [type(command) for command in published] == [
        ScanCreated,
        CancelRequested,
        CancelRequested,
    ]


async def test_finished_scan_cannot_be_cancelled(
    client: AsyncClient, project_id: int, db_session: AsyncSession
) -> None:
    scan_id = (await launch(client, project_id)).json()["id"]
    stored = await db_session.get_one(Scan, scan_id)
    stored.state = ScanState.COMPLETED
    await db_session.commit()

    response = await client.post(f"/api/v1/scans/{scan_id}/cancel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "scan_finished"


# --- Delete (FR-HIS-9, I3) ---


async def test_deleted_scan_is_hidden_at_once_and_its_audit_entry_stays(
    client: AsyncClient, project_id: int, published: list[Any], db_session: AsyncSession
) -> None:
    kept = (await launch(client, project_id)).json()["id"]
    scan_id = (await launch(client, project_id)).json()["id"]

    unconfirmed = await client.delete(f"/api/v1/scans/{scan_id}")
    assert unconfirmed.status_code == 400
    assert unconfirmed.json()["error"]["code"] == "confirmation_required"
    assert (await client.get(f"/api/v1/scans/{scan_id}")).status_code == 200

    deleted = await client.delete(f"/api/v1/scans/{scan_id}", params={"confirm": "true"})
    assert deleted.status_code == 204

    gone = [
        await client.get(f"/api/v1/scans/{scan_id}"),
        await client.post(f"/api/v1/scans/{scan_id}/cancel"),
        await client.delete(f"/api/v1/scans/{scan_id}", params={"confirm": "true"}),
    ]
    assert [response.status_code for response in gone] == [404, 404, 404]
    listed = (await client.get(f"/api/v1/projects/{project_id}/scans")).json()
    assert (listed["total"], [item["id"] for item in listed["items"]]) == (1, [kept])
    # The row waits for the purge; the orchestrator is told to stop the scan.
    stored = await db_session.get_one(Scan, scan_id)
    assert stored.deleted_at is not None
    assert isinstance(published[-1], CancelRequested) and published[-1].scan_id == scan_id
    assert "scan.delete" in await audit_actions(db_session)


async def test_scan_of_a_deleted_project_is_hidden(client: AsyncClient, project_id: int) -> None:
    scan_id = (await launch(client, project_id)).json()["id"]

    await client.delete(f"/api/v1/projects/{project_id}", params={"confirm": "true"})

    assert (await client.get(f"/api/v1/scans/{scan_id}")).status_code == 404
    assert (await client.get(f"/api/v1/projects/{project_id}/scans")).status_code == 404


# --- Configuration: ceilings and scanner capabilities ---


@pytest.mark.parametrize(
    "extra_env", [{"NDM_GLOBAL_RATE_PPS": "800", "NDM_MAX_CONCURRENT_SCANS": "2"}]
)
async def test_config_reports_ceilings_and_port_profiles(client: AsyncClient) -> None:
    config = (await client.get("/api/v1/config")).json()

    assert config["limits"] == {
        "max_targets": 256,
        "max_concurrent_scans": 2,
        # The default rate never exceeds the operator ceiling.
        "pps": {"min": 1, "max": 800, "default": 800},
        "timeout_ms": {"min": 100, "max": 10_000, "default": 1500},
        "retries": {"min": 0, "max": 3, "default": 1},
        "hosts_in_parallel": {"min": 1, "max": 256, "default": 64},
    }
    assert config["port_profiles"] == [
        {"name": "top-100", "ports": 100},
        {"name": "top-1000", "ports": 1000},
        {"name": "full", "ports": 65535},
    ]
    assert config["scanner"] is None


@pytest.mark.parametrize(("syn", "alive"), [(True, True), (False, True), (True, False)])
async def test_config_reports_what_the_scanner_can_do(
    client: AsyncClient, db_session: AsyncSession, syn: bool, alive: bool
) -> None:
    await scanner(db_session, syn=syn, alive=alive)

    config = (await client.get("/api/v1/config")).json()

    assert config["scanner"] == {
        "vantage_point": "default",
        "alive": alive,
        "syn_capable": syn,
        "icmp_capable": True,
    }
