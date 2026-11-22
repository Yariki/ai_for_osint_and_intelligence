"""The seeding tool: the plan reaches the A7 envelope, and a small plan writes what it says."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ndm.db.models import Project

# The tool is a script, not part of a package: load it from its file.
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "seed_envelope.py"
_spec = importlib.util.spec_from_file_location("seed_envelope", SCRIPT)
assert _spec is not None and _spec.loader is not None
seed_envelope: Any = importlib.util.module_from_spec(_spec)
sys.modules["seed_envelope"] = seed_envelope
_spec.loader.exec_module(seed_envelope)

PORTS, Plan, SeedRefused, seed = (
    seed_envelope.PORTS,
    seed_envelope.Plan,
    seed_envelope.SeedRefused,
    seed_envelope.seed,
)


def test_plan_at_full_scale_is_the_envelope() -> None:
    plan = Plan.for_scale(1.0)

    assert plan.projects == 100
    assert plan.host_observations >= 50_000_000
    assert plan.port_observations >= 250_000_000
    # No more than one scan above the envelope.
    assert plan.host_observations - 50_000_000 < 256
    # The largest project has 65,536 addresses, for the project-wide view.
    assert plan.networks(0) * 256 == 65_536
    assert sum(plan.scans_of(project) for project in range(plan.projects)) == plan.scans


@pytest.mark.parametrize("scale", [1.0, 0.1, 0.001, 0.00001])
def test_plan_keeps_five_open_ports_per_target_at_every_scale(scale: float) -> None:
    plan = Plan.for_scale(scale)

    assert plan.port_observations == 5 * plan.host_observations
    assert 1 <= plan.projects <= plan.scans
    assert all(plan.scans_of(project) >= 1 for project in range(plan.projects))


@pytest.mark.parametrize("scale", [0, -1, 1.5])
def test_scale_outside_its_range_is_refused(scale: float) -> None:
    with pytest.raises(ValueError):
        Plan.for_scale(scale)


async def counts(owner_engine: AsyncEngine, query: str) -> Any:
    async with owner_engine.connect() as connection:
        return (await connection.execute(text(query))).all()


# The comparison pair of check 4.12, written at every scale: two scans of 256
# addresses that all answer; 1,000 open ports each in the first, and in the
# second 1,000 on every second address and 500 on the others.
PAIR_SCANS = 2
PAIR_HOSTS = 2 * 256
PAIR_PORTS = 256 * 1000 + 128 * 1000 + 128 * 500


async def test_small_plan_writes_the_rows_it_reports(owner_engine: AsyncEngine) -> None:
    plan = Plan(projects=3, scans=8)

    reported = await seed(owner_engine, plan)

    assert reported["project"] == 3
    assert reported["scan"] == 8 + PAIR_SCANS
    assert plan.host_observations == 8 * 256
    assert reported["host_observation"] == plan.host_observations + PAIR_HOSTS
    assert plan.port_observations == 8 * 128 * len(PORTS)
    assert reported["port_observation"] == plan.port_observations + PAIR_PORTS
    # The hosts that answered, the pair, and the 20 hops of each project's traceroute.
    assert reported["enrichment_snapshot"] == 8 * 128 + PAIR_HOSTS + 3 * 20
    assert reported["classification_result"] == 8 * 128 + PAIR_HOSTS
    assert reported["attestation"] == reported["scope_entry"] == 3
    # Project 1 has 3 scans of 3 networks, the others 3 and 2: 8 networks, 256 addresses each.
    assert reported["project_host"] == 8 * 256
    # Every second address answers, and every address of the pair's network.
    assert reported["host"] == 8 * 128 + 128
    # Each project has a traceroute of 20 hops of 3 probes, and a schedule (all three
    # are among the first 20). Its runs are the scans of the project's first network:
    # one each, and in project 0 also the pair, so project 0 has two stored diffs.
    assert reported["traceroute_run"] == 3
    assert reported["traceroute_probe"] == 3 * 20 * 3
    assert reported["traceroute_responder"] == 3 * 20
    assert reported["schedule"] == 3
    assert reported["schedule_run"] == 3 + PAIR_SCANS
    assert (reported["scan_diff"], reported["alert"]) == (2, 2)
    assert reported["change_event"] == 2 * 5
    # One audit entry per seeded scan and per traceroute.
    assert reported["audit_event"] == 8 + 3
    # What the installation has once.
    assert (reported["vantage_point"], reported["scan_profile"]) == (1, 10)
    assert reported["reference_dataset"] == 10
    assert reported["rir_delegation"] == reported["rir_inetnum"] == 1000

    # Every scan is complete and consistent with what a real one leaves behind.
    [(states, with_targets)] = await counts(
        owner_engine,
        "SELECT array_agg(DISTINCT state::text), "
        "count(*) FILTER (WHERE cardinality(targets) = 256) FROM scan",
    )
    assert (states, with_targets) == (["completed"], 8 + PAIR_SCANS)
    [(orphans,)] = await counts(
        owner_engine,
        "SELECT count(*) FROM port_observation p WHERE NOT EXISTS ("
        "SELECT 1 FROM host_observation o "
        "WHERE o.scan_id = p.scan_id AND o.address = p.address AND o.reachable)",
    )
    assert orphans == 0
    [(outside,)] = await counts(
        owner_engine,
        "SELECT count(*) FROM host_observation o JOIN scan s ON s.id = o.scan_id "
        "WHERE NOT o.address = ANY (s.targets)",
    )
    assert outside == 0
    [(unscoped,)] = await counts(
        owner_engine,
        "SELECT count(*) FROM project_host h WHERE NOT EXISTS (SELECT 1 FROM scope_entry e "
        "JOIN attestation a ON a.id = e.attestation_id WHERE a.project_id = h.project_id "
        "AND h.address BETWEEN e.start_address AND e.end_address)",
    )
    assert unscoped == 0
    # The project points at the latest scan of each address.
    [(stale,)] = await counts(
        owner_engine,
        "SELECT count(*) FROM project_host h WHERE latest_scan_id IS DISTINCT FROM ("
        "SELECT max(o.scan_id) FROM host_observation o JOIN scan s ON s.id = o.scan_id "
        "WHERE s.project_id = h.project_id AND o.address = h.address)",
    )
    assert stale == 0
    # Snapshots of scans have a location, and hosts of one network share it.
    [(points, networks)] = await counts(
        owner_engine,
        "SELECT count(DISTINCT ST_AsText(location)), "
        "count(DISTINCT network(set_masklen(address, 24))) FROM enrichment_snapshot "
        "WHERE scan_id IS NOT NULL",
    )
    assert points == networks == 8


async def test_database_that_holds_projects_is_refused_unless_forced(
    owner_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    db_session.add(Project(name="A real project"))
    await db_session.commit()

    with pytest.raises(SeedRefused, match="already holds projects"):
        await seed(owner_engine, Plan(projects=1, scans=1))
    assert await counts(owner_engine, "SELECT count(*) FROM scan") == [(0,)]

    reported = await seed(owner_engine, Plan(projects=1, scans=1), force=True)
    assert (reported["project"], reported["scan"]) == (2, 1 + PAIR_SCANS)
