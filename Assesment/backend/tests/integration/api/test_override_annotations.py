"""Check 3.5: an override applies to the address in every scan of the
project, later scans included, and not in another project; the automatic
result is still returned; clearing restores it; tags and notes do not alter
scan data. Setting and clearing an override are audited."""

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import AuditEvent, Scan, ScanState
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import Outbox, ScanFactory, snapshot
from tests.tools.enrichment_scan import RECORDED, SCOPE, run_phases, scan_to_end

HOST = "193.0.6.1"  # a web server by the ruleset


def override_url(scan: Scan, address: str = HOST) -> str:
    return f"/api/v1/projects/{scan.project_id}/hosts/{address}/override"


async def detail(client: AsyncClient, scan: Scan, address: str = HOST) -> dict[str, Any]:
    response = await client.get(f"/api/v1/scans/{scan.id}/hosts/{address}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def row(client: AsyncClient, scan: Scan, address: str = HOST) -> dict[str, Any]:
    rows = (await client.get(f"/api/v1/scans/{scan.id}/hosts", params={"limit": 200})).json()
    found: list[dict[str, Any]] = [item for item in rows["items"] if item["address"] == address]
    assert len(found) == 1
    return found[0]


async def later_scan(
    orchestrator: Orchestrator, outbox: Outbox, make_scan: ScanFactory, first: Scan
) -> Scan:
    outbox.chunks.clear()
    return await scan_to_end_in(
        orchestrator, outbox, make_scan, first.project_id, first.attestation_id
    )


async def scan_to_end_in(
    orchestrator: Orchestrator,
    outbox: Outbox,
    make_scan: ScanFactory,
    project_id: int,
    attestation_id: int,
) -> Scan:
    from tests.integration.orchestrator.conftest import run_to_end, scanner_reports

    scan = await make_scan(
        state=ScanState.QUEUED,
        targets=RECORDED.targets,
        scope=SCOPE,
        project_id=project_id,
        attestation_id=attestation_id,
    )
    await scanner_reports(orchestrator)
    await run_to_end(orchestrator, outbox, RECORDED.world)
    await run_phases(orchestrator)
    return scan


# --- The automatic result in the API ---


async def test_detail_and_row_return_the_automatic_classification(
    client: AsyncClient, classified_scan: Scan
) -> None:
    found = await detail(client, classified_scan)

    classification = found["classification"]
    assert (classification["device_type"], classification["sub_role"]) == ("server", "web")
    assert (
        classification["confidence"],
        classification["ruleset_version"],
        classification["run"],
    ) == (0.65, "1", 1)
    # The ports, and the names the reverse DNS of this fixture gives the host.
    assert [r["rule"] for r in classification["rules"]] == ["server-web-port", "server-web-name"]
    assert classification["rules"][0]["evidence"] == {"open_ports": [80, 443]}
    assert (found["type_override"], found["effective_type"]) == (None, "server")
    assert (found["tags"], found["notes"]) == ([], None)
    listed = await row(client, classified_scan)
    assert {
        k: listed[k]
        for k in ("device_type", "sub_role", "confidence", "type_override", "effective_type")
    } == {
        "device_type": "server",
        "sub_role": "web",
        "confidence": 0.65,
        "type_override": None,
        "effective_type": "server",
    }


# --- Override ---


async def test_override_applies_in_every_scan_of_the_project_and_the_automatic_result_stays(
    client: AsyncClient,
    classified_scan: Scan,
    enriching: Orchestrator,
    outbox: Outbox,
    make_scan: ScanFactory,
) -> None:
    response = await client.put(override_url(classified_scan), json={"device_type": "router"})
    assert response.status_code == 200, response.text
    assert response.json()["type_override"] == "router"

    later = await later_scan(enriching, outbox, make_scan, classified_scan)

    for scan in (classified_scan, later):
        found = await detail(client, scan)
        assert (found["type_override"], found["effective_type"]) == ("router", "router")
        # Both values stay visible (FR-CLS-5).
        assert found["classification"]["device_type"] == "server"
        listed = await row(client, scan)
        assert (listed["device_type"], listed["effective_type"]) == ("server", "router")


async def test_override_does_not_reach_another_project(
    client: AsyncClient,
    classified_scan: Scan,
    enriching: Orchestrator,
    outbox: Outbox,
    make_scan: ScanFactory,
) -> None:
    await client.put(override_url(classified_scan), json={"device_type": "router"})
    outbox.chunks.clear()
    other = await scan_to_end(enriching, outbox, make_scan)  # a project of its own
    assert other.project_id != classified_scan.project_id

    found = await detail(client, other)

    assert (found["type_override"], found["effective_type"]) == (None, "server")


async def test_clearing_the_override_restores_the_automatic_result(
    client: AsyncClient, classified_scan: Scan, db_session: AsyncSession
) -> None:
    await client.put(override_url(classified_scan), json={"device_type": "camera"})

    cleared = await client.delete(override_url(classified_scan))

    assert cleared.status_code == 200
    assert cleared.json()["type_override"] is None
    found = await detail(client, classified_scan)
    assert (found["type_override"], found["effective_type"]) == (None, "server")
    await db_session.commit()
    entries = (
        await db_session.execute(
            select(AuditEvent.action, AuditEvent.object_id, AuditEvent.details)
            .where(AuditEvent.object_type == "host")
            .order_by(AuditEvent.id)
        )
    ).all()
    where = f"{classified_scan.project_id}/{HOST}"
    assert [tuple(entry) for entry in entries] == [
        ("host.override_set", where, {"device_type": "camera", "previous": None}),
        ("host.override_cleared", where, {"previous": "camera"}),
    ]
    # Clearing again changes nothing and writes nothing.
    await client.delete(override_url(classified_scan))
    await db_session.commit()
    assert (
        len(
            (
                await db_session.execute(select(AuditEvent).where(AuditEvent.object_type == "host"))
            ).all()
        )
        == 2
    )


@pytest.mark.parametrize("device_type", ["printer", "Router", "", "router; drop"])
async def test_override_must_name_a_type_of_the_taxonomy(
    client: AsyncClient, classified_scan: Scan, device_type: str
) -> None:
    response = await client.put(override_url(classified_scan), json={"device_type": device_type})

    assert response.status_code == 422


async def test_override_of_an_address_no_scan_of_the_project_targeted(
    client: AsyncClient, classified_scan: Scan
) -> None:
    unknown = await client.put(
        override_url(classified_scan, "9.9.9.9"), json={"device_type": "router"}
    )
    malformed = await client.put(
        override_url(classified_scan, "9.9.9"), json={"device_type": "router"}
    )

    assert unknown.status_code == 404
    assert malformed.status_code == 422


# --- Tags and notes ---


async def test_tags_and_notes_are_kept_and_do_not_alter_scan_data(
    client: AsyncClient, classified_scan: Scan, db_session: AsyncSession
) -> None:
    before = await snapshot(db_session, classified_scan)
    before_detail = await detail(client, classified_scan)

    response = await client.put(
        f"/api/v1/projects/{classified_scan.project_id}/hosts/{HOST}/annotations",
        json={"tags": ["edge", " customer-A ", "edge", ""], "notes": "  Owned by the campus IT.  "},
    )

    assert response.status_code == 200
    assert (response.json()["tags"], response.json()["notes"]) == (
        ["customer-A", "edge"],
        "Owned by the campus IT.",
    )
    found = await detail(client, classified_scan)
    assert (found["tags"], found["notes"]) == (["customer-A", "edge"], "Owned by the campus IT.")
    # Everything the scan observed is as it was.
    after = await snapshot(db_session, classified_scan)
    assert after["observations"] == before["observations"]
    assert after["ports"] == before["ports"]
    assert found["classification"] == before_detail["classification"]
    assert found["enrichment"] == before_detail["enrichment"]

    cleared = await client.put(
        f"/api/v1/projects/{classified_scan.project_id}/hosts/{HOST}/annotations", json={"tags": []}
    )
    assert (cleared.json()["tags"], cleared.json()["notes"]) == ([], None)


async def test_a_tag_longer_than_64_characters_is_refused(
    client: AsyncClient, classified_scan: Scan
) -> None:
    response = await client.put(
        f"/api/v1/projects/{classified_scan.project_id}/hosts/{HOST}/annotations",
        json={"tags": ["x" * 65]},
    )

    assert response.status_code == 422


# --- Re-run of classification ---


async def test_rerun_of_classification_is_requested_audited_and_refused_while_running(
    client: AsyncClient, classified_scan: Scan, make_scan: ScanFactory, db_session: AsyncSession
) -> None:
    response = await client.post(f"/api/v1/scans/{classified_scan.id}/classify")
    running = await make_scan()

    assert response.status_code == 202
    assert response.json()["classification_requested"] is True
    assert (await client.post(f"/api/v1/scans/{running.id}/classify")).status_code == 409
    await db_session.commit()
    entry = await db_session.scalar(select(AuditEvent).where(AuditEvent.action == "scan.classify"))
    assert entry is not None and entry.object_id == str(classified_scan.id)
    stored = await db_session.get_one(Scan, classified_scan.id, populate_existing=True)
    assert stored.classification_requested_at is not None
    assert stored.classification_requested_at <= datetime.now(UTC)
