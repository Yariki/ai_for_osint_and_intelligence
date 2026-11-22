# Phase 3 — Demonstration Script and Exit Checks

Part 1 is the written demonstration for check 3.13, to be carried out on the authorized public range. Part 2 is the record of checks 3.1 to 3.13 and of the common criteria of delivery plan section 2.2.

## Part 1. Demonstration (check 3.13)

**You need.** The stack of the Phase 2 demonstration (MaxMind key, tile provider, the authorized public range), with a scanner that has its raw sockets (operator guide, section 4), and a browser. The test switch is off.

| # | Step | Expected result |
| --- | --- | --- |
| 1 | In the project of the authorized range, run a scan with the profile top-1000 and wait for it to complete. | The scan's phase passes through `enrichment` and `classification` to `completed`. |
| 2 | Open the workspace on the scan. Choose a host of the table. | The detail shows a **Device type** section: the type, its confidence, the ruleset version, and each rule that matched with what it matched, or "No rule matched". |
| 3 | Note the share of `unknown`: filter the table by device type "Unknown" and compare the count with the unfiltered count. | Recorded below. It is information for the product owner (R5), not a pass condition. |
| 4 | On a host with an open TCP port, press **Trace route**, keep ICMP, start. | Hops appear one by one while the trace runs. Public hops show a city and an AS holder; private hops are marked "private: no location"; a router that does not answer shows "no answer". |
| 5 | Look at the map. | The path runs through the hops that have a location. A segment that skips a silent or unlocated hop is dashed. |
| 6 | Trace the same host again with TCP (the port field empty) and with UDP. | Each run is in the host's traceroute list with its method, parameters and vantage point. TCP used an open port of the host. |
| 7 | Try to trace an address outside the project scope, by entering a host of another project's scan in the URL. | The traceroute is refused with "outside the project scope"; the audit log has a `traceroute.rejected` entry. |
| 8 | On the host of step 2, press **Set type**, choose another type, save. | The detail shows the chosen type "set by the analyst", and below it the automatic result. The table's type column shows the chosen type with "(set)". |
| 9 | Add the tags `edge` and `demo` and a note; save. Filter by tag `demo`. | Only that host is in the table and on the map. |
| 10 | Run a second scan of the same range. Open the host in it. | The override, tags and note are there in the new scan; the automatic result is the new scan's own. |
| 11 | In the list of scans, press **Classify again** on the first scan. | Within seconds, the first scan's hosts have a second classification result (run 2); the first is kept. |
| 12 | Open the **Audit log**. | Entries `traceroute.run` (three), `traceroute.rejected`, `host.override_set`, `scan.classify`. |

**Share of `unknown` on the authorized range:** not yet recorded (the demonstration has not been carried out).

## Part 2. Record of exit checks

| Check | How it is run | Result | Date | Note |
| --- | --- | --- | --- | --- |
| 3.1 Rule evaluation | `tests/unit/orchestrator/test_rules.py`, `test_ruleset_regression.py` | Passed | 2026-11-17 | Every condition kind matches and does not; the combination formula; the threshold; every type and sub-role is produced by a fixture; every rule fires for one fixture and not for another. |
| 3.2 Ruleset regression | `tests/unit/orchestrator/test_ruleset_regression.py` | Passed | 2026-11-17 | 28 fixed hosts against the stored output of ruleset 1. |
| 3.3 Rule inputs | `test_rules.py` | Passed | 2026-11-17 | The evaluator takes the four allowed inputs and no other; the loader refuses any other condition or enrichment field. |
| 3.4 Stored classification is stable | `tests/integration/orchestrator/test_classification_stored.py` | Passed, reservation A | 2026-11-17 | |
| 3.5 Override and annotations | `tests/integration/api/test_override_annotations.py` | Passed, reservation A | 2026-11-17 | Including a later scan and another project. |
| 3.6 Traceroute scope | `test_traceroute_scope_rejection.py` (API), `tests/integration/scanner/test_traceroute_scope.py` (scanner), `test_traceroute_concurrency.py` (orchestrator) | Passed | 2026-11-18 | All three points, including the job placed on the real queue by hand. |
| 3.7 Three methods | `tests/lab/test_traceroute_methods.py`; `tests/unit/scanner/test_quoted.py` | Passed | 2026-11-19 | On the lab: ICMP, TCP and UDP each report the known hop sequence; TCP without a port uses an open port of the host; UDP probes carry no payload; private hops listed and marked unlocatable. |
| 3.8 Hops are never probed directly | `tests/lab/test_hops_not_probed.py`; `test_quoted.py` | Passed | 2026-11-19 | The capture on the lab, over all three methods and a top-100 scan: no probe to a router's address, only to the target (ICMP unreachable messages addressed to a router were allowed by the check). |
| 3.9 Hop storage and enrichment | `tests/integration/orchestrator/test_hop_recording.py` | Passed, reservation A | 2026-11-17 | |
| 3.10 Live hops and path | `tests/lab/test_live_hops.py`; frontend `traceroutes.spec.ts`, `useMap.spec.ts`; browser | **Partly run** | 2026-11-18 | The event stream on the lab: hops arrive one TTL at a time before the run ends. Frontend: hops are applied in TTL order; the segment across a silent or unlocated hop is dashed. The browser: not run. |
| 3.11 Concurrency | `tests/integration/orchestrator/test_traceroute_concurrency.py` | Passed, reservation A | 2026-11-17 | |
| 3.12 Hop rule | `tests/integration/orchestrator/test_hop_rule.py` | Passed, reservation A | 2026-11-17 | |
| 3.13 Demonstration | Part 1 | **Not run** | | Needs the authorized range, a scanner with raw sockets, and a browser. |

The lab extension of TASK-049 (the router chain, `deploy/lab`) has not been started either: its acceptance (the target reachable through the chain; a capture on the silent router) is part of 3.7 and 3.8.

**Reservations A and B, lifted on 2026-11-18.** The whole backend suite, the tests of this phase among them, ran on the pinned `postgis/postgis:17-3.5` and RabbitMQ images: 1,478 passed, none failed. The rows above that still name A or B passed again on the real images.

**Reservation A: the database.** PostgreSQL 18 with PostGIS 3.6, unpacked from the distribution's packages, stood in for the pinned `postgis/postgis:17-3.5`.

**Reservation B: the broker.** No RabbitMQ. The tests that need it, two new ones among them (the traceroute queue and a traceroute job placed on it by hand), were written and not run.

**What was run, on 2026-11-17.** 1,215 backend tests (with the Phase 2 load checks and the lab suite skipped) and 162 frontend tests passed; ruff, mypy, the import rule, ESLint, vue-tsc and the frontend build were clean.

### Common criteria (delivery plan 2.2)

| Criterion | State |
| --- | --- |
| 1. CI green | Not observed. Locally, every step that needs no Docker passes. |
| 2. Clean start and demonstration | Not run: needs Docker. |
| 3. Migrations apply, revert, apply; earlier data kept | Passed (A): `test_migration_0004.py`, including the snapshots of traceroute runs on revert. |
| 4. Audit entry in the same transaction, with a test | Passed: the traceroute run and `traceroute.run` (`test_traceroutes.py`); override set and cleared (`test_override_annotations.py`). |
| 5. Deletion purge covers every new table | Passed: `test_purge.py` with runs, probes, responders, hop snapshots and classification results. |
| 6. Route-enumeration test covers new endpoints | Passed: `test_route_auth.py`. |
| 7. No failed or skipped check | **Not met**: see above. |
| 8. Operator guide | Done: sections 7 and 12. |

### Notes for the product owner

- **Taxonomy.** The types of FR-CLS-1 are `router`, `gateway`, `dns_server`, `camera` and `server` with the sub-roles `web`, `mail`, `database`, `remote_access`, `file`, plus `unknown`. A new type needs a new ruleset version only; no data migration.
- **A tie** between two types with the same confidence goes to the type listed first in the ruleset (router before DNS server, for example).
- **Reverse DNS of traceroute hops** is looked up beside the recording of hops, within seconds, so that a slow resolver never delays the results of scans. Private hops are never sent to the resolver.
- **One table beyond the architecture's list:** `traceroute_responder` holds each responding address of a run with its reverse DNS and the unlocatable flag.

**Also open from earlier phases.** Gate 0, checks 1A.8 and 1A.9, 1B.10 to 1B.19, and Phase 2's 2.7, 2.8, 2.10 to 2.15.

**Phase 3 is not complete until every check above is recorded as passed.**
