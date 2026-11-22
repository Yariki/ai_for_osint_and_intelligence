# Phase 1B — Demonstration Script and Exit Checks

This document has two parts. Part 1 is the written demonstration for check 1B.19: a person follows it on the lab host. Part 2 is the record of checks 1B.1 to 1B.19.

## Part 1. Demonstration (check 1B.19)

**You need.** The Linux lab host, prepared as the operator guide says ([../operator-guide.md](../operator-guide.md), section 4), with a clean checkout, and a browser (current Chromium or Firefox).

**Set-up.** In `Assesment/deploy`:

```sh
docker compose -f lab/compose.lab.yaml up -d --build
docker compose --env-file .env --env-file lab/lab.env up -d --build
```

Create the account as in the Phase 1A demonstration. The lab settings switch the test switch on, because the lab's addresses are private.

| # | Step | Expected result |
| --- | --- | --- |
| 1 | `docker compose ps -a` | `proxy`, `api`, `orchestrator`, `scanner`, `postgres` and `rabbitmq` are `healthy`; `migrate` and `scanner-netinit` have exited with status 0. |
| 2 | Log in. Create a project `Lab`, open it, save the scope `10.77.3.0/24` with the confirmation. | "The scope was saved." |
| 3 | Open **Scans** of the project and choose **New scan**. | The dialog shows the scanner as connected, with SYN available. |
| 4 | Enter the target `10.77.3.0/24`, keep the profile top-1000. | The estimate appears: 256 hosts × 1,000 ports, 256,000 probes, about 4 minutes. |
| 5 | Change the rate to 5000. | The estimate changes to about 51 seconds. |
| 6 | Add a second target line `10.77.4.1` and start. | An error on that target: it is not inside the project scope. No scan is created. |
| 7 | Remove the second line and start the scan. | The scan is in the list as `queued`, then `running`. |
| 8 | Open the scan. | The host table fills while the scan runs: the lab hosts appear one after another, and their open-port counts rise. The progress line advances. No reload is needed. |
| 9 | Select host `10.77.3.10`. | The detail shows its evidence, the open ports 22, 80 and 443 with their nominal service names and the note that the names are nominal, and the closed and filtered counts. |
| 10 | Wait for the scan to complete. Compare the table with `deploy/lab/manifest.yaml`. | State `completed`, technique `syn`. Every host of the manifest is listed with exactly its open ports among the top 1,000. |
| 11 | Go back to the list. Start a second scan of the same target with the profile `full`. While it runs, choose **Cancel**. | The scan becomes `cancelled` and is marked `partial`. What it had found is still shown when it is opened. |
| 12 | Delete the second scan, confirming the question. | It is gone from the list. |
| 13 | Open **Audit log**. | Entries for the scope change, the rejected scan of step 6 (`scan.rejected`), both launches (`scan.launch`), the cancellation (`scan.cancel`) and the deletion (`scan.delete`), and `test_switch.enabled` from the start of the stack. |
| 14 | In `Assesment/deploy`: `./checks/inspect-scanner-image.sh` | Every line starts with `PASS`. |

## Part 2. Record of exit checks

Fill in the result of each check when it is run. A check that cannot be run is recorded with its reason and needs the product owner's acceptance (delivery plan 2.2, item 7).

| Check | How it is run | Result | Date | Note |
| --- | --- | --- | --- | --- |
| 1B.1 Chunk planning | `pytest tests/unit/orchestrator/test_planning.py` | Passed | 2026-11-16 | |
| 1B.2 Probe estimate | `pytest tests/unit/core/test_estimate.py` | Passed | 2026-11-16 | |
| 1B.3 Packet build and reply match | `pytest tests/unit/scanner` | Passed | 2026-11-16 | |
| 1B.4 Idempotent recording | `pytest tests/integration/orchestrator/test_recording_idempotent.py` | Passed | 2026-11-18 | 13 delivery orders; on the pinned images (see below) |
| 1B.5 Immutability | `pytest tests/integration/orchestrator/test_immutability.py` | Passed | 2026-11-18 | |
| 1B.6 Dispatch recovery | `pytest tests/integration/orchestrator/test_dispatch_recovery.py` | Passed | 2026-11-18 | |
| 1B.7 Poison message | `pytest tests/integration/orchestrator/test_poison_message.py`, `tests/integration/core/test_broker.py`, `tests/integration/scanner/test_chunk_failure.py` | Passed | 2026-11-18 | On RabbitMQ it first failed: a message rejected with requeue was redelivered for ever (defect 4 below); fixed. |
| 1B.8 Scope enforcement at three points | `test_scan_scope_rejection.py` (API), `test_scope_recheck.py` (orchestrator), `tests/integration/scanner/test_scope_rejection.py` (scanner) | Passed | 2026-11-18 | Including the chunk placed on the real queue by hand. |
| 1B.9 Queue, cancel, single instance | `pytest tests/integration/orchestrator/test_queue_cancel_lock.py` | Passed | 2026-11-18 | |
| 1B.10 SYN accuracy | `tests/lab/test_accuracy.py` | Passed | 2026-11-19 | Exact on the /24, receive drops 0. |
| 1B.11 Connect fallback | `tests/lab/test_accuracy.py` | Passed | 2026-11-19 | The fallback works (it first could not start at all: defect 5, fixed). It first failed on one host: 10.77.3.21 answers ping only and drops probes to closed ports, so without NET_RAW (no ICMP) discovery does not find it. The product owner decided on 2026-11-19 to record and warn (release checklist 2a, TASK-079). Run again on the rebuilt images: connect mode found every host TCP discovery can find, with the same open ports as the SYN scan; only 10.77.3.21 was not found; the scan records `icmp_discovery_unavailable`; a forced SYN scan is rejected. |
| 1B.12 No payload | `tests/lab/test_no_payload.py` | Passed | 2026-11-19 | Both techniques; every half-open connection torn down; connect mode closes at once. |
| 1B.13 Scanner killed | `tests/lab/test_recovery.py` | Passed | 2026-11-19 | Also: the orchestrator killed alone. |
| 1B.14 Stack restarted | `tests/lab/test_recovery.py` | Passed | 2026-11-19 | First new finding within 2 minutes of the restart: passed. |
| 1B.15 Shared rate | `tests/lab/test_shared_rate.py` | Passed | 2026-11-19 | Four scans, three at a time; highest 10-second window 49,618 probes against a ceiling of 50,000, on the wire. |
| 1B.16 Many chunks | `tests/lab/test_many_chunks.py` | Passed | 2026-11-19 | 1,048,608 probes (every port of 16 addresses plus discovery). |
| 1B.17 Production networking | `tests/lab/test_production_networking.py` | Passed | 2026-11-18 | The two rules installed once; the connection-tracking count stayed flat. |
| 1B.18 Live table | `tests/lab/test_live_latency.py`; frontend `workspace.spec.ts`; browser | **Partly run** | 2026-11-18 | The 2-second limit at the event stream: passed on the lab. Reload after a dropped connection: passed (frontend test). The browser: not run. |
| 1B.19 Demonstration | Part 1 of this document | **Not run** | | Needs the lab host and a browser. |

**Reservations A and B, lifted.** Until 2026-11-18 there was no Docker: the database tests ran against a locally started PostgreSQL standing in for `postgis/postgis:17-3.5` (A), and the broker's part was exercised in memory, with 33 tests that need RabbitMQ not run (B). On 2026-11-18 the whole backend suite ran on the pinned images through `testcontainers`: 1,478 passed, none failed (the lab and load tests skipped, as they need the lab). That run found two defects of the product, both fixed (defects 4 and 6 in the release checklist), and four faults of the tests themselves.

**The lab, 2026-11-18 and 19.** The release images `1.0.0-rc.1` built from the working tree, the stack started with `lab/lab.env`, the lab started from `deploy/lab`. Every check above that needs the lab ran on it. A further defect was found and fixed (defect 5: the scanner without NET_RAW could not start). The lab suite also needed corrections of its own before it measured what it means to measure: captures without sudo (tcpdump in a container), IP packets only (bridges' ARP requests matched `host` and `net`), each probe counted once on the wire, discovery ports kept apart from port-scan replies, the replay harness timing replies when it sends them, and the lab resolver moved off port 5353 (held by mDNS). These are test changes; the product's behaviour in each was within its requirement.

**Phase 1B is complete when check 1B.11 is decided and 1B.18 and 1B.19 are done in a browser.**
