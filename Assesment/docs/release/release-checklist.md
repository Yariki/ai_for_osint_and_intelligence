# Release Checklist — Release 1.0.0-rc.1

| | |
| --- | --- |
| Release | 1.0.0-rc.1 |
| Date | 2026-11-19 |
| Audience | Product owner; release sign-off |
| Decision | **Not ready for release.** Gate 0 and the lab checks of every phase have now run on the lab host with the release images, and all passed (1B.11 after the product owner's decision, section 2a). The load checks have run at the full envelope; one defect they found is fixed (defect 10). Still not run: the acceptance run on a clean host (5.1), restore on a second host (5.7) and every check in a browser. Decisions of the product owner are open (sections 2 and 5). |

## 1. Phase 5 checks

| Check | Record | State |
| --- | --- | --- |
| 5.1 Acceptance run | `acceptance-run.md` | **Not run**: needs a clean host and the authorized range; the lab host is now available |
| 5.2 Full-range endurance | `capacity-report.md`, section 6; `tests/load/test_endurance.py` | **Passed** on the lab, 2026-11-19: exact, 0 receive drops over 33.2 million probes, through a restart of the whole stack. 3 h 18 min, longer than the launch estimate shows (defect 8). Passed again after TASK-078 on the rebuilt images: 3 h 20 min |
| 5.3 Queries at the envelope | `capacity-report.md`, `tests/load/test_queries_release.py`, `test_queries_envelope.py` | **Passed** at the full envelope, 2026-11-19, after defect 10 was fixed: every list and filter 0.01–0.83 s, comparison 0.64 s |
| 5.4 Storage | `capacity-report.md`, `tests/load/test_storage_size.py` | **Done** at the full envelope, 2026-11-19: 56 GB for the database; one deviation above 50 % reported (`enrichment_snapshot`, at its upper bound) |
| 5.5 Hosts open on every port | `capacity-report.md`, section 5; `tests/load/test_all_ports_open.py` | **Passed** on the lab, 2026-11-19: about 3,780 port rows a second; about 34 MB lasting growth per scan |
| 5.6 Deletion | `posture-report.md`, `tests/integration/test_deletion_complete.py` | **Passed**, on the pinned images |
| 5.7 Backup and restore | `backup-restore-record.md` | **Not run**: needs the deployment and a second host |
| 5.8 Operability | `posture-report.md`, `deploy/checks/inspect-release.sh` | **Passed** on the running stack, 2026-11-19, after defect 9 was fixed |
| 5.9 Container and network posture | `posture-report.md`, `inspect-release.sh --dependencies` | **Passed** on the running stack, 2026-11-19; dependency scan: nothing at the proposed blocking level, which is not yet agreed |
| 5.10 Browsers | `acceptance-run.md`, section 5 | **Not run** |

**Reservations A and B, lifted on 2026-11-18.** Until then the database tests ran against a stand-in for the pinned `postgis/postgis:17-3.5`, and the broker tests were written and not run, for lack of Docker. On 2026-11-18 the whole backend suite ran on the pinned images: 1,478 passed, none failed. Defects 4 and 6 below were found by that run. On 2026-11-19, with the fixes of this phase, it is 1,483 passed, none failed.

**Gate 0 and the lab, 2026-11-18 and 19.** Gate 0 is go (`docs/spike/phase-0-report.md`). Every lab check of Phases 1B to 4 ran on the lab host with the release images (the phase records, `docs/demo/`): all passed but 1B.11. After TASK-078 changed the images, the release images were rebuilt and the stack migrated (0006); on them the whole lab suite ran again with the same result (31 passed, 1B.11 failed on the same host, before the decision; the engine smoke test, which needs NET_RAW, 4 of 4 in the image as documented), `inspect-release.sh` passed (5.8, 5.9), and 5.5 ran again (capacity report, section 5).

## 2. Question Q1: hosts that answer on every port

**The question** (architecture section 19): a /24 of hosts open on every port, scanned with the top-1000 profile every hour, writes millions of port rows a day and exhausts the A7 envelope in weeks. Should stored open ports per host be capped, with the host flagged as "responds on all ports"? A cap would relax FR-SCN-2.

**The measurement** (capacity report, section 5, on the lab): one such scan stores 256,000 open ports in 68 s, about 3,780 rows a second, which the stack sustained without difficulty. It leaves about 34 MB on disk. Hourly, that is 6.1 million rows and about 0.8 GB a day, and it reaches the 250 million open ports of A7 in about 41 days on its own.

**Options for the product owner:**

1. **No cap** (the architecture's proposed answer). Every open port is stored, as FR-SCN-2 requires. The operator guide warns about the case (section 2); an operator can lower the frequency of such a schedule. Storage beyond A7 is the operator's to provide.
2. **A cap with a flag.** Above a number of open ports per host (for example 500), the host is stored as "responds on all ports" with a count instead of each port. It changes FR-SCN-2, the port-observation write and the diff of such hosts. It would be a new task (TASK-078 onward).

**Decision:** not yet taken. To be recorded here with its date and the name of who decided.

## 2a. Question: hosts that only ping, when the scanner has no NET_RAW (check 1B.11)

**What happens.** Without NET_RAW the scanner can send neither SYN probes nor ICMP echo. It falls back to connect scans (FR-CFG-6), and discovery is left with TCP probes alone. A host that answers ping and drops probes to closed ports, like lab host 10.77.3.21 (open 21, 3306, 5432; its discovery ports 80 and 443 dropped), is then never found, so its open ports are not scanned. Nothing on the scan says that ICMP discovery was unavailable. Check 1B.11 expects the same results as the SYN scan and fails on that one host; every other host matches.

**Options for the product owner:**

1. **Record and warn** (proposed). The scan records "ICMP discovery unavailable"; the scan page and the export show it; check 1B.11 compares against the hosts that connect-mode discovery can find. No default changes.
2. **Scan all targets when ICMP is unavailable.** The scan port-scans every target regardless of discovery, which keeps 1B.11 literal but overrides the analyst's FR-CFG-3 choice and makes such scans of sparse ranges much slower.

Either is a new task. **Decision (2026-11-19, the product owner): option 1, record and warn.** Implemented by TASK-079: the scan records `icmp_discovery_unavailable`, the workspace shows a warning, every export row carries it; the spec's AC 2 and check 1B.11 now compare connect mode against the hosts TCP discovery can find. Check 1B.11 passed on the lab on the rebuilt images.

## 3. Requirement coverage (delivery plan section 7)

Every "must" requirement needs a passed check. Each group below names the checks that are still open and bear on it; a group with none open has every check passed (some with reservation A or B).

| Requirements | Phase | Open checks that bear on them |
| --- | --- | --- |
| FR-ACC-1 to 4, FR-PRJ-1 to 5, FR-AUD-2, 3, NFR-SEC-3 to 5 | 1A | 1A.9 (demonstration) |
| FR-PRJ-6, FR-PRJ-7, NFR-SEC-1, NFR-SEC-6 | 1A, 1B, 3, 4 | None |
| FR-CFG-1 to 7, 9 | 1B, 3, 4 | None: 1B.11 passed as decided (section 2a). FR-CFG-9's estimate is low for sparse full-range scans (defect 8, a "should") |
| FR-SCN-1 to 10 | 1B to 4 | 1B.19 (demonstration) |
| NFR-SEC-2 | 0, 1B | None: Gate 0 check 0.6; 5.9 on the running stack |
| NFR-PRF-1 to 3 | 0, 1B, 5 | None: Gate 0, 1B.10, 1B.15, 1B.16, 5.2 |
| NFR-PRF-4 | 2, 5 | 2.12 (map under load in a browser); 2.13 and 5.3 passed at the full envelope |
| NFR-PRF-5 | 4 | None: 4.12 passed at the full envelope |
| NFR-REL-1, 2, 4 | 1B | None: 1B.7, 1B.13, 1B.14, 5.2 |
| FR-ENR-1 to 8 | 2, 3 | 2.15 (demonstration) |
| FR-MAP-1 to 11 | 1B to 4 | 1B.18 and 2.11 partly (the browser part), 2.12, 4.10 by hand |
| FR-CLS-1 to 7 | 3 | 3.13 (demonstration) |
| FR-TRC-1 to 7 | 3 | 3.10 partly (the browser part), 3.13 |
| FR-SCH-1 to 6, FR-HIS-2 to 8 | 4 | 4.14 (demonstration) |
| FR-HIS-1, 9 | 1B | 1B.19 |
| FR-EXP-1 to 4, FR-CFG-8 | 4 | 4.14 (export opened in a spreadsheet) |
| FR-AUD-1 | All | None: `tests/integration/test_audit_completeness.py` passed |
| NFR-OPS-1, 4 | All | None |
| NFR-OPS-2, 3 | 1A, 1B, 2, 5 | None: 5.8 on the running stack |
| NFR-OPS-5 | 5 | 5.7 |
| NFR-LIC-1, 2 | 2, 5 | None: 2.14 on the running stack and its images; NFR-LIC-2 inventory in `docs/licences.md`, checked by `tests/unit/test_licences.py` |

**Result:** section 7 of the delivery plan still has "must" requirements without a passed check. What is open is now of one kind: the demonstrations and the browser checks (a browser, the authorized range).

## 4. Defects

| # | Found | What | State |
| --- | --- | --- | --- |
| 1 | 5.3, on the seeded worst case | The change-status filter on a scan of hosts open on every port took 1.12 s, above the 1-second limit of NFR-PRF-4; the comparison 1.39 s. The diff read one row per open port. | **Fixed** in this phase: ports are read per host, aggregated. 0.29–0.31 s and 0.55–0.59 s. Tests unchanged and passing. |
| 2 | 5.4 | `enrichment_snapshot` is about twice the estimate of architecture section 10.3 at its upper bound. | **Reported**, not a defect of a requirement: the operator guide now names about 80 GB for the database at A7. |
| 3 | TASK-072 | `postgis/postgis:17-3.5` is a moving tag within PostgreSQL 17 and PostGIS 3.5. | **Mitigated**: the build script records the digest of every image; `VERSIONS.md` explains how to pin by digest. |
| 4 | Real RabbitMQ (1B.7) | On RabbitMQ 4 a quorum queue does not count a message rejected with requeue towards its delivery limit. A result the orchestrator kept failing on, or a chunk the scanner kept failing on, was delivered again for ever instead of reaching the dead-letter queue (NFR-REL-4). | **Fixed**: the orchestrator's result handler and the scanner count failed deliveries per message (`broker.FailedDeliveries`) and reject it to the dead-letter queue at the limit. `test_broker.py`, `scanner/test_chunk_failure.py`. |
| 5 | Lab (1B.11) | The scanner could not start without NET_RAW: the kernel refuses to run the interpreter copy that carries the capability when the capability is not in the container's bounding set. The connect fallback (FR-CFG-6) never got to run. | **Fixed**: a launcher (`ndm.scanner.start`) picks the capable interpreter only when NET_RAW is available. `tests/unit/scanner/test_start.py`; on the lab, the scanner reports SYN and ICMP unavailable and scans in connect mode. |
| 6 | Real PostgreSQL image (migrations) | The image's own start-up script installed `postgis_topology`, `fuzzystrmatch` and the TIGER geocoder into the product database: about 60 tables and two schemas that nothing uses, carried by every backup. | **Fixed**: `deploy/postgres/no-postgis-init.sh` replaces that script; migration 0003 installs PostGIS alone. `test_migrations.py`, `test_compose.py`. |
| 7 | Lab (recovery) | The orchestrator and the scanner run as process 1 and ignored SIGTERM, so every `docker compose stop`, restart or upgrade waited 10 seconds and then killed them. | **Fixed**: `init: true` for both; they stop in under a second. Recovery after a kill is unchanged and passed (1B.13, 1B.14). `test_compose.py`. |
| 8 | 5.2 | The launch estimate (FR-CFG-9) counts the time of the probes alone. Each chunk also waits out the reply timeout after each round, one chunk after the other: the full-range scan of the lab's /24 took 3 h 18 min against an estimate of at most 1 h 52 min. | **Reported**, not fixed: a "should" requirement, no acceptance criterion broken. Two remedies, either a new task: add the waits to the estimate, or let the scanner send the next chunk while one waits (capacity report, section 6). |
| 9 | 5.8, on the running stack | The api and the scanner logged nothing about scans or traceroutes on the normal path, only on cancellation, refusal and failure, so NFR-OPS-2's "in all three processes" could not be shown. | **Fixed**: `scan_created`, `traceroute_created`, `chunk_done` and `traceroute_done`, each with its identifier. `test_scans.py`, `test_traceroutes.py`, `scanner/test_log_identifiers.py`. The two errors of the inspection scripts found on the way are in `posture-report.md`, section 6. |
| 10 | 5.3 and 2.13, at the full envelope | The project-wide view of 65,536 addresses took 17.7–42.4 s and the project's map points 8.0 s, against the 1-second limit of NFR-PRF-4: the join from `project_host` to each address's latest observation read all 50 million observations. | **Fixed** by TASK-078, the fallback of architecture 10.2: a copy of what the view shows on `project_host`, kept current by every writer, migration 0006 to fill it. 0.04–0.21 s, map points 0.46 s. `test_project_view_copy.py`, `test_migration_0006.py`; the copy matched the join on every address of the envelope and of the lab. |
| 11 | Lab (1B.11) | Without NET_RAW a scan's discovery has no ICMP echo, so a host that answers ping only is not found, and nothing said so. | **Fixed as decided** (section 2a) by TASK-079: the scan records and shows it. `test_queue_cancel_lock.py`, `test_export.py`, `test_migration_0007.py`, `ProjectWorkspaceView.spec.ts`; 1B.11 passed on the lab. |

No open defect is known that breaks an acceptance criterion or a "must" requirement. Defect 8 is open and bears on a "should". That statement covers what has run, now including Gate 0 and the lab; the checks of section 1 that are not run may find more.

## 5. What remains before release

1. The product owner's decisions: Q1 (section 2), Q4 on host preparation (`docs/spike/phase-0-report.md`, section 5), the blocking severity of the dependency scan (`posture-report.md`, section 4), and the earlier ones still open (registry records opt-in, the tie rule of classification, the decisions listed in the phase records). Whether defect 8 is fixed before release.
2. Check 5.1, the acceptance run on a clean host with the authorized range, with checks 5.7 (restore on a second host), 5.8 and 5.9 (`inspect-release.sh --dependencies`) and 5.10 (browsers) on the same deployment, and the demonstrations and browser checks of every phase (1A.9, 1B.18, 1B.19, 2.11, 2.12, 2.15, 3.10, 3.13, 4.10, 4.14).
3. Optionally, `net.core.rmem_max` raised to 8,388,608 on the lab host (it needs root); Gate 0 showed no drop without it.
4. Every result recorded in this file, and the release tagged `ndm-v1.0.0` from the commit that passed.
