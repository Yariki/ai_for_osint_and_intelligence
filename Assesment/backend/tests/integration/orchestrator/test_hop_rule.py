"""Check 3.12: an address seen as an intermediate traceroute hop fires the hop
rule in the next classification; an address seen only as a traceroute target
does not."""

from ipaddress import IPv4Address

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ndm.db.models import (
    ClassificationResult,
    Scan,
    ScanState,
    TracerouteProbe,
    TracerouteRun,
    TracerouteState,
)
from ndm.orchestrator.classification_phase import seen_as_hop
from ndm.orchestrator.tick import Orchestrator
from tests.integration.orchestrator.conftest import Outbox, ScanFactory
from tests.tools.enrichment_scan import probe, run_phases

TARGET = IPv4Address("8.8.8.8")
HOP = IPv4Address("1.0.0.1")
ELSEWHERE = IPv4Address("193.0.6.1")


async def traced(db: AsyncSession, scan: Scan, target: IPv4Address, *hops: IPv4Address) -> None:
    """A finished traceroute to ``target`` through ``hops``, ending at the target."""
    run = TracerouteRun(
        project_id=scan.project_id,
        attestation_id=scan.attestation_id,
        target=target,
        method="icmp",
        parameters={"port": None, "max_hops": 30, "probes_per_hop": 1, "timeout_ms": 1000},
        state=TracerouteState.COMPLETED,
        reached=True,
    )
    db.add(run)
    await db.flush()
    for ttl, responder in enumerate([*hops, target], start=1):
        db.add(TracerouteProbe(run_id=run.id, ttl=ttl, probe=0, responder=responder, rtt_ms=1.0))
    await db.commit()


async def classification(
    db: AsyncSession, scan: Scan, address: IPv4Address
) -> ClassificationResult:
    await db.commit()
    found = await db.scalar(
        select(ClassificationResult).where(
            ClassificationResult.scan_id == scan.id,
            ClassificationResult.address == address,
            ClassificationResult.latest,
        )
    )
    assert found is not None
    return found


async def test_lookup_excludes_rows_where_the_address_is_the_target(
    db_session: AsyncSession, make_scan: ScanFactory
) -> None:
    scan = await make_scan(state=ScanState.COMPLETED, phase=None)
    await traced(db_session, scan, TARGET, IPv4Address("10.0.0.1"), HOP)

    found = await seen_as_hop(db_session, [TARGET, HOP, ELSEWHERE, IPv4Address("10.0.0.1")])

    assert found == {HOP, IPv4Address("10.0.0.1")}
    assert await seen_as_hop(db_session, []) == set()


async def test_an_intermediate_hop_fires_the_hop_rule_and_a_target_does_not(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    classifying_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    earlier = await make_scan(state=ScanState.COMPLETED, phase=None)
    # 1.0.0.1 forwarded a traceroute to 8.8.8.8; 8.8.8.8 was only ever a target.
    await traced(db_session, earlier, TARGET, HOP)

    scan = await probe(classifying_orchestrator, outbox, make_scan)
    await run_phases(classifying_orchestrator)

    hop = await classification(db_session, scan, HOP)
    target = await classification(db_session, scan, TARGET)
    assert "router-seen-as-hop" in [r["rule"] for r in hop.rules]
    assert [r for r in hop.rules if r["rule"] == "router-seen-as-hop"][0]["evidence"] == {
        "seen_as_hop": True
    }
    assert hop.device_type == "router"
    assert "router-seen-as-hop" not in [r["rule"] for r in target.rules]


async def test_a_hop_seen_after_a_scan_counts_in_the_next_classification_only(
    db_session: AsyncSession,
    make_scan: ScanFactory,
    classifying_orchestrator: Orchestrator,
    outbox: Outbox,
) -> None:
    first = await probe(classifying_orchestrator, outbox, make_scan)
    await run_phases(classifying_orchestrator)
    before = await classification(db_session, first, HOP)

    await traced(db_session, first, TARGET, HOP)
    outbox.chunks.clear()
    second = await probe(classifying_orchestrator, outbox, make_scan)
    await run_phases(classifying_orchestrator)

    assert "router-seen-as-hop" not in [r["rule"] for r in before.rules]
    # The earlier result is unchanged; the next classification sees the hop.
    assert "router-seen-as-hop" not in [
        r["rule"] for r in (await classification(db_session, first, HOP)).rules
    ]
    assert "router-seen-as-hop" in [
        r["rule"] for r in (await classification(db_session, second, HOP)).rules
    ]
