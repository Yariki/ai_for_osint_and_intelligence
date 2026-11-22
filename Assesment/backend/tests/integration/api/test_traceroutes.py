"""The traceroute API: start, history, and the run hop by hop (FR-TRC-1 to FR-TRC-7)."""

import io
import json
from ipaddress import IPv4Address
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import HopReply, TracerouteDone, TracerouteHop, TracerouteRequested
from ndm.db.models import AuditEvent, Scan, TracerouteRun, TracerouteState
from ndm.orchestrator.tick import Orchestrator
from ndm.orchestrator.traceroute import record_traceroute
from tests.integration.api.test_datasets import RecordingPublisher


@pytest.fixture
def published(app: FastAPI) -> list[Any]:
    publisher = RecordingPublisher()
    app.state.commands = publisher
    return publisher.commands


async def start(client: AsyncClient, project_id: int, **body: Any) -> Any:
    return await client.post(f"/api/v1/projects/{project_id}/traceroutes", json=body)


# --- Starting a traceroute ---


async def test_a_started_run_is_logged_with_its_identifier(
    client: AsyncClient, enriched_scan: Scan, published: list[Any], json_log: io.StringIO
) -> None:
    """Check 5.8: the API's line about a traceroute carries its run_id (NFR-OPS-2)."""
    run_id = (await start(client, enriched_scan.project_id, target="8.8.8.8")).json()["id"]

    lines = [json.loads(line) for line in json_log.getvalue().splitlines()]
    (line,) = [line for line in lines if line["event"] == "traceroute_created"]
    assert (line["run_id"], line["project_id"]) == (run_id, enriched_scan.project_id)


async def test_a_run_is_created_audited_and_announced(
    client: AsyncClient, enriched_scan: Scan, published: list[Any], db_session: AsyncSession
) -> None:
    response = await start(client, enriched_scan.project_id, target="8.8.8.8")

    assert response.status_code == 201, response.text
    run = response.json()
    assert {k: run[k] for k in ("target", "method", "state", "reached", "vantage_point")} == {
        "target": "8.8.8.8",
        "method": "icmp",
        "state": "queued",
        "reached": None,
        "vantage_point": None,
    }
    # The defaults of FR-TRC-2.
    assert run["parameters"] == {
        "port": None,
        "max_hops": 30,
        "probes_per_hop": 3,
        "timeout_ms": 1000,
    }
    assert published == [TracerouteRequested(run_id=run["id"], message_id=published[0].message_id)]
    entry = await db_session.scalar(select(AuditEvent).where(AuditEvent.action == "traceroute.run"))
    assert entry is not None
    assert (entry.actor, entry.object_id) == ("analyst", str(run["id"]))
    assert entry.details == {
        "project_id": enriched_scan.project_id,
        "target": "8.8.8.8",
        "method": "icmp",
        "port": None,
        "max_hops": 30,
        "probes_per_hop": 3,
        "timeout_ms": 1000,
    }


async def test_tcp_defaults_to_an_open_port_of_the_host(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    # 193.0.6.1 has 22, 80 and 443 open in the recorded scan; the scan probed 22, 80, 443, 8080.
    project = enriched_scan.project_id

    by_default = (await start(client, project, target="193.0.6.1", method="tcp")).json()
    chosen = (await start(client, project, target="193.0.6.1", method="tcp", port=8443)).json()

    assert by_default["parameters"]["port"] == 22
    assert chosen["parameters"]["port"] == 8443


async def test_tcp_to_a_host_without_a_known_open_port_needs_a_port(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    response = await start(client, enriched_scan.project_id, target="8.8.8.4", method="tcp")

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "no_open_port"


async def test_udp_starts_at_port_33434_and_icmp_has_no_port(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    project = enriched_scan.project_id

    udp = (await start(client, project, target="8.8.8.8", method="udp")).json()
    icmp = (await start(client, project, target="8.8.8.8", method="icmp", port=80)).json()

    assert udp["parameters"]["port"] == 33434
    assert icmp["parameters"]["port"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"target": "8.8.8.8", "method": "sctp"},
        {"target": "8.8.8.8", "max_hops": 0},
        {"target": "8.8.8.8", "max_hops": 65},
        {"target": "8.8.8.8", "probes_per_hop": 11},
        {"target": "8.8.8.8", "timeout_ms": 50},
        {"target": "8.8.8.8", "port": 70000},
        {"target": "8.8.8.0/24"},
        {"target": "not an address"},
        {"target": "8.8.8.8", "extra": 1},
    ],
)
async def test_invalid_requests_are_refused(
    client: AsyncClient, enriched_scan: Scan, published: list[Any], body: dict[str, Any]
) -> None:
    response = await start(client, enriched_scan.project_id, **body)

    assert response.status_code == 422
    assert published == []


async def test_no_traceroute_in_an_archived_or_unknown_project(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    await client.post(f"/api/v1/projects/{enriched_scan.project_id}/archive")

    archived = await start(client, enriched_scan.project_id, target="8.8.8.8")
    unknown = await start(client, 999999, target="8.8.8.8")

    assert (archived.status_code, archived.json()["error"]["code"]) == (409, "project_archived")
    assert unknown.status_code == 404


# --- History and detail ---


async def record(orchestrator: Orchestrator, *messages: Any) -> None:
    async with orchestrator.sessions() as db:
        for message in messages:
            await record_traceroute(db, message, orchestrator.enricher)
            await db.commit()


async def test_history_of_a_host_lists_its_runs_newest_first(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    project = enriched_scan.project_id
    first = (await start(client, project, target="8.8.8.8")).json()
    await start(client, project, target="193.0.6.1")
    second = (await start(client, project, target="8.8.8.8", method="udp")).json()

    history = (
        await client.get(f"/api/v1/projects/{project}/traceroutes", params={"target": "8.8.8.8"})
    ).json()
    everything = (await client.get(f"/api/v1/projects/{project}/traceroutes")).json()

    assert [run["id"] for run in history["items"]] == [second["id"], first["id"]]
    assert [run["method"] for run in history["items"]] == ["udp", "icmp"]
    assert (history["total"], everything["total"]) == (2, 3)


async def test_detail_gives_every_hop_with_its_responders_times_and_loss(
    client: AsyncClient,
    enriched_scan: Scan,
    enriching: Orchestrator,
    db_session: AsyncSession,
) -> None:
    created = (
        await start(client, enriched_scan.project_id, target="8.8.8.8", probes_per_hop=2)
    ).json()
    run = await db_session.get_one(TracerouteRun, created["id"])
    run.state = TracerouteState.RUNNING
    await db_session.commit()

    def hop(ttl: int, *replies: tuple[str, float] | None) -> TracerouteHop:
        return TracerouteHop(
            run_id=created["id"],
            ttl=ttl,
            replies=[
                HopReply(probe=i)
                if r is None
                else HopReply(probe=i, address=IPv4Address(r[0]), rtt_ms=r[1], kind="time_exceeded")
                for i, r in enumerate(replies)
            ],
        )

    await record(
        enriching,
        hop(1, ("10.0.0.1", 0.5), ("10.0.0.1", 0.6)),
        hop(2, None, None),
        hop(3, ("193.0.6.1", 11.0), ("1.0.0.1", 12.0)),
        hop(4, ("8.8.8.8", 20.0), None),
        TracerouteDone(run_id=created["id"], reached=True, probes_sent=8),
    )

    detail = (await client.get(f"/api/v1/traceroutes/{created['id']}")).json()

    assert (detail["state"], detail["reached"]) == ("completed", True)
    assert [hop["ttl"] for hop in detail["hops"]] == [1, 2, 3, 4]
    first, silent, balanced, target = detail["hops"]
    assert (first["lost"], first["silent"]) == (0, False)
    assert first["responders"][0] | {"ptr_status": None} == {
        "address": "10.0.0.1",
        "rtt_ms": [0.5, 0.6],
        "unlocatable": True,
        "ptr_names": [],
        "ptr_status": None,
        "asn": None,
        "as_holder": None,
        "organization": None,
        "country": None,
        "city": None,
        "latitude": None,
        "longitude": None,
        "accuracy_radius_km": None,
    }
    assert (silent["responders"], silent["lost"], silent["silent"]) == ([], 2, True)
    assert [p["address"] for p in silent["probes"]] == [None, None]
    # Two routers answered one TTL: both are listed, each with its own probes.
    by_address = {r["address"]: r for r in balanced["responders"]}
    assert by_address["193.0.6.1"]["rtt_ms"] == [11.0, None]
    assert by_address["1.0.0.1"]["rtt_ms"] == [None, 12.0]
    assert (by_address["193.0.6.1"]["city"], by_address["193.0.6.1"]["unlocatable"]) == (
        "Amsterdam",
        False,
    )
    assert by_address["193.0.6.1"]["latitude"] == 52.3759
    assert (target["lost"], target["responders"][0]["address"]) == (1, "8.8.8.8")


async def test_detail_of_a_run_that_does_not_exist_or_whose_project_is_deleted(
    client: AsyncClient, enriched_scan: Scan
) -> None:
    created = (await start(client, enriched_scan.project_id, target="8.8.8.8")).json()
    assert (await client.get(f"/api/v1/traceroutes/{created['id']}")).status_code == 200

    await client.delete(f"/api/v1/projects/{enriched_scan.project_id}", params={"confirm": "true"})

    assert (await client.get(f"/api/v1/traceroutes/{created['id']}")).status_code == 404
    assert (await client.get("/api/v1/traceroutes/999999")).status_code == 404
