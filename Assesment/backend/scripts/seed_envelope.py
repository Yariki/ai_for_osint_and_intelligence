"""Fill a database to the sizing envelope (assumption A7), for load checks.

    uv run python scripts/seed_envelope.py --database-url <owner URL> [--scale 0.01] [--force]

At scale 1 it writes 100 projects, about 50 million host observations and
250 million open ports. That takes hours and tens of gigabytes; use a smaller
scale to try it out. The largest project has 65,536 addresses, at every scale
that leaves room for it, because the project-wide view is measured on it.

It fills every table that holds data at rest (TABLES below): projects with
their scopes, scans and everything they observed, enrichment snapshots and
classification results, the host registry and the hosts of each project; 20
active schedules (A7) whose runs are the scans of one network of their
project, with a stored diff and an alert for each run after the first;
traceroutes of twenty hops with their hop snapshots; an audit entry for every
scan; the vantage point, scan profiles, reference datasets and the registry
tables at the size of the five registries' data. The data is made up: every
scan targets one /24, every second address answers and has ten open ports.
The tables of work in progress (scan_chunk, session) stay empty, as on an
installation with nothing running.

The largest project also gets the comparison pair of check 4.12, at every
scale: two scans of one /24 with the top-1000 ports, in which every address
answers on every port (the case of question Q1). Between the two, every second
address closed half of its ports and moved to another city with a new dataset
version, so the diff is as large as such a pair can make it. The pair is
marked with "seeded": "comparison" in its parameters.

The tool writes bulk data. It refuses to run against a database that already
holds a project, unless --force is given. Run it with the URL of the owning
role, on a migrated database; it analyzes the tables when it is done.
"""

import argparse
import asyncio
import math
import sys
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from ndm.db import project_view

# The envelope (A7).
PROJECTS = 100
HOST_OBSERVATIONS = 50_000_000
PORT_OBSERVATIONS = 250_000_000

TARGETS_PER_SCAN = 256  # every scan targets one /24
PORTS = [22, 25, 53, 80, 110, 143, 443, 993, 3389, 8080]
# Every second target answers, and has all of PORTS open: 5 open ports per target.
OPEN_PORTS_PER_SCAN = TARGETS_PER_SCAN // 2 * len(PORTS)
assert OPEN_PORTS_PER_SCAN * HOST_OBSERVATIONS == PORT_OBSERVATIONS * TARGETS_PER_SCAN

# Project p owns addresses inside 20.p.0.0/16.
FIRST_ADDRESS = 20 << 24
LARGE_PROJECT_NETWORKS = 256  # 65,536 addresses
OTHER_PROJECT_NETWORKS = 16  # 4,096 addresses

SCANS_PER_BATCH = 200

TABLES = [
    "account",
    "vantage_point",
    "scan_profile",
    "reference_dataset",
    "rir_delegation",
    "rir_inetnum",
    "audit_event",
    "project",
    "attestation",
    "scope_entry",
    "scan",
    "host",
    "project_host",
    "host_observation",
    "port_observation",
    "enrichment_snapshot",
    "classification_result",
    "schedule",
    "schedule_run",
    "traceroute_run",
    "traceroute_probe",
    "traceroute_responder",
    "scan_diff",
    "change_event",
    "alert",
]

# The comparison pair (check 4.12): every address answers on every probed port.
PAIR_PORTS = 1000
HOPS = 20

# The rest of A7 and the reference data, at full scale.
SCHEDULES = 20  # active schedules
TRACEROUTES_PER_PROJECT = 50
DELEGATIONS = 250_000  # IPv4 delegations of the five registries
INETNUMS = 6_000_000  # RIPE, APNIC and AFRINIC inetnum objects
CHANGES_PER_DIFF = 5
PROFILES = 10


class SeedRefused(Exception):
    pass


@dataclass(frozen=True)
class Plan:
    projects: int
    scans: int
    traceroutes: int = 1
    """Traceroutes per project."""
    delegations: int = 1000
    inetnums: int = 1000

    @classmethod
    def for_scale(cls, scale: float) -> Plan:
        if not 0 < scale <= 1:
            raise ValueError("the scale must be above 0 and at most 1")
        # Rounded up, so that the full scale reaches the envelope.
        scans = math.ceil(HOST_OBSERVATIONS * scale / TARGETS_PER_SCAN)
        return cls(
            projects=max(1, min(scans, round(PROJECTS * scale))),
            scans=scans,
            traceroutes=max(1, round(TRACEROUTES_PER_PROJECT * scale)),
            delegations=max(1000, math.ceil(DELEGATIONS * scale)),
            inetnums=max(1000, math.ceil(INETNUMS * scale)),
        )

    @property
    def host_observations(self) -> int:
        return self.scans * TARGETS_PER_SCAN

    @property
    def port_observations(self) -> int:
        return self.scans * OPEN_PORTS_PER_SCAN

    def networks(self, project: int) -> int:
        """How many /24 networks a project has. The first project is the large one."""
        wanted = LARGE_PROJECT_NETWORKS if project == 0 else OTHER_PROJECT_NETWORKS
        return max(1, min(wanted, self.scans_of(project)))

    def scans_of(self, project: int) -> int:
        """Scans are spread evenly; the first projects take the remainder."""
        share, rest = divmod(self.scans, self.projects)
        return share + (1 if project < rest else 0)


async def _create_project(db: AsyncConnection, plan: Plan, project: int) -> tuple[int, int]:
    """A project with its scope and its hosts. Returns the project and attestation ids."""
    base = FIRST_ADDRESS + (project << 16)
    last = base + plan.networks(project) * TARGETS_PER_SCAN - 1
    project_id = await db.scalar(
        text("INSERT INTO project (name) VALUES (:name) RETURNING id"),
        {"name": f"Seeded project {project + 1:03d}"},
    )
    attestation_id = await db.scalar(
        text(
            "INSERT INTO attestation (project_id, account_id) "
            "VALUES (:project, (SELECT min(id) FROM account)) RETURNING id"
        ),
        {"project": project_id},
    )
    await db.execute(
        text("UPDATE project SET current_attestation_id = :attestation WHERE id = :project"),
        {"attestation": attestation_id, "project": project_id},
    )
    await db.execute(
        text(
            "INSERT INTO scope_entry "
            "(attestation_id, kind, original_text, start_address, end_address) "
            "VALUES (:attestation, 'range', "
            "host('0.0.0.0'::inet + :first) || '-' || host('0.0.0.0'::inet + :last), "
            "'0.0.0.0'::inet + :first, '0.0.0.0'::inet + :last)"
        ),
        {"attestation": attestation_id, "first": base, "last": last},
    )
    await db.execute(
        text(
            "INSERT INTO host (address, first_seen_at, last_seen_at, is_public) "
            "SELECT '0.0.0.0'::inet + n, now(), now(), true "
            "FROM generate_series(CAST(:first AS bigint), :last) AS n WHERE n % 2 = 0 "
            "ON CONFLICT DO NOTHING"
        ),
        {"first": base, "last": last},
    )
    assert project_id is not None and attestation_id is not None
    return project_id, attestation_id


async def _create_scans(
    db: AsyncConnection, plan: Plan, project: int, project_id: int, attestation_id: int,
    first_scan: int, count: int,
) -> None:  # fmt: skip
    """``count`` completed scans of one project, with everything they observed.

    Scan number n of a project targets the project's network n modulo its
    number of networks, so each network is scanned again and again.
    """
    base = FIRST_ADDRESS + (project << 16)
    parameters = {
        "project": project_id,
        "attestation": attestation_id,
        "base": base,
        "networks": plan.networks(project),
        "first": first_scan,
        "last": first_scan + count - 1,
        "ports": PORTS,
    }
    await db.execute(text("CREATE TEMP TABLE seeded (id bigint, network bigint) ON COMMIT DROP"))
    await db.execute(
        text(
            """
            WITH created AS (
                INSERT INTO scan (project_id, attestation_id, state, targets, probed_ports,
                                  parameters, technique_requested, technique_used, seed,
                                  counters, created_at, started_at, finished_at,
                                  vantage_point_id, dataset_versions)
                SELECT :project, :attestation, 'completed',
                       ARRAY(SELECT '0.0.0.0'::inet + (:base + (n % :networks) * 256 + a)
                             FROM generate_series(0, 255) AS a),
                       '{[1,1001)}', '{}', 'automatic', 'syn', n,
                       '{"chunks_total": 26, "chunks_done": 26, "probes_sent": 256000}',
                       now() - (:last - n + 1) * interval '1 hour',
                       now() - (:last - n + 1) * interval '1 hour',
                       now() - (:last - n + 1) * interval '1 hour' + interval '5 minutes',
                       (SELECT min(id) FROM vantage_point),
                       '{"geolite2-city": "2026-11-13", "geolite2-asn": "2026-11-13"}'
                FROM generate_series(CAST(:first AS bigint), :last) AS n
                RETURNING id, seed
            )
            INSERT INTO seeded SELECT id, :base + (seed % :networks) * 256 FROM created
            """
        ),
        parameters,
    )
    await db.execute(
        text(
            """
            INSERT INTO host_observation (scan_id, address, reachable, evidence,
                                          closed_count, filtered_count, ptr_names, ptr_status)
            SELECT s.id, '0.0.0.0'::inet + (s.network + a), a % 2 = 0,
                   CASE WHEN a % 2 = 0
                        THEN '{"icmp": {"at": "2026-11-16T09:00:00Z", "rtt_ms": 12.5}}'::jsonb
                        ELSE '{}'::jsonb END,
                   CASE WHEN a % 2 = 0 THEN 990 ELSE 0 END, 0,
                   CASE WHEN a % 4 = 0 THEN ARRAY['host-' || a || '.example.net'] ELSE '{}' END,
                   CASE WHEN a % 4 = 0 THEN 'ok' WHEN a % 2 = 0 THEN 'no_name' END
            FROM seeded AS s, generate_series(0, 255) AS a
            """
        )
    )
    await db.execute(
        text(
            """
            INSERT INTO port_observation (scan_id, address, port)
            SELECT s.id, '0.0.0.0'::inet + (s.network + a), port
            FROM seeded AS s, generate_series(0, 254, 2) AS a, unnest(CAST(:ports AS int[])) AS port
            """
        ),
        {"ports": PORTS},
    )
    await db.execute(
        text(
            """
            INSERT INTO enrichment_snapshot (scan_id, address, asn, as_holder, network_name,
                    organization, organization_source, rir, registration_country, country,
                    city, location, accuracy_radius_km, dataset_versions)
            SELECT s.id, '0.0.0.0'::inet + (s.network + a),
                   64496 + (s.network / 256) % 50, 'EXAMPLE-AS-' || (s.network / 256) % 50,
                   'NET-' || s.network / 256, 'Example Organization ' || (s.network / 256) % 200,
                   'registry', 'arin',
                   (ARRAY['US', 'DE', 'NL', 'AU', 'BR'])[1 + (s.network / 256) % 5],
                   (ARRAY['US', 'DE', 'NL', 'AU', 'BR'])[1 + (s.network / 256) % 5],
                   'City ' || (s.network / 256) % 300,
                   -- One point per network: co-located hosts are the normal case.
                   ST_SetSRID(ST_MakePoint(-170 + (s.network / 256) % 340,
                                           -60 + (s.network / 256 / 340) % 120), 4326)::geography,
                   50, '{"geolite2-city": "2026-11-13", "geolite2-asn": "2026-11-13"}'
            FROM seeded AS s, generate_series(0, 254, 2) AS a
            """
        )
    )
    # Every launch is in the audit log.
    await db.execute(
        text(
            """
            INSERT INTO audit_event (occurred_at, actor, action, object_type, object_id, details)
            SELECT scan.created_at, 'seeded', 'scan.launch', 'scan', scan.id::text,
                   jsonb_build_object('project_id', scan.project_id, 'targets', 256,
                                      'port_profile', 'top-1000')
            FROM seeded JOIN scan ON scan.id = seeded.id
            """
        )
    )
    await db.execute(
        text(
            """
            INSERT INTO classification_result (scan_id, address, device_type, sub_role,
                                               confidence, rules, ruleset_version)
            SELECT s.id, '0.0.0.0'::inet + (s.network + a),
                   (ARRAY['server', 'router', 'dns_server', 'unknown'])[1 + a / 2 % 4],
                   CASE WHEN a / 2 % 4 = 0 THEN 'web' END, 0.8,
                   '[{"rule": "seeded", "matched": "port 80"}]', '1'
            FROM seeded AS s, generate_series(0, 254, 2) AS a
            """
        )
    )
    # The project remembers the latest scan that targeted each address.
    await db.execute(
        text(
            """
            INSERT INTO project_host (project_id, address, first_seen_at, last_seen_at,
                                      latest_scan_id)
            SELECT :project, '0.0.0.0'::inet + (latest.network + a),
                   CASE WHEN a % 2 = 0 THEN now() END, CASE WHEN a % 2 = 0 THEN now() END,
                   latest.id
            FROM (SELECT network, max(id) AS id FROM seeded GROUP BY network) AS latest,
                 generate_series(0, 255) AS a
            ON CONFLICT (project_id, address) DO UPDATE
            SET latest_scan_id = greatest(project_host.latest_scan_id, excluded.latest_scan_id)
            """
        ),
        {"project": project_id},
    )


async def _create_reference(db: AsyncConnection, plan: Plan) -> None:
    """What an installation has once: the vantage point, scan profiles, the
    reference datasets and the registry tables."""
    await db.execute(
        text(
            "INSERT INTO vantage_point (name, egress_address, capabilities, counters, "
            "last_heartbeat_at) VALUES ('default', '192.0.2.1', "
            "'{\"syn\": true, \"icmp\": true}', '{}', now())"
        )
    )
    await db.execute(
        text(
            "INSERT INTO scan_profile (name, parameters) "
            "SELECT 'Seeded profile ' || n, jsonb_build_object('port_profile', 'top-1000', "
            "'pps', 1000 * n, 'technique', 'automatic') FROM generate_series(1, :profiles) n"
        ),
        {"profiles": PROFILES},
    )
    await db.execute(
        text(
            "INSERT INTO reference_dataset (name, version, status, loaded_at, checked_at) "
            "SELECT name, '2026-11-13', 'ok', now(), now() FROM unnest(ARRAY["
            "'geolite2-city', 'geolite2-asn', 'rir-delegation-afrinic', 'rir-delegation-apnic', "
            "'rir-delegation-arin', 'rir-delegation-lacnic', 'rir-delegation-ripe', "
            "'rir-objects-ripe', 'rir-objects-apnic', 'rir-objects-afrinic']) AS name"
        )
    )
    # Ranges spread over the IPv4 space, 256 addresses each.
    await db.execute(
        text(
            "INSERT INTO rir_delegation (rir, ip_range, country, delegated_on, status) "
            "SELECT (ARRAY['afrinic', 'apnic', 'arin', 'lacnic', 'ripe'])[1 + n % 5], "
            "int8range(16777216 + n * 4096, 16777216 + n * 4096 + 256), "
            "(ARRAY['US', 'DE', 'NL', 'AU', 'BR'])[1 + n % 5], DATE '2010-01-01' + n % 5000, "
            "'allocated' FROM generate_series(0, :rows - 1) n"
        ),
        {"rows": plan.delegations},
    )
    # A series of bigint: at full scale n * 512 passes the range of a 4-byte integer.
    await db.execute(
        text(
            "INSERT INTO rir_inetnum (rir, ip_range, network_name, organization, country) "
            "SELECT (ARRAY['ripe', 'apnic', 'afrinic'])[1 + n % 3], "
            "int8range(16777216 + n * 512, 16777216 + n * 512 + 256), 'NET-' || n, "
            "'Example Organization ' || n % 10000, (ARRAY['DE', 'NL', 'AU'])[1 + n % 3] "
            "FROM generate_series(0, CAST(:rows AS bigint) - 1) n"
        ),
        {"rows": plan.inetnums},
    )


async def _create_extras(
    db: AsyncConnection, plan: Plan, project: int, project_id: int, attestation_id: int
) -> None:
    """The traceroutes of a project with their hop snapshots; and, for the
    first SCHEDULES projects, a schedule whose runs are the scans of the
    project's first network, with a stored diff and an alert for each run
    after the first."""
    base = FIRST_ADDRESS + (project << 16)
    hops = (30 << 24) + (project << 12)
    values = {
        "project": project_id,
        "attestation": attestation_id,
        "base": base,
        "hops": hops,
        "hop_count": HOPS,
        "runs": plan.traceroutes,
    }
    await db.execute(text("CREATE TEMP TABLE runs (id bigint, n int) ON COMMIT DROP"))
    await db.execute(
        text(
            """
            WITH created AS (
                INSERT INTO traceroute_run (project_id, attestation_id, vantage_point_id, target,
                                            method, parameters, state, reached, dispatched_at,
                                            finished_at)
                SELECT :project, :attestation, (SELECT min(id) FROM vantage_point),
                       '0.0.0.0'::inet + (:base + 2 * (n % 128)),
                       (ARRAY['icmp', 'tcp', 'udp'])[1 + n % 3],
                       '{"port": null, "max_hops": 30, "probes_per_hop": 3, "timeout_ms": 1000}',
                       'completed', true, now() - n * interval '1 day',
                       now() - n * interval '1 day' + interval '20 seconds'
                FROM generate_series(0, :runs - 1) n
                RETURNING id
            )
            INSERT INTO runs SELECT id, row_number() OVER (ORDER BY id) FROM created
            """
        ),
        values,
    )
    await db.execute(
        text(
            "INSERT INTO traceroute_probe (run_id, ttl, probe, responder, rtt_ms, reply_kind) "
            "SELECT runs.id, ttl, probe, '0.0.0.0'::inet + (:hops + ttl), ttl * 2.5 + probe, "
            "'time_exceeded' FROM runs, generate_series(1, :hop_count) AS ttl, "
            "generate_series(0, 2) AS probe"
        ),
        values,
    )
    await db.execute(
        text(
            "INSERT INTO traceroute_responder "
            "(run_id, address, unlocatable, ptr_names, ptr_status) "
            "SELECT runs.id, '0.0.0.0'::inet + (:hops + ttl), false, "
            "ARRAY['hop-' || ttl || '.example.net'], 'ok' "
            "FROM runs, generate_series(1, :hop_count) AS ttl"
        ),
        values,
    )
    await db.execute(
        text(
            "INSERT INTO enrichment_snapshot (traceroute_run_id, address, asn, as_holder, "
            "organization, organization_source, country, city, dataset_versions) "
            "SELECT runs.id, '0.0.0.0'::inet + (:hops + ttl), 64500 + ttl, 'TRANSIT-' || ttl, "
            "'Transit ' || ttl, 'as_holder', 'DE', 'Frankfurt', "
            '\'{"geolite2-city": "2026-11-13"}\' '
            "FROM runs, generate_series(1, :hop_count) AS ttl"
        ),
        values,
    )
    await db.execute(
        text(
            "INSERT INTO audit_event (actor, action, object_type, object_id, details) "
            "SELECT 'seeded', 'traceroute.run', 'traceroute', id::text, "
            "jsonb_build_object('project_id', CAST(:project AS bigint)) FROM runs"
        ),
        values,
    )
    if project >= SCHEDULES:
        return

    schedule_id = await db.scalar(
        text(
            "INSERT INTO schedule (project_id, name, targets, parameters, interval_seconds, "
            "next_run_at, last_run_at, last_outcome) "
            "VALUES (:project, 'Seeded daily', ARRAY[host('0.0.0.0'::inet + :base) || '/24'], "
            "'{\"port_profile\": \"top-1000\"}', 86400, now() + interval '1 day', now(), "
            "'started') RETURNING id"
        ),
        values,
    )
    scheduled = values | {"schedule": schedule_id, "changes": CHANGES_PER_DIFF}
    await db.execute(
        text(
            "UPDATE scan SET schedule_id = :schedule "
            "WHERE project_id = :project AND targets[1] = '0.0.0.0'::inet + :base"
        ),
        scheduled,
    )
    await db.execute(
        text(
            "INSERT INTO schedule_run (schedule_id, planned_at, outcome, scan_id) "
            "SELECT :schedule, created_at, 'started', id FROM scan WHERE schedule_id = :schedule"
        ),
        scheduled,
    )
    await db.execute(
        text(
            """
            WITH pairs AS (
                SELECT lag(id) OVER (ORDER BY id) AS base_id, id AS target_id
                FROM scan WHERE schedule_id = :schedule
            ),
            diffs AS (
                INSERT INTO scan_diff (base_scan_id, target_scan_id, compared_ports,
                                       not_comparable, summary)
                SELECT base_id, target_id, '{[1,1001)}', '{}',
                       jsonb_build_object('port_opened', CAST(:changes AS int),
                                          'compared_addresses', 256)
                FROM pairs WHERE base_id IS NOT NULL
                RETURNING id, target_scan_id
            ),
            events AS (
                INSERT INTO change_event (diff_id, address, kind, port)
                SELECT diffs.id, '0.0.0.0'::inet + (:base + 2 * k), 'port_opened', 8443
                FROM diffs, generate_series(0, :changes - 1) AS k
            )
            INSERT INTO alert (project_id, type, summary, link, read_at)
            SELECT :project, 'changes_detected', 'Seeded daily: 5 changes',
                   jsonb_build_object('diff_id', diffs.id, 'scan_id', diffs.target_scan_id),
                   CASE WHEN diffs.id % 4 = 0 THEN NULL ELSE now() END
            FROM diffs
            """
        ),
        scheduled,
    )


async def _create_comparison_pair(
    db: AsyncConnection, project_id: int, attestation_id: int
) -> None:
    """The two scans of check 4.12, in the project's first /24 (see the module text)."""
    base = FIRST_ADDRESS
    for number, versions in enumerate(
        ('{"geolite2-city": "2026-11-06"}', '{"geolite2-city": "2026-11-13"}')
    ):
        scan_id = await db.scalar(
            text(
                """
                INSERT INTO scan (project_id, attestation_id, state, targets, probed_ports,
                                  parameters, technique_requested, technique_used, seed,
                                  dataset_versions, created_at, started_at, finished_at,
                                  vantage_point_id)
                SELECT :project, :attestation, 'completed',
                       ARRAY(SELECT '0.0.0.0'::inet + (:base + a) FROM generate_series(0, 255) a),
                       '{[1,1001)}',
                       '{"seeded": "comparison", "discovery": null, "port_profile": "top-1000"}',
                       'automatic', 'syn', CAST(:number AS integer), CAST(:versions AS jsonb),
                       now() - interval '2 hours' + CAST(:number AS integer) * interval '1 hour',
                       now() - interval '2 hours' + CAST(:number AS integer) * interval '1 hour',
                       now() - interval '111 minutes'
                           + CAST(:number AS integer) * interval '1 hour',
                       (SELECT min(id) FROM vantage_point)
                RETURNING id
                """
            ),
            {
                "project": project_id,
                "attestation": attestation_id,
                "base": base,
                "number": number,
                "versions": versions,
            },
        )
        params = {"scan": scan_id, "base": base, "later": number, "versions": versions}
        await db.execute(
            text(
                "INSERT INTO host_observation (scan_id, address, reachable, evidence, "
                "closed_count, filtered_count, ptr_names, ptr_status) "
                "SELECT :scan, '0.0.0.0'::inet + (:base + a), true, '{}', 0, 0, "
                "ARRAY['all-ports-' || a || '.example.net'], 'ok' FROM generate_series(0, 255) a"
            ),
            params,
        )
        # In the later scan every second address answers on half of the ports only.
        await db.execute(
            text(
                "INSERT INTO port_observation (scan_id, address, port) "
                "SELECT :scan, '0.0.0.0'::inet + (:base + a), port "
                "FROM generate_series(0, 255) a, generate_series(1, :ports) port "
                "WHERE :later = 0 OR a % 2 = 0 OR port <= :ports / 2"
            ),
            params | {"ports": PAIR_PORTS},
        )
        await db.execute(
            text(
                "INSERT INTO enrichment_snapshot (scan_id, address, asn, as_holder, "
                "organization, organization_source, rir, registration_country, country, city, "
                "accuracy_radius_km, dataset_versions) "
                "SELECT :scan, '0.0.0.0'::inet + (:base + a), 64496, 'EXAMPLE-AS', "
                "'Example Hosting', 'as_holder', 'ripe', 'NL', 'NL', "
                "CASE WHEN :later = 1 AND a % 2 = 1 THEN 'Leiden' ELSE 'Delft' END, 20, "
                "CAST(:versions AS jsonb) FROM generate_series(0, 255) a"
            ),
            params,
        )
        await db.execute(
            text(
                "INSERT INTO classification_result (scan_id, address, device_type, confidence, "
                "rules, ruleset_version) SELECT :scan, '0.0.0.0'::inet + (:base + a), 'server', "
                "0.9, '[]', '1' FROM generate_series(0, 255) a"
            ),
            params,
        )

    # Every address answers in the pair: all of them are known hosts, and the
    # project now sees them as the later scan of the pair did.
    await db.execute(
        text(
            "INSERT INTO host (address, first_seen_at, last_seen_at, is_public) "
            "SELECT '0.0.0.0'::inet + (:base + a), now(), now(), true "
            "FROM generate_series(0, 255) a ON CONFLICT DO NOTHING"
        ),
        {"base": base},
    )
    await db.execute(
        text(
            "UPDATE project_host SET latest_scan_id = :scan, "
            "first_seen_at = coalesce(first_seen_at, now()), last_seen_at = now() "
            "WHERE project_id = :project AND address <<= CAST(:network AS inet)"
        ),
        {"scan": scan_id, "project": project_id, "network": f"{_dotted(base)}/24"},
    )


def _dotted(number: int) -> str:
    return ".".join(str(number >> shift & 255) for shift in (24, 16, 8, 0))


async def seed(engine: AsyncEngine, plan: Plan, *, force: bool = False) -> dict[str, int]:
    """Write the planned data. Returns the number of rows in each seeded table."""
    async with engine.begin() as db:
        if await db.scalar(text("SELECT count(*) FROM project")) and not force:
            raise SeedRefused(
                "The database already holds projects. Seeding would mix made-up data "
                "into them. Use an empty database, or --force."
            )
        if not await db.scalar(text("SELECT count(*) FROM account")):
            await db.execute(
                text("INSERT INTO account (username, password_hash) VALUES ('seeded', '!')")
            )
        if not await db.scalar(text("SELECT count(*) FROM vantage_point")):
            await _create_reference(db, plan)

    done = 0
    for project in range(plan.projects):
        async with engine.begin() as db:
            project_id, attestation_id = await _create_project(db, plan, project)
        remaining = plan.scans_of(project)
        while remaining:
            count = min(SCANS_PER_BATCH, remaining)
            # One transaction per batch: an interrupted run leaves whole scans behind.
            async with engine.begin() as db:
                await _create_scans(
                    db, plan, project, project_id, attestation_id,
                    plan.scans_of(project) - remaining, count,
                )  # fmt: skip
            remaining -= count
            done += count
            print(f"\r{done} of {plan.scans} scans", end="", file=sys.stderr, flush=True)
        async with engine.begin() as db:
            # The pair first: its scans are in the scheduled network of project 0, so
            # that project has runs to compare at every scale.
            if project == 0:
                await _create_comparison_pair(db, project_id, attestation_id)
            await _create_extras(db, plan, project, project_id, attestation_id)
            # The copy the project-wide view reads, as the orchestrator keeps it.
            await project_view.refresh(db, project_id=project_id)
    print(file=sys.stderr)

    async with engine.connect() as db:
        autocommit = await db.execution_options(isolation_level="AUTOCOMMIT")
        for table in TABLES:
            await autocommit.execute(text(f"ANALYZE {table}"))
        return {
            table: await db.scalar(text(f"SELECT count(*) FROM {table}")) or 0 for table in TABLES
        }


async def run(url: str, scale: float, force: bool) -> None:
    plan = Plan.for_scale(scale)
    print(
        f"Seeding {plan.projects} projects, {plan.scans} scans, "
        f"{plan.host_observations:,} host observations, {plan.port_observations:,} open ports."
    )
    engine = create_async_engine(url)
    try:
        counts = await seed(engine, plan, force=force)
    finally:
        await engine.dispose()
    for table, rows in counts.items():
        print(f"{table:22} {rows:>14,}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill a database to the sizing envelope (A7).")
    parser.add_argument("--database-url", required=True, help="URL of the owning role")
    parser.add_argument("--scale", type=float, default=1.0, help="share of the envelope, 0 to 1")
    parser.add_argument("--force", action="store_true", help="seed although projects exist")
    arguments = parser.parse_args()
    try:
        asyncio.run(run(arguments.database_url, arguments.scale, arguments.force))
    except SeedRefused as refusal:
        raise SystemExit(str(refusal)) from None


if __name__ == "__main__":
    main()
