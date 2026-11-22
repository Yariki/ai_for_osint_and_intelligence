# Acceptance Run — Release 1.0.0-rc.1

| | |
| --- | --- |
| Release | 1.0.0-rc.1 (`deploy/release/VERSIONS.md`, digests in `deploy/release/digests-1.0.0-rc.1.txt`) |
| Checks | 5.1 (AC 1 to AC 11 in one run), 5.10 (browsers) |
| Audience | Product owner; the person who carries out the run |
| State | **Not carried out.** Needs a clean host, the lab host, the authorized public range and two browsers. |

## 1. Rules of the run

- **Clean host, guide only.** The release candidate is installed on a host that has never run the product, by following `docs/operator-guide.md` and nothing else: no repository knowledge, no help from the developers. Whatever the guide does not say, and had to be found out, is a **defect of the guide**: record it in section 4 and raise it as a task from TASK-078.
- **One run.** AC 1 to AC 11 are run one after the other on that one deployment, without reinstalling in between. A failed criterion stops the run; after the fix the whole run starts again.
- **Evidence.** For each criterion keep what the "Evidence" column names, under `docs/release/evidence/` (screenshots, exports, test output, captures).
- **Images.** The images are the release images, loaded from the release artifact or built by `deploy/release/build-images.sh` from the tag. Record their digests against `digests-1.0.0-rc.1.txt` in section 2.

## 2. The deployment

| | Value |
| --- | --- |
| Host (operating system, kernel, CPU, memory, disk) | |
| Docker Engine and Compose versions | |
| Images: digests match `digests-1.0.0-rc.1.txt` | |
| Date and time of installation (UTC) | |
| Operator | |
| Guide sections followed, in order | |
| Time from start to a healthy stack | |

## 3. AC 1 to AC 11

The checks named are those of the delivery plan; the tests named run against this deployment with `NDM_LAB=1` where they are lab tests.

| AC | What is done | Evidence | Result |
| --- | --- | --- | --- |
| 1. No scan outside the scope or public space; rejections audited | Launch a scan with a target outside the scope, one with a private address, one on the never-scan list; start a traceroute outside the scope; narrow the scope under a schedule and let it run (phase 4 demonstration, steps 3 and 11) | Each refusal on screen; the audit log entries `scan.rejected` (api and scheduler), `traceroute.rejected`, `schedule.rejected` | |
| 2. Exact open ports, SYN and connect, technique recorded | `tests/lab/test_accuracy.py` against the lab; one scan with the scanner's raw sockets and one without | Test output; the scan detail showing `syn` and `connect` | |
| 3. No application-layer payload | `tests/lab/test_no_payload.py` with the capture | Test output and the capture file | |
| 4. Recovery without duplicates or gaps | `tests/lab/test_recovery.py`: scanner killed; whole stack restarted | Test output | |
| 5. Enrichment fields and versions; no enrichment egress but reverse DNS | Phase 2 demonstration steps 7 and 11 on the authorized range; `tests/lab/test_enrichment_egress.py` | Screenshot of a host detail; `inspect-2.sh` output; egress test output | |
| 6. Classification with evidence; override persists | Phase 3 demonstration steps 2, 8 and 10 | Screenshots of the detail before and after a second scan | |
| 7. Live map within 2 s; co-located and unlocated hosts | `tests/lab/test_live_map.py`; phase 2 demonstration steps 5 and 6 | Test output with the measured delays; screenshots | |
| 8. Traceroute by three methods, live, on the map, in history | `tests/lab/test_traceroute_methods.py`, `test_live_hops.py`; phase 3 demonstration steps 4 to 6 | Test output; screenshot of the path; the host's traceroute list | |
| 9. Scheduled run, skip, alert on changes | `tests/lab/test_scheduled_change.py`; phase 4 demonstration steps 2, 4, 6 and 7 | Test output; screenshots of the schedule, the skipped run and the alert | |
| 10. Port changes only for ports probed in both | Compare a top-100 and a top-1000 scan of the same targets (phase 4 demonstration, step 5) | Screenshot of "Ports compared"; the diff export | |
| 11. CSV and JSON export with metadata | Export hosts, a diff and a traceroute, as CSV and JSON; open the CSV in a spreadsheet | The six files; a screenshot of the spreadsheet | |

Also on this deployment, for the release record: `deploy/checks/inspect-release.sh --dependencies` (checks 5.8, 5.9) and the backup (`docs/release/backup-restore-record.md`, check 5.7).

## 4. Defects of the guide and of the product found during the run

| # | Found at | What | Task | Fixed and run again |
| --- | --- | --- | --- | --- |
| | | | | |

## 5. Browsers (check 5.10)

The demonstration scripts `docs/demo/phase-1a.md` to `phase-4.md`, each carried out in both browsers on this deployment. The browser versions are current releases on the day of the run (assumption A5).

| Script | Chromium (version) | Firefox (version) | Notes |
| --- | --- | --- | --- |
| Phase 1A | | | |
| Phase 1B | | | |
| Phase 2 | | | |
| Phase 3 | | | |
| Phase 4 | | | |

## 6. Result

Not yet recorded. Check 5.1 passes when every row of section 3 is "passed" in one run and section 4 holds no open defect that breaks a criterion; check 5.10 when every cell of section 5 is "passed".
