# Network Discovery Map — Delivery Plan

| | |
|---|---|
| Version | 1.0 |
| Date | 2026-11-14 |
| Status | Proposed; input to task breakdown and scheduling |
| Based on | Network Discovery Map — Technical Specification v1.0; Architecture v1.0 |
| Audience | Implementation team; product owner for sections 3 and 4 |

Requirement identifiers (FR-…, NFR-…, C-…, R…, A…) refer to the specification. Identifiers AD…, AS…, I… and Q… refer to the architecture. "AC n" is acceptance criterion n in section 12 of the specification. This document says in what order the system is built and how each step is proven complete; it does not restate the requirements or the design.

## 1. Summary

The work is cut into seven phases. Each phase ends with a stack that starts from a clean checkout and is checked with tests and lab runs that need nothing from a later phase.

| Phase | Result that can be demonstrated | Depends on | Closes | Relative size |
|---|---|---|---|---|
| 0 | Scanner spike: measured proof that the Python SYN engine and host networking work at 5,000 packets per second | — | Gate for R1 and R2 | S |
| 1A | Foundation: the analyst logs in over TLS, creates projects with attested scopes and reads the audit log | — | Access, projects, scope rules, audit base | M |
| 1B | Scan pipeline: manual scans run end to end, survive restarts and appear live in the host table | 0, 1A | AC 2, 3, 4; AC 1 for manual scans | XL |
| 2 | Enrichment and map: hosts carry registration and location data and appear on the map while the scan runs | 1B | AC 5, 7 | L |
| 3 | Traceroute, classification and annotations | 2 | AC 6, 8; AC 1 for traceroutes | L |
| 4 | Schedules, comparison, alerts and export | 3 | AC 9, 10, 11; AC 1 in full | L |
| 5 | Hardening and release: all acceptance criteria on the release candidate, capacity envelope, operator guide | 4 | Version 1 release | M |

```text
0  ─────┐
        ├──► 1B ──► 2 ──► 3 ──► 4 ──► 5
1A ─────┘
```

Phases 0 and 1A do not depend on each other and run in parallel. Nothing in 1A is wasted if phase 0 fails its gate.

Sizes are relative effort, not durations. Calendar dates need the team size and start date, which neither source document gives.

## 2. Rules for every phase

### 2.1 What "independently verifiable" means

- Every check in a phase's validation table can be run at the end of that phase, with only what that phase and earlier phases delivered.
- A requirement that spans phases is cut into parts, and each part is assigned to one phase (section 7). No phase is closed "pending" a later one.
- Checks are automated unless marked Manual or Inspection. Lab and load checks run on demand, because they need raw-socket privileges; everything else runs in CI.

Check methods used in the tables:

| Method | Meaning |
|---|---|
| Unit | Pure functions, no I/O. |
| Integration | Real PostgreSQL and RabbitMQ through `testcontainers` (NFR-TST-1). |
| Lab | The Compose lab: target containers with known open ports, test switch on (NFR-TST-2). |
| Load | Lab network or seeded database, measured against a number. |
| Inspection | Reading configuration, images or packet captures. |
| Manual | A person follows a written script on a clean deployment. |

### 2.2 Completion criteria common to every phase from 1A onward

A phase is complete when its own criteria hold and all of the following hold:

1. CI is green: `ruff`, `mypy`, `vue-tsc`, ESLint, unit tests, integration tests, dependency scan.
2. The stack starts with `docker compose up` from a clean checkout on a clean host, and the phase's demonstration script has been carried out on it.
3. Every migration added in the phase applies, reverts and applies again. A database left by the previous phase migrates forward with its data (NFR-OPS-4).
4. Every action the phase adds from the FR-AUD-1 list writes its audit entry in the same transaction as the action, and a test proves it.
5. Every table the phase adds for scan or project data is covered by the deletion purge and its test (I3). This applies from 1B.
6. Every endpoint the phase adds is covered by the route-enumeration authentication test (check 1A.3).
7. No check in the phase's validation table fails or is skipped. A check that cannot be run is recorded with its reason and accepted by the product owner.
8. The operator guide covers every configuration value and host-preparation step the phase adds.

## 3. Changes to the phase cut

The architecture keeps the specification's four phases and adds phase 0. This plan makes the further changes below. None of them changes the design.

| Change | Reason |
|---|---|
| Phase 1 is split into 1A and 1B. | Phase 1 is the largest phase and mixes two unrelated kinds of work: the web foundation and the scan engine. 1A needs no scanner, so it can be verified and demonstrated on its own, and it can run while the spike is open. |
| Phase 5 is added. | Several requirements belong to no feature phase: query times at the A7 envelope (NFR-PRF-4), backup and restore (NFR-OPS-5), the operator guide, the licence inventory (NFR-LIC-2), and a single run of all acceptance criteria on the release images. |
| Filters arrive in three steps (FR-MAP-6). | Each filter can only be verified once its data exists: open port, ASN, organization, country and reverse-DNS text in phase 2; device type and tag in phase 3; change status in phase 4. |
| The host detail view grows over three phases (FR-MAP-9). | Evidence and ports in 1B; enrichment, reverse DNS and timeline in 2; classification, override, tags, notes and traceroute history in 3. |
| Scan profiles (FR-CFG-8) move to phase 4. | They are a "should", and their main use is in schedules. |
| The alert for a dead-lettered message moves to phase 4. | The alert list does not exist before phase 4. From 1B the scan is already marked failed with the reason, which is what NFR-REL-4 requires. |
| The `host` and `project_host` tables are created and filled from 1B. | Filling them from the first scan avoids a backfill when the project-wide view and the annotations arrive. |
| A findings-replay harness is added in phase 2. | Lab targets sit in non-public space and have no enrichment data. The harness publishes recorded findings for public addresses to the orchestrator, so enrichment and the map are tested without probing anything. |

## 4. Decisions and prerequisites

### 4.1 Decisions needed from the product owner

Each item has a default the design already uses, so none blocks the start. The column "Needed before" is the last point at which a different answer is still cheap.

| Item | Subject | Default in use | Needed before |
|---|---|---|---|
| I3 | Deletion hides at once and purges in the background; audit entries stay | As written | Start of 1A |
| I1 | One global host per address; annotations per project | As written | Start of 1B |
| I4 | A scope reduction stops a running scan (`scope_changed`) | As written | Start of 1B |
| Q4 | One-time host preparation by the operator is acceptable | Yes | Gate 0, with the list of steps the spike confirms |
| I5 | Local enrichment runs inline when a host is first seen | As written | Start of 2 (medium cost to reverse) |
| I6 | One enrichment row per host per scan, not shared | As written | Start of 2 |
| I2 | Hops are stored, never probed unless in scope and part of a scan | As written | Start of 3 |
| Q3 | "Change status" for a manual scan compares against the latest earlier completed scan | Yes | Start of 4 |
| Q1 | Cap on stored open ports for hosts that answer on every port | No cap | Exit of 5, with the measurement from check 5.5 |

Three proposals made by this plan also need confirmation:

| # | Proposal | Needed before |
|---|---|---|
| P1 | NFR-PRF-4 says the map "stays interactive" with 10,000 hosts but gives no number. Proposed pass condition: pan and zoom hold at least 30 frames per second on the reference workstation, and a filter change redraws within 1 second. | Start of 2 |
| P2 | FR-MAP-7 counts 2 seconds from "recorded"; AC 7 counts from "discovery". The plan measures from the moment the scanner receives the reply, which is the stricter reading. | Start of 1B |
| P3 | FR-TRC-8 (side-by-side traceroute comparison, priority "could") is not planned for version 1. | Start of 3 |

### 4.2 Prerequisites

| Prerequisite | Owner | Needed for |
|---|---|---|
| A Linux lab host that matches the deployment target (kernel, Docker with host networking, root for host preparation). A desktop Docker installation is not a substitute: its host networking behaves differently. | Team | Phase 0, and every lab or load check after it |
| MaxMind account and licence key | Operator / product owner | Phase 2 |
| A tile provider chosen for development and test; there is no default source (R6) | Product owner | Phase 2 |
| A small public range the team is authorized to scan, for the manual checks that need real enrichment data and real routed paths | Product owner | Phases 2 and 3; phase 5 |
| Verified download locations and terms for the five registries' bulk data (R3) | Team | First task of phase 2 |
| A maintained cron-expression library chosen | Team | Phase 4 |

## 5. Phases

### Phase 0 — Scanner spike

**Goal.** Prove the two assumptions the scanner design rests on before anything is built on them: pure-Python SYN scanning at the global ceiling (R1), and host networking with a connection-tracking bypass (R2).

**Depends on.** The lab host.

**Deliverables.**

- The Compose lab, kept for all later phases:
  - target containers with fixed open-port sets, including a host that resets closed ports, a host that drops them, and addresses with no host;
  - a manifest file that lists the expected open ports per address and is the single source of truth for accuracy checks;
  - targets placed one routed hop behind a router container, so unanswered addresses behave as on the Internet instead of failing address resolution on a shared segment. The scanned /24 does not contain the lab gateway.
- Spike scanner: raw TCP send, capture socket with a kernel packet filter, stateless keyed-hash reply matching, token-bucket pacing, read-out of the kernel drop counter, ICMP echo.
- Scanner container set-up: non-root user, all capabilities dropped except `NET_RAW`, file capability on the interpreter, no `no-new-privileges`.
- Prototype of `scanner-netinit` with the two `NOTRACK` rules.
- Spike report: measured rates, drops and processor load; connection-tracking counts with and without the rules; the choice between raw and capture socket for receiving; the host-preparation steps that proved necessary; a go or no-go statement.

Carried into 1B: the lab, the manifest, and the packet builder and reply matcher with their unit tests. The rest of the spike code is disposable.

**Validation.**

| # | Check | Method | Pass condition |
|---|---|---|---|
| 0.1 | Accuracy at the ceiling | Lab, host networking | Top-1000 scan of the lab /24 with "all targets" (256,000 probes) at 5,000 packets per second reports exactly the manifest's open ports, in three consecutive runs. |
| 0.2 | Own replies not dropped | Lab | The kernel drop counter of the capture socket is zero in all three runs of 0.1. |
| 0.3 | Headroom | Load | The highest rate that still gives exact results is recorded. Below 10,000 packets per second it is reported at the gate as a risk, not as a failure. |
| 0.4 | Connection tracking | Lab | Five minutes at 5,000 packets per second against filtered targets: with the rules, the host's connection-tracking count stays flat; without them, the growth is measured and recorded. |
| 0.5 | Half-open teardown, no payload | Inspection of a capture | Every SYN/ACK is followed by a reset from the host; no packet sent to a target carries TCP payload. |
| 0.6 | Privileges | Inspection | The process runs with a non-zero user ID and exactly one effective capability, and SYN scanning works. |
| 0.7 | Source-port guard | Lab | With a source-port range that overlaps the ephemeral range, the scanner reports no SYN capability. |
| 0.8 | Receive path | Lab | Raw and capture sockets are compared with the host firewall active; the choice and its reason are recorded. |

**Completion criteria (Gate 0).**

- Checks 0.1 and 0.2 pass; checks 0.3 to 0.8 are done and their results are in the report.
- The product owner has answered Q4 against the confirmed host-preparation list.
- If 0.1 or 0.2 cannot be met, phase 1B does not start. A native-language scanner is raised with the product owner as a deviation under C-1, and the scanner part of 1B is re-estimated. Phase 1A continues unchanged.

### Phase 1A — Foundation

**Goal.** A deployable stack in which the analyst can log in, define projects with attested scopes and read the audit log. No scanning.

**Depends on.** Nothing. I3 should be confirmed.

**Deliverables.**

- Repository layout as in architecture section 17; one Python package with pinned dependencies; backend and frontend builds; the CI pipeline of section 2.2.
- Compose stack with `proxy` (Caddy, TLS with the internal CA), `api`, `postgres` and `migrate`: pinned images, health checks, resource limits, non-root, `no-new-privileges`, only port 443 published.
- Settings read from `NDM_` environment variables; JSON logging with secrets redacted at the logging boundary.
- Schema: `account`, `session`, `project`, `attestation`, `scope_entry`, `audit_event`. The application role cannot update, delete or truncate `audit_event`, and a trigger rejects the same.
- `ndm.core.scope`: strict parsing, the explicit table of non-public ranges, containment, never-scan list, project scope ceiling (A1).
- API: first-run setup with a one-time token, login, logout, password change, idle expiry, login throttling; project create, rename, describe, archive, delete; scope replace with attestation and scope history; filterable audit list; router-level authentication; one error shape; pagination.
- Frontend: setup, login, project list, scope editor with the attestation confirmation, audit log, password change; API types generated from the OpenAPI schema.
- Audit actions: login, failed login, project creation, archival and deletion, scope change with its attestation, test switch at start-up.

**Validation.**

| # | Check | Method | Pass condition |
|---|---|---|---|
| 1A.1 | Public-address rule | Unit | For every range in FR-PRJ-3 and the further special-purpose blocks, the first and last address are rejected and the addresses just outside are accepted. An entry that partly overlaps a non-public range is rejected as a whole. |
| 1A.2 | Strict parsing | Unit | Host bits set, leading zeros, reversed ranges and mixed notations are each rejected with an error, never normalized (NFR-SEC-5). |
| 1A.3 | Authentication fails closed | Integration | A test enumerates every registered route; each one except `/healthz`, login and first-run setup answers 401 without a session. The test needs no change when an endpoint is added. |
| 1A.4 | First run and sessions | Integration | Setup needs the token and works once; a password change ends other sessions; an idle session expires; repeated failed logins are delayed or refused. |
| 1A.5 | Scope and attestation | Integration | A scope cannot be saved without the confirmation; each save creates a new immutable attestation with the exact entries; never-scan addresses and scopes above the ceiling are rejected. |
| 1A.6 | Audit is append-only | Integration | Update, delete and truncate on `audit_event` fail for the application role and at the trigger. An action that rolls back leaves no audit entry. |
| 1A.7 | Project states | Integration | An archived project rejects writes. A deleted project disappears from every endpoint; its audit entries remain. |
| 1A.8 | Secrets and exposure | Inspection | Configured secret values occur in no log line and no image layer. Only port 443 is published. PostgreSQL is not reachable from outside the internal network. |
| 1A.9 | Demonstration | Manual | On a clean host: setup over TLS, login, project with a scope, a rejected private range, the matching audit entries. |

**Completion criteria.**

- Checks 1A.1 to 1A.9 pass, and the common criteria of section 2.2 hold.
- Covered in full: FR-ACC-1 to 4, FR-PRJ-1 to 5, FR-AUD-2, FR-AUD-3, NFR-SEC-3, NFR-SEC-4, NFR-SEC-5, NFR-OPS-2. Covered for the API only: FR-PRJ-6, NFR-SEC-6.

### Phase 1B — Scan pipeline

**Goal.** Manual scans work end to end: launch, probe, record, recover after any restart, and show results live in the host table.

**Depends on.** Gate 0 passed; phase 1A complete. I1, I4 and P2 should be confirmed.

**Deliverables.**

- Messaging: exchanges and queues of architecture section 9.1 (traceroute queue excluded), versioned message models, quorum queues with delivery limit and dead-letter exchange, publisher confirms.
- Schema: `vantage_point`, `scan`, `scan_chunk`, `host`, `project_host`, `host_observation` and `port_observation` (both hash-partitioned into 16).
- Orchestrator: single-instance lock; tick over database state; queue promotion up to the concurrent-scan ceiling; chunk planning with seeded shuffle; dispatch, lease and re-publish; idempotent result handlers; immutability after completion; `interrupted` detection from the heartbeat; cancellation; scope re-check before each chunk; dead-letter consumer that fails the scan; deletion purge; resolution of the `automatic` technique; metrics endpoint.
- Scanner, production version: discovery by ICMP and TCP; SYN and connect port scan; per-scan and global token buckets; its own scope check before each chunk with its own never-scan list; findings flushed every 500 ms; hello and heartbeat with capabilities and counters; cancel handling; health file. Separate `ndm-scanner` image without database or web dependencies, and the CI import rule (C-3).
- `scanner-netinit`, and the scanner's start-up checks of host preparation.
- Test switch honoured only when set on all three processes, with the audit entry.
- Data files: top-100 and top-1000 port lists and IANA service names, each with recorded provenance and licence (R4, NFR-LIC-2). Curating the top-1000 list has no dependency and should start during phase 0.
- API: scan list, create, estimate, detail, cancel, delete; host rows for a scan with sorting and paging; host detail with evidence, open ports and closed and filtered counts; parameter ceilings and scanner capabilities; the server-sent event stream.
- Frontend: scan list; launch dialog with targets, discovery methods, port profile, technique, tunable parameters and the probe and duration estimate; host table with live progress; host detail panel.
- Audit actions: scan launch, cancellation and deletion; every rejection by a scope check at the API, the orchestrator and the scanner.

Internal checkpoints, in order. They structure the work; they are not gates.

1. Pipeline against a stub scanner that publishes scripted findings: checks 1B.1 to 1B.9 green.
2. Real scanner in the lab: checks 1B.10 to 1B.17 green.
3. User interface: checks 1B.18 and 1B.19 green.

**Validation.**

| # | Check | Method | Pass condition |
|---|---|---|---|
| 1B.1 | Chunk planning | Unit | Every host and port pair of a scan is in exactly one chunk; chunks hold about 10,000 probes; the same seed gives the same plan; the order is not sorted. |
| 1B.2 | Probe estimate | Unit | 256 hosts give 25,600, 256,000 and 16,776,960 probes for the three profiles (NFR-PRF-1). |
| 1B.3 | Packet build and reply match | Unit | A reply is accepted only with the expected acknowledgement number and source-port mapping; unrelated segments are ignored. |
| 1B.4 | Idempotent recording | Integration | Delivering every message of a recorded scan twice, and out of order, leaves the database identical to single in-order delivery (NFR-REL-1). |
| 1B.5 | Immutability | Integration | Findings that arrive after the scan reached a final state change nothing (FR-SCN-9). |
| 1B.6 | Dispatch recovery | Integration | A crash between publish and commit leads to a re-publish; a chunk past its lease is re-published; after five attempts the scan is failed. |
| 1B.7 | Poison message | Integration | An invalid message goes to the dead-letter queue, the scan is failed with the reason, and nothing loops (NFR-REL-4). |
| 1B.8 | Scope enforcement at three points | Integration | Out-of-scope, non-public and never-scan targets are rejected at creation. A scope reduced mid-scan fails the scan with `scope_changed` and keeps its results. A chunk placed on the queue by hand with an out-of-scope target is rejected by the scanner with nothing sent. Each case leaves an audit entry. |
| 1B.9 | Queue, cancel, single instance | Integration | A scan above the concurrent ceiling stays queued until a slot is free. A cancelled scan keeps its results and is marked partial. A second orchestrator stays idle. |
| 1B.10 | SYN accuracy (AC 2) | Lab | A /24 with the top-1000 profile reports exactly the manifest's open ports, and the closed and filtered counts match; the scan records `syn`. |
| 1B.11 | Connect fallback (AC 2) | Lab | The same scan with a scanner started without the capability reports the same open ports and records `connect`. A forced SYN scan is rejected at creation. As decided on 2026-11-19 (AC 2): the same open ports for every host that TCP discovery can find, and the scan records that ICMP discovery was unavailable. |
| 1B.12 | No payload (AC 3) | Inspection of captures | In SYN and in connect mode, no packet to a target carries application data, and connect-mode connections close as soon as they are established. |
| 1B.13 | Scanner killed (AC 4) | Lab | Killing the scanner mid-scan ends in a completed scan without analyst action. Observations equal those of an uninterrupted run; the counters show at most one chunk probed twice. |
| 1B.14 | Stack restarted (AC 4) | Lab | Restarting the whole stack mid-scan gives the same result. The first new finding arrives within 2 minutes of the services being healthy (NFR-REL-2). The same holds when only the orchestrator is killed. |
| 1B.15 | Shared rate | Load | Three concurrent scans plus one queued: the send rate over any 10-second window stays at or below the global ceiling, no scan exceeds its own rate, all three advance, and receive drops are zero. |
| 1B.16 | Many chunks | Lab | A full-range scan of 16 hosts (about one million probes, about 100 chunks) completes with exact results; chunk rows are gone after completion and the totals remain. |
| 1B.17 | Production networking | Lab, host networking | With the production Compose file and `scanner-netinit`, check 1B.10 passes and the connection-tracking count stays flat. |
| 1B.18 | Live table (FR-MAP-7) | Lab | Each host row and progress update reaches the browser within 2 seconds of the scanner receiving the reply. After a dropped connection the view reloads to the correct state. |
| 1B.19 | Demonstration | Manual | On the lab host: launch a scan with the estimate shown, watch the table fill, cancel a second scan, delete a scan, read the audit entries. |

**Completion criteria.**

- Checks 1B.1 to 1B.19 pass, and the common criteria of section 2.2 hold.
- AC 2, AC 3 and AC 4 are met. AC 1 is met for manual scans.
- The scanner image contains no external scanner or diagnostic binary and no database driver (C-2, C-3).
- Metrics show probe rate, queue depth, scan duration and scan outcomes (NFR-OPS-3 without dataset age).

### Phase 2 — Enrichment and map

**Goal.** Every discovered host carries registration and location data from local datasets and reverse DNS, and appears on the map within 2 seconds while the scan runs.

**Depends on.** Phase 1B. I5, I6 and P1 should be confirmed. MaxMind key, tile provider and the authorized public range are available.

**Deliverables.**

- Verification of registry download locations and terms (R3), recorded in the operator guide. This is the first task, because it decides which loaders are built.
- Schema: `reference_dataset`, `rir_inetnum`, `rir_delegation`, `enrichment_snapshot`; reverse-DNS columns on `host_observation`.
- Dataset management: GeoLite2 download with the operator's key, checksum, atomic replacement and reader reopen; delegation statistics for all five registries; detailed objects for RIPE, APNIC and AFRINIC; operator-supplied bulk files for ARIN and LACNIC; one transaction per registry; update schedule, manual trigger, versions and ages; audit entry per update; dataset-age metric.
- Enrichment: inline local enrichment when a host is first seen; the enrichment phase with reverse DNS through the configured resolver, the global off switch and the retry of missed hosts; unavailable fields marked; dataset versions on each snapshot and on the scan; re-run as an additional snapshot.
- Post-probe phase runner: failed phase recorded on the scan, and "retry failed phase" (NFR-REL-3).
- API: one query builder for table rows and map points with the filters open port, ASN, organization, country and reverse-DNS text; project-wide latest-state view; host detail extended with enrichment, dataset versions, reverse DNS and the timeline across scans; dataset endpoints; tile configuration.
- Frontend: project workspace with the MapLibre map, clustering, the list for clusters that cannot be split, the "unlocated" panel, the accuracy-radius circle, scan selector defaulting to the latest scan, filter bar mirrored in the URL, live updates, "approximate" labelling, MaxMind and tile attribution; dataset settings page.
- Test tools: the findings-replay harness; a seeding tool that fills a database to the A7 envelope; a lab resolver with known PTR records.

**Validation.**

| # | Check | Method | Pass condition |
|---|---|---|---|
| 2.1 | Registry parsing and lookup | Unit | Fixture files of each registry parse; the most specific covering range wins; without one, organization falls back to the AS holder and the source is recorded (FR-ENR-3). |
| 2.2 | Dataset update | Integration | A reader during a load sees the complete old set. A failed download or a checksum mismatch keeps the previous version and records the failure. |
| 2.3 | Scanning without datasets | Integration | With no datasets a scan completes and the fields are marked unavailable. A re-run after loading adds a second snapshot and leaves the first unchanged (FR-ENR-7). |
| 2.4 | Reproducible snapshots | Integration | Each reachable host has one snapshot per scan with its dataset versions. A dataset update does not change the snapshots of earlier scans (FR-ENR-5). |
| 2.5 | Phase failure is contained | Integration | A forced failure in enrichment marks the scan failed with the phase recorded and leaves all observations intact; retrying the phase completes the scan. |
| 2.6 | Inline enrichment under redelivery | Integration | Redelivered findings create no second snapshot. |
| 2.7 | Reverse DNS | Lab | PTR names from the lab resolver are stored with the scan. With the switch off, a capture shows no DNS query. |
| 2.8 | No per-address egress (AC 5) | Inspection of a capture | During a replayed scan with dataset updates paused, the only traffic leaving the host from the orchestrator is DNS to the configured resolver. |
| 2.9 | Filters agree | Integration | For generated filter combinations the table endpoint and the map endpoint return the same set of addresses (FR-MAP-6, FR-MAP-8). |
| 2.10 | Live map (AC 7) | Lab with the replay harness | Every host is drawn within 2 seconds of its finding's timestamp, with and without an active filter. |
| 2.11 | Co-located and unlocated hosts (AC 7) | Frontend test and Manual | With 300 hosts on one coordinate, each can be selected from the cluster list. Hosts without coordinates are in the "unlocated" panel. Hosts on the map plus unlocated hosts equal the rows in the table. |
| 2.12 | Map under load (NFR-PRF-4) | Load | With 10,000 hosts loaded, the pass condition of P1 holds in current Chromium and Firefox. |
| 2.13 | Queries at the envelope (NFR-PRF-4) | Load, seeded database | Host table, each filter and the scan list answer within 1 second. The project-wide view answers within 1 second for 65,536 hosts; if it does not, the fallback in architecture section 10.2 is applied and the check repeated. |
| 2.14 | Licences | Inspection | No GeoLite2 file is in the repository or in any image. Attribution is visible. Without a configured tile source the map shows a configuration notice and contacts no tile server. |
| 2.15 | Demonstration | Manual | A scan of the authorized public range: hosts appear on the map as they are found, with ASN, AS holder, organization, country, city, coordinates, radius and dataset versions in the detail view. |

**Completion criteria.**

- Checks 2.1 to 2.15 pass, and the common criteria of section 2.2 hold.
- AC 5 and AC 7 are met.
- The operator guide covers the MaxMind key, the tile provider and the fact that reverse DNS sends target addresses to the resolver (FR-ENR-4).

### Phase 3 — Traceroute, classification and annotations

**Goal.** The analyst can trace the path to a host and see it on the map, and every host has a classification with evidence that the analyst can override and annotate.

**Depends on.** Phase 2. I2 and P3 should be confirmed.

Traceroute is built first, because the classification input "seen as an intermediate hop" reads its data. Apart from that rule the two streams are independent and can be worked on in parallel.

**Deliverables.**

- Lab extension: a chain of router containers in front of a target, one router that stays silent, and hops in private address space.
- Schema: `traceroute_run`, `traceroute_probe`, `classification_result`; override, tags and notes on `project_host`; traceroute runs as owners of enrichment snapshots.
- Scanner: traceroute by ICMP, TCP and UDP with a per-probe TTL; matching through the quoted header; one message per TTL; tokens taken from both rate buckets; the traceroute queue with prefetch equal to the concurrent ceiling.
- Orchestrator: job dispatch with the scope attached; hop recording; every hop address added to the host table; hop enrichment and reverse DNS; private and CGNAT hops flagged as unlocatable; UI events.
- Classification: ruleset version 1 as YAML with taxonomy, rules and confidences; the evaluator; the confidence threshold; the classification phase of a scan; re-run with the current ruleset (FR-CLS-7).
- API and frontend: traceroute start from the host detail with method, port, maximum hops, probes per hop and timeout; live hops; path layer with dashed segments across silent or unlocated hops; traceroute history; classification with confidence and evidence; override set and clear with both values shown; tags and notes; filters by device type and tag.
- Audit actions: traceroute execution, classification override.

**Validation.**

| # | Check | Method | Pass condition |
|---|---|---|---|
| 3.1 | Rule evaluation | Unit | Each rule has a matching and a non-matching fixture. Combined confidence follows the formula; below the threshold the type is `unknown`. Every type and sub-role of FR-CLS-1 is produced by at least one fixture. |
| 3.2 | Ruleset regression | Unit | The ruleset run over a fixed set of host fixtures gives the stored expected output; any difference requires a new ruleset version. |
| 3.3 | Rule inputs | Inspection and Unit | The evaluator receives only open ports, PTR names, enrichment attributes and the hop flag (FR-CLS-2). |
| 3.4 | Stored classification is stable | Integration | Loading a newer ruleset leaves stored results unchanged; a re-run adds a result beside the original (FR-CLS-4, FR-CLS-7). |
| 3.5 | Override and annotations (AC 6) | Integration | An override applies to the address in every scan of the project, including scans made later, and not in another project. The automatic result is still returned. Clearing restores it. Tags and notes do not alter scan data. |
| 3.6 | Traceroute scope (AC 1) | Integration | A traceroute to an address outside the scope is rejected at the API and, for a job placed on the queue by hand, at the scanner. Both leave an audit entry. |
| 3.7 | Three methods (AC 8) | Lab | ICMP, TCP and UDP each report the lab's known hop sequence with per-probe times. The silent router is recorded as a hop without an answer. UDP probes carry no payload. |
| 3.8 | Hops are never probed directly | Inspection of a capture | During traceroutes and during a following scan of the target, no packet has an out-of-scope hop address as its destination (I2). |
| 3.9 | Hop storage and enrichment | Integration | Every responding hop is in the host table and has an enrichment snapshot and a reverse-DNS result. Private and CGNAT hops are stored, not enriched and flagged unlocatable. Duplicate hop messages change nothing. |
| 3.10 | Live hops and path (AC 8) | Lab | Hop events reach the browser one TTL at a time, before the run ends. The path is drawn; the segment across the silent hop is dashed. After a reload the run is in the host's history with method, parameters and vantage point. |
| 3.11 | Concurrency | Integration | Traceroutes above the concurrent ceiling wait; a running scan does not delay a traceroute. |
| 3.12 | Hop rule | Integration | An address seen as an intermediate hop fires the hop rule in the next classification; an address seen only as a traceroute target does not. |
| 3.13 | Demonstration | Manual | On the authorized public range: a traceroute by each method with enriched public hops on the map; a host's classification with evidence; an override that is still in place after a new scan. |

**Completion criteria.**

- Checks 3.1 to 3.13 pass, and the common criteria of section 2.2 hold.
- AC 6 and AC 8 are met. AC 1 is met for traceroutes.
- The share of `unknown` results on the authorized public range is recorded. It is information for the product owner, not a pass condition (R5).
- The ruleset file carries a version and a licence record (NFR-LIC-2).

### Phase 4 — Schedules, comparison, alerts and export

**Goal.** Scans recur on a schedule, any two scans can be compared, changes found by a scheduled run raise an alert, and results leave the system as CSV and JSON.

**Depends on.** Phase 3. Q3 should be confirmed. The cron library is chosen.

**Deliverables.**

- Schema: `scan_profile`, `schedule`, `schedule_run`, `scan_diff`, `change_event`, `alert`.
- The diff function over two scans: compared and not-comparable addresses; ports limited to those probed in both; changes in reverse DNS, classification and enrichment; the dataset-change flag.
- Scheduler: the tick, the skip rule, the scope check with schedule flagging, one catch-up run after downtime, the minimum interval for intervals and cron expressions, time zones.
- The diff phase of scheduled scans with baseline selection and a stored result; alerts for non-empty diffs and for failed, skipped and rejected runs; the alert for dead-lettered messages.
- Scan profiles, usable in the launch dialog and in schedules.
- Export: CSV and JSON, streamed, for hosts with the active filters, for diffs and for traceroutes, each with the metadata of FR-EXP-4.
- API and frontend: schedules with next run and last outcome, enable and disable; comparison view; alert list with read state and an unread indicator on every page; the "change status" filter.
- Audit actions: schedule creation and change; rejected scheduled runs.

**Validation.**

| # | Check | Method | Pass condition |
|---|---|---|---|
| 4.1 | Diff rules | Unit | Fixture pairs give the expected events for hosts appeared and disappeared, ports opened and closed, and changes in reverse DNS, classification and enrichment. Addresses targeted by one scan only are listed as not comparable. |
| 4.2 | Different port profiles (AC 10) | Unit and Integration | Comparing a top-100 scan with a top-1000 scan reports port changes only for ports probed in both and states which ports were compared. A host not port-scanned in one scan produces a host event and no port events. |
| 4.3 | Dataset changes | Integration | An enrichment change between scans with different dataset versions carries the dataset-change flag (FR-HIS-5). |
| 4.4 | Schedule arithmetic | Unit | The next run is strictly after the current time for intervals and cron expressions, across time zones and daylight-saving changes. Intervals and cron expressions below the minimum are rejected. |
| 4.5 | Run at the planned time (AC 9) | Integration with a controlled clock | A due schedule creates its scan within one scheduler tick of the planned time. |
| 4.6 | Skip (AC 9) | Integration | With the previous run still active, the new run is recorded as skipped and an alert is raised. |
| 4.7 | Catch-up | Integration | After a simulated downtime of several intervals, exactly one run starts. |
| 4.8 | Scheduled scope check (AC 1) | Integration | After the scope is reduced, the run is rejected, the schedule is flagged, and an alert and an audit entry exist. Nothing reaches the scanner. |
| 4.9 | Baseline and alert (AC 9) | Integration | The baseline is the previous completed, non-partial run of the same schedule; cancelled, failed and partial runs are passed over. A non-empty diff raises one alert that links to the stored diff, and the link still resolves after later runs. An empty diff raises none. |
| 4.10 | Alerts in the interface | Frontend test and Manual | The unread indicator is on every page and follows the read state; a new alert appears without a reload. |
| 4.11 | Export (AC 11) | Integration | Host, diff and traceroute exports parse as CSV and as JSON, contain the same rows as the filtered table query, and carry scan identifier, scan time, vantage point and dataset versions. |
| 4.12 | Comparison time (NFR-PRF-5) | Load, seeded database | Comparing two 256-address scans with the largest realistic port counts completes within 5 seconds. |
| 4.13 | Scheduled change detection (AC 9) | Lab | With the minimum interval lowered for the test, a schedule runs twice against the lab; a port opened on a target between the runs produces an alert whose diff shows that port. |
| 4.14 | Demonstration | Manual | A schedule with its next run shown, a manual comparison, an alert opened from the indicator, an export opened in a spreadsheet. |

**Completion criteria.**

- Checks 4.1 to 4.14 pass, and the common criteria of section 2.2 hold.
- AC 9, AC 10 and AC 11 are met. AC 1 is met in full: manual scans, traceroutes and scheduled runs.
- Every action in FR-AUD-1 now has an audit entry and a test.

### Phase 5 — Hardening and release

**Goal.** Version 1 is verified as a whole on the release images, at the capacity the specification names, and the operator has the documentation to run it.

**Depends on.** Phase 4.

**Deliverables.**

- Release candidate images with pinned versions.
- A recorded run of AC 1 to AC 11 on a clean host.
- Load and capacity report.
- Operator guide, complete: host preparation, backup and restore, reverse-DNS exposure, tile provider, MaxMind key, expected abuse reports (architecture section 15.5).
- Licence inventory of every bundled data file and dependency (NFR-LIC-2).
- Fixes for defects found in this phase.

**Validation.**

| # | Check | Method | Pass condition |
|---|---|---|---|
| 5.1 | Acceptance run | Lab and Manual | AC 1 to AC 11 pass in one run on the release candidate, deployed on a clean host by following the operator guide only. |
| 5.2 | Full-range endurance | Load | A full-range scan of a /24 (16.8 million probes) at the global ceiling, with one stack restart in the middle, completes with exact results and zero receive drops. |
| 5.3 | Queries at the envelope | Load | On a database seeded to A7 with every table filled, all list, filter, timeline and comparison endpoints meet NFR-PRF-4 and NFR-PRF-5. |
| 5.4 | Storage | Load | Measured table and index sizes at A7 are compared with architecture section 10.3; a deviation above 50 percent is reported. |
| 5.5 | Hosts open on every port (Q1) | Load | A /24 of all-ports-open hosts scanned with the top-1000 profile: write rate and storage growth are measured and put to the product owner with Q1. |
| 5.6 | Deletion | Integration | After a scan and a project are deleted and the purge has run, no row of theirs remains in any table except `host` and `audit_event`. |
| 5.7 | Backup and restore | Manual | A database restored by the documented procedure on a second host shows the same projects, scans and audit log (NFR-OPS-5). |
| 5.8 | Operability | Inspection | Every log line of a scan and a traceroute carries its identifier, in all three processes. All metrics of NFR-OPS-3 are present. Every service has a health check and a resource limit. |
| 5.9 | Container and network posture | Inspection | Capabilities, users, `no-new-privileges`, read-only file systems and published ports match architecture section 14. The dependency scan reports nothing above the agreed severity. |
| 5.10 | Browsers | Manual | The demonstration scripts of phases 1A to 4 pass in current Chromium and Firefox (A5). |

**Completion criteria.**

- Checks 5.1 to 5.10 pass.
- Q1 is decided.
- Section 7 shows no "must" requirement without a passed check.
- No open defect breaks an acceptance criterion or a "must" requirement.

## 6. Acceptance criteria by phase

| AC | Subject | First met in | Checks |
|---|---|---|---|
| 1 | No scan outside scope or public space; rejections audited | 1B (manual scans), 3 (traceroutes), 4 (schedules) | 1B.8, 3.6, 4.8 |
| 2 | Exact open ports, SYN and connect, technique recorded | 1B | 1B.10, 1B.11 |
| 3 | No application-layer payload | 1B | 1B.12 |
| 4 | Recovery with no duplicate and no missing observations | 1B | 1B.13, 1B.14 |
| 5 | Enrichment fields and versions; no enrichment egress but reverse DNS | 2 | 2.4, 2.8, 2.15 |
| 6 | Classification with evidence; persistent override | 3 | 3.1, 3.5 |
| 7 | Hosts on the map within 2 seconds; co-located and unlocated hosts | 2 | 2.10, 2.11 |
| 8 | Traceroute by three methods, live, on the map, in history | 3 | 3.7, 3.10 |
| 9 | Scheduled run, skip, alert on changes | 4 | 4.5, 4.6, 4.9, 4.13 |
| 10 | Port changes only for ports probed in both scans | 4 | 4.2 |
| 11 | CSV and JSON export with metadata | 4 | 4.11 |

All eleven are run again together in check 5.1.

## 7. Requirement coverage

| Requirements | Phase |
|---|---|
| FR-ACC-1 to 4 | 1A |
| FR-PRJ-1 to 5 | 1A |
| FR-PRJ-6 | 1A (API), 1B (orchestrator and scanner) |
| FR-PRJ-7 | 1B (creation, chunk dispatch, scanner), 3 (traceroute), 4 (scheduled run) |
| FR-CFG-1 to 6, FR-CFG-9 | 1B |
| FR-CFG-7 | 1B (rate, concurrent scans), 3 (concurrent traceroutes), 4 (minimum interval) |
| FR-CFG-8 | 4 |
| FR-SCN-1 to 3, 5 to 7, 9, 10 | 1B |
| FR-SCN-4 | 1B (states, discovery and port-scan phases), 2 (enrichment), 3 (classification), 4 (diff) |
| FR-SCN-8 | 1B; dataset versions in 2 |
| FR-SCH-1 to 6 | 4 |
| FR-ENR-1 to 8 | 2; hop enrichment in 3 |
| FR-CLS-1 to 7 | 3 |
| FR-MAP-1 to 5, 7, 10, 11 | 2; live progress in the host table already in 1B |
| FR-MAP-6 | 2 (five filters), 3 (device type, tag), 4 (change status) |
| FR-MAP-8 | 1B (table, sorting), 2 (filters) |
| FR-MAP-9 | 1B (evidence, ports), 2 (enrichment, reverse DNS, timeline), 3 (classification, annotations, traceroutes) |
| FR-TRC-1 to 7 | 3 |
| FR-TRC-8 | Not planned (P3) |
| FR-HIS-1, FR-HIS-9 | 1B |
| FR-HIS-2 to 8 | 4 |
| FR-EXP-1 to 4 | 4 |
| FR-AUD-1 | Every phase, for the actions it introduces; complete in 4 |
| FR-AUD-2, FR-AUD-3 | 1A |
| NFR-SEC-1 | 1A (authentication), 1B (scope) |
| NFR-SEC-2 | 0 (proven), 1B (delivered) |
| NFR-SEC-3 to 5 | 1A |
| NFR-SEC-6 | 1A (API), 1B (all three processes, audit entry) |
| NFR-PRF-1 to 3 | 0 (feasibility), 1B (delivered), 5 (endurance) |
| NFR-PRF-4 | 2; whole system in 5 |
| NFR-PRF-5 | 4 |
| NFR-REL-1, 2, 4 | 1B |
| NFR-REL-3 | 2 |
| NFR-OPS-1, NFR-OPS-4 | Every phase from 1A |
| NFR-OPS-2 | 1A; all processes confirmed in 5 |
| NFR-OPS-3 | 1B; dataset age in 2 |
| NFR-OPS-5 | 5 |
| NFR-LIC-1, NFR-LIC-3 | 2 |
| NFR-LIC-2 | 1B (port lists, service names), 3 (ruleset), 5 (inventory) |
| NFR-TST-1 | 1A |
| NFR-TST-2 | 0 (lab), 1B (scans, recovery), 3 (traceroute) |
| C-1 to C-5 | Respected throughout; C-2 and C-3 checked in 1B |

## 8. Risks to the plan

| Risk | Effect on the plan | Handling |
|---|---|---|
| The Python scanner misses the rate or the accuracy (R1) | The scanner part of 1B is re-planned in another language | Gate 0 before 1B starts. 1A is unaffected and proceeds in parallel. |
| Host networking needs more host preparation than expected (R2) | Deployment model and Q4 change | Measured in checks 0.4 and 0.8; decided at Gate 0. |
| Phase 1B is the largest phase and holds the recovery logic | A late phase 1B delays everything after it | Three internal checkpoints; the stub scanner lets orchestrator work start before the real scanner is ready. |
| Curating the top-1000 port list takes longer than assumed (R4) | Check 1B.10 cannot run | The task has no dependency; it starts during phase 0. |
| Registry bulk data is not available on the expected terms (R3) | Fewer loaders, more fallback to the AS holder | Verified as the first task of phase 2; the fallback is already designed. |
| The project-wide view is too slow at 65,536 hosts | Extra work in phase 2 | Check 2.13 with the documented fallback. |
| Interpretations I1 to I6 are overturned after their phase started | Rework; medium for I5, low for the others | Section 4.1 gives the last cheap moment for each. |
| No authorized public range is available | Checks 2.15 and 3.13 cannot run with real data | The replay harness covers the automated checks; the two manual checks need the range. |
| Storage grows past the A7 envelope for hosts open on every port (Q1) | A cap is added late | Measured in check 5.5. The cap touches only the port-observation write and the host flag. |

## 9. Not planned for version 1

- FR-TRC-8, side-by-side comparison of two traceroutes (priority "could"; see P3).
- Everything in section 3.2 and section 10 of the specification. The plan keeps what the architecture already provides for them: the vantage point on every observation, per-vantage queue names, and hop-level traceroute data.
