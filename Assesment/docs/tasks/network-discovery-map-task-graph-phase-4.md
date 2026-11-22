# Network Discovery Map — Task Graph, Phase 4

| | |
| --- | --- |
| Version | 1.0 |
| Date | 2026-11-15 |
| Status | Proposed; ready for assignment |
| Based on | Network Discovery Map — Delivery Plan v1.0, section 5 (Phase 4) and section 2.2; Architecture v1.0, sections 11.3, 11.4 and 12 |
| Audience | Implementation team |

This document breaks Phase 4 (Schedules, comparison, alerts and export) of the delivery plan into tasks. The conventions, the field meanings and the rules of the graph are in the index, [network-discovery-map-task-graph.md](network-discovery-map-task-graph.md).

## 1. Scope and entry conditions

**Goal of the phase.** Scans recur on a schedule, any two scans can be compared, changes found by a scheduled run raise an alert, and results leave the system as CSV and JSON.

**Entry conditions.**

- Phase 3 is complete (TASK-059).
- Q3 should be confirmed by the product owner: "change status" for a manual scan compares against the latest earlier completed scan.
- A maintained cron-expression library is chosen, for TASK-060.

## 2. Overview

| Task | Title | Depends on | Risk | Checks closed |
| --- | --- | --- | --- | --- |
| TASK-060 | Groundwork: cron library, settings, messages, schema | TASK-059 | high | — |
| TASK-061 | Diff function over two scans | TASK-060 | medium | 4.1, 4.2, 4.3 |
| TASK-062 | Schedule arithmetic | TASK-060 | low | 4.4 |
| TASK-063 | Alerts and the diff phase of scheduled scans | TASK-061 | medium | 4.9 |
| TASK-064 | Scheduler | TASK-062, TASK-063 | high | 4.5, 4.6, 4.7, 4.8 |
| TASK-065 | Scan profile and schedule API | TASK-062 | high | — |
| TASK-066 | Comparison, alert and change-status API | TASK-061, TASK-065 | medium | — |
| TASK-067 | Export as CSV and JSON | TASK-066 | high | 4.11 |
| TASK-068 | Frontend schedules and scan profiles | TASK-065 | medium | — |
| TASK-069 | Frontend comparison, alerts, change-status filter, export | TASK-067, TASK-068 | medium | Part of 4.10 |
| TASK-070 | Lab, load and audit-completeness checks | TASK-064, TASK-067 | medium | 4.12, 4.13 |
| TASK-071 | Operator guide, demonstration and Phase 4 exit checks | TASK-069, TASK-070 | medium | 4.10, 4.14 |

| Wave | Tasks that run in parallel |
| --- | --- |
| 1 | TASK-060 |
| 2 | TASK-061, TASK-062 |
| 3 | TASK-063, TASK-065 |
| 4 | TASK-064, TASK-066, TASK-068 |
| 5 | TASK-067 |
| 6 | TASK-069, TASK-070 |
| 7 | TASK-071 |

## 3. Tasks

### TASK-060 — Groundwork: cron library, settings, messages and schema

- **dependencies:** TASK-059
- **risk_level:** high (migration)

**Description.**

- Pin the chosen cron-expression library.
- Setting: the minimum schedule interval (FR-CFG-7).
- Message: the alert-created UI event.
- One migration and the ORM models for `scan_profile`, `schedule`, `schedule_run`, `scan_diff`, `change_event` and `alert`, as in architecture section 10.1.
- Register the tables that hold scan or project data for the deletion purge.

**expected_files.**

- `Assesment/backend/pyproject.toml` (modified)
- `Assesment/backend/uv.lock` (modified)
- `Assesment/backend/src/ndm/core/settings.py` (modified)
- `Assesment/backend/src/ndm/core/messages.py` (modified)
- `Assesment/backend/src/ndm/db/models.py` (modified)
- `Assesment/backend/src/ndm/db/migrations/versions/0005_schedules_diff_alerts.py`
- `Assesment/backend/src/ndm/orchestrator/purge.py` (modified)
- `Assesment/backend/tests/integration/db/test_migration_0005.py`
- `Assesment/backend/tests/integration/orchestrator/test_purge.py` (modified)

**Acceptance.**

- The migration applies, reverts and applies again; a database left by Phase 3 migrates forward and keeps its data.
- The purge test passes with schedules, runs, diffs, change events and alerts present.

### TASK-061 — Diff function over two scans

- **dependencies:** TASK-060
- **risk_level:** medium

**Description.**

- One function that computes a diff from two scan identifiers. Automatic diffs, manual comparison, the "change status" filter and the exports all use it.
- Addresses compared: those targeted by both scans. The rest are listed as not comparable (FR-HIS-2).
- Ports compared: the intersection of the two scans' probed ports, stated in the result (FR-HIS-4). For an address not port-scanned in one of the scans, no port change is reported; the change is the host appearing or disappearing.
- Events for hosts appeared and disappeared, ports opened and closed, and changes in reverse DNS, classification and enrichment.
- An enrichment change carries the dataset-change flag when the dataset version behind the changed field differs between the two snapshots (FR-HIS-5).
- The function reads only; storing a result belongs to TASK-063.

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/diff.py`
- `Assesment/backend/tests/fixtures/diff/scan_pairs.json`
- `Assesment/backend/tests/unit/orchestrator/test_diff_rules.py`
- `Assesment/backend/tests/integration/orchestrator/test_diff_profiles.py`
- `Assesment/backend/tests/integration/orchestrator/test_diff_dataset_flag.py`

**Acceptance.**

- Check 4.1: fixture pairs give the expected events for each kind of change; addresses targeted by one scan only are listed as not comparable.
- Check 4.2: comparing a top-100 scan with a top-1000 scan reports port changes only for ports probed in both and states which ports were compared; a host not port-scanned in one scan produces a host event and no port events.
- Check 4.3: an enrichment change between scans with different dataset versions carries the dataset-change flag.

### TASK-062 — Schedule arithmetic

- **dependencies:** TASK-060
- **risk_level:** low

**Description.**

- Pure functions for intervals and cron expressions with a time zone.
- The next run is the first occurrence strictly after the current time.
- The minimum interval: for an interval, a direct comparison; for a cron expression, a check of the gaps between upcoming occurrences (FR-SCH-6).

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/schedule_math.py`
- `Assesment/backend/tests/unit/orchestrator/test_schedule_math.py`

**Acceptance.**

- Check 4.4: the next run is strictly after the current time for intervals and cron expressions, across time zones and daylight-saving changes; intervals and cron expressions below the minimum are rejected.

### TASK-063 — Alerts and the diff phase of scheduled scans

- **dependencies:** TASK-061
- **risk_level:** medium (writes diffs and alerts)

**Description.**

- An alert writer: it stores the alert and emits the alert-created UI event after the commit.
- The diff phase, registered with the phase runner after classification, for scheduled scans only.
  - The baseline is the previous run of the same schedule that completed and is not partial. Cancelled, failed and partial runs are passed over (FR-HIS-6).
  - The result is stored in `scan_diff` and `change_event`, because an alert must keep pointing at what was detected at the time.
  - A non-empty diff raises one alert that links to the stored diff. An empty diff raises none.
- An alert for a failed scheduled run.
- An alert for a dead-lettered message, which was deferred from Phase 1B.

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/alerts.py`
- `Assesment/backend/src/ndm/orchestrator/diff_phase.py`
- `Assesment/backend/src/ndm/orchestrator/phases.py` (modified)
- `Assesment/backend/src/ndm/orchestrator/lifecycle.py` (modified)
- `Assesment/backend/src/ndm/orchestrator/deadletter.py` (modified)
- `Assesment/backend/tests/integration/orchestrator/test_baseline_alert.py`
- `Assesment/backend/tests/integration/orchestrator/test_failure_alerts.py`

**Acceptance.**

- Check 4.9: the baseline is the previous completed, non-partial run of the same schedule; a non-empty diff raises one alert that links to the stored diff, and the link still resolves after later runs; an empty diff raises none.
- A failed scheduled run and a dead-lettered message each raise one alert.
- A manual scan runs no diff phase.

### TASK-064 — Scheduler

- **dependencies:** TASK-062, TASK-063
- **risk_level:** high (starts scans without the analyst; scope check at the start of a scheduled run)

**Description.**

- A tick every 15 seconds selects enabled schedules whose `next_run_at` has passed, with `FOR UPDATE SKIP LOCKED`. For each one, in a single transaction:
  1. if the previous run is still active, record a skipped run and raise an alert (FR-SCH-3);
  2. otherwise run the scope check; on failure record a rejected run, flag the schedule, raise an alert and write an audit entry (FR-SCH-5);
  3. otherwise create the scan in `queued`;
  4. set `next_run_at` to the first occurrence strictly after the current time, which gives exactly one catch-up run after downtime (FR-SCH-4).
- Scheduled and manual scans share the queue and the concurrent ceiling.
- Schedules of an archived project do not run.
- The audit actor of a scheduled action is the system (FR-AUD-2).

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/scheduler.py`
- `Assesment/backend/src/ndm/orchestrator/main.py` (modified)
- `Assesment/backend/tests/integration/orchestrator/test_scheduler.py`
- `Assesment/backend/tests/integration/orchestrator/test_scheduled_scope_check.py`

**Acceptance.**

- Check 4.5: with a controlled clock, a due schedule creates its scan within one scheduler tick of the planned time.
- Check 4.6: with the previous run still active, the new run is recorded as skipped and an alert is raised.
- Check 4.7: after a simulated downtime of several intervals, exactly one run starts.
- Check 4.8: after the scope is reduced, the run is rejected, the schedule is flagged, and an alert and an audit entry exist; nothing reaches the scanner.

### TASK-065 — Scan profile and schedule API

- **dependencies:** TASK-062
- **risk_level:** high (creates schedules that scan without the analyst; audited)

**Description.**

- Scan profiles: create, read, update, delete (FR-CFG-8). A profile is a named parameter set, validated against the operator ceilings like a scan.
- Schedules: create, read, update, delete, enable and disable, with next run and last outcome.
- A schedule's targets are checked against the project scope when it is saved, and again by the scheduler at each run.
- An interval or a cron expression below the minimum is rejected.
- Audit entries for schedule creation and for every change, in the same transaction.
- An archived project rejects schedule writes.

**expected_files.**

- `Assesment/backend/src/ndm/api/routers/profiles.py`
- `Assesment/backend/src/ndm/api/routers/schedules.py`
- `Assesment/backend/src/ndm/api/schemas/profiles.py`
- `Assesment/backend/src/ndm/api/schemas/schedules.py`
- `Assesment/backend/src/ndm/api/app.py` (modified)
- `Assesment/backend/tests/integration/api/test_profiles.py`
- `Assesment/backend/tests/integration/api/test_schedules.py`

**Acceptance.**

- A schedule with out-of-scope targets or an interval below the minimum is rejected.
- Creating and changing a schedule each leave an audit entry.
- The schedule list shows the next run and the last outcome.
- Check 1A.3 still passes, unchanged, with the new routes.

### TASK-066 — Comparison, alert and change-status API

- **dependencies:** TASK-061, TASK-065
- **risk_level:** medium (reads scan data through the existing authentication)

**Description.**

- Comparison: the diff of any two scans of a project, computed on request and not stored; a stored diff by its identifier.
- Alerts: list, mark read, unread count.
- The "change status" filter in the query builder (FR-MAP-6). For a manual scan the comparison is against the latest earlier completed scan (Q3).

**expected_files.**

- `Assesment/backend/src/ndm/api/routers/comparison.py`
- `Assesment/backend/src/ndm/api/routers/alerts.py`
- `Assesment/backend/src/ndm/api/schemas/comparison.py`
- `Assesment/backend/src/ndm/api/schemas/alerts.py`
- `Assesment/backend/src/ndm/api/queries/hosts.py` (modified)
- `Assesment/backend/src/ndm/api/routers/hosts.py` (modified)
- `Assesment/backend/src/ndm/api/app.py` (modified)
- `Assesment/backend/tests/integration/api/test_comparison.py`
- `Assesment/backend/tests/integration/api/test_alerts.py`
- `Assesment/backend/tests/integration/api/test_change_status_filter.py`

**Acceptance.**

- A manual comparison returns the same events as the diff function for the same two scans and stores nothing.
- A stored diff is returned unchanged after later scans.
- The unread count follows the read state.
- Check 2.9 still passes with the new filter: table and map return the same set of addresses.

### TASK-067 — Export as CSV and JSON

- **dependencies:** TASK-066
- **risk_level:** high (scan data leaves the system)

**Description.**

- `format=csv` and `format=json` on the host, diff and traceroute endpoints. Responses are streamed.
- The host export applies the active filters through the same query builder as the table.
- Every export carries the metadata of FR-EXP-4: scan identifier, scan time, vantage point and dataset versions.
- CSV cells that begin with a formula character are escaped, so a spreadsheet does not run them. PTR names and notes come from outside and from the analyst.
- Exports sit behind the same authentication as every other endpoint.

**expected_files.**

- `Assesment/backend/src/ndm/api/export.py`
- `Assesment/backend/src/ndm/api/routers/hosts.py` (modified)
- `Assesment/backend/src/ndm/api/routers/comparison.py` (modified)
- `Assesment/backend/src/ndm/api/routers/traceroutes.py` (modified)
- `Assesment/backend/tests/integration/api/test_export.py`

**Acceptance.**

- Check 4.11: host, diff and traceroute exports parse as CSV and as JSON, contain the same rows as the filtered table query, and carry scan identifier, scan time, vantage point and dataset versions.
- A PTR name that starts with `=` is exported as text.
- An export answers 401 without a session.

### TASK-068 — Frontend schedules and scan profiles

- **dependencies:** TASK-065
- **risk_level:** medium

**Description.**

- Regenerate the API types.
- Schedules view: list with next run, last outcome and flag reason; create and edit with targets, parameters, interval or cron expression and time zone; enable and disable.
- Scan profiles: create and edit; usable in the launch dialog and in the schedule dialog.
- Rejections by the server are shown on the field concerned.

**expected_files.**

- `Assesment/frontend/src/api/schema.d.ts` (regenerated)
- `Assesment/frontend/src/router/index.ts` (modified)
- `Assesment/frontend/src/stores/schedules.ts`
- `Assesment/frontend/src/views/SchedulesView.vue`
- `Assesment/frontend/src/components/ScheduleDialog.vue`
- `Assesment/frontend/src/components/ScanProfileSelect.vue`
- `Assesment/frontend/src/components/ScanLaunchDialog.vue` (modified)
- `Assesment/frontend/src/stores/schedules.spec.ts`
- `Assesment/frontend/src/components/ScheduleDialog.spec.ts`

**Acceptance.**

- Vitest: choosing a profile fills the launch and schedule dialogs with its parameters; an interval below the minimum is shown as an error.
- A schedule can be created, disabled and enabled in the browser, and shows its next run.

### TASK-069 — Frontend comparison, alerts, change-status filter and export

- **dependencies:** TASK-067, TASK-068
- **risk_level:** medium

**Description.**

- Regenerate the API types.
- Comparison view for two scans: changes by kind, the ports that were compared, the addresses that are not comparable, and the dataset-change flag on enrichment changes.
- Alert list with read state. An unread indicator on every page, which follows the read state and shows a new alert without a reload.
- The "change status" filter in the filter bar, mirrored in the URL.
- Export menu on the host table, the comparison view and the traceroute detail, for CSV and JSON with the active filters.

**expected_files.**

- `Assesment/frontend/src/api/schema.d.ts` (regenerated)
- `Assesment/frontend/src/router/index.ts` (modified)
- `Assesment/frontend/src/stores/alerts.ts`
- `Assesment/frontend/src/views/ComparisonView.vue`
- `Assesment/frontend/src/views/AlertsView.vue`
- `Assesment/frontend/src/components/AlertIndicator.vue`
- `Assesment/frontend/src/components/ExportMenu.vue`
- `Assesment/frontend/src/App.vue` (modified)
- `Assesment/frontend/src/components/FilterBar.vue` (modified)
- `Assesment/frontend/src/composables/useHostFilters.ts` (modified)
- `Assesment/frontend/src/composables/useEventStream.ts` (modified)
- `Assesment/frontend/src/stores/alerts.spec.ts`
- `Assesment/frontend/src/components/AlertIndicator.spec.ts`

**Acceptance.**

- Check 4.10, frontend test: the unread indicator follows the read state; an alert-created event raises the count without a reload.
- The export menu requests the export with the filters that are active in the table.
- A stored diff opens from its alert.

### TASK-070 — Lab, load and audit-completeness checks

- **dependencies:** TASK-064, TASK-067
- **risk_level:** medium

**Description.**

- Lab test: with the minimum interval lowered for the test, a schedule runs twice against the lab, and a port is opened on a target between the runs.
- Load test of the comparison on a seeded database. The seeding tool is extended to the tables of Phases 3 and 4.
- An audit-completeness test: it performs every action in the FR-AUD-1 list once and finds the entry for each.

**expected_files.**

- `Assesment/backend/tests/lab/test_scheduled_change.py`
- `Assesment/backend/tests/load/test_comparison_time.py`
- `Assesment/backend/tests/integration/test_audit_completeness.py`
- `Assesment/backend/scripts/seed_envelope.py` (modified)

**Acceptance.**

- Check 4.12: comparing two 256-address scans with the largest realistic port counts completes within 5 seconds (NFR-PRF-5).
- Check 4.13: the port opened between the two runs produces an alert whose diff shows that port.
- Every action in FR-AUD-1 has an audit entry and a test.

### TASK-071 — Operator guide, demonstration and Phase 4 exit checks

- **dependencies:** TASK-069, TASK-070
- **risk_level:** medium

**Description.**

- Extend the operator guide with the minimum schedule interval and with what the operator sees when a schedule is flagged.
- Write the demonstration script for check 4.14 and carry it out.
- Confirm check 4.10 by hand on every page.
- Go through the common criteria of delivery plan section 2.2 and record the result of each of checks 4.1 to 4.14.

**expected_files.**

- `Assesment/docs/operator-guide.md` (modified)
- `Assesment/docs/demo/phase-4.md`

**Acceptance.**

- Check 4.14: a schedule with its next run shown, a manual comparison, an alert opened from the indicator, an export opened in a spreadsheet.
- Check 4.10: the unread indicator is on every page.
- Every check from 4.1 to 4.14 is recorded as passed, with none skipped. AC 9, AC 10 and AC 11 are met; AC 1 is met in full.

## 4. Coverage of Phase 4

| Phase 4 check | Task |
| --- | --- |
| 4.1 Diff rules | TASK-061 |
| 4.2 Different port profiles | TASK-061 |
| 4.3 Dataset changes | TASK-061 |
| 4.4 Schedule arithmetic | TASK-062 |
| 4.5 Run at the planned time | TASK-064 |
| 4.6 Skip | TASK-064 |
| 4.7 Catch-up | TASK-064 |
| 4.8 Scheduled scope check | TASK-064 |
| 4.9 Baseline and alert | TASK-063 |
| 4.10 Alerts in the interface | TASK-069 (frontend test), TASK-071 (manual) |
| 4.11 Export | TASK-067 |
| 4.12 Comparison time | TASK-070 |
| 4.13 Scheduled change detection | TASK-070 |
| 4.14 Demonstration | TASK-071 |

| Common criterion (delivery plan 2.2) | Task |
| --- | --- |
| 1. CI green | Every task |
| 2. Clean start and demonstration | TASK-071 |
| 3. Migrations apply, revert, apply; earlier data kept | TASK-060 |
| 4. Audit entry in the same transaction, with a test | TASK-064, TASK-065; the whole FR-AUD-1 list in TASK-070 |
| 5. Deletion purge covers every new table | TASK-060 |
| 6. Route-enumeration test covers new endpoints | TASK-065, TASK-066, TASK-067 |
| 7. No failed or skipped check | TASK-071 |
| 8. Operator guide | TASK-071 |
