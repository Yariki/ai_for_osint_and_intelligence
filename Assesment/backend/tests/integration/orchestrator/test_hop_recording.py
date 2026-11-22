"""Hop recording (check 3.9, FR-TRC-3, FR-TRC-4, I2): every responding hop is
in the host table and, if public, has an enrichment snapshot and a
reverse-DNS result; private and CGNAT hops are stored, not enriched and
marked unlocatable; duplicate hop messages change nothing."""

from ipaddress import IPv4Address
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.core.messages import (
    HopReply,
    TracerouteDone,
    TracerouteHop,
    TracerouteHopEvent,
    TracerouteRejected,
)
from ndm.db.models import (
    AuditEvent,
    EnrichmentSnapshot,
    GeoPoint,
    Host,
    ScanState,
    TracerouteProbe,
    TracerouteResponder,
    TracerouteRun,
    TracerouteState,
)
from ndm.orchestrator.deadletter import handle_dead_letter
from ndm.orchestrator.tick import Orchestrator
from ndm.orchestrator.traceroute import record_traceroute
from tests.integration.orchestrator.conftest import ScanFactory
from tests.tools import reference_data
from tests.tools.enrichment_scan import RECORDED

TARGET = IPv4Address("8.8.8.8")
PUBLIC_HOP = IPv4Address("193.0.6.1")  # in the reference data: Amsterdam
PRIVATE_HOP = IPv4Address("10.0.0.1")
CGNAT_HOP = IPv4Address("100.64.0.1")


def ip(address: str) -> IPv4Address:
    return IPv4Address(address)


def hop(ttl: int, *replies: tuple[str, float, str] | None) -> TracerouteHop:
    """A hop message: one entry per probe, None for a probe nobody answered.
    Its run is filled in when it is recorded."""
    return TracerouteHop(
        run_id=0,
        ttl=ttl,
        replies=[
            HopReply(probe=index)
            if reply is None
            else HopReply(probe=index, address=ip(reply[0]), rtt_ms=reply[1], kind=reply[2])
            for index, reply in enumerate(replies)
        ],
    )


RUN: dict[str, int] = {}
PATH = [
    hop(1, ("10.0.0.1", 0.6, "time_exceeded"), ("10.0.0.1", 0.7, "time_exceeded")),
    hop(2, ("100.64.0.1", 3.0, "time_exceeded"), None),
    hop(3, None, None),  # the silent router
    # A load-balanced hop: two routers answer the same TTL (FR-TRC-3).
    hop(4, ("193.0.6.1", 12.0, "time_exceeded"), ("1.0.0.1", 13.5, "time_exceeded")),
    hop(5, ("8.8.8.8", 20.1, "echo_reply"), ("8.8.8.8", 19.8, "echo_reply")),
]


@pytest.fixture
async def run(
    db_session: AsyncSession, make_scan: ScanFactory, enriching_orchestrator: Orchestrator
) -> TracerouteRun:
    """A dispatched run, with every reference dataset loaded."""
    assert enriching_orchestrator.enricher is not None
    await reference_data.load(
        enriching_orchestrator.sessions,
        enriching_orchestrator.settings,
        enriching_orchestrator.enricher.geolite,
        RECORDED.reference,
    )
    scan = await make_scan(state=ScanState.COMPLETED, phase=None)
    created = TracerouteRun(
        project_id=scan.project_id,
        attestation_id=scan.attestation_id,
        target=TARGET,
        method="icmp",
        parameters={"port": None, "max_hops": 30, "probes_per_hop": 2, "timeout_ms": 1000},
        state=TracerouteState.RUNNING,
    )
    db_session.add(created)
    await db_session.commit()
    RUN["id"] = created.id
    return created


async def record(orchestrator: Orchestrator, *messages: Any) -> list[TracerouteHopEvent]:
    events: list[TracerouteHopEvent] = []
    async with orchestrator.sessions() as db:
        for message in messages:
            if message.run_id == 0:
                message = message.model_copy(update={"run_id": RUN["id"]})
            events += await record_traceroute(db, message, orchestrator.enricher)
            await db.commit()
    return events


async def stored(db: AsyncSession, run: TracerouteRun) -> dict[str, Any]:
    await db.commit()
    probes = (
        await db.execute(
            select(
                TracerouteProbe.ttl,
                TracerouteProbe.probe,
                func.host(TracerouteProbe.responder),
                TracerouteProbe.rtt_ms,
                TracerouteProbe.reply_kind,
            )
            .where(TracerouteProbe.run_id == run.id)
            .order_by(TracerouteProbe.ttl, TracerouteProbe.probe)
        )
    ).all()
    responders = (
        await db.execute(
            select(
                func.host(TracerouteResponder.address),
                TracerouteResponder.unlocatable,
                TracerouteResponder.ptr_status,
            ).where(TracerouteResponder.run_id == run.id)
        )
    ).all()
    snapshots = (
        await db.execute(
            select(func.host(EnrichmentSnapshot.address), EnrichmentSnapshot.asn).where(
                EnrichmentSnapshot.traceroute_run_id == run.id
            )
        )
    ).all()
    hosts = (await db.execute(select(func.host(Host.address), Host.is_public))).all()
    return {
        "probes": [tuple(row) for row in probes],
        "responders": sorted(tuple(row) for row in responders),
        "snapshots": sorted(tuple(row) for row in snapshots),
        "hosts": sorted(tuple(row) for row in hosts),
    }


async def test_every_probe_is_stored_with_its_responder_or_as_unanswered(
    db_session: AsyncSession, run: TracerouteRun, enriching_orchestrator: Orchestrator
) -> None:
    await record(enriching_orchestrator, *PATH)

    probes = (await stored(db_session, run))["probes"]
    assert len(probes) == 10
    assert probes[4:6] == [(3, 0, None, None, None), (3, 1, None, None, None)]
    assert probes[6:8] == [
        (4, 0, "193.0.6.1", 12.0, "time_exceeded"),
        (4, 1, "1.0.0.1", 13.5, "time_exceeded"),
    ]


async def test_every_responder_is_in_the_host_table(
    db_session: AsyncSession, run: TracerouteRun, enriching_orchestrator: Orchestrator
) -> None:
    await record(enriching_orchestrator, *PATH)

    assert (await stored(db_session, run))["hosts"] == sorted(
        [
            ("10.0.0.1", False),
            ("100.64.0.1", False),
            ("193.0.6.1", True),
            ("1.0.0.1", True),
            ("8.8.8.8", True),
        ]
    )


async def test_public_hops_are_enriched_and_private_and_cgnat_hops_are_not(
    db_session: AsyncSession, run: TracerouteRun, enriching_orchestrator: Orchestrator
) -> None:
    await record(enriching_orchestrator, *PATH)

    found = await stored(db_session, run)
    assert found["snapshots"] == [("1.0.0.1", 64498), ("193.0.6.1", 64497), ("8.8.8.8", 64496)]
    assert found["responders"] == [
        ("1.0.0.1", False, None),  # reverse DNS still to do
        ("10.0.0.1", True, "not_public"),
        ("100.64.0.1", True, "not_public"),
        ("193.0.6.1", False, None),
        ("8.8.8.8", False, None),
    ]
    snapshot = await db_session.scalar(
        select(EnrichmentSnapshot).where(EnrichmentSnapshot.address == PUBLIC_HOP)
    )
    assert snapshot is not None
    assert (snapshot.scan_id, snapshot.city, snapshot.location) == (
        None,
        "Amsterdam",
        GeoPoint(latitude=52.3759, longitude=4.8975),
    )


async def test_each_public_hop_gets_a_reverse_dns_result(
    db_session: AsyncSession, run: TracerouteRun, enriching_orchestrator: Orchestrator
) -> None:
    enricher = enriching_orchestrator.enricher
    assert enricher is not None
    asked: list[str] = []

    async def resolver(addresses: Any, nameserver: Any) -> dict[Any, tuple[list[str], str]]:
        asked.extend(str(address) for address in addresses)
        return {
            a: (["hop.example.nl"], "ok") if a == PUBLIC_HOP else ([], "no_name") for a in addresses
        }

    enricher.resolve = resolver
    enricher.settings = enricher.settings.model_copy(update={"ptr_enabled": True})
    await record(enriching_orchestrator, *PATH)

    assert await enricher.resolve_hop_names(enriching_orchestrator.sessions) == 3
    assert await enricher.resolve_hop_names(enriching_orchestrator.sessions) == 0

    # Private and CGNAT addresses were never sent to the resolver.
    assert sorted(asked) == ["1.0.0.1", "193.0.6.1", "8.8.8.8"]
    names: dict[str, list[str]] = {
        address: value
        for address, value in await db_session.execute(
            select(func.host(TracerouteResponder.address), TracerouteResponder.ptr_names)
        )
    }
    assert names["193.0.6.1"] == ["hop.example.nl"]
    statuses: dict[str, str | None] = {
        address: value
        for address, value in await db_session.execute(
            select(func.host(TracerouteResponder.address), TracerouteResponder.ptr_status)
        )
    }
    assert statuses == {
        "1.0.0.1": "no_name",
        "10.0.0.1": "not_public",
        "100.64.0.1": "not_public",
        "193.0.6.1": "ok",
        "8.8.8.8": "no_name",
    }


async def test_with_reverse_dns_switched_off_no_hop_is_looked_up(
    db_session: AsyncSession, run: TracerouteRun, enriching_orchestrator: Orchestrator
) -> None:
    enricher = enriching_orchestrator.enricher
    assert enricher is not None

    async def resolver(addresses: Any, nameserver: Any) -> Any:
        raise AssertionError("no query may be sent")

    enricher.resolve = resolver  # the settings of these tests have the switch off
    await record(enriching_orchestrator, *PATH)

    assert await enricher.resolve_hop_names(enriching_orchestrator.sessions) == 3
    statuses = set(await db_session.scalars(select(TracerouteResponder.ptr_status)))
    assert statuses == {"disabled", "not_public"}


async def test_duplicate_hop_messages_change_nothing(
    db_session: AsyncSession, run: TracerouteRun, enriching_orchestrator: Orchestrator
) -> None:
    await record(enriching_orchestrator, *PATH)
    once = await stored(db_session, run)

    await record(enriching_orchestrator, *PATH, *reversed(PATH))

    assert await stored(db_session, run) == once


async def test_each_hop_makes_one_event_with_its_responders_and_its_loss(
    run: TracerouteRun, enriching_orchestrator: Orchestrator
) -> None:
    events = await record(enriching_orchestrator, *PATH)

    assert [event.ttl for event in events] == [1, 2, 3, 4, 5]
    assert all(event.state == "running" and event.run_id == run.id for event in events)
    first, cgnat, silent, balanced, target = events
    assert [(str(r.address), r.rtt_ms, r.unlocatable) for r in first.responders] == [
        ("10.0.0.1", [0.6, 0.7], True)
    ]
    assert (cgnat.lost, cgnat.responders[0].unlocatable) == (1, True)
    assert (silent.responders, silent.lost) == ([], 2)
    by_address = {str(r.address): r for r in balanced.responders}
    assert by_address["193.0.6.1"].rtt_ms == [12.0, None]
    assert by_address["1.0.0.1"].rtt_ms == [None, 13.5]
    assert (by_address["193.0.6.1"].latitude, by_address["193.0.6.1"].longitude) == (
        52.3759,
        4.8975,
    )
    assert target.responders[0].address == TARGET


async def test_done_ends_the_run_and_later_messages_are_discarded(
    db_session: AsyncSession, run: TracerouteRun, enriching_orchestrator: Orchestrator
) -> None:
    events = await record(
        enriching_orchestrator, *PATH, TracerouteDone(run_id=run.id, reached=True, probes_sent=10)
    )
    assert (events[-1].ttl, events[-1].state) == (None, "completed")

    late = await record(enriching_orchestrator, hop(6, ("9.9.9.9", 1.0, "time_exceeded")))

    assert late == []
    stored_run = await db_session.get_one(TracerouteRun, run.id, populate_existing=True)
    assert (stored_run.state, stored_run.reached) == (TracerouteState.COMPLETED, True)
    assert stored_run.finished_at is not None
    assert "9.9.9.9" not in [h for h, _ in (await stored(db_session, run))["hosts"]]


async def test_a_rejection_by_the_scanner_fails_the_run_with_an_audit_entry(
    db_session: AsyncSession, run: TracerouteRun, enriching_orchestrator: Orchestrator
) -> None:
    await record(enriching_orchestrator, TracerouteRejected(run_id=run.id, reason="out_of_scope"))

    stored_run = await db_session.get_one(TracerouteRun, run.id, populate_existing=True)
    assert (stored_run.state, stored_run.failure_reason) == (
        TracerouteState.FAILED,
        "rejected: out_of_scope",
    )
    entry = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "traceroute.rejected")
    )
    assert entry is not None
    assert (entry.object_id, entry.details["point"]) == (str(run.id), "scanner")


async def test_a_dead_lettered_job_fails_its_run(
    db_session: AsyncSession, run: TracerouteRun
) -> None:
    body = b'{"type": "traceroute_job", "run_id": %d}' % run.id
    headers = {"x-death": [{"queue": "scanner.test.traceroutes", "reason": "delivery_limit"}]}

    failed = await handle_dead_letter(db_session, body, headers)
    await db_session.commit()

    assert isinstance(failed, TracerouteRun) and failed.id == run.id
    stored_run = await db_session.get_one(TracerouteRun, run.id, populate_existing=True)
    assert stored_run.failure_reason == "dead_letter: delivery_limit in scanner.test.traceroutes"
    # A second time, the run has ended already: nothing happens.
    assert await handle_dead_letter(db_session, body, headers) is None
