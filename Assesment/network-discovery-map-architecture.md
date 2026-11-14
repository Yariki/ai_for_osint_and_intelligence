# Network Discovery Map — Architecture

| | |
|---|---|
| Version | 1.0 |
| Date | 2026-11-14 |
| Status | Proposed; input to implementation planning |
| Based on | Network Discovery Map — Technical Specification v1.0 |
| Audience | Implementation planner; product owner for sections 3 and 19 |

Requirement identifiers (FR-…, NFR-…, D…, A…, C-…, R…) refer to the specification. This document says how the system is built; it does not restate what the system must do.

## 1. Summary

The system is six long-running containers on one host: a reverse proxy, an API, an orchestrator, a scanner worker, PostgreSQL and RabbitMQ.

- The **API** serves the UI and validates every request. It never probes anything.
- The **orchestrator** owns every scan from queue to completion. It cuts a scan into small work units (chunks), sends them to the scanner, records what comes back, and runs enrichment, classification, diffing, schedules and dataset updates.
- The **scanner** is the only component that touches targets. It has no database access; it receives chunks and returns findings through the broker.
- **PostgreSQL holds all state.** RabbitMQ carries work and notifications, and anything in it can be rebuilt from the database.

Three choices shape the rest of the design:

1. **A chunk is the unit of work, of checkpointing and of recovery.** A redelivered chunk is simply probed again, and every write is an idempotent upsert on a natural key. There is no separate checkpoint mechanism.
2. **Findings are streamed while a chunk runs**, and location lookup happens the moment a host is first seen, so the map updates inside the 2-second budget.
3. **The scanner runs on the host network with only the raw-socket capability.** This avoids NAT and connection-tracking problems for SYN scanning, at the cost of network isolation for that one container.

## 2. Drivers, assumptions and trade-off

**Main trade-off.** The design optimizes for simplicity and safe recovery on a single host, not for throughput or horizontal scale. Every process runs as exactly one instance. At 5,000 packets per second and three concurrent scans, one of each is enough.

**Assumptions the design depends on:**

| # | Assumption |
|---|---|
| AS1 | One host, one scanner worker, one vantage point (D4, D12). |
| AS2 | Operator ceilings stay near the proposed defaults: 5,000 packets per second globally, 3 concurrent scans, 5 concurrent traceroutes (NFR-PRF-2). |
| AS3 | The broker is reachable only from the host itself. Messages are trusted in version 1; job signing comes with remote agents. |
| AS4 | The operator can apply a small one-time host preparation (two sysctl values, optionally two firewall rules). See section 15.3. |
| AS5 | Data volume stays within A7. |

**Alternative considered.** One process holding the API, orchestration and scanning would be simpler to deploy, but it breaks NFR-SEC-2 (raw sockets in the web process) and C-3 (scanner as a separate worker), so it was not pursued.

## 3. Interpretations of the specification

The specification contains statements that conflict or can be read two ways. The design uses the readings below. Each one should be confirmed by the product owner; none of them changes the component structure, and the cost of reversing each is noted.

| # | Spec text | Problem | Reading used | Cost to reverse |
|---|---|---|---|---|
| I1 | A4 | "Identified by IP within a project" and "merged across projects, global view" pull in different directions. | An address is one global host. Observations from all projects attach to it. Annotations (override, tags, notes) stay per project, as FR-CLS-5 and FR-CLS-6 require. | Low |
| I2 | FR-TRC-5 and section 10 | The sentence about port-scanning hops is garbled, and section 10 lists hop storage as a future feature while FR-TRC-5 makes it a must. | All hop addresses are stored in the host table in version 1. A hop is never probed directly unless it is inside the project scope and part of a scan. To scan a hop, the analyst adds it to the scope with an attestation first. This is the fail-closed reading and matches FR-PRJ-7 and acceptance criterion 1. | Low |
| I3 | A3, FR-PRJ-1, FR-HIS-9 | "Marked as removed" against "removes all of its data". | Deletion marks the scan or project as deleted at once, so it disappears from every view. A background job then removes the rows physically. Audit entries are kept. | Low |
| I4 | FR-PRJ-5 and FR-PRJ-7 | "Scope changes take effect for future scans only" against "inside the scope at the moment of execution". | Historical data is untouched by scope changes. A running scan is re-checked against the current scope before each chunk; if a target has left the scope, the scan stops, keeps its results and is marked failed with reason `scope_changed`. | Low |
| I5 | FR-SCN-4 and FR-MAP-7 | The phase list puts enrichment after the port scan, but a host cannot appear on the map without coordinates. | Local enrichment (location, ASN, registration) runs inline when a host is first seen. The enrichment phase that follows does reverse DNS and retries anything that was unavailable. | Medium |
| I6 | FR-ENR-5 | Storage cost. | One enrichment row per host per scan, with no sharing between hosts or scans, as written. | Low |

## 4. System overview

```
                    Browser (Vue 3 SPA)
                           │  HTTPS: REST + server-sent events
                    ┌──────▼──────┐
                    │    proxy    │  TLS, static files, /api → api
                    └──────┬──────┘
                    ┌──────▼──────┐   SQL    ┌───────────────────┐
                    │     api     ├─────────►│    PostgreSQL     │
                    └──┬───────▲──┘          │     + PostGIS     │
              commands │       │ UI events   └─────────▲─────────┘
                    ┌──▼───────┴──┐                    │ SQL
                    │  RabbitMQ   │                    │
                    └──┬───────▲──┘                    │
  chunks, traceroutes  │       │ findings, results     │
        ┌──────────────┘       │   ┌───────────────────┴──┐
        │     ┌────────────────┴───┤     orchestrator     ├──► DNS resolver (PTR)
        │     │   commands, events │                      ├──► MaxMind, RIR downloads
 ┌──────▼─────┴──┐                 └──────────────────────┘
 │    scanner    │
 │ host network  ├──► targets (ICMP, TCP, UDP)
 │ NET_RAW only  │
 └───────────────┘
```

| Component | Responsibility | Does not |
|---|---|---|
| proxy (Caddy) | TLS termination, static SPA files, routing `/api` to the API. | Hold any logic. |
| api (FastAPI) | Authentication, input validation, scope checks at creation, REST reads and writes, exports, server-sent events. Publishes commands to the orchestrator. | Probe, hold raw-socket capability, run long jobs. |
| orchestrator | Scan lifecycle and chunk dispatch, recording of findings, enrichment, classification, diff and alerts, scheduler, dataset updates, deletion purge. | Serve HTTP to the browser, send probes. |
| scanner | Host discovery, port scan, traceroute. Its own scope check before every chunk. Rate limiting. | Access the database, expose any listening port. |
| PostgreSQL + PostGIS | All persistent state. | |
| RabbitMQ | Work distribution to the scanner, results back, commands from the API, transient UI events. | Hold state that cannot be rebuilt. |

Two one-shot containers run at start-up: `migrate` (schema migrations) and `scanner-netinit` (optional connection-tracking bypass, section 8.5).

## 5. Key decisions

| # | Decision | Reason | Alternative rejected |
|---|---|---|---|
| AD1 | PostgreSQL is the only source of truth. The orchestrator is driven by a periodic tick over database state; broker messages only make it react sooner. | A lost or duplicated message can never corrupt or stall a scan. Removes the dual-write problem between database and broker. | Transactional outbox: more machinery for the same guarantee. |
| AD2 | The orchestrator splits a scan into chunks of about 10 seconds of probing and keeps one chunk per scan in flight. | One mechanism gives checkpointing (FR-SCN-7), fair sharing between concurrent scans (FR-SCN-6), cancellation and scope re-checks. | One message per scan with checkpoints kept by the scanner: needs scanner-side state or database access. |
| AD3 | The scanner has no database access and gets everything it needs, including the scope, in the chunk message. | C-3 and D12: a remote agent can later run the same code. | Read-only database access for the scope check: ties the worker to the central database. |
| AD4 | The scanner streams findings every 500 ms while a chunk runs. | FR-MAP-7 and acceptance criterion 7. | Reporting at chunk end: up to 10 seconds late. |
| AD5 | SYN scanning uses stateless reply matching: the probe's sequence number is a keyed hash of the target, and a reply is valid only if it acknowledges that value. | Accurate matching at rate with no per-probe lookup table, and replies to other traffic can never be mistaken for results. | Per-probe state table keyed by port: slower and fragile under retries. |
| AD6 | The scanner container uses host networking, drops all capabilities except `NET_RAW`, and runs as a non-root user. | Resolves R2: no NAT and no per-container connection tracking between scanner and targets. | Bridged networking: every probe creates a NAT entry; a full-range scan exhausts the table. |
| AD7 | Addresses are the natural key (`inet`) across host, observation and annotation tables. Other keys are `bigint` identities. | Idempotent upserts need no lookup step. `inet` also holds IPv6 (C-5). `bigint` keeps the indexes on the 250-million-row table small; UUIDs would add size without benefit on a single installation. | Surrogate host IDs and UUID keys. |
| AD8 | One row per open port. The two observation tables are hash-partitioned by scan into 16 partitions. | Matches the information model and A7. Hash partitions need no maintenance job, and retrofitting partitioning later means rewriting the table. | Range partitions by time: need a partition-creation job and bring nothing, since history is never dropped by age. |
| AD9 | Server-sent events for live updates (R9). | One-way traffic, plain HTTP through the proxy, cookie authentication and automatic reconnect built in. | WebSocket: two-way capability that nothing here needs. |
| AD10 | Session cookie with server-side sessions in PostgreSQL. | One user, one browser application, no external API (D8). Sessions can be revoked. | JWT: revocation and expiry are harder, with no gain. |
| AD11 | One Python package with three entry points (`api`, `orchestrator`, `scanner`) and two images. | Shared message contracts and scope code without a packaging workspace. | Separate packages per service: more structure than three entry points need. |
| AD12 | MapLibre GL JS for the map. | Built-in clustering and WebGL rendering keep 10,000 points interactive (NFR-PRF-4). | Leaflet with marker clustering: its fan-out is unusable when hundreds of hosts share one point, which FR-MAP-2 says is the normal case. |
| AD13 | Classification is a small evaluator over a versioned YAML ruleset shipped in the package. | D6: rules are not user-editable, so a rule-engine library adds nothing. | Rule-engine dependency. |
| AD14 | RIR registration data is loaded into PostgreSQL and queried with a range index; GeoLite2 is read from its `.mmdb` files. | Each dataset is used in the form its publisher provides, with no conversion step. | Building one combined lookup file: needs a writer toolchain. |

Dependencies beyond the fixed stack, each with its reason: Caddy (TLS proxy required by NFR-SEC-4), `maxminddb` (GeoLite2 reader), `dnspython` (reverse DNS through a chosen resolver), `argon2-cffi` (NFR-SEC-3), `structlog` (NFR-OPS-2), `prometheus-client` (NFR-OPS-3), `httpx` (dataset downloads), a cron-expression library (FR-SCH-1; pick a maintained one at planning time), MapLibre GL JS, Pinia, `openapi-typescript` (typed API client generated from the OpenAPI schema). No packet-crafting library is used; the scanner uses the standard library's sockets.

## 6. Scan execution

### 6.1 States and phases

```
queued ──► running ──► completed
              │  ▲
              │  └──► interrupted   (returns to running without analyst action)
              ├──► cancelled        (results kept, partial)
              └──► failed           (results kept, partial; failed phase recorded)
```

Phases of a running scan, in order: `discovery`, `port_scan`, `enrichment`, `classification`, `diff` (scheduled scans only). The first two are executed by the scanner, the rest by the orchestrator.

`interrupted` is set when the scanner's heartbeat has been missing for 30 seconds while a scan is running, and at orchestrator start-up for every scan found in `running`. The scan returns to `running` when the next finding or chunk result arrives.

### 6.2 Chunks

When a scan is promoted to `running`, the orchestrator:

1. Creates one host-observation row per target, all marked unreachable.
2. Creates the discovery chunk: all targets, with the selected discovery methods (FR-CFG-2).
3. After discovery, creates the port-scan chunks for the hosts that answered, or for all targets when FR-CFG-3 says so.

A port-scan chunk is a group of hosts multiplied by a slice of ports:

- The host group size is the "hosts probed in parallel" parameter (FR-CFG-6), proposed default 64.
- The slice size is chosen so that one chunk holds about 10,000 probes, which is about 10 seconds at the default rate. This is the checkpoint unit of R8.
- Hosts and ports are shuffled with a seed stored on the scan before they are cut (FR-SCN-10). The scanner shuffles again inside the chunk.

A full-range scan of 256 hosts produces about 1,700 chunk rows. Chunk rows are deleted when the scan reaches a final state; their totals are kept on the scan and on the host observations.

### 6.3 Flow of one chunk

```
orchestrator                         scanner
     │  scope re-check (I4), publish chunk ─────►│
     │                                           │ own scope check (6.6)
     │                                           │ probe, rate-limited
     │◄───── findings (every 500 ms) ────────────│
     │  upsert rows, emit UI event               │
     │◄───── chunk done (counts) ────────────────│ ack chunk
     │  mark chunk done, dispatch next           │
```

Rules:

- The orchestrator publishes a chunk with publisher confirmation, then commits the chunk as `dispatched`. A crash between the two leaves the chunk `pending`, so it is published again. The result is at-least-once delivery with no lost work.
- The scanner acknowledges a chunk only after it has published the final result.
- The orchestrator consumes results one at a time, in order, so findings are always recorded before the completion of their chunk.
- A chunk that stays `dispatched` beyond its lease (the larger of 5 minutes and three times its expected duration) is published again. After 5 attempts the scan is failed. This covers a lost broker volume.

### 6.4 Idempotency and recovery

Every write caused by a message is safe to repeat (NFR-REL-1):

| Write | Key | Behaviour on repeat |
|---|---|---|
| Host observation | scan, address | Reachability is OR-ed. Evidence is a JSON object keyed by probe (`icmp`, `tcp:443`), merged with `||`. |
| Port observation | scan, address, port | `ON CONFLICT DO NOTHING`. |
| Chunk result (closed and filtered counts per host) | scan, chunk number | Stored only if the chunk is not already done. Counts are summed onto host observations once, when the port-scan phase ends. |
| Enrichment snapshot, classification result | scan, address, run number | `ON CONFLICT DO NOTHING`. |
| Traceroute probe | run, TTL, probe number | `ON CONFLICT DO NOTHING`. |

Observation writes are accepted only while the scan is in a probing phase. This is how a completed scan stays immutable (FR-SCN-9): late or duplicate messages are discarded.

Recovery cases (FR-SCN-7, NFR-REL-2, acceptance criterion 4):

| Failure | What happens |
|---|---|
| Scanner crashes | The broker redelivers the unacknowledged chunk when the scanner reconnects. At most one chunk is probed twice; duplicates collapse on the keys above. |
| Orchestrator crashes | Chunks and results wait in durable queues. On restart, the tick continues from database state. |
| Whole stack restarts | Queues are durable and messages persistent. Both of the above apply. |
| Post-probe phase fails | The scan is marked failed with the phase recorded. Probe results are untouched. The analyst retries that phase alone (NFR-REL-3). |
| Message fails repeatedly | After 5 deliveries it moves to the dead-letter queue. The orchestrator consumes that queue, fails the scan or run, and raises an alert (NFR-REL-4). |

### 6.5 Concurrency and rate limits

- **Queue.** The orchestrator promotes the oldest `queued` scan whenever fewer than the configured maximum are running or interrupted. Manual and scheduled scans share the queue.
- **Fairness.** One chunk in flight per scan means concurrent scans progress side by side.
- **Rate.** The scanner holds one token bucket per scan and one global bucket. Every probe, including traceroute probes, takes a token from both. The per-scan rate arrives in the chunk and is clamped to the operator ceiling in the scanner's own environment (FR-CFG-7).

### 6.6 Scope enforcement

One pure module, `ndm.core.scope`, implements strict parsing, the public-address test and containment. It is used at four points:

| Point | Component | Checked against | On failure |
|---|---|---|---|
| Scan or traceroute creation | api | Current project scope, public-address table, never-scan list | Request rejected, audit entry |
| Scheduled run start | orchestrator | Same | Run rejected, schedule flagged, alert, audit entry (FR-SCH-5) |
| Each chunk dispatch | orchestrator | Current project scope; project still active; scan not cancelled | Scan failed with `scope_changed`, audit entry |
| Before the first probe of each chunk | scanner | Scope carried in the chunk, its own public-address table, its own copy of the never-scan list | Whole chunk rejected, nothing sent, scan failed, audit entry |

Details:

- **Fail closed.** A missing scope, a parse error or an unreachable database at any point means nothing is sent.
- **Public-address test.** An explicit table of non-public ranges, covering the list in FR-PRJ-3 plus the remaining IANA special-purpose blocks (192.0.0.0/24, 198.18.0.0/15). Library helpers such as `is_global` are not used because their meaning has changed between Python versions.
- **Strict parsing (NFR-SEC-5).** CIDR blocks with host bits set, leading zeros, reversed ranges and mixed notations are rejected.
- **Never-scan list (FR-PRJ-6).** An operator environment value, read independently by the API, the orchestrator and the scanner.
- **Test switch (NFR-SEC-6).** `NDM_ALLOW_NON_PUBLIC_TARGETS` must be set on all three processes to take effect. Each logs it at start-up; the orchestrator writes the audit entry, including the value the scanner reports in its hello message.

### 6.7 Cancellation

The API sets `cancel_requested_at` on the scan and notifies the orchestrator. The orchestrator stops dispatching and broadcasts a cancel message to the scanner, which stops sending for that scan and returns what it has. If the broadcast is lost, probing still ends with the chunk in flight, at most about 10 seconds later.

### 6.8 Phases after probing

The orchestrator runs these in-process, one scan at a time per phase:

1. **Enrichment.** Reverse DNS for every reachable host, then local enrichment for any host that missed it inline.
2. **Classification.** Section 10.2.
3. **Diff.** Scheduled scans only; section 10.3.

Each phase reads only stored data and writes with the keys in 6.4, so a phase interrupted by a crash is simply run again.

## 7. Traceroute execution

Traceroute follows the same pattern as a chunk, with its own queue so that traceroutes never wait behind scan chunks. The queue's prefetch equals the maximum number of concurrent traceroutes.

1. The API validates that the target is in scope and creates the run. The orchestrator publishes the job with the scope attached.
2. The scanner checks the scope, then probes TTL by TTL and publishes one message per TTL as soon as its probes are answered or timed out (FR-TRC-6).
3. For each responding address the orchestrator stores the probe rows, adds the address to the global host table, writes an enrichment snapshot and a reverse-DNS result, and emits a UI event. Private and CGNAT addresses are stored without enrichment and flagged as unlocatable (FR-TRC-4).

## 8. Scanner worker

### 8.1 Structure

One asyncio process. It consumes the chunk queue, the traceroute queue and the cancel broadcast, and shares one set of sockets and one receive loop across all work in flight.

At start-up it tries to open its raw sockets and reports what worked in a hello message: SYN capability, raw ICMP capability, vantage-point name and egress address. It repeats this as a heartbeat every 10 seconds, with counters for probes sent, replies received, receive-buffer drops and current rate. The orchestrator stores the capabilities on the vantage point and exports the counters as metrics, so the scanner needs no listening port.

### 8.2 Techniques

| Technique | Send | Receive and match |
|---|---|---|
| TCP SYN | Raw TCP socket; the kernel adds the IP header and routes. Header built from a template, checksum computed in Python. No payload. | Capture socket with a kernel packet filter that passes only TCP segments addressed to the scanner's source-port range. Each in-flight chunk has its own source port; the reply's destination port selects the chunk, and the acknowledgement number must equal the keyed hash plus one. SYN/ACK means open, RST means closed, silence after retries means filtered. |
| TCP connect | `asyncio.open_connection` with a timeout, under a concurrency limit. | Connected means open, and the connection is closed immediately with nothing sent or read (FR-SCN-3). Refused means closed. Timeout means filtered. |
| ICMP echo | Raw ICMP socket, identifier and sequence chosen by the scanner. | Echo reply matched on identifier and sequence. |
| Traceroute (ICMP, TCP, UDP) | The same sockets with a per-probe TTL. UDP probes carry a zero-length payload. | ICMP time-exceeded and unreachable messages on the raw ICMP socket, matched through the original header they quote. |

Technique selection (FR-CFG-5): the orchestrator resolves `automatic` once, at scan start, from the capabilities the scanner reported, and records the result on the scan. A forced SYN scan is rejected at creation when the capability is absent.

### 8.3 Sustaining the rate (R1)

- The sender paces in small bursts from the token bucket; the receive loop drains the capture socket on every readiness event.
- The kernel filter is essential under host networking: without it the socket would receive a copy of all TCP traffic on the host.
- The receive buffer is raised to the system maximum, and the kernel's drop counter is read and reported. A non-zero drop count during a scan is recorded as a warning on that scan (NFR-PRF-2).
- The source-port range (proposed 61000–61999) must lie outside the kernel's ephemeral range. The scanner checks this at start-up and withdraws its SYN capability if they overlap.

Pure-Python packet construction is expected to reach several times the 5,000 packets-per-second ceiling, but this is the main technical risk and is validated first (section 17, phase 0).

### 8.4 Privileges (NFR-SEC-2)

- Compose drops all capabilities and adds `NET_RAW` only.
- The process runs as a non-root user. A non-root process does not receive container capabilities automatically, so the image sets the `cap_net_raw` file capability on its own copy of the Python interpreter.
- `no-new-privileges` must **not** be set on this container: it disables file capabilities. It is set on every other container.

### 8.5 Host networking (R2)

With host networking the scanner sends from the host's own address, with no NAT in between.

Two effects must be handled:

- **Half-open teardown.** The host kernel answers an unexpected SYN/ACK with a reset. This is the desired behaviour: it closes the half-open connection at the target.
- **Connection tracking.** Docker loads the kernel's connection-tracking module, which then also tracks packets sent from raw sockets. Unanswered SYNs stay in the table for about two minutes; at 5,000 packets per second against filtered targets that is roughly 600,000 entries, above the usual default limit, and a full table disrupts all networking on the host. The mitigation is two `NOTRACK` rules for the scanner's source-port range, installed by the one-shot `scanner-netinit` container (host network, `NET_ADMIN`, exits after installing). Receiving on a capture socket rather than a raw TCP socket keeps reception independent of the host firewall's treatment of untracked packets.

Both points are confirmed in phase 0 before anything else is built on them.

The isolation cost is that a compromised scanner sees the host's network namespace. It is limited by the single capability, the non-root user, no listening ports and no database credentials.

## 9. Messaging

### 9.1 Topology

| Exchange | Queue | Publisher → consumer | Content |
|---|---|---|---|
| `ndm.scanner` (direct) | `scanner.<vantage>.chunks` | orchestrator → scanner | Scan chunks |
| `ndm.scanner` (direct) | `scanner.<vantage>.traceroutes` | orchestrator → scanner | Traceroute jobs |
| `ndm.scanner.control` (fanout) | exclusive, per scanner | orchestrator → scanner | Cancel |
| `ndm.results` (direct) | `orchestrator.results` | scanner → orchestrator | Hello and heartbeat, findings, chunk done, chunk rejected, traceroute hop, traceroute done |
| `ndm.commands` (direct) | `orchestrator.commands` | api → orchestrator | Notifications that state changed: scan created, cancel requested, traceroute requested, re-run requested, dataset update requested |
| `ndm.events` (topic) | exclusive, per API process | orchestrator → api | Transient UI events |
| `ndm.dlx` (fanout) | `ndm.dead` | broker → orchestrator | Dead-lettered messages |

### 9.2 Rules

- Work and result queues are durable quorum queues with a delivery limit of 5 and the dead-letter exchange set. Messages are persistent. Publishers use confirms; consumers acknowledge manually after processing.
- A message that fails validation is rejected without requeue and goes straight to the dead-letter queue. A transient failure (database unavailable) is retried after an in-process backoff by negative acknowledgement with requeue; the delivery limit bounds the retries.
- Commands from the API carry only identifiers. The API commits to the database first and then publishes. If the publish is lost, the orchestrator's tick (every 5 seconds) finds the change anyway (AD1).
- UI events and the cancel broadcast are not durable. Losing one delays an update; it does not lose data.
- Message bodies are JSON, defined as Pydantic models in `ndm.core.messages`, each with a schema version, a message identifier and the scan or traceroute identifier used for log correlation.
- All exchanges and queues are declared idempotently by each process at start-up.
- Queue names include the vantage-point name, so a second scanner later gets its own queues with no change to the topology.

### 9.3 Chunk message

```json
{
  "schema": 1,
  "scan_id": 4812,
  "chunk_seq": 17,
  "phase": "port_scan",
  "technique": "syn",
  "targets": ["198.51.100.7", "198.51.100.23"],
  "ports": [443, 22, 8080],
  "discovery": null,
  "pps": 1000,
  "timeout_ms": 1500,
  "retries": 1,
  "seed": 91823,
  "attestation_id": 31,
  "scope": [["198.51.100.0", "198.51.100.255"]]
}
```

## 10. Data model

All timestamps are `timestamptz` in UTC. Constraints (foreign keys, `CHECK`, `UNIQUE`, `NOT NULL`) are declared in the database. Fixed sets that must be extensible without migration (device type, alert type, audit action) are `text` validated by the application; closed sets (scan state, phase) are PostgreSQL enums.

### 10.1 Tables

| Table | Key | Main content |
|---|---|---|
| `account` | id | Username, argon2id hash. One row. |
| `session` | token hash | Account, created, last seen, expires. |
| `project` | id | Name, description, state, current attestation, `deleted_at`. |
| `attestation` | id | Project, account, timestamp. Immutable. One per scope version. |
| `scope_entry` | id | Attestation, kind (address, CIDR, range), original text, start and end address. |
| `scan_profile` | id | Name, parameter set (`jsonb`). |
| `schedule` | id | Project, targets, parameters, interval or cron expression with time zone, enabled, flag reason, `next_run_at`, last outcome. |
| `schedule_run` | id | Schedule, planned time, outcome (started, skipped, rejected), scan. |
| `vantage_point` | id | Name, egress address, location, capabilities, last heartbeat. |
| `scan` | id | Project, schedule, attestation, vantage point, state, phase, targets (`inet[]`), probed ports (`int4multirange`), parameters (`jsonb`), technique requested and used, seed, dataset versions, counters, partial flag, failure reason, timings, `cancel_requested_at`, `deleted_at`. |
| `scan_chunk` | scan, chunk number | Phase, specification, state, dispatch time, attempts, result. Deleted when the scan ends. |
| `host` | address | Global registry (I1): first and last seen, public flag. Holds scan targets that answered and all traceroute hops. |
| `project_host` | project, address | Type override, tags (`text[]`), notes, first and last seen in the project, latest scan that targeted the address. |
| `host_observation` | scan, address | Vantage point, reachable, evidence (`jsonb`), closed count, filtered count, PTR names, PTR status. One row per target, reachable or not. |
| `port_observation` | scan, address, port | An open port. No other columns. |
| `enrichment_snapshot` | id | Owner (scan or traceroute run) and address, run number, latest flag, ASN, AS holder, network name, organization and its source, registry, registration country, location country and city, point (`geography`), accuracy radius, unavailable fields, dataset versions. |
| `classification_result` | id | Scan, address, run number, latest flag, device type, sub-role, confidence, fired rules with evidence (`jsonb`), ruleset version. |
| `traceroute_run` | id | Project, target, method, parameters, vantage point, state, timings. |
| `traceroute_probe` | run, TTL, probe number | Responding address or null, round-trip time, reply kind. Loss and the set of responders per hop are derived on read. |
| `scan_diff` | id | Base scan, target scan, compared ports, addresses not comparable, summary counts. Stored for automatic diffs only. |
| `change_event` | id | Diff, address, kind, port, before, after, dataset-change flag. |
| `alert` | id | Project, type, summary, link target, created, read. |
| `reference_dataset` | id | Source, version, load time, status. |
| `rir_inetnum` | id | Registry, range, network name, organization, country. |
| `rir_delegation` | id | Registry, range, country, date, status. All five registries. |
| `audit_event` | id | UTC timestamp, actor, action, object type and identifier, details (`jsonb`). No foreign keys, so entries survive deletions. |

### 10.2 Design notes

- **Partitioning.** `host_observation` and `port_observation` are hash-partitioned by scan into 16 partitions. Every hot query selects by scan and touches one partition.
- **Indexes on the large tables.** `port_observation` has only its primary key. `host_observation` has one secondary index on (address, scan), which serves the host timeline (FR-MAP-9) and the global host view; open ports for a timeline are then read through the primary key.
- **Latest snapshot.** Re-running enrichment or classification adds a row with the next run number and moves the latest flag (FR-ENR-7, FR-CLS-7). A partial unique index on (scan, address) where the flag is set gives a direct join from an observation to its current snapshot.
- **Probed ports as a multirange.** "Was this port probed in both scans" (FR-HIS-4) is the intersection of two columns.
- **Effective device type.** The override in `project_host` if present, otherwise the latest classification. Both are returned (FR-CLS-5).
- **Project-wide view (FR-MAP-5).** `project_host` points at the latest scan that targeted each address; the view joins from there. If this join proves too slow at 65,536 hosts, the fallback is to copy the display columns onto `project_host` at scan completion.
- **PostGIS.** Used for the location point only. No spatial index is created until a query needs one; the map loads points by scan or project, not by area.
- **Scope at the scan.** A scan references the attestation it was validated against (FR-SCN-8); attestations and their entries are never updated.
- **Database roles.** Migrations run as the owning role. The application role has no `UPDATE`, `DELETE` or `TRUNCATE` on `audit_event`, and a trigger rejects them as a second barrier (FR-AUD-3).
- **Deletion (I3).** A purge task in the orchestrator removes the rows of deleted scans and projects in batches. `host` rows and audit entries stay.
- **IPv6 (C-5).** Address columns are `inet`. The IPv4-only parts are the RIR range column, the public-address table and the scanner.

### 10.3 Sizing check against A7

| Table | Rows | Estimate |
|---|---|---|
| `port_observation` | 250 million | About 13 GB of rows and 8–10 GB of primary-key index |
| `host_observation` | 50 million | About 10–15 GB, depending on evidence size |
| `enrichment_snapshot`, `classification_result` | Up to 50 million each, in practice the reachable share | 5–10 GB each at the upper bound |
| `scan` | About 200,000 | Negligible |

These fit one PostgreSQL instance on ordinary hardware. See question Q1 in section 19 for the one case that can break the envelope.

## 11. Orchestrator functions

### 11.1 Enrichment and reference datasets

| Field | Source |
|---|---|
| Country, city, coordinates, accuracy radius | GeoLite2 City |
| ASN, AS holder (shown as "ISP", FR-ENR-3) | GeoLite2 ASN |
| Registry, registration country | Delegation statistics, all five RIRs |
| Network name, organization | Most specific `rir_inetnum` range covering the address; falls back to the AS holder, with the source recorded |
| Reverse DNS | `dnspython` asynchronous resolver against the configured resolver; can be switched off globally (R10) |

- **GeoLite2.** The orchestrator downloads the databases with the operator's licence key, verifies the published checksum, replaces the file atomically on a volume only it mounts, and reopens the reader. The version is the build date in the file. A daily check keeps the installation inside the licence's update window (NFR-LIC-1).
- **RIR data.** Downloaded weekly by default. Each registry is parsed and loaded in one transaction that replaces that registry's rows, so readers never see a half-loaded set. Lookup uses a GiST index on an IPv4 range column.
- **Uneven coverage (R3).** Detailed objects are loaded for RIPE, APNIC and AFRINIC. For ARIN and LACNIC only delegation statistics are loaded unless the operator supplies bulk-access files; the loader accepts them from a configured path. Exact file locations and terms are verified during implementation.
- **Availability (FR-ENR-7).** A missing or failed dataset marks the affected fields as unavailable in the snapshot. Scanning continues. A re-run writes a new snapshot.
- Every snapshot records the dataset versions it used; the scan records the versions current when it started (FR-SCN-8).

### 11.2 Classification

- The ruleset is a YAML file in the package with a version, the taxonomy, and a list of rules. Each rule has conditions over the allowed inputs (open ports, PTR patterns, enrichment attributes, seen-as-hop; FR-CLS-2) and asserts a device type, an optional sub-role and a confidence.
- For each type, the confidences of the fired rules are combined as `1 − ∏(1 − c)`. The highest-scoring type wins; below the configured threshold the type is `unknown`.
- The result stores the fired rules with the evidence each matched and the ruleset version (FR-CLS-3).
- "Seen as an intermediate hop" is a lookup on `traceroute_probe` by responder address, excluding rows where the address is the run's target.
- Top-port lists and IANA service names are data files in the package with recorded provenance (R4, NFR-LIC-2). The port lists are curated for this project and are not derived from nmap's frequency data.

### 11.3 Comparison and alerts

One function computes a diff from two scan identifiers. It is used for automatic diffs, manual comparison, the "change status" filter (FR-MAP-6) and exports.

- **Addresses compared.** Those targeted by both scans. The rest are listed as not comparable (FR-HIS-2).
- **Ports compared.** The intersection of the two scans' probed ports, stated in the result (FR-HIS-4). For an address that was not port-scanned in one of the scans (unreachable under "scan only discovered hosts"), port changes are not reported; the change appears as the host appearing or disappearing.
- **Enrichment changes.** Flagged as dataset-related when the dataset version behind the changed field differs between the two snapshots (FR-HIS-5).
- **Automatic diff.** After a scheduled scan completes, the baseline is the previous scan of the same schedule that completed and is not partial (FR-HIS-6). The result is stored, because an alert must keep pointing at what was detected at the time.
- **Manual comparison.** Computed on request and not stored. With at most 256 addresses per scan it is well inside 5 seconds (NFR-PRF-5).
- **Alerts.** Raised for a non-empty automatic diff and for failed, skipped and rejected scheduled runs (FR-HIS-7).

### 11.4 Scheduler

A tick every 15 seconds selects enabled schedules whose `next_run_at` has passed, using `FOR UPDATE SKIP LOCKED`. For each one, in a single transaction:

1. If the previous run is still active, record a skipped run and raise an alert (FR-SCH-3).
2. Otherwise run the scope check. On failure, record a rejected run, flag the schedule and raise an alert (FR-SCH-5).
3. Otherwise create the scan in `queued`.
4. Set `next_run_at` to the first occurrence strictly after the current time. After downtime this yields exactly one catch-up run (FR-SCH-4).

The minimum interval is enforced when a schedule is saved; for cron expressions, by checking the gaps between upcoming occurrences (FR-SCH-6).

### 11.5 Single instance

The orchestrator takes a PostgreSQL session-level advisory lock at start-up and does nothing until it holds it. A second instance started by mistake stays idle.

## 12. API

### 12.1 Conventions

- Prefix `/api/v1`. JSON only. Pydantic v2 request and response models, kept separate from ORM models. One error shape for all failures; no internals in error bodies.
- Collection endpoints paginate and have default limits.
- One database session per request through dependency injection; SQLAlchemy 2.0 async with explicit loading.
- Authentication is a router-level dependency applied to everything except `/healthz` and the login and first-run endpoints, so a new endpoint is protected by default (FR-ACC-1).

### 12.2 Resources

| Area | Endpoints |
|---|---|
| Access | First-run setup, login, logout, password change, current session |
| Configuration | Tile source and attribution, parameter ceilings, scanner capabilities |
| Projects | CRUD, archive; current scope, replace scope with attestation, scope history |
| Scans | List, create, estimate (probe count and duration, FR-CFG-9), detail, cancel, delete, retry failed phase, re-run enrichment, re-run classification |
| Hosts | Table rows for a scan or the project-wide view, with filters, sorting and paging; map points for the same filters; host detail with timeline; annotation update; global host list |
| Scan profiles, schedules | CRUD; enable and disable schedules |
| Traceroutes | Start for a host, detail, list per host |
| Comparison | Diff of two scans; stored diff by identifier |
| Alerts | List, mark read, unread count |
| Export | `format=csv` or `format=json` on the host, diff and traceroute endpoints; streamed; same filters; metadata header (FR-EXP-4) |
| Audit, datasets | Filterable audit list; dataset versions and ages, trigger update |
| Events | One server-sent event stream |

The table rows and the map points come from one query builder, so filters always apply to both together (FR-MAP-6, FR-MAP-8).

### 12.3 Authentication

- **First run.** While no account exists, the API prints a one-time setup token to its log; the setup page requires it. This stops a stranger from claiming a fresh instance that is already reachable.
- **Sessions.** Random 256-bit token in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie; only its hash is stored. Idle expiry is configurable. A password change ends all other sessions.
- **Cross-site requests.** The strict cookie, a JSON-only API and no CORS allowance together prevent them; no separate token is needed.
- **Throttling (FR-ACC-4).** Failed logins are audit events; the login endpoint counts recent ones and delays or refuses accordingly.

### 12.4 Live updates

- The API holds one exclusive queue on `ndm.events` and forwards events to the connected browser as server-sent events. With one analyst there is one stream carrying everything; the client filters by project and scan.
- Event types: scan state and progress, hosts changed (with slim rows: address, coordinates, device type, open-port count), traceroute hop, alert created.
- Budget for FR-MAP-7: scanner flush at most 500 ms, then broker, database write and event in tens of milliseconds, leaving more than a second of margin.
- Events are not replayed. On reconnect the client reloads the current view through REST.
- The proxy does not buffer the event route.

## 13. Frontend

- Vue 3 with `<script setup lang="ts">`, Vuetify 3, Pinia, Vue Router, Vite; TypeScript strict mode. API types are generated from the OpenAPI schema.
- **Views.** Setup and login; project list; project workspace (map, host table, filter bar, scan selector); host detail panel; scan list and launch dialog; comparison; schedules; alerts; audit log; settings (datasets, password); global hosts.
- **Stores.** Session; projects; workspace (selected scan, filters, hosts); alerts. Filters are mirrored in the URL so a view survives a reload.
- **Composables.** `useEventStream` (connection, reconnect, dispatch), `useHostFilters`, `useMap`.
- **Map.** MapLibre GL with a raster tile source taken from configuration (FR-MAP-11) and a clustered GeoJSON source.
  - Clicking a cluster that cannot be split further opens a list of its hosts (FR-MAP-2).
  - Hosts without coordinates go to the "unlocated" panel (FR-MAP-3).
  - Selecting a host draws its accuracy radius as a circle polygon (FR-MAP-10).
  - A traceroute path is a line layer; segments that skip silent or unlocated hops are dashed (FR-TRC-6).
- **Live updates.** With no filter active, changed hosts are applied to the store directly. With filters active, the client refetches the filtered set, debounced to 500 ms, so filter logic exists only on the server.
- Location is labelled approximate wherever it is shown, with the radius (FR-ENR-8). IANA service names are labelled as nominal (FR-SCN-3). MaxMind and tile attribution are shown (NFR-LIC-1, NFR-LIC-3).

## 14. Security

| Concern | Measure |
|---|---|
| Fail-closed scope | Section 6.6 |
| Fail-closed authentication | Router-level dependency, section 12.3 |
| Raw-socket privilege | Scanner only, single capability, non-root (section 8.4) |
| Network exposure | Only the proxy publishes a port (443). RabbitMQ is published on the host's loopback address only, for the scanner. PostgreSQL is not published. |
| Secrets | Environment only; redacted at the logging boundary (NFR-SEC-3) |
| Input | Validated by Pydantic at the API boundary; strict address parsing; SQL only through SQLAlchemy parameters |
| Audit | Written in the same transaction as the action it records; append-only at the database level |
| Containers | Non-root, read-only root filesystem where possible, `no-new-privileges` except on the scanner, resource limits |
| Dependencies | Pinned through `uv.lock` and the frontend lockfile; vulnerability scan in CI |

## 15. Deployment and operation

### 15.1 Compose services

| Service | Image | Network | Notes |
|---|---|---|---|
| `proxy` | Caddy, pinned | internal, publishes 443 | Internal CA certificate by default; ACME when a public domain is configured. Serves the built SPA. |
| `api` | `ndm-backend` | internal | Health check on `/healthz`. |
| `orchestrator` | `ndm-backend` | internal, outbound internet | Mounts the reference-data volume. |
| `scanner` | `ndm-scanner` | host | `cap_drop: ALL`, `cap_add: NET_RAW`, raised file-descriptor limit. Health check on a heartbeat file. |
| `postgres` | PostGIS, pinned | internal | Named volume. PostgreSQL 14 or later is required for multiranges. |
| `rabbitmq` | RabbitMQ 4.x, pinned | internal, loopback publish | Named volume. |
| `migrate` | `ndm-backend` | internal | One-shot; `api` and `orchestrator` start after it succeeds. |
| `scanner-netinit` | minimal, pinned | host | One-shot; `NET_ADMIN`; section 8.5. |

`ndm-scanner` installs only the core and scanner dependencies: no SQLAlchemy, no FastAPI. An import rule in CI keeps `ndm.scanner` from importing `ndm.db` or `ndm.api` (C-3).

### 15.2 Configuration

All configuration is environment variables read through Pydantic settings, prefixed `NDM_`. Groups:

- Database and broker URLs.
- Session idle timeout.
- Operator ceilings: global rate, concurrent scans, concurrent traceroutes, minimum schedule interval, project scope ceiling (FR-CFG-7, A1).
- Never-scan list.
- Scanner: vantage-point name, egress address, source-port range.
- Enrichment: MaxMind account and licence key, dataset update intervals, PTR resolver and on/off switch.
- Map: tile URL template and attribution. There is no default tile source; the operator must choose a provider whose policy permits the use (R6).
- Test switch: `NDM_ALLOW_NON_PUBLIC_TARGETS`.

### 15.3 Host preparation

Documented for the operator and verified by the scanner at start-up where possible:

- Raise `net.core.rmem_max` so the scanner can enlarge its receive buffer.
- Keep the scanner's source-port range outside `net.ipv4.ip_local_port_range`.
- Allow `scanner-netinit` to install its two rules, or apply the equivalent rules by hand.

### 15.4 Observability

- **Logs.** `structlog`, JSON, with the scan or traceroute identifier bound from the message into every log line of every process (NFR-OPS-2).
- **Metrics.** Prometheus format on the API and the orchestrator, reachable on the internal network only. The orchestrator exports the scanner's counters from its heartbeat, plus queue depth, scan durations and outcomes, and dataset age (NFR-OPS-3).
- **Health.** HTTP health checks on API and orchestrator; heartbeat file for the scanner; native checks for PostgreSQL and RabbitMQ.

### 15.5 Operator documentation to deliver

Host preparation; backup and restore of the database (NFR-OPS-5); the fact that reverse DNS sends target addresses to the resolver (FR-ENR-4); choosing a tile provider; obtaining a MaxMind key; expected abuse reports (R7).

## 16. Testing

| Layer | Scope | Environment |
|---|---|---|
| Unit | Scope parsing and containment, chunk planning, rule evaluation, diff computation, schedule arithmetic, packet building and reply matching | Pure functions, no I/O |
| Integration | API endpoints, orchestrator handlers, idempotency under duplicate and reordered messages, scheduler rules, audit immutability | Real PostgreSQL and RabbitMQ through `testcontainers`; transaction rollback per test where the code under test allows it (NFR-TST-1) |
| Scanner end to end | Discovery, SYN and connect scans, the three traceroute methods, exact open-port sets, no payload in captures, recovery after killing the scanner and after restarting the stack | Compose lab: isolated bridge network with target containers of known open ports, scanner attached to that network, test switch on (NFR-TST-2, acceptance criteria 2, 3, 4, 8) |
| Frontend | Stores and composables; components with logic | Vitest; `vue-tsc` and ESLint in CI |
| Load | Sustained rate with zero receive drops; query times on a database seeded to the A7 envelope | Lab network; seeded database (NFR-PRF-1, NFR-PRF-4) |

CI runs lint (`ruff`), type checks (`mypy`, `vue-tsc`), unit and integration tests and a dependency scan on every change. The lab suite runs on demand and before a release, because it needs raw-socket privileges.

## 17. Repository layout and delivery

```
backend/
  pyproject.toml, uv.lock
  src/ndm/
    core/          settings, logging, scope, message models, port lists, ruleset
    db/            ORM models, session, Alembic migrations
    api/           routers, schemas, authentication, event stream
    orchestrator/  tick, scan lifecycle, result handlers, scheduler,
                   enrichment, datasets, classification, diff, purge
    scanner/       worker, rate limiter, SYN, connect, ICMP, traceroute
  tests/
frontend/          Vue application
deploy/            compose files, Caddyfile, Dockerfiles, lab network
docs/              operator guide
```

Delivery follows the specification's four phases, with one phase added in front and two adjustments:

| Phase | Content | Note |
|---|---|---|
| 0 | Scanner spike: SYN send and receive at 5,000 packets per second against the lab; connection-tracking behaviour under host networking; non-root with file capability; raw versus capture socket for receiving. | Exit criterion: no missed and no false open ports at the ceiling in three consecutive lab runs. If it cannot be met, a native-language scanner is raised with the product owner as a deviation under C-1 before phase 1 continues. |
| 1 | As specified. Includes the broker topology, chunking, findings streaming, the event stream and the vantage-point heartbeat. | The event stream moves here from phase 2 because the host table already needs live progress. |
| 2 | As specified. | Inline enrichment at discovery (I5) is part of this phase. |
| 3 | As specified. | |
| 4 | As specified. | |

## 18. Resolution of the specification's open issues

| # | Issue | Resolution |
|---|---|---|
| R1 | Python raw-socket rate | Standard-library sockets, stateless matching, kernel packet filter (section 8). Validated in phase 0 with a defined escalation. |
| R2 | Container networking for SYN | Host networking for the scanner only, with a connection-tracking bypass (section 8.5). |
| R3 | Uneven RIR data | Detailed data where published in bulk; delegation statistics for all five; AS-holder fallback with the source recorded; optional operator-supplied bulk files (section 11.1). |
| R4 | Port lists | Own curated lists with recorded provenance (section 11.2). Curating the top-1000 list is real work and needs a task in the plan. |
| R5 | Classification quality | Confidence threshold, evidence stored and shown, `unknown` as an honest default (section 11.2). |
| R6 | Tile provider | Configurable source and attribution, no default (section 15.2). |
| R7 | Abuse exposure | Rate ceilings, never-scan list checked in three places, audit log, operator documentation. |
| R8 | Checkpoint granularity | A chunk of about 10,000 probes: about 1,700 chunk rows for the largest scan and at most about 10 seconds of repeated probing (section 6.2). |
| R9 | Live transport | Server-sent events (AD9). |
| R10 | Reverse DNS exposure | Configurable resolver and a global off switch (section 11.1). |

## 19. Open questions for the product owner

None of these blocks planning; each has a default the design already uses.

| # | Question | Default used |
|---|---|---|
| Q1 | Some hosts answer on every port. A /24 of them scanned with the top-1000 profile every hour writes about 6 million port rows a day and exhausts the A7 envelope in weeks. Should stored open ports per host be capped, with the host flagged as "responds on all ports"? This would relax FR-SCN-2. | No cap. Every open port is stored, as FR-SCN-2 requires. |
| Q2 | Are the readings I1–I6 in section 3 correct? I2 (no probing of out-of-scope hops) and I4 (a scope reduction stops a running scan) are the two with safety impact. | As written in section 3. |
| Q3 | Should the "change status" filter for a manual scan compare against the latest earlier completed scan of the project, restricted to common addresses? | Yes. |
| Q4 | Is one-time host preparation by the operator (section 15.3) acceptable for a product described as a single Compose stack? | Yes. |
