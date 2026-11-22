# Load and Capacity Report — Release 1.0.0-rc.1

| | |
| --- | --- |
| Release | 1.0.0-rc.1 |
| Date | 2026-11-19 |
| Checks | 5.2, 5.3, 5.4, 5.5 (delivery plan, Phase 5) |
| Audience | Product owner, operators |

## 1. Summary

| Check | What | State | Result |
| --- | --- | --- | --- |
| 5.2 Full-range endurance | 16.8 million probes at the ceiling, stack restarted halfway | **Passed on the lab** | Completed, exact, 0 receive drops over 33.2 million probes (with retries) in 3 h 18 min, through a restart of the whole stack at 1 h 38 min. Takes longer than the launch estimate shows (section 6). |
| 5.3 Queries at the envelope | Every list, filter, timeline and comparison endpoint | **Passed at the full envelope**, after a fix | It first failed: the project-wide view took 17.7–42.4 s (limit 1 s). Fixed by TASK-078; now every list and filter 0.01–0.83 s, comparison 0.64 s (limit 5 s) (section 3). |
| 5.4 Storage | Sizes at A7 against architecture 10.3 | **Measured at the full envelope** | Port and host observations as estimated. **`enrichment_snapshot` is above the estimate by more than 50 %** at its upper bound (section 4). The database at A7: 56 GB. |
| 5.5 Hosts open on every port (Q1) | Write rate and storage growth | **Run on the lab** | About 3,780 open-port rows a second; about 34 MB lasting growth per scan, 0.8 GB a day if scanned hourly; A7's open ports full in about 41 days (section 5). |

None of this ran on the reference hardware. All of it ran on the lab host (section 1 of `docs/spike/phase-0-report.md`; 20 cores, 61 GB of memory, NVMe disk) on 2026-11-19: checks 5.2 and 5.5 against the release stack on the lab, checks 5.3 and 5.4 against a database of the pinned `postgis/postgis:17-3.5` image seeded to the full envelope and given the product's limits (1 GB of memory, 2 CPUs, as `deploy/compose.yaml`). An earlier run of 5.3 and 5.4 at 0.2 % of the envelope (2026-11-17, on a stand-in database; repeated on the pinned image) had passed; at full scale 5.3 failed, as below. The commands are in section 7.

**Defects found and fixed during this work.**

- **The project-wide view at the envelope (TASK-078).** The view of a project of 65,536 addresses took 17.7–42.4 seconds for every filter, its last page 18.6 s, the project's map points 8.0 s: NFR-PRF-4 allows 1 second. The view joined `project_host` to the observation of each address's latest scan. The planner does not see that this join gives one row per address; it costs a lookup into the 16 hash partitions of `host_observation` as a probe of all 16, and reads all 50 million observations instead (12.6 s for the count alone). Forcing the lookup per address brought each statement to 0.7–1.2 s, still too slow for a page, which needs two. The fallback of architecture 10.2 is applied: `project_host` holds a copy of what the view shows of the latest observation, every writer keeps it current in its own transaction, and the view reads that table alone. After it: 0.04–0.21 s. Migration 0006 fills the copy of an existing installation; at the full envelope it took 27 s. The writers pay for it: refreshing the copy of a /24 takes 0.07 s, of a /16 (65,536 addresses, at the start of such a scan and at the end of its probing) about 3.5 s, with the product's limits; the write rate of check 5.5 fell by 6 % (section 5).
- **The comparison of hosts open on every port.** The comparison read every open port of both scans as one row each. On the pair of check 4.12 (256,000 open ports per scan) it took 1.39 s, and the change-status filter, which computes the same diff, took 1.12 s, above the 1-second limit for a filter. The diff now reads one row per host with its ports aggregated (`ndm/orchestrator/diff.py`, `load_view`): comparison 0.55–0.64 s, filter 0.27–0.31 s. The tests of the diff, the comparison, the filter and the automatic diff pass unchanged. It was small enough to be fixed with the measurement and has no task of its own.
- **The seeding tool at full scale.** It stopped at the registry objects: their address ranges were computed as 4-byte integers, which overflow beyond 4.2 million objects (6 million at full scale). Fixed in `scripts/seed_envelope.py`; not a defect of the product.

## 2. The seeded database

`backend/scripts/seed_envelope.py` fills every table that holds data at rest: 50 million host observations and 250 million open ports at full scale, the enrichment snapshots and classification results of the reachable half, 20 active schedules with their runs, stored diffs and alerts, 50 traceroutes per project with hop snapshots, an audit entry per scan and per traceroute, the registry tables at the size of the five registries' data (250,000 delegations, 6 million inetnum objects), and the comparison pair of check 4.12. The tables of work in progress (`scan_chunk`, `session`) stay empty, as on an installation with nothing running.

At full scale it wrote 195,315 scans, 50,000,640 host observations, 250,448,640 open ports, 25.1 million enrichment snapshots, 25.0 million classification results and 471,040 project addresses in 100 projects (the largest of 65,536 addresses), in 1 h 05 min. The database then took 56 GB on disk.

## 3. Query times (check 5.3)

Median of three requests through the whole API, in process, on the database at the full envelope, after TASK-078.

| Endpoint | Seconds | Limit |
| --- | --- | --- |
| Scan list of a project, a page of 200 (the largest page) | 0.83 | 1 |
| Scan list of a project, the default page | 0.30 | 1 |
| Project-wide view of 65,536 addresses, no filter and each filter (reachable, open port, ASN, organization, country, name, three together) | 0.04–0.16 | 1 |
| Project-wide view, last page | 0.21 | 1 |
| Map points of the project (32,896 located) | 0.46 | 1 |
| Host table of a scan, change status "changed" | 0.30 | 1 |
| Map points of a scan, change status "unchanged" | 0.33 | 1 |
| Host table of a scan, any filter or sort | 0.02–0.05 | 1 |
| Host detail with its timeline | 0.035 | 1 |
| Traceroute list, by target, and detail | 0.012–0.056 | 1 |
| Schedules, a schedule, its runs | 0.011–0.015 | 1 |
| Stored diff, automatic diff of a scan, baseline | 0.011–0.012 | 1 |
| Alerts (all, unread, by project), unread count | 0.010–0.016 | 1 |
| Audit log (all, by action, by object) | 0.035–0.092 | 1 |
| Projects, scope, scope history, profiles, datasets, configuration | 0.006–0.015 | 1 |
| Comparison of two scans of a /24, top-1000, every port open (4.12 pair) | 0.58–0.81, median 0.64 | 5 |
| The same comparison as CSV (64,129 lines) | 2.2 | 5 |

**Before TASK-078**, on the same database: project-wide view 17.7 s (reachable) to 42.4 s (name filter), its last page 18.6 s, the project's map points 8.0 s. Everything else as above within 15 %.

**To watch.** The scan list's largest page, 0.83 s, is the closest to its limit. It is not the planner's compilation (0.87 s with JIT off); it is the per-scan figures of 200 scans. The default page takes 0.30 s. If a slower host takes the largest page over the limit, the fallback is a smaller largest page.

## 4. Storage (check 5.4)

Size per row measured on the database at the full envelope, tables and their indexes together, summed over the hash partitions of the observation tables. For the two tables seeded at half the envelope's upper bound, the size at A7 is the size per row multiplied by the upper bound.

| Table | Rows | Bytes per row (table + index) | At A7 | Estimate (architecture 10.3) | Deviation above 50 % |
| --- | --- | --- | --- | --- | --- |
| `port_observation` | 250.4 million | 103.6 (52.2 + 51.4) | 24.1 GB | 21–23 GB | No (5 % above) |
| `host_observation` | 50.0 million | 214.2 (131.1 + 83.1) | 10.0 GB | 10–15 GB | No |
| `enrichment_snapshot` | 25.1 million | 424.7 (291.3 + 133.4) | 19.8 GB at 50 million rows | 5–10 GB | **Yes** |
| `classification_result` | 25.0 million | 267.5 (147.1 + 120.4) | 12.5 GB at 50 million rows | 5–10 GB | No (25 % above) |
| `project_host`, with the copy of TASK-078 | 471,040 | about 370 | 175 MB | not estimated | — |

The 0.2 % run of 2026-11-17 had given sizes per row within 10 % of these.

**Reported deviation: `enrichment_snapshot`.** A snapshot holds the location as a PostGIS point, eight text fields and the dataset versions as JSON, with three indexes. At the upper bound of architecture 10.3, a snapshot for every one of 50 million observations, it needs about 20 GB, twice the estimate. In practice snapshots exist for reachable hosts only; at the seed's share of one half, it is 10.7 GB here, at the top of the estimate. Snapshot and classification together reach 32 GB at the upper bound, against 10–20 GB estimated.

This does not break the envelope on one PostgreSQL instance. The database seeded to A7 took 56 GB; the operator guide (section 2) names about 80 GB for the database at A7, which leaves room for the upper bound of the snapshots and for enrichment re-runs, each a new snapshot.

The seeded data makes up its values; real snapshots with long organization names, and real evidence in host observations, are larger. The figures are a lower bound for the text-heavy tables.

## 5. Hosts open on every port (check 5.5, question Q1)

**Measured on the lab, 2026-11-19** (`tests/load/test_all_ports_open.py`, scan 97): one top-1000 scan of the lab's all-ports-open /24 (`deploy/lab/allopen`, played as 10.77.4.0/24) at the ceiling of 5,000 packets per second, on the release images and the pinned `postgis/postgis:17-3.5`. Two runs gave the same figures within 1 %.

| | Measured |
| --- | --- |
| Scan time | 68 s |
| Open ports stored | 256,000 |
| **Write rate** | **about 3,780 port rows a second** for the whole scan |
| Port observations, on disk | +27.2 MB (106 bytes per row, indexes included) |
| Host observations, on disk | +69.0 MB right after the scan |
| What the scan's rows hold | 11.3 MB of port rows, 6.7 MB of host rows |
| Database, on disk | +96.6 MB right after the scan |

**The host observations.** A host's evidence holds one entry per open port, and the row is written again, in full, with every findings message (every half second), as the evidence grows. Right after the scan the table therefore also holds the dead versions of those rows: about 62 MB here. Vacuum makes that space reusable for the next scans; it does not return it to the file system. For an ordinary host with a handful of open ports the effect is negligible.

**For question Q1** (the same /24 scanned every hour):

- the lasting growth is about **34 MB per scan** (27 MB of port observations, 7 MB of host observations): about **0.8 GB a day**, a little above the 0.6 GB a day estimated from the seeded pair;
- 6.1 million port rows a day: that alone fills the 250 million open ports of A7 in **about 41 days**;
- the write rate of about 3,800 rows a second during the scan is well within what the stack sustained here; it adds no constraint of its own.

**Again after TASK-078** (scan 103, the rebuilt images): 72 s, about **3,550 port rows a second**, 6 % below the first run, because each findings message now also refreshes the project's copy of its hosts (`project_host` with each host's open ports). The port observations grew by the same 27.3 MB; the host observations by almost nothing, as the space freed after the earlier runs was reused. `project_host` stayed at 18 MB, its dead rows cleared by autovacuum.

The decision on Q1 is the product owner's (release checklist, section 2).

## 6. Full-range endurance (check 5.2)

**Passed on the lab, 2026-11-19** (`tests/load/test_endurance.py`, scan 98): every port of the lab's /24 (`10.77.3.0/24`, all 256 addresses scanned, `scan_all_targets`) at the ceiling of 5,000 packets per second, reply timeout 1.5 s, one retry, on the release images. Halfway through, `docker compose restart` restarted every service; nothing was done by hand after it.

| | Measured |
| --- | --- |
| Port-scan chunks | 1,684, plus the discovery chunk |
| Probes sent | 33,162,221 (16,776,960 before retries) |
| Receive drops | 0 |
| Restart | after 5,906 s, once half of the chunks were done |
| Duration | 11,854 s (3 h 18 min) |
| Result | `completed`, not partial; exactly the manifest's open ports, nothing missed, nothing false |

**Again after TASK-078** (scan 131, on the rebuilt images): passed again, exact, 0 receive drops over the same 33,162,221 probes, restart after 5,948 s, 11,972 s in all (1 % longer).

**Why it takes three times the 56 minutes of NFR-PRF-1.** That figure counts probes before retries. Here 249 of the 256 addresses never answer, so nearly every probe is sent twice: 33.2 million probes, 1 h 51 min of sending at 5,000 per second. And each chunk sends its probes, waits out the reply timeout, resends what is unanswered and waits again, one chunk after the other: two waits of 1.5 s per chunk, about 1 h 24 min over 1,684 chunks. While it sends, the scanner holds the ceiling; averaged over the scan, it sends 2,800 probes a second.

| | Seconds |
| --- | --- |
| Sending, 33,162,221 probes at 5,000 a second | 6,632 |
| Waiting for late replies, 1,684 chunks × 2 × 1.5 s | 5,052 |
| Restart and the chunk probed again after it | about 170 |
| **Total measured** | **11,854** |

**What it means.**

- No requirement is broken: NFR-PRF-1 counts probes before retries, and NFR-PRF-2 asks for the configured rate without dropped replies, which held while sending.
- The launch dialog's estimate (FR-CFG-9, a "should") shows the time of the probes alone, at most 1 h 52 min here, and leaves out the waits. Against sparse ranges, every port, it is too low by about 75 %. Release checklist, defect 8.
- The waits could overlap with the next chunk's sending (the scanner would take a second chunk while the first waits), which would bring this scan near its sending time. That is a change to the scanner's chunk handling, not made in this phase.

## 7. How to repeat

The database of 5.3 and 5.4 as it ran here: the pinned image with the product's own start-up scripts, its own volume, a port on the loopback address; seeded without limits, then measured with the product's.

```sh
cd Assesment/deploy
docker run -d --name ndm-envelope-db --user postgres --shm-size 256m \
  -e POSTGRES_USER=ndm_owner -e POSTGRES_PASSWORD=<owner password> -e POSTGRES_DB=ndm \
  -e NDM_APP_DB_ROLE=ndm_app -e NDM_APP_DB_PASSWORD=<app password> \
  -v ndm-envelope-data:/var/lib/postgresql/data \
  -v "$PWD/postgres/no-postgis-init.sh:/docker-entrypoint-initdb.d/10_postgis.sh:ro" \
  -v "$PWD/postgres/init-roles.sh:/docker-entrypoint-initdb.d/20-init-roles.sh:ro" \
  -p 127.0.0.1:55432:5432 postgis/postgis:17-3.5

cd ../backend
URL=postgresql+asyncpg://ndm_owner:<owner password>@127.0.0.1:55432/ndm
NDM_MIGRATION_DATABASE_URL=$URL NDM_APP_DB_ROLE=ndm_app uv run alembic upgrade head
uv run python scripts/seed_envelope.py --database-url $URL     # about an hour; 56 GB

docker update --cpus 2 --memory 1g --memory-swap 1g ndm-envelope-db && docker restart ndm-envelope-db
NDM_LOAD_DATABASE_URL=$URL NDM_LOAD_REPORT_DIR=../docs/release/measurements uv run pytest tests/load -s

# Lab checks 5.2 and 5.5, on the lab host with the release candidate and the lab running:
NDM_LAB=1 NDM_LAB_USERNAME=analyst NDM_LAB_PASSWORD=... NDM_LOAD_REPORT_DIR=../docs/release/measurements \
  uv run pytest tests/load/test_endurance.py tests/load/test_all_ports_open.py -s
```

Afterwards `docker rm -f ndm-envelope-db && docker volume rm ndm-envelope-data` returns the disk.
