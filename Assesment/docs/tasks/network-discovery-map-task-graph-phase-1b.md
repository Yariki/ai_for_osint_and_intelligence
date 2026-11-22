# Network Discovery Map — Task Graph, Phase 1B

| | |
| --- | --- |
| Version | 1.0 |
| Date | 2026-11-15 |
| Status | Proposed; ready for assignment |
| Based on | Network Discovery Map — Delivery Plan v1.0, section 5 (Phase 1B) and section 2.2; Architecture v1.0, sections 6, 8, 9 and 10 |
| Audience | Implementation team |

This document breaks Phase 1B (Scan pipeline) of the delivery plan into tasks. The conventions, the field meanings and the rules of the graph are in the index, [network-discovery-map-task-graph.md](network-discovery-map-task-graph.md).

## 1. Scope and entry conditions

**Goal of the phase.** Manual scans work end to end: launch, probe, record, recover after any restart, and show results live in the host table.

**Entry conditions.**

- Phase 1A is complete (TASK-010) and Gate 0 has passed (TASK-015).
- I1, I4 and P2 should be confirmed by the product owner.
- TASK-016 is the exception: it depends only on the scaffold and should start during Phase 0.

**Checkpoints of the delivery plan.**

| Checkpoint | Reached when these tasks are complete |
| --- | --- |
| 1. Pipeline against a stub scanner, checks 1B.1 to 1B.9 | TASK-016 to TASK-024, and TASK-027 for the scanner's part of 1B.8 |
| 2. Real scanner in the lab, checks 1B.10 to 1B.17 | TASK-026 to TASK-029 |
| 3. User interface, checks 1B.18 and 1B.19 | TASK-030 to TASK-032 |

## 2. Overview

| Task | Title | Depends on | Risk | Checks closed |
| --- | --- | --- | --- | --- |
| TASK-016 | Port lists, service names and probe estimate | TASK-001 | low | 1B.2 |
| TASK-017 | Groundwork: dependencies, settings, messages, broker topology | TASK-010, TASK-015 | high | — |
| TASK-018 | Schema for the scan pipeline | TASK-010, TASK-015 | high | — |
| TASK-019 | Stub scanner and recorded-scan fixture | TASK-017 | low | — |
| TASK-020 | Chunk planning | TASK-016, TASK-017 | medium | 1B.1 |
| TASK-021 | Result recording: idempotent handlers | TASK-018, TASK-019 | high | 1B.4, 1B.5 |
| TASK-022 | Orchestrator runtime: lock, tick, lifecycle, dispatch | TASK-020, TASK-021 | high | 1B.6, 1B.9, part of 1B.8 |
| TASK-023 | Dead-letter consumer, deletion purge and metrics | TASK-022 | high | 1B.7 |
| TASK-024 | Scan API | TASK-016, TASK-017, TASK-018 | high | Part of 1B.8, part of 1B.11 |
| TASK-025 | Host API and server-sent event stream | TASK-024 | medium | — |
| TASK-026 | Scanner engine: sockets, rate limiter, techniques | TASK-012, TASK-017 | high | — |
| TASK-027 | Scanner worker | TASK-026 | high | Part of 1B.8 |
| TASK-028 | Images, Compose services and `scanner-netinit` | TASK-023, TASK-025, TASK-027 | high | C-2 and C-3 inspection |
| TASK-029 | Lab suite: accuracy, payload, recovery, rate | TASK-028 | medium | 1B.10 to 1B.17, part of 1B.18 |
| TASK-030 | Frontend scans: list and launch dialog | TASK-025 | medium | — |
| TASK-031 | Frontend live host table and host detail panel | TASK-030 | medium | Part of 1B.18 |
| TASK-032 | Operator guide, demonstration and Phase 1B exit checks | TASK-029, TASK-031 | medium | 1B.18, 1B.19 |

| Wave | Tasks that run in parallel |
| --- | --- |
| Early | TASK-016, any time after TASK-001 |
| 1 | TASK-017, TASK-018 |
| 2 | TASK-019, TASK-020, TASK-024, TASK-026 |
| 3 | TASK-021, TASK-025, TASK-027 |
| 4 | TASK-022, TASK-030 |
| 5 | TASK-023, TASK-031 |
| 6 | TASK-028 |
| 7 | TASK-029 |
| 8 | TASK-032 |

The longest chain is TASK-017 → 019 → 021 → 022 → 023 → 028 → 029 → 032. The API, the scanner and the frontend each run beside it.

## 3. Tasks

### TASK-016 — Port lists, service names and probe estimate

- **dependencies:** TASK-001
- **risk_level:** low

**Description.**

- Curate the top-100 and top-1000 TCP port lists for this project. They are not derived from nmap's frequency data (R4).
- Add the IANA service-name file.
- Record the provenance and the licence of each data file (NFR-LIC-2).
- `ndm.core.ports`: load the three port profiles (top-100, top-1000, full range) and look up the nominal service name of a port.
- `ndm.core.estimate`: probe count and duration from the number of hosts, the profile, the rate and the retries.

**expected_files.**

- `Assesment/backend/src/ndm/core/data/top-100-tcp.txt`
- `Assesment/backend/src/ndm/core/data/top-1000-tcp.txt`
- `Assesment/backend/src/ndm/core/data/iana-service-names.csv`
- `Assesment/backend/src/ndm/core/data/PROVENANCE.md`
- `Assesment/backend/src/ndm/core/ports.py`
- `Assesment/backend/src/ndm/core/estimate.py`
- `Assesment/backend/tests/unit/core/test_ports.py`
- `Assesment/backend/tests/unit/core/test_estimate.py`

**Acceptance.**

- Check 1B.2: 256 hosts give 25,600, 256,000 and 16,776,960 probes for the three profiles (NFR-PRF-1).
- The lists hold exactly 100 and 1,000 distinct ports, and the top-100 list is a subset of the top-1000 list.
- The provenance file names the source and the licence of every data file.

### TASK-017 — Groundwork: dependencies, settings, message models and broker topology

- **dependencies:** TASK-010, TASK-015
- **risk_level:** high (broker credentials; settings shared by all three processes)

**Description.**

- Pin the dependencies Phase 1B adds: an asyncio AMQP client and `prometheus-client`. Put the scanner's dependencies in their own group, so the scanner image installs the core and scanner dependencies only.
- Settings: broker URL, global rate ceiling (default 5,000 packets per second), concurrent-scan ceiling (default 3), vantage-point name, egress address and source-port range.
- `ndm.core.messages`: versioned Pydantic models for the messages of architecture section 9, without traceroute. Each carries a schema version, a message identifier and the scan identifier.
  - work: chunk (section 9.3), cancel;
  - results: hello and heartbeat, findings, chunk done, chunk rejected;
  - commands: scan created, cancel requested;
  - UI events: scan state and progress, hosts changed.
- `ndm.core.broker`: declare the exchanges and queues of section 9.1 idempotently, without the traceroute queue.
  - Work and result queues are durable quorum queues with a delivery limit of 5 and the dead-letter exchange.
  - Messages are persistent, publishers use confirms, consumers acknowledge manually.
  - A message that fails validation is rejected without requeue.
- A RabbitMQ fixture through `testcontainers` beside the PostgreSQL fixture.
- A CI import rule: `ndm.scanner` must not import `ndm.db` or `ndm.api` (C-3).
- Create the `ndm.orchestrator` package.

**expected_files.**

- `Assesment/backend/pyproject.toml` (modified)
- `Assesment/backend/uv.lock` (modified)
- `.github/workflows/ndm-ci.yml` (modified)
- `Assesment/backend/src/ndm/core/settings.py` (modified)
- `Assesment/backend/src/ndm/core/messages.py`
- `Assesment/backend/src/ndm/core/broker.py`
- `Assesment/backend/src/ndm/orchestrator/__init__.py`
- `Assesment/backend/tests/integration/conftest.py` (modified)
- `Assesment/backend/tests/unit/core/test_messages.py`
- `Assesment/backend/tests/integration/core/test_broker.py`

**Acceptance.**

- The declared topology matches section 9.1, and declaring it a second time changes nothing.
- An invalid message and a message with an unknown schema version go to the dead-letter queue. A message delivered five times without acknowledgement goes there too.
- The CI import rule fails on a test import of `ndm.db` from `ndm.scanner`.

### TASK-018 — Schema for the scan pipeline

- **dependencies:** TASK-010, TASK-015
- **risk_level:** high (migration)

**Description.**

- One migration and the ORM models for `vantage_point`, `scan`, `scan_chunk`, `host`, `project_host`, `host_observation` and `port_observation`, as in architecture section 10.1.
- `host_observation` and `port_observation` are hash-partitioned by scan into 16 partitions (AD8). `port_observation` has only its primary key. `host_observation` has one secondary index on (address, scan).
- Scan state and phase are PostgreSQL enums. Targets are `inet[]`, probed ports an `int4multirange`. A scan references the attestation it was validated against.
- The application role gets its privileges on the new tables; the protections on `audit_event` stay as they are.

**expected_files.**

- `Assesment/backend/src/ndm/db/models.py` (modified)
- `Assesment/backend/src/ndm/db/migrations/versions/0002_scan_pipeline.py`
- `Assesment/backend/tests/integration/db/test_migration_0002.py`

**Acceptance.**

- The migration applies, reverts and applies again.
- A database left by Phase 1A migrates forward and keeps its data (NFR-OPS-4).
- Both observation tables have 16 partitions.

### TASK-019 — Stub scanner and recorded-scan fixture

- **dependencies:** TASK-017
- **risk_level:** low

**Description.**

- A test tool that plays the scanner on the broker: it sends hello and heartbeat with configurable capabilities, consumes chunks and publishes scripted findings, chunk-done and chunk-rejected messages.
- It can duplicate, reorder and delay messages, and it can stop without acknowledging a chunk, to simulate a crash.
- A recorded-scan fixture: the complete message sequence of one small scan.

**expected_files.**

- `Assesment/backend/tests/tools/stub_scanner.py`
- `Assesment/backend/tests/fixtures/recorded_scan.json`
- `Assesment/backend/tests/integration/tools/test_stub_scanner.py`

**Acceptance.**

- The stub plays the recorded scan over a real broker.
- Each fault mode produces the fault it names, proven by a test that reads the published messages.

### TASK-020 — Chunk planning

- **dependencies:** TASK-016, TASK-017
- **risk_level:** medium (a planning error loses or repeats probes)

**Description.**

- Pure functions that turn a scan into chunk specifications matching the chunk message.
- The discovery chunk holds all targets with the selected discovery methods.
- A port-scan chunk is a group of hosts multiplied by a slice of ports. The group size is the "hosts probed in parallel" parameter (default 64). The slice size is chosen so that a chunk holds about 10,000 probes.
- Hosts and ports are shuffled with the seed stored on the scan before they are cut (FR-SCN-10).

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/planning.py`
- `Assesment/backend/tests/unit/orchestrator/test_planning.py`

**Acceptance.**

- Check 1B.1: every host and port pair of a scan is in exactly one chunk; chunks hold about 10,000 probes; the same seed gives the same plan; the order is not sorted.

### TASK-021 — Result recording: idempotent handlers

- **dependencies:** TASK-018, TASK-019
- **risk_level:** high (writes scan results; decides whether a completed scan stays immutable)

**Description.**

- Handler functions that take a result message and a database session and write with the keys of architecture section 6.4:
  - host observation: reachability is OR-ed and evidence is merged per probe;
  - port observation: `ON CONFLICT DO NOTHING`;
  - chunk result: stored only if the chunk is not already done; counts are summed onto host observations once, when the port-scan phase ends.
- Observation writes are accepted only while the scan is in a probing phase. Late and duplicate messages are discarded (FR-SCN-9).
- `host` and `project_host` are filled from the first scan: first and last seen, and the latest scan that targeted the address.
- A chunk the scanner rejected fails the scan and writes the audit entry in the same transaction.
- After the commit, the handler emits the UI events: hosts changed with slim rows, and progress.

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/recording.py`
- `Assesment/backend/src/ndm/orchestrator/events.py`
- `Assesment/backend/tests/integration/orchestrator/test_recording_idempotent.py`
- `Assesment/backend/tests/integration/orchestrator/test_immutability.py`

**Acceptance.**

- Check 1B.4: delivering every message of the recorded scan twice, and out of order, leaves the database identical to single in-order delivery (NFR-REL-1).
- Check 1B.5: findings that arrive after the scan reached a final state change nothing.
- A rejected chunk leaves a failed scan and one audit entry.

### TASK-022 — Orchestrator runtime: lock, tick, lifecycle and dispatch

- **dependencies:** TASK-020, TASK-021
- **risk_level:** high (scope re-check before each chunk; must fail closed)

**Description.**

- Entry point with a PostgreSQL session-level advisory lock. A second instance stays idle.
- A tick every 5 seconds over database state. Commands from the API only make the orchestrator react sooner (AD1).
- Lifecycle:
  - promote the oldest queued scan while fewer than the ceiling are running or interrupted;
  - create one host-observation row per target, all unreachable, then the discovery chunk, then the port-scan chunks;
  - resolve the `automatic` technique once from the vantage point's capabilities and record it on the scan;
  - on completion, delete the chunk rows and keep the totals.
- Dispatch, one chunk in flight per scan:
  - re-check the scope before each chunk: current project scope, project still active, scan not cancelled. On failure the scan fails with `scope_changed`, keeps its results and leaves an audit entry;
  - publish with confirmation, then commit the chunk as dispatched;
  - publish again after the lease (the larger of 5 minutes and three times the expected duration); fail the scan after five attempts.
- Consume results one at a time, in order, through the handlers of TASK-021.
- Vantage point: store capabilities and counters from hello and heartbeat. Mark running scans `interrupted` when the heartbeat is missing for 30 seconds and at start-up; return them to `running` on the next finding.
- Cancellation: stop dispatching, broadcast the cancel message, mark the scan cancelled and partial.
- Test switch: it takes effect only when set on all three processes. The orchestrator writes the audit entry, including the value the scanner reports in its hello message.

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/main.py`
- `Assesment/backend/src/ndm/orchestrator/tick.py`
- `Assesment/backend/src/ndm/orchestrator/lifecycle.py`
- `Assesment/backend/src/ndm/orchestrator/dispatch.py`
- `Assesment/backend/src/ndm/orchestrator/vantage.py`
- `Assesment/backend/tests/integration/orchestrator/test_dispatch_recovery.py`
- `Assesment/backend/tests/integration/orchestrator/test_queue_cancel_lock.py`
- `Assesment/backend/tests/integration/orchestrator/test_scope_recheck.py`
- `Assesment/backend/tests/integration/orchestrator/test_test_switch.py`

**Acceptance.**

- Check 1B.6: a crash between publish and commit leads to a re-publish; a chunk past its lease is re-published; after five attempts the scan is failed.
- Check 1B.9: a scan above the concurrent ceiling stays queued until a slot is free; a cancelled scan keeps its results and is marked partial; a second orchestrator stays idle.
- Check 1B.8, middle part: a scope reduced mid-scan fails the scan with `scope_changed`, keeps its results and leaves an audit entry.
- The test switch has no effect unless all three processes report it, and its use leaves an audit entry (NFR-SEC-6).

### TASK-023 — Dead-letter consumer, deletion purge and metrics

- **dependencies:** TASK-022
- **risk_level:** high (deletes user data)

**Description.**

- Dead-letter consumer: a dead-lettered message fails its scan with the reason, and nothing loops. The alert for it arrives in Phase 4.
- Deletion purge (I3): remove the rows of deleted scans and projects in batches. `host` rows and audit entries stay.
- The purge works from a registry of tables. A test fails when a table that holds scan or project data is not registered, so later phases cannot forget one (delivery plan 2.2 item 5).
- Metrics in Prometheus format on the internal network: probe rate and the scanner's counters from its heartbeat, queue depth, scan durations and scan outcomes. An HTTP health check.

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/deadletter.py`
- `Assesment/backend/src/ndm/orchestrator/purge.py`
- `Assesment/backend/src/ndm/orchestrator/metrics.py`
- `Assesment/backend/src/ndm/orchestrator/main.py` (modified)
- `Assesment/backend/tests/integration/orchestrator/test_poison_message.py`
- `Assesment/backend/tests/integration/orchestrator/test_purge.py`
- `Assesment/backend/tests/integration/orchestrator/test_metrics.py`

**Acceptance.**

- Check 1B.7: an invalid message goes to the dead-letter queue, the scan is failed with the reason, and nothing loops (NFR-REL-4).
- After a scan and a project are deleted and the purge has run, none of their rows remain except in `host` and `audit_event`.
- The metrics show probe rate, queue depth, scan duration and scan outcomes (NFR-OPS-3 without dataset age).

### TASK-024 — Scan API

- **dependencies:** TASK-016, TASK-017, TASK-018
- **risk_level:** high (scope check at creation)

**Description.**

- Endpoints: scan list, create, estimate, detail, cancel, delete; parameter ceilings and scanner capabilities.
- Creation checks the targets against the current project scope, the public-address table and the never-scan list, and rejects them otherwise with an audit entry. It also rejects:
  - more than 256 addresses in one scan (A1);
  - parameters above the operator ceilings;
  - a forced SYN scan when the vantage point reports no SYN capability;
  - any scan in an archived project.
- A created scan is `queued` and references the current attestation.
- The API commits first and then publishes a command that carries identifiers only.
- Cancel sets `cancel_requested_at`. Delete sets `deleted_at` and hides the scan at once (I3).
- Audit entries in the same transaction as the action: scan launch, cancellation, deletion and every rejection by the scope check.

**expected_files.**

- `Assesment/backend/src/ndm/api/routers/scans.py`
- `Assesment/backend/src/ndm/api/routers/config.py`
- `Assesment/backend/src/ndm/api/schemas/scans.py`
- `Assesment/backend/src/ndm/api/schemas/config.py`
- `Assesment/backend/src/ndm/api/commands.py`
- `Assesment/backend/src/ndm/api/app.py` (modified)
- `Assesment/backend/tests/integration/api/test_scans.py`
- `Assesment/backend/tests/integration/api/test_scan_scope_rejection.py`

**Acceptance.**

- Check 1B.8, creation part: out-of-scope, non-public and never-scan targets are rejected at creation, each with an audit entry.
- Check 1B.11, creation part: a forced SYN scan is rejected when the capability is absent.
- A scan is still created when the command cannot be published; the orchestrator's tick finds it.
- Check 1A.3 still passes, unchanged, with the new routes.

### TASK-025 — Host API and server-sent event stream

- **dependencies:** TASK-024
- **risk_level:** medium (reads scan data through the existing authentication)

**Description.**

- Host rows for a scan with sorting and paging.
- Host detail with evidence, open ports with their nominal service names, and closed and filtered counts.
- One server-sent event stream. The API holds one exclusive queue on `ndm.events` and forwards the events to the connected browser. Events are not replayed.

**expected_files.**

- `Assesment/backend/src/ndm/api/routers/hosts.py`
- `Assesment/backend/src/ndm/api/routers/events.py`
- `Assesment/backend/src/ndm/api/schemas/hosts.py`
- `Assesment/backend/src/ndm/api/events.py`
- `Assesment/backend/src/ndm/api/app.py` (modified)
- `Assesment/backend/tests/integration/api/test_hosts.py`
- `Assesment/backend/tests/integration/api/test_event_stream.py`

**Acceptance.**

- Sorting and paging give a stable order with no row repeated or missed across pages.
- An event published to `ndm.events` reaches a connected client.
- The event stream answers 401 without a session; check 1A.3 passes unchanged.

### TASK-026 — Scanner engine: sockets, rate limiter and techniques

- **dependencies:** TASK-012, TASK-017
- **risk_level:** high (raw sockets; sends packets to targets)

**Description.**

- Sockets: raw TCP for sending; the receive path chosen in the spike report, with a kernel filter for the source-port range, the receive buffer raised and the drop counter read; raw ICMP.
- Rate limiter: one token bucket per scan and one global bucket. Every probe takes a token from both. The per-scan rate is clamped to the operator ceiling from the scanner's own environment.
- SYN scan: one source port per chunk in flight; SYN/ACK means open, RST closed, silence after the retries filtered; targets shuffled again inside the chunk; no payload.
- Connect scan: `asyncio.open_connection` with a timeout under a concurrency limit. A connection is closed as soon as it is established, with nothing sent or read.
- Discovery by ICMP echo and by TCP.

**expected_files.**

- `Assesment/backend/src/ndm/scanner/sockets.py`
- `Assesment/backend/src/ndm/scanner/ratelimit.py`
- `Assesment/backend/src/ndm/scanner/syn.py`
- `Assesment/backend/src/ndm/scanner/connect.py`
- `Assesment/backend/src/ndm/scanner/icmp.py`
- `Assesment/backend/src/ndm/scanner/discovery.py`
- `Assesment/backend/tests/unit/scanner/test_ratelimit.py`
- `Assesment/backend/tests/unit/scanner/test_filter.py`
- `Assesment/backend/tests/lab/test_engine_smoke.py`

**Acceptance.**

- Unit: the tokens granted over any window stay at or below the limit, and two scans that share the global bucket both advance.
- Unit: the kernel filter passes only TCP segments addressed to the source-port range.
- Lab: a SYN scan and a connect scan of one lab host each report the manifest's open ports.

### TASK-027 — Scanner worker

- **dependencies:** TASK-026
- **risk_level:** high (the scanner's own scope check; the last barrier before a packet is sent)

**Description.**

- One asyncio process that consumes the chunk queue and the cancel broadcast, with one set of sockets and one receive loop for all work in flight. It has no listening port and no database access.
- Scope check before the first probe of each chunk: the scope carried in the chunk, its own public-address table and its own copy of the never-scan list. On failure the whole chunk is rejected, nothing is sent and a chunk-rejected message is published. A missing scope or a parse error counts as failure.
- Findings are flushed every 500 ms. A chunk is acknowledged only after its final result has been published.
- Hello at start-up and heartbeat every 10 seconds: SYN capability, raw-ICMP capability, vantage-point name, egress address, counters and the value of the test switch.
- Start-up checks of host preparation: receive-buffer limit, and the source-port range against the ephemeral range. On overlap the scanner withdraws its SYN capability.
- Cancel handling and a heartbeat file for the container health check.

**expected_files.**

- `Assesment/backend/src/ndm/scanner/main.py`
- `Assesment/backend/src/ndm/scanner/scopecheck.py`
- `Assesment/backend/src/ndm/scanner/hostprep.py`
- `Assesment/backend/tests/integration/scanner/test_scope_rejection.py`
- `Assesment/backend/tests/integration/scanner/test_worker_protocol.py`

**Acceptance.**

- Check 1B.8, scanner part: a chunk placed on the queue by hand with an out-of-scope target is rejected with nothing sent, and the rejection reaches the orchestrator.
- A chunk is acknowledged after its final result, never before.
- A cancel message stops sending for that scan, and the scanner returns what it has.
- Without the capability, the hello message reports no SYN.

### TASK-028 — Images, Compose services and `scanner-netinit`

- **dependencies:** TASK-023, TASK-025, TASK-027
- **risk_level:** high (privileged container, host networking, broker credentials)

**Description.**

- The `ndm-scanner` image: core and scanner dependencies only, a non-root user, the `cap_net_raw` file capability on its own copy of the interpreter. No external scanner or diagnostic binary and no database driver (C-2, C-3).
- Compose services:
  - `rabbitmq`: pinned 4.x, named volume, published on the host's loopback address only;
  - `orchestrator`;
  - `scanner`: host network, `cap_drop: ALL`, `cap_add: NET_RAW`, no `no-new-privileges`, raised file-descriptor limit, health check on the heartbeat file;
  - `scanner-netinit`: the production version of the prototype.
- The proxy does not buffer the event route.
- A lab overlay that attaches the scanner to the lab and sets the test switch on all three processes.
- An inspection script for the scanner image.

**expected_files.**

- `Assesment/deploy/Dockerfile.scanner`
- `Assesment/deploy/compose.yaml` (modified)
- `Assesment/deploy/Caddyfile` (modified)
- `Assesment/deploy/.env.example` (modified)
- `Assesment/deploy/scanner-netinit/install-rules.sh` (modified)
- `Assesment/deploy/lab/compose.lab.yaml` (modified)
- `Assesment/deploy/checks/inspect-scanner-image.sh`

**Acceptance.**

- `docker compose up` from a clean checkout brings every service to healthy.
- Inspection: the scanner image holds no database driver, no web framework and no external scanner or diagnostic binary.
- The scanner process runs with a non-zero user ID and exactly one effective capability.
- RabbitMQ is reachable on the loopback address only; port 443 stays the only port published to the outside.

### TASK-029 — Lab suite: accuracy, payload, recovery and rate

- **dependencies:** TASK-028
- **risk_level:** medium

**Description.**

- On-demand tests against the lab, driven by the manifest. They need raw-socket privileges and do not run in CI.
- One test module per group of checks: accuracy in SYN and connect mode, packet captures, recovery, shared rate, many chunks, production networking, and latency at the event stream.

**expected_files.**

- `Assesment/backend/tests/lab/conftest.py`
- `Assesment/backend/tests/lab/test_accuracy.py`
- `Assesment/backend/tests/lab/test_no_payload.py`
- `Assesment/backend/tests/lab/test_recovery.py`
- `Assesment/backend/tests/lab/test_shared_rate.py`
- `Assesment/backend/tests/lab/test_many_chunks.py`
- `Assesment/backend/tests/lab/test_production_networking.py`
- `Assesment/backend/tests/lab/test_live_latency.py`

**Acceptance.**

- Check 1B.10: a /24 with the top-1000 profile reports exactly the manifest's open ports, the closed and filtered counts match, and the scan records `syn`.
- Check 1B.11: the same scan with a scanner started without the capability reports the same open ports and records `connect`. (As decided on 2026-11-19: for every host that TCP discovery can find; the scan records that ICMP discovery was unavailable. TASK-079.)
- Check 1B.12: in both modes no packet to a target carries application data, and connect-mode connections close as soon as they are established.
- Check 1B.13: killing the scanner mid-scan ends in a completed scan without analyst action; observations equal those of an uninterrupted run; at most one chunk is probed twice.
- Check 1B.14: restarting the whole stack mid-scan gives the same result, with the first new finding within 2 minutes of the services being healthy. The same holds when only the orchestrator is killed.
- Check 1B.15: with three concurrent scans and one queued, the send rate over any 10-second window stays at or below the global ceiling, no scan exceeds its own rate, all three advance, and receive drops are zero.
- Check 1B.16: a full-range scan of 16 hosts completes with exact results; chunk rows are gone and the totals remain.
- Check 1B.17: with the production Compose file and `scanner-netinit`, check 1B.10 passes and the connection-tracking count stays flat.
- Check 1B.18, at the event stream: each host row and progress event arrives within 2 seconds of the scanner receiving the reply (P2).

### TASK-030 — Frontend scans: list and launch dialog

- **dependencies:** TASK-025
- **risk_level:** medium

**Description.**

- Regenerate the API types.
- Scan list with state, progress, cancel, and delete behind a confirmation.
- Launch dialog: targets, discovery methods, port profile, technique, tunable parameters within the operator ceilings, and the probe and duration estimate from the estimate endpoint.
- The dialog shows the scanner's capabilities and the server's rejections. It does no scope validation of its own.

**expected_files.**

- `Assesment/frontend/src/api/schema.d.ts` (regenerated)
- `Assesment/frontend/src/router/index.ts` (modified)
- `Assesment/frontend/src/stores/scans.ts`
- `Assesment/frontend/src/views/ScanListView.vue`
- `Assesment/frontend/src/components/ScanLaunchDialog.vue`
- `Assesment/frontend/src/stores/scans.spec.ts`
- `Assesment/frontend/src/components/ScanLaunchDialog.spec.ts`

**Acceptance.**

- Vitest: the estimate is refreshed when a parameter changes; a rejected target is shown as an error; SYN cannot be forced when the capability is absent.
- A scan can be launched, cancelled and deleted in the browser.

### TASK-031 — Frontend live host table and host detail panel

- **dependencies:** TASK-030
- **risk_level:** medium

**Description.**

- `useEventStream`: connection, reconnect and dispatch of events. Events are not replayed, so after a reconnect the current view is reloaded through REST.
- Workspace store with the selected scan and its hosts.
- Host table with live progress, sorting and paging.
- Host detail panel with evidence, open ports and closed and filtered counts. Service names are labelled as nominal (FR-SCN-3).

**expected_files.**

- `Assesment/frontend/src/composables/useEventStream.ts`
- `Assesment/frontend/src/stores/workspace.ts`
- `Assesment/frontend/src/views/ScanDetailView.vue`
- `Assesment/frontend/src/components/HostTable.vue`
- `Assesment/frontend/src/components/HostDetailPanel.vue`
- `Assesment/frontend/src/router/index.ts` (modified)
- `Assesment/frontend/src/composables/useEventStream.spec.ts`
- `Assesment/frontend/src/stores/workspace.spec.ts`

**Acceptance.**

- Vitest: a hosts-changed event updates the matching rows; after a dropped connection the view reloads to the correct state (check 1B.18, second sentence).
- The table fills while a lab scan runs.

### TASK-032 — Operator guide, demonstration and Phase 1B exit checks

- **dependencies:** TASK-029, TASK-031
- **risk_level:** medium

**Description.**

- Extend the operator guide with every configuration value and host-preparation step Phase 1B adds: the receive-buffer limit, the source-port range, `scanner-netinit` or its manual equivalent, the operator ceilings and the test switch.
- Write the demonstration script for check 1B.19 and carry it out on the lab host.
- Confirm check 1B.18 in a browser.
- Go through the common criteria of delivery plan section 2.2 and record the result of each of checks 1B.1 to 1B.19.

**expected_files.**

- `Assesment/docs/operator-guide.md` (modified)
- `Assesment/docs/demo/phase-1b.md`

**Acceptance.**

- Check 1B.19: launch a scan with the estimate shown, watch the table fill, cancel a second scan, delete a scan, read the audit entries.
- Check 1B.18 holds in the browser.
- Every check from 1B.1 to 1B.19 is recorded as passed, with none skipped. AC 2, AC 3 and AC 4 are met; AC 1 is met for manual scans.

## 4. Coverage of Phase 1B

| Phase 1B check | Task |
| --- | --- |
| 1B.1 Chunk planning | TASK-020 |
| 1B.2 Probe estimate | TASK-016 |
| 1B.3 Packet build and reply match | TASK-012 (Phase 0); kept green in CI |
| 1B.4 Idempotent recording | TASK-021 |
| 1B.5 Immutability | TASK-021 |
| 1B.6 Dispatch recovery | TASK-022 |
| 1B.7 Poison message | TASK-023 |
| 1B.8 Scope enforcement at three points | TASK-024 (creation), TASK-022 (mid-scan), TASK-027 (scanner) |
| 1B.9 Queue, cancel, single instance | TASK-022 |
| 1B.10 to 1B.17 Lab checks | TASK-029, on the stack of TASK-028 |
| 1B.18 Live table | TASK-029 (latency), TASK-031 (reload), TASK-032 (browser) |
| 1B.19 Demonstration | TASK-032 |

| Common criterion (delivery plan 2.2) | Task |
| --- | --- |
| 1. CI green | Every task; the import rule comes with TASK-017 |
| 2. Clean start and demonstration | TASK-028, TASK-032 |
| 3. Migrations apply, revert, apply; earlier data kept | TASK-018 |
| 4. Audit entry in the same transaction, with a test | TASK-021, TASK-022, TASK-024 |
| 5. Deletion purge covers every new table | TASK-023 |
| 6. Route-enumeration test covers new endpoints | TASK-024, TASK-025 |
| 7. No failed or skipped check | TASK-032 |
| 8. Operator guide | TASK-032 |
