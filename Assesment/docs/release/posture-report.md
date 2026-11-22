# Posture Report — Release 1.0.0-rc.1

| | |
| --- | --- |
| Release | 1.0.0-rc.1 |
| Date | 2026-11-19 |
| Checks | 5.6 (deletion), 5.8 (operability), 5.9 (container and network posture) |
| Audience | Product owner, operators |

## 1. Summary

| Check | State | Result |
| --- | --- | --- |
| 5.6 Deletion | **Passed** | `tests/integration/test_deletion_complete.py`: every table filled, a scheduled scan and a whole project deleted and purged; no row of theirs remains outside `host` and `audit_event`. |
| 5.8 Operability | **Passed** on the lab host | `inspect-release.sh` on the running stack, 2026-11-19: every log line about a scan or a traceroute carries its identifier in the api, the orchestrator and the scanner; all five metrics of NFR-OPS-3 exported; every long-running service healthy; every service has its memory and CPU limit. Two defects found and fixed on the way (section 6). |
| 5.9 Container and network posture | **Passed** on the lab host; blocking severity not yet agreed | On the running stack, as declared in section 3: users, capabilities, `no-new-privileges`, read-only roots, published ports; the scanner process has exactly NET_RAW. Dependency scans: no finding at the proposed blocking level (section 4), which awaits the product owner. |

The deletion test first ran against a stand-in database (reservation A); on 2026-11-18 it passed again on the pinned `postgis/postgis:17-3.5`, with the whole backend suite.

**Where the checks of the running stack ran.** The lab host, not a clean host: the release images `1.0.0-rc.1` built from the working tree (not yet from a tagged commit), the stack started with `lab/lab.env`, after the lab suite had run scans and traceroutes on it. Check 5.1 repeats them on the clean host of the acceptance run.

## 2. Deletion (check 5.6)

The test fills every table, with the seeding tool and the two tables of work in progress it leaves out (a scan chunk, a session). It then finds what belongs to the deleted scan and project from the schema itself: starting from the scan and the project, it follows every foreign key, except those set to null when their target goes, so a table added later is covered without a change to the test. Before the purge the check finds rows of the deleted scan and project in every table the purge knows; after it, none.

| What | Result |
| --- | --- |
| Tables filled before the deletion | All of them |
| Deleted | One scheduled scan (with its runs, stored diffs, change events, alerts, chunk, observations, snapshots and classification results) and one whole project (scans, scopes and attestations, project hosts, schedules, traceroutes with probes, responders and hop snapshots, alerts) |
| Rows of theirs left after the purge | None |
| `audit_event` | Unchanged: entries survive deletions (I3) |
| `host` | Unchanged: the global address registry (I1) |
| The other project | Untouched |

## 3. Container and network posture as declared (architecture section 14)

From `deploy/compose.yaml` and the Dockerfiles, checked by `tests/unit/test_compose.py`:

| Service | User | Capabilities | `no-new-privileges` | Read-only root | Health check | Limits |
| --- | --- | --- | --- | --- | --- | --- |
| proxy | 10002 | none | yes | yes | yes | 256 MB, 1 CPU |
| api | 10001 | none | yes | yes | yes | 512 MB, 1 CPU |
| orchestrator | 10001 | none | yes | yes | yes | 512 MB, 1 CPU |
| scanner | 10003 | NET_RAW | **no** (1) | yes | yes | 512 MB, 2 CPU |
| scanner-netinit | **root** (2) | NET_ADMIN | yes | yes | one-shot | 64 MB, 0.5 CPU |
| rabbitmq | rabbitmq | none | yes | **no** (3) | yes | 1 GB, 1 CPU |
| migrate | 10001 | none | yes | yes | one-shot | 256 MB, 1 CPU |
| postgres | postgres | none | yes | yes | yes | 1 GB, 2 CPU |

1. The scanner gets NET_RAW from a file capability on its interpreter; `no-new-privileges` would switch that off. Architecture section 14 names this exception.
2. `scanner-netinit` changes the host firewall once, with NET_ADMIN on the host network, and exits. It can be skipped and the rules applied by hand (`NDM_NETINIT_SKIP`).
3. RabbitMQ writes node files outside its data volume. Section 14 asks for read-only roots "where possible".

**Network exposure.** Only the proxy publishes a port (443). RabbitMQ is published on the host's loopback address only, for the scanner. PostgreSQL publishes nothing and is on the internal network only. The API and the database are on a network with no route out; the orchestrator has a second network for its outgoing connections (datasets, resolver). Checked by `test_compose.py` and, on the running stack, by `inspect-1a.sh`.

## 4. Dependency scan

Run on 2026-11-19 by `inspect-release.sh --dependencies`; they also run in CI on every push.

| Scan | Findings |
| --- | --- |
| `pip-audit` on `uv.lock` (87 packages, all extras) | None |
| `npm audit` on `package-lock.json` | 11 of moderate severity, none high or critical; all in development tooling (`minimatch` and `glob` under eslint, typescript-eslint, `@vue/test-utils` and `openapi-typescript`). `npm audit --omit=dev`: none. Recorded, not blocking under the proposal below. |

**Proposed blocking level, to be agreed with the product owner:**

- A finding of **high or critical** severity blocks the release, unless the vulnerable code is shown not to be reachable in the product and the product owner accepts that in writing here.
- `pip-audit` gives no severity; any known vulnerability in a package of an image (`ndm-backend`, `ndm-scanner`) blocks, with the same exception. One in a development-only package is recorded and does not block.
- `npm audit --audit-level=high`, as in CI. The frontend's dependencies end in static files served by the proxy, so most findings in build tooling do not reach the product; they are recorded and do not block.

**Agreed level:** not yet agreed.

## 5. Running the checks on the release candidate

On the host of the acceptance run, with the stack of the release candidate up, after a scan and a traceroute:

```sh
cd Assesment/deploy
./checks/inspect-release.sh --dependencies
```

It also runs `inspect-1a.sh` and `inspect-scanner-image.sh`. Record each `FAIL` line here as a defect (TASK-078 onwards).

## 6. Results on the release candidate

`inspect-release.sh --dependencies` on the lab host, 2026-11-19: **Checks 5.8 and 5.9: PASSED**, after these corrections:

| Found | What | Change |
| --- | --- | --- |
| 5.8 | The api and the scanner logged nothing about scans or traceroutes on the normal path, only on cancellation, refusal and failure, so "in all three processes" could not be shown. | **Product:** the api logs `scan_created` and `traceroute_created`, the scanner `chunk_done` and `traceroute_done`, each with its identifier. Tests: `test_scans.py`, `test_traceroutes.py`, `scanner/test_log_identifiers.py`. |
| 5.8, earlier | The orchestrator and the scanner ignored SIGTERM as process 1 (release checklist, defect 7). | **Product:** `init: true` for both. |
| 5.9 | With `init: true`, process 1 of the scanner container is Docker's init; the inspection read its capabilities (none) instead of the scanner's. | **Inspection:** `inspect-scanner-image.sh` reads the scanner process found by its command line. |
| 5.9 | Docker 29 reports added capabilities as `CAP_NET_RAW`; the inspection expected `NET_RAW`. | **Inspection:** the prefix is ignored. |
