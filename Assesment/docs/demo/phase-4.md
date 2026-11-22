# Phase 4 — Demonstration Script and Exit Checks

Part 1 is the written demonstration for check 4.14. Part 2 is the record of checks 4.1 to 4.14 and of the common criteria of delivery plan section 2.2.

## Part 1. Demonstration (check 4.14)

**You need.** The stack of the Phase 3 demonstration, started with `NDM_MIN_SCHEDULE_INTERVAL_SECONDS=600` in `.env` so that the demonstration does not take hours; a project whose scope is the authorized range, with one completed scan; a browser; and a spreadsheet program.

| # | Step | Expected result |
| --- | --- | --- |
| 1 | In the project, open **Schedules**, press **New schedule**. Name it, enter a part of the range as targets, choose "Every few hours" and 0.05 hours (3 minutes). Save. | Refused: the interval field says the runs would be 3 minutes apart and the minimum is 10 minutes. |
| 2 | Choose a scan profile in the dialog (or save the current parameters as one first). Set 0.2 hours (12 minutes). Save. | The schedule is listed with its next run, about 12 minutes from now, and "not run yet". |
| 3 | Edit the schedule and add a target outside the scope. Save. | Refused: the target field names the address and says it cannot be scheduled. The audit log has `schedule.rejected`. |
| 4 | Wait for the first run. | The last run shows "scan N completed"; the link opens the workspace on that scan. |
| 5 | In **Scans**, press **Compare**, choose the earlier manual scan and the scheduled scan. | The changes are grouped by kind; the compared ports and the addresses that are not comparable are listed; an enrichment change made under a new dataset version says "dataset updated". |
| 6 | Wait for the second run. If anything changed on the range, an alert is raised. | The bell in the top bar shows 1 without a reload. |
| 7 | Press the bell, open the alert. | The alert is marked read, the count goes down, and the stored diff of the two runs opens. |
| 8 | In the workspace on the second scheduled scan, set the filter "Change status: Changed since the previous scan". | Table and map show the changed hosts only; the URL holds the filter. |
| 9 | In the host table, **Export** → CSV. Open the file in a spreadsheet. | One row per host of the filtered table, with the scan identifier, scan time, vantage point and dataset versions in every row. A reverse-DNS name beginning with `=` shows as text. |
| 10 | Disable the schedule on the Schedules page. Enable it again. | Disabled, no next run shown; enabled, the next run is counted from now, not caught up. |
| 11 | Narrow the project scope so that it no longer covers the schedule's targets, and wait for the next run. | The schedule shows "stopped: targets_out_of_scope"; an alert says the run was rejected and the schedule flagged; the audit log has `scan.rejected` by `system` with the point `scheduler`. No scan was created. |
| 12 | Visit every page: projects, a project, scans, schedules, workspace, comparison, alerts, audit log, datasets, password. | The bell is in the top bar on each one (check 4.10, by hand). |

**Result:** not yet carried out.

## Part 2. Record of exit checks

| Check | How it is run | Result | Date | Note |
| --- | --- | --- | --- | --- |
| 4.1 Diff rules | `tests/unit/orchestrator/test_diff_rules.py` with `tests/fixtures/diff/scan_pairs.json` | Passed | 2026-11-17 | Every kind of change, and addresses targeted by one scan only. |
| 4.2 Different port profiles | `test_diff_rules.py`; `tests/integration/orchestrator/test_diff_profiles.py` | Passed, reservation A | 2026-11-17 | A top-100-like and a larger profile: port changes only for ports probed in both; a host not port-scanned in one scan gives a host event only. |
| 4.3 Dataset changes | `tests/integration/orchestrator/test_diff_dataset_flag.py` | Passed, reservation A | 2026-11-17 | |
| 4.4 Schedule arithmetic | `tests/unit/orchestrator/test_schedule_math.py` | Passed | 2026-11-17 | Intervals and cron in UTC, Kyiv, New York, Tokyo and Lord Howe (a half-hour daylight-saving shift); both clock changes; a year of runs only moves forward; minimum checked over the coming cron runs. |
| 4.5 Run at the planned time | `tests/integration/orchestrator/test_scheduler.py` | Passed, reservation A | 2026-11-17 | With the clock given to the scheduler: not due one second before; started within one 15-second tick. |
| 4.6 Skip | `test_scheduler.py` | Passed, reservation A | 2026-11-17 | |
| 4.7 Catch-up | `test_scheduler.py` | Passed, reservation A | 2026-11-17 | Interval and cron; also four scheduler passes at once start one run. |
| 4.8 Scheduled scope check | `tests/integration/orchestrator/test_scheduled_scope_check.py` | Passed, reservation A | 2026-11-17 | Also the never-scan list and a project without a scope. Nothing reaches the scanner: no scan row, no chunk published. |
| 4.9 Baseline and alert | `tests/integration/orchestrator/test_baseline_alert.py`; `test_failure_alerts.py` | Passed, reservation A | 2026-11-17 | Cancelled, failed, partial, deleted runs and other schedules are passed over; a manual scan runs no diff phase; running the phase twice stores one diff. Failed scheduled runs and dead-lettered messages raise one alert each. |
| 4.10 Alerts in the interface | frontend `AlertIndicator.spec.ts`, `alerts.spec.ts`, `App.spec.ts`, `ComparisonView.spec.ts`; by hand | **Partly run** | 2026-11-18 | Frontend: the count follows the read state, rises on an alert-created event without a reload, is loaded again after a reconnect; the indicator is in the top bar of the application shell; a stored diff opens from its alert. By hand on every page: not run (no browser). |
| 4.11 Export | `tests/integration/api/test_export.py` | Passed, reservation A | 2026-11-17 | Host (five filter and order combinations), project, diff (computed and stored) and traceroute exports, CSV and JSON; formula escaping; 401 without a session. |
| 4.12 Comparison time | `tests/load/test_comparison_time.py` | Passed | 2026-11-19 | The pair is the full worst case at every scale: two top-1000 scans of a /24, every address open on every port, 64,000 closed ports and 128 enrichment changes between them. First run (2026-11-18, 0.05 % of the envelope): median 1.39 s, limit 5 s; export 2.69 s; then 0.55–0.59 s after the diff was changed to read each host's ports as one row (Phase 5, capacity report). At the full envelope on 2026-11-19 (pinned database image, the product's limits): 0.64 s, its CSV export of 64,129 lines 2.2 s. |
| 4.13 Scheduled change detection | `tests/lab/test_scheduled_change.py` | Passed | 2026-11-19 | A port opened on a lab host between two scheduled runs raised an alert naming that port. |
| 4.14 Demonstration | Part 1 | **Not run** | | Needs the stack, a browser and the authorized range. |

**Reservations A and B, lifted on 2026-11-18.** The whole backend suite, the tests of this phase among them, ran on the pinned `postgis/postgis:17-3.5` and RabbitMQ images: 1,478 passed, none failed. The rows above that still name A or B passed again on the real images.

**Reservation A: the database.** PostgreSQL 18 with PostGIS 3.6, unpacked from the distribution's packages, stood in for the pinned `postgis/postgis:17-3.5`.

**Reservation B: the broker.** No RabbitMQ. The Phase 4 code publishes events through the existing paths; the tests that need a real broker were not run.

**What was run, on 2026-11-17 and 2026-11-18.** 1,407 backend tests (lab and load suites skipped; the 27 load checks were run separately at 0.05 % of the envelope and passed) and 187 frontend tests passed; ruff, mypy, the import rule, ESLint, vue-tsc and the frontend build were clean. pip-audit and npm audit were not run in this phase (no network); the one new dependency is `cronsim` (BSD licence, no dependencies of its own).

### Common criteria (delivery plan 2.2)

| Criterion | State |
| --- | --- |
| 1. CI green | Not observed. Locally, every step that needs no Docker passes. |
| 2. Clean start and demonstration | Not run: needs Docker. |
| 3. Migrations apply, revert, apply; earlier data kept | Passed (A): `test_migration_0005.py`. |
| 4. Audit entry in the same transaction, with a test | Passed: schedule creation, change, enabling, disabling and deletion; profile changes; rejection at save time (`test_schedules.py`, `test_profiles.py`); the scheduler's launch and rejection as `system` (`test_scheduler.py`, `test_scheduled_scope_check.py`). `tests/integration/test_audit_completeness.py` performs every action of FR-AUD-1 once and finds each entry. |
| 5. Deletion purge covers every new table | Passed: `test_purge.py` with schedules, runs, diffs, change events and alerts. |
| 6. Route-enumeration test covers new endpoints | Passed: `test_route_auth.py`; the export variants are covered by `test_export.py`. |
| 7. No failed or skipped check | **Not met**: 4.10 by hand, 4.12 at the full envelope, 4.13 and 4.14. |
| 8. Operator guide | Done: section 7 (`NDM_MIN_SCHEDULE_INTERVAL_SECONDS`) and section 13 (schedules, a flagged schedule, comparison, alerts, export). |

### Notes for the product owner

- **The change-status filter** is relative to one scan: in the whole-project view it is not offered, and the API refuses it there. For a scheduled scan the previous scan is the baseline of its automatic diff; for a manual scan, or the first run of a schedule, it is the latest earlier completed scan of the project (Q3). With the filter set, the table also shows hosts that disappeared, which are otherwise hidden as unreachable.
- **A flagged schedule stays stopped until it is saved again**, also when the scope is widened again in the meantime. Saving is the analyst's confirmation that the targets are right.
- **Enabling a disabled schedule does not catch up**: its next run is counted from the moment it is enabled.
- **Every message taken from the dead-letter queue raises an alert**, also when its scan had already ended or the message names no scan; the latter is an alert of the installation, shown to every account.
- **Additions beyond the task list:** a list of a schedule's runs (`GET /schedules/{id}/runs`), the automatic diff of a scan (`GET /scans/{id}/diff`) and its baseline (`GET /scans/{id}/baseline`); scan-profile changes are audited, although FR-AUD-1 does not ask for it.
- **`NDM_MIN_SCHEDULE_INTERVAL_SECONDS`** is now passed by `deploy/compose.yaml` with the other operator ceilings (`x-scan-settings`), and listed in `.env.example`. It was added to the settings in TASK-060 but had not been wired into Compose.

**Also open from earlier phases.** Gate 0, checks 1A.8 and 1A.9, 1B.10 to 1B.19, Phase 2's 2.7, 2.8, 2.10 to 2.15, and Phase 3's 3.6 to 3.8, 3.10 and 3.13.

**Phase 4 is not complete until every check above is recorded as passed.**
