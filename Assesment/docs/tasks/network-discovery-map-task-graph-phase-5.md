# Network Discovery Map — Task Graph, Phase 5

| | |
| --- | --- |
| Version | 1.0 |
| Date | 2026-11-15 |
| Status | Proposed; ready for assignment |
| Based on | Network Discovery Map — Delivery Plan v1.0, section 5 (Phase 5); Architecture v1.0, sections 10.3, 14 and 15 |
| Audience | Implementation team |

This document breaks Phase 5 (Hardening and release) of the delivery plan into tasks. The conventions, the field meanings and the rules of the graph are in the index, [network-discovery-map-task-graph.md](network-discovery-map-task-graph.md).

## 1. Scope and entry conditions

**Goal of the phase.** Version 1 is verified as a whole on the release images, at the capacity the specification names, and the operator has the documentation to run it.

**Entry conditions.**

- Phase 4 is complete (TASK-071).
- The lab host and a second clean host are available, the second for the acceptance run and the restore.
- The authorized public range is available for the acceptance run.

**Defect fixes.** This phase finds defects; it does not plan their fixes. Each defect that breaks an acceptance criterion or a "must" requirement becomes a new task, numbered from TASK-078, with its own files and risk level. The check that found it is run again after the fix.

## 2. Overview

| Task | Title | Depends on | Risk | Checks closed |
| --- | --- | --- | --- | --- |
| TASK-072 | Release candidate images with pinned versions | TASK-071 | medium | — |
| TASK-073 | Operator guide completed and licence inventory | TASK-071 | low | NFR-LIC-2 |
| TASK-074 | Capacity and load: endurance, envelope, storage, Q1 | TASK-072 | medium | 5.2, 5.3, 5.4, 5.5 |
| TASK-075 | Deletion, operability and posture checks | TASK-072 | high | 5.6, 5.8, 5.9 |
| TASK-076 | Acceptance run, backup and restore, browsers | TASK-073, TASK-074, TASK-075 | high | 5.1, 5.7, 5.10 |
| TASK-077 | Release sign-off | TASK-076 | low | Phase 5 completion |
| TASK-078 | Project-wide view from a copy on `project_host` (defect of 2.13 and 5.3) | TASK-074 | high | 2.13, 5.3 again |
| TASK-079 | A scan records and shows that ICMP discovery was unavailable (decision on 1B.11) | TASK-072 | low | 1B.11 again |

| Wave | Tasks that run in parallel |
| --- | --- |
| 1 | TASK-072, TASK-073 |
| 2 | TASK-074, TASK-075 |
| 3 | TASK-076 |
| 4 | TASK-077 |

## 3. Tasks

### TASK-072 — Release candidate images with pinned versions

- **dependencies:** TASK-071
- **risk_level:** medium (the images are what the operator runs)

**Description.**

- Build the release candidate images `ndm-backend`, `ndm-scanner`, the proxy and `scanner-netinit` from a tagged commit, with a version label on each.
- The Compose file refers to every image by an exact version: the project's own images by the release tag, third-party images by a pinned version.
- A release workflow that builds the images and runs the CI stages on the tagged commit.
- A script that builds the same images locally, for an operator without access to the workflow.

**expected_files.**

- `.github/workflows/ndm-release.yml`
- `Assesment/deploy/compose.yaml` (modified)
- `Assesment/deploy/release/build-images.sh`
- `Assesment/deploy/release/VERSIONS.md`

**Acceptance.**

- The stack starts on a clean host from the release candidate images alone.
- No image reference in the Compose file is unpinned or uses `latest`.
- `VERSIONS.md` lists every image with its version.

### TASK-073 — Operator guide completed and licence inventory

- **dependencies:** TASK-071
- **risk_level:** low

**Description.**

- Complete the operator guide so that a deployment needs nothing else (architecture section 15.5):
  - host preparation;
  - backup and restore of the database (NFR-OPS-5), as a procedure with commands;
  - the fact that reverse DNS sends target addresses to the resolver;
  - choosing a tile provider;
  - obtaining a MaxMind key;
  - expected abuse reports (R7).
- Licence inventory of every bundled data file and every dependency of the backend and the frontend (NFR-LIC-2).

**expected_files.**

- `Assesment/docs/operator-guide.md` (modified)
- `Assesment/docs/licences.md`

**Acceptance.**

- Every `NDM_` setting in `core/settings.py` is described in the guide.
- The inventory covers the port lists, the service names, the ruleset and every dependency in the two lockfiles, each with its licence.

### TASK-074 — Capacity and load: endurance, envelope, storage and Q1

- **dependencies:** TASK-072
- **risk_level:** medium

**Description.**

- Extend the seeding tool so that it fills every table to the A7 envelope.
- Lab targets that answer on every port, for check 5.5.
- Load tests on the release candidate: endurance, query times, storage sizes, and the all-ports-open case.
- Write the load and capacity report. It holds the measured numbers, the comparison with architecture section 10.3 and the measurement that goes to the product owner with Q1.

**expected_files.**

- `Assesment/backend/scripts/seed_envelope.py` (modified)
- `Assesment/backend/tests/load/test_endurance.py`
- `Assesment/backend/tests/load/test_queries_release.py`
- `Assesment/backend/tests/load/test_storage_size.py`
- `Assesment/backend/tests/load/test_all_ports_open.py`
- `Assesment/deploy/lab/allopen/Dockerfile`
- `Assesment/deploy/lab/allopen/listen.py`
- `Assesment/deploy/lab/compose.lab.yaml` (modified)
- `Assesment/docs/release/capacity-report.md`

**Acceptance.**

- Check 5.2: a full-range scan of a /24 (16.8 million probes) at the global ceiling, with one stack restart in the middle, completes with exact results and zero receive drops.
- Check 5.3: on a database seeded to A7 with every table filled, all list, filter, timeline and comparison endpoints meet NFR-PRF-4 and NFR-PRF-5.
- Check 5.4: measured table and index sizes at A7 are compared with architecture section 10.3; a deviation above 50 percent is reported.
- Check 5.5: for a /24 of all-ports-open hosts scanned with the top-1000 profile, write rate and storage growth are measured and are in the report.

### TASK-075 — Deletion, operability and posture checks

- **dependencies:** TASK-072
- **risk_level:** high (proves that deletion removes all user data; verifies the security posture)

**Description.**

- A deletion test over the complete schema: it fills every table, deletes a scan and a project, runs the purge and looks for remaining rows.
- An inspection script for the release candidate that reads the running stack and the images:
  - every log line of a scan and of a traceroute carries its identifier, in all three processes;
  - all metrics of NFR-OPS-3 are present;
  - every service has a health check and a resource limit;
  - capabilities, users, `no-new-privileges`, read-only file systems and published ports match architecture section 14.
- The dependency scan on the release candidate. The severity above which a finding blocks the release is agreed with the product owner and written into the report.
- A posture report with the result of each point.

**expected_files.**

- `Assesment/backend/tests/integration/test_deletion_complete.py`
- `Assesment/deploy/checks/inspect-release.sh`
- `Assesment/docs/release/posture-report.md`

**Acceptance.**

- Check 5.6: after a scan and a project are deleted and the purge has run, no row of theirs remains in any table except `host` and `audit_event`.
- Check 5.8: the log, metric, health-check and resource-limit points all hold.
- Check 5.9: the container and network posture matches architecture section 14, and the dependency scan reports nothing above the agreed severity.

### TASK-076 — Acceptance run, backup and restore, browsers

- **dependencies:** TASK-073, TASK-074, TASK-075
- **risk_level:** high (restores user data; the result decides the release)

**Description.**

- Deploy the release candidate on a clean host by following the operator guide only. Anything the guide does not say is a defect of the guide.
- Run AC 1 to AC 11 in one run on that deployment and record the evidence for each.
- Restore a database backup on a second host by the documented procedure and compare projects, scans and audit log.
- Carry out the demonstration scripts of Phases 1A to 4 in current Chromium and Firefox.

**expected_files.**

- `Assesment/docs/release/acceptance-run.md`
- `Assesment/docs/release/backup-restore-record.md`

**Acceptance.**

- Check 5.1: AC 1 to AC 11 pass in one run on the release candidate.
- Check 5.7: the restored database shows the same projects, scans and audit log (NFR-OPS-5).
- Check 5.10: the demonstration scripts of Phases 1A to 4 pass in current Chromium and Firefox (A5).

### TASK-077 — Release sign-off

- **dependencies:** TASK-076
- **risk_level:** low

**Description.**

- Put Q1 to the product owner with the measurement of check 5.5 and record the decision. A decision for a cap becomes a new task; it touches the port-observation write and the host flag.
- Go through section 7 of the delivery plan and confirm that every "must" requirement has a passed check.
- List the open defects and confirm that none breaks an acceptance criterion or a "must" requirement.
- Record the result of each of checks 5.1 to 5.10.

**expected_files.**

- `Assesment/docs/release/release-checklist.md`

**Acceptance.**

- Checks 5.1 to 5.10 are recorded as passed.
- Q1 is decided.
- Section 7 of the delivery plan shows no "must" requirement without a passed check.
- No open defect breaks an acceptance criterion or a "must" requirement.

### TASK-078 — Project-wide view from a copy on `project_host`

Raised on 2026-11-19 by check 5.3 (and 2.13) at the full envelope: the project-wide view of 65,536 addresses took 17.7 to 42.4 seconds and its map points 8.0 seconds, against the 1-second limit of NFR-PRF-4. The planner does not see that each address of `project_host` has one observation in its latest scan; it costs a lookup into the 16 hash partitions as a probe of all 16, and reads all 50 million observations instead. Forcing the per-address lookup still took 0.7 to 1.2 seconds per statement. Architecture section 10.2 names the fallback; the delivery plan (Phase 2, TASK-046) says it is raised as a new task.

- **dependencies:** TASK-074
- **risk_level:** high (a schema change and a copy that every writer of the observation tables must keep current)

**Description.**

- Migration 0006: the fields the view shows of the latest observation, with its open ports, latest enrichment snapshot and latest classification, as columns of `project_host`, filled from the existing rows.
- One function recomputes the copy of given rows from the observation tables. It locks the rows before it reads, so the copy is computed from everything committed by a writer that held them.
- Every writer calls it in its own transaction: scan start, each findings message, the closed and filtered counts, each reverse-DNS batch, every enrichment snapshot and every classification result of a scan (re-runs included), and scan deletion.
- The query builder reads the project-wide view, its map points and its export from `project_host` alone; the view of one scan is unchanged.
- The seeding tool fills the copy.

**expected_files.**

- `Assesment/backend/src/ndm/db/migrations/versions/0006_project_view_copy.py`
- `Assesment/backend/src/ndm/db/project_view.py`
- `Assesment/backend/src/ndm/db/models.py` (modified)
- `Assesment/backend/src/ndm/api/queries/hosts.py`, `api/export.py`, `api/routers/scans.py` (modified)
- `Assesment/backend/src/ndm/orchestrator/lifecycle.py`, `recording.py`, `enrichment.py`, `classification_phase.py` (modified)
- `Assesment/backend/scripts/seed_envelope.py` (modified)
- `Assesment/backend/tests/integration/api/test_project_view_copy.py`
- `Assesment/backend/tests/integration/db/test_migration_0006.py`

**Acceptance.**

- After every kind of write, the copy equals the join it replaces, for every address of the project: `test_project_view_copy.py`, which fails if any one of the refresh calls is removed.
- The tests of the project-wide view, its filters, its map and its export pass unchanged.
- Check 2.13 and check 5.3 pass at the full envelope.

### TASK-079 — A scan records and shows that ICMP discovery was unavailable

Raised on 2026-11-19 by check 1B.11. Without NET_RAW the scanner sends neither SYN probes nor ICMP echo requests; discovery rests on the TCP probes, and a host that answers ping only is not found (lab host 10.77.3.21). Nothing said so. The product owner chose to record and warn (release checklist, section 2a): no default changes, and check 1B.11 compares against the hosts that TCP discovery can find.

- **dependencies:** TASK-072
- **risk_level:** low (one column set at scan start, shown and exported)

**Description.**

- Migration 0007: `scan.icmp_discovery_unavailable`, false for existing scans.
- At scan start, set when the scan's discovery asks for ICMP echo and the vantage point reports no ICMP capability.
- The scan in the API, the scan columns of every export row, and a warning in the workspace's scan summary.
- Check 1B.11 as decided.

**expected_files.**

- `Assesment/backend/src/ndm/db/migrations/versions/0007_icmp_discovery_unavailable.py`
- `Assesment/backend/src/ndm/db/models.py`, `orchestrator/lifecycle.py`, `api/schemas/scans.py`, `api/routers/scans.py`, `api/export.py` (modified)
- `Assesment/backend/openapi.json`, `Assesment/frontend/src/api/schema.d.ts` (regenerated)
- `Assesment/frontend/src/views/ProjectWorkspaceView.vue` (modified)
- Tests: `tests/integration/orchestrator/test_queue_cancel_lock.py`, `tests/integration/api/test_export.py`, `tests/integration/db/test_migration_0007.py`, `frontend/src/views/ProjectWorkspaceView.spec.ts`, `tests/lab/test_accuracy.py`

**Acceptance.**

- The flag is set exactly when ICMP discovery is asked for and unavailable; not when discovery has no ICMP or is skipped.
- The scan, the CSV and JSON exports and the workspace show it.
- Check 1B.11 passes on the lab as decided.

## 4. Coverage of Phase 5

| Phase 5 check | Task |
| --- | --- |
| 5.1 Acceptance run | TASK-076 |
| 5.2 Full-range endurance | TASK-074 |
| 5.3 Queries at the envelope | TASK-074 |
| 5.4 Storage | TASK-074 |
| 5.5 Hosts open on every port (Q1) | TASK-074; decision in TASK-077 |
| 5.6 Deletion | TASK-075 |
| 5.7 Backup and restore | TASK-076, with the procedure of TASK-073 |
| 5.8 Operability | TASK-075 |
| 5.9 Container and network posture | TASK-075 |
| 5.10 Browsers | TASK-076 |

| Deliverable of the delivery plan | Task |
| --- | --- |
| Release candidate images with pinned versions | TASK-072 |
| Recorded run of AC 1 to AC 11 on a clean host | TASK-076 |
| Load and capacity report | TASK-074 |
| Operator guide, complete | TASK-073 |
| Licence inventory | TASK-073 |
| Fixes for defects found in this phase | New tasks from TASK-078, raised as defects are found |
