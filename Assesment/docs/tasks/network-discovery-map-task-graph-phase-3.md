# Network Discovery Map — Task Graph, Phase 3

| | |
| --- | --- |
| Version | 1.0 |
| Date | 2026-11-15 |
| Status | Proposed; ready for assignment |
| Based on | Network Discovery Map — Delivery Plan v1.0, section 5 (Phase 3) and section 2.2; Architecture v1.0, sections 7, 8.2 and 11.2 |
| Audience | Implementation team |

This document breaks Phase 3 (Traceroute, classification and annotations) of the delivery plan into tasks. The conventions, the field meanings and the rules of the graph are in the index, [network-discovery-map-task-graph.md](network-discovery-map-task-graph.md).

## 1. Scope and entry conditions

**Goal of the phase.** The analyst can trace the path to a host and see it on the map, and every host has a classification with evidence that the analyst can override and annotate.

**Entry conditions.**

- Phase 2 is complete (TASK-047).
- I2 and P3 should be confirmed by the product owner. FR-TRC-8 is not planned (P3).
- The authorized public range is available for TASK-059.

**Two streams.** Traceroute (TASK-050, TASK-051, TASK-054, TASK-056) and classification (TASK-052, TASK-053, TASK-055, TASK-057) are independent, with one exception: the classification input "seen as an intermediate hop" reads traceroute data, so the classification phase (TASK-053) follows hop recording (TASK-051).

## 2. Overview

| Task | Title | Depends on | Risk | Checks closed |
| --- | --- | --- | --- | --- |
| TASK-048 | Groundwork: dependencies, schema, settings, messages, traceroute queue | TASK-047 | high | — |
| TASK-049 | Lab extension: router chain, silent router, private hops | TASK-047 | low | — |
| TASK-050 | Scanner traceroute by ICMP, TCP and UDP | TASK-048 | high | Part of 3.6 |
| TASK-051 | Orchestrator traceroute: dispatch, hop recording, hop enrichment | TASK-048 | high | 3.9, 3.11 |
| TASK-052 | Ruleset version 1 and the rule evaluator | TASK-048 | medium | 3.1, 3.2, 3.3 |
| TASK-053 | Classification phase, re-run and hop rule | TASK-051, TASK-052 | medium | 3.4, 3.12 |
| TASK-054 | Traceroute API | TASK-048 | high | Part of 3.6 |
| TASK-055 | Classification, override and annotation API | TASK-048 | high | 3.5 |
| TASK-056 | Frontend traceroute: start, live hops, path, history | TASK-054, TASK-055 | medium | Part of 3.10 |
| TASK-057 | Frontend classification, override, tags, notes, filters | TASK-056 | medium | — |
| TASK-058 | Lab suite: traceroute methods, hops, live path | TASK-049, TASK-050, TASK-051, TASK-053, TASK-054 | medium | 3.7, 3.8, 3.10 |
| TASK-059 | Operator guide, demonstration and Phase 3 exit checks | TASK-057, TASK-058 | medium | 3.13 |

| Wave | Tasks that run in parallel |
| --- | --- |
| 1 | TASK-048, TASK-049 |
| 2 | TASK-050, TASK-051, TASK-052, TASK-054, TASK-055 |
| 3 | TASK-053, TASK-056 |
| 4 | TASK-057, TASK-058 |
| 5 | TASK-059 |

## 3. Tasks

### TASK-048 — Groundwork: dependencies, schema, settings, messages and traceroute queue

- **dependencies:** TASK-047
- **risk_level:** high (migration)

**Description.**

- Pin the YAML parser that the ruleset of TASK-052 needs.
- One migration and the ORM models for `traceroute_run`, `traceroute_probe` and `classification_result`; the override, tags and notes on `project_host`; traceroute runs as owners of enrichment snapshots.
- `classification_result` has the run number, the latest flag and a partial unique index on (scan, address) where the flag is set.
- Register the new tables for the deletion purge.
- Settings: concurrent-traceroute ceiling (default 5) and the classification confidence threshold.
- Messages: traceroute job, traceroute hop, traceroute done; commands for a requested traceroute and a re-run of classification; the traceroute-hop UI event.
- Broker: the traceroute queue, with a prefetch equal to the concurrent-traceroute ceiling.
- Command helpers for the API, so the two API tasks of this phase do not change the same file.

**expected_files.**

- `Assesment/backend/pyproject.toml` (modified)
- `Assesment/backend/uv.lock` (modified)
- `Assesment/backend/src/ndm/db/models.py` (modified)
- `Assesment/backend/src/ndm/db/migrations/versions/0004_traceroute_classification.py`
- `Assesment/backend/src/ndm/orchestrator/purge.py` (modified)
- `Assesment/backend/src/ndm/core/settings.py` (modified)
- `Assesment/backend/src/ndm/core/messages.py` (modified)
- `Assesment/backend/src/ndm/core/broker.py` (modified)
- `Assesment/backend/src/ndm/api/commands.py` (modified)
- `Assesment/backend/tests/integration/db/test_migration_0004.py`
- `Assesment/backend/tests/integration/orchestrator/test_purge.py` (modified)
- `Assesment/backend/tests/integration/core/test_broker.py` (modified)

**Acceptance.**

- The migration applies, reverts and applies again; a database left by Phase 2 migrates forward and keeps its data.
- The purge test passes with traceroute runs, probes and classification results present.
- The traceroute queue is declared as a durable quorum queue with the dead-letter exchange.

### TASK-049 — Lab extension: router chain, silent router and private hops

- **dependencies:** TASK-047
- **risk_level:** low

**Description.**

- A chain of router containers in front of one lab target.
- One router in the chain stays silent: it forwards packets and sends no ICMP errors.
- Some hops sit in private address space.
- The manifest gains the expected hop sequence per target, including the silent hop.

**expected_files.**

- `Assesment/deploy/lab/hops/Dockerfile`
- `Assesment/deploy/lab/hops/entrypoint.sh`
- `Assesment/deploy/lab/compose.lab.yaml` (modified)
- `Assesment/deploy/lab/manifest.yaml` (modified)
- `Assesment/deploy/lab/README.md` (modified)

**Acceptance.**

- The target behind the chain is reachable from the lab host, and the open-port checks of Phase 1B still pass on the extended lab.
- A capture on the silent router shows forwarded probes and no ICMP error sent by it.

### TASK-050 — Scanner traceroute by ICMP, TCP and UDP

- **dependencies:** TASK-048
- **risk_level:** high (raw sockets; the scanner's own scope check for traceroute jobs)

**Description.**

- Traceroute with the scanner's existing sockets and a per-probe TTL, by ICMP, by TCP to a chosen port and by UDP. UDP probes carry a zero-length payload.
- Replies are ICMP time-exceeded and unreachable messages on the raw ICMP socket, matched through the original header they quote.
- One message per TTL, published as soon as that TTL's probes are answered or timed out (FR-TRC-6).
- Every probe takes a token from the global bucket and from the job's own bucket.
- The worker consumes the traceroute queue and checks the scope carried in the job before the first probe. On failure nothing is sent and the job is rejected.
- Only the target is ever a destination. A hop address is never probed directly (I2).

**expected_files.**

- `Assesment/backend/src/ndm/scanner/traceroute.py`
- `Assesment/backend/src/ndm/scanner/quoted.py`
- `Assesment/backend/src/ndm/scanner/main.py` (modified)
- `Assesment/backend/tests/unit/scanner/test_quoted.py`
- `Assesment/backend/tests/integration/scanner/test_traceroute_scope.py`

**Acceptance.**

- Unit: a quoted header is matched to its probe for each of the three methods; an ICMP error that quotes another flow is ignored.
- Check 3.6, scanner part: a traceroute job placed on the queue by hand with an out-of-scope target is rejected with nothing sent.
- No traceroute probe carries payload.

### TASK-051 — Orchestrator traceroute: dispatch, hop recording and hop enrichment

- **dependencies:** TASK-048
- **risk_level:** high (attaches the scope to the job; writes host data)

**Description.**

- Dispatch a requested run to the traceroute queue with the project scope attached. Runs above the concurrent ceiling wait; a running scan does not delay a traceroute.
- Record each hop message: probe rows keyed by run, TTL and probe number with `ON CONFLICT DO NOTHING`. A hop that did not answer is recorded as such.
- Add every responding address to the global `host` table (I2).
- For each public responding address write an enrichment snapshot owned by the run and a reverse-DNS result.
- Private and CGNAT addresses are stored, not enriched, and flagged as unlocatable (FR-TRC-4).
- Emit the traceroute-hop UI event after the commit.
- The dead-letter consumer fails a run whose message was dead-lettered.

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/traceroute.py`
- `Assesment/backend/src/ndm/orchestrator/main.py` (modified)
- `Assesment/backend/src/ndm/orchestrator/deadletter.py` (modified)
- `Assesment/backend/tests/integration/orchestrator/test_hop_recording.py`
- `Assesment/backend/tests/integration/orchestrator/test_traceroute_concurrency.py`

**Acceptance.**

- Check 3.9: every responding hop is in the host table and has an enrichment snapshot and a reverse-DNS result; private and CGNAT hops are stored, not enriched and flagged unlocatable; duplicate hop messages change nothing.
- Check 3.11: traceroutes above the concurrent ceiling wait; a running scan does not delay a traceroute.

### TASK-052 — Ruleset version 1 and the rule evaluator

- **dependencies:** TASK-048
- **risk_level:** medium

**Description.**

- Ruleset version 1 as a YAML file in the package: version, the taxonomy of FR-CLS-1 with its sub-roles, and the rules with their confidences. A licence record sits beside it (NFR-LIC-2).
- The evaluator: a pure function. Its only inputs are open ports, PTR names, enrichment attributes and the hop flag (FR-CLS-2).
- For each type the confidences of the fired rules are combined as `1 − ∏(1 − c)`. The highest-scoring type wins; below the threshold the type is `unknown`.
- The result holds the fired rules with the evidence each matched, and the ruleset version (FR-CLS-3).
- A fixed set of host fixtures with the stored expected output, as a regression test for the ruleset.

**expected_files.**

- `Assesment/backend/src/ndm/core/ruleset/v1.yaml`
- `Assesment/backend/src/ndm/core/ruleset/LICENCE.md`
- `Assesment/backend/src/ndm/orchestrator/classification.py`
- `Assesment/backend/tests/fixtures/classification/hosts.json`
- `Assesment/backend/tests/fixtures/classification/expected.json`
- `Assesment/backend/tests/unit/orchestrator/test_rules.py`
- `Assesment/backend/tests/unit/orchestrator/test_ruleset_regression.py`

**Acceptance.**

- Check 3.1: each rule has a matching and a non-matching fixture; combined confidence follows the formula; below the threshold the type is `unknown`; every type and sub-role of FR-CLS-1 is produced by at least one fixture.
- Check 3.2: the ruleset run over the fixed fixtures gives the stored expected output; a difference requires a new ruleset version.
- Check 3.3: the evaluator's signature accepts only the four allowed inputs.

### TASK-053 — Classification phase, re-run and hop rule

- **dependencies:** TASK-051, TASK-052
- **risk_level:** medium (writes classification results)

**Description.**

- The classification phase of a scan, registered with the phase runner after enrichment. It reads stored data only and writes with the key (scan, address, run number).
- The hop flag: a lookup on `traceroute_probe` by responder address that excludes rows where the address is the run's target.
- A re-run with the current ruleset adds a result with the next run number beside the original and moves the latest flag (FR-CLS-7).
- Loading a newer ruleset changes no stored result (FR-CLS-4).

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/classification_phase.py`
- `Assesment/backend/src/ndm/orchestrator/phases.py` (modified)
- `Assesment/backend/src/ndm/orchestrator/main.py` (modified)
- `Assesment/backend/tests/integration/orchestrator/test_classification_stored.py`
- `Assesment/backend/tests/integration/orchestrator/test_hop_rule.py`

**Acceptance.**

- Check 3.4: loading a newer ruleset leaves stored results unchanged; a re-run adds a result beside the original.
- Check 3.12: an address seen as an intermediate hop fires the hop rule in the next classification; an address seen only as a traceroute target does not.
- A forced failure in the phase marks the scan failed with the phase recorded, and a retry completes it.

### TASK-054 — Traceroute API

- **dependencies:** TASK-048
- **risk_level:** high (scope check at traceroute creation)

**Description.**

- Start a traceroute for a host with method, port, maximum hops, probes per hop and timeout; run detail; list of runs per host.
- The target must be inside the current project scope, public and not on the never-scan list. Otherwise the request is rejected with an audit entry.
- A created run is committed first; then the command is published.
- Audit entry for every traceroute execution, in the same transaction as the run.
- The TCP method defaults to an open port of the host (FR-TRC-2).

**expected_files.**

- `Assesment/backend/src/ndm/api/routers/traceroutes.py`
- `Assesment/backend/src/ndm/api/schemas/traceroutes.py`
- `Assesment/backend/src/ndm/api/app.py` (modified)
- `Assesment/backend/tests/integration/api/test_traceroutes.py`
- `Assesment/backend/tests/integration/api/test_traceroute_scope_rejection.py`

**Acceptance.**

- Check 3.6, API part: a traceroute to an address outside the scope is rejected and leaves an audit entry.
- The run detail gives, per TTL, every responding address, the per-probe times and the loss, and marks hops without an answer (FR-TRC-3).
- Check 1A.3 still passes, unchanged, with the new routes.

### TASK-055 — Classification, override and annotation API

- **dependencies:** TASK-048
- **risk_level:** high (writes analyst data; audited override)

**Description.**

- Host detail and host rows return the automatic classification with confidence, evidence and ruleset version, the override if there is one, and the effective device type.
- Set and clear a type override on `project_host`. It applies to the address in every scan of the project, including later scans, and in no other project. Each change writes an audit entry.
- Tags and notes on `project_host`. They never alter scan data.
- Filters by device type and by tag in the query builder. The device-type filter uses the effective type.
- Scan action: re-run classification.

**expected_files.**

- `Assesment/backend/src/ndm/api/routers/hosts.py` (modified)
- `Assesment/backend/src/ndm/api/schemas/hosts.py` (modified)
- `Assesment/backend/src/ndm/api/queries/hosts.py` (modified)
- `Assesment/backend/src/ndm/api/routers/scans.py` (modified)
- `Assesment/backend/src/ndm/api/schemas/scans.py` (modified)
- `Assesment/backend/tests/integration/api/test_override_annotations.py`
- `Assesment/backend/tests/integration/api/test_classification_filters.py`

**Acceptance.**

- Check 3.5: an override applies to the address in every scan of the project, including scans made later, and not in another project; the automatic result is still returned; clearing restores it; tags and notes do not alter scan data.
- Check 2.9 still passes with the two new filters: table and map return the same set of addresses.
- Setting and clearing an override each leave an audit entry.

### TASK-056 — Frontend traceroute: start, live hops, path and history

- **dependencies:** TASK-054, TASK-055
- **risk_level:** medium

**Description.**

- Regenerate the API types.
- Start a traceroute from the host detail panel with method, port, maximum hops, probes per hop and timeout. A rejection by the server is shown.
- Hops appear one TTL at a time while the run is in progress, through the event stream.
- The path is a line layer on the map. A segment that skips a silent or unlocated hop is dashed (FR-TRC-6). Private and CGNAT hops are listed and marked as unlocatable.
- Traceroute history of the host with method, parameters and vantage point.

**expected_files.**

- `Assesment/frontend/src/api/schema.d.ts` (regenerated)
- `Assesment/frontend/src/stores/traceroutes.ts`
- `Assesment/frontend/src/components/TracerouteDialog.vue`
- `Assesment/frontend/src/components/TracerouteHops.vue`
- `Assesment/frontend/src/components/TracerouteHistory.vue`
- `Assesment/frontend/src/components/HostDetailPanel.vue` (modified)
- `Assesment/frontend/src/composables/useMap.ts` (modified)
- `Assesment/frontend/src/composables/useEventStream.ts` (modified)
- `Assesment/frontend/src/stores/traceroutes.spec.ts`
- `Assesment/frontend/src/composables/useMap.spec.ts` (modified)

**Acceptance.**

- Vitest: hop events are applied in TTL order; the segment across a silent or unlocated hop is built as dashed (check 3.10, in part).
- A traceroute can be started in the browser, and after a reload the run is in the host's history.

### TASK-057 — Frontend classification, override, tags, notes and filters

- **dependencies:** TASK-056
- **risk_level:** medium

**Description.**

- Classification in the host detail panel: device type, sub-role, confidence and the fired rules with their evidence.
- Override set and clear. While an override is set, both the override and the automatic value are shown (FR-CLS-5).
- Tags and notes on the host.
- Filters by device type and by tag in the filter bar, mirrored in the URL.
- Scan action in the scan list: re-run classification.

**expected_files.**

- `Assesment/frontend/src/components/ClassificationCard.vue`
- `Assesment/frontend/src/components/OverrideDialog.vue`
- `Assesment/frontend/src/components/HostAnnotations.vue`
- `Assesment/frontend/src/components/HostDetailPanel.vue` (modified)
- `Assesment/frontend/src/components/FilterBar.vue` (modified)
- `Assesment/frontend/src/composables/useHostFilters.ts` (modified)
- `Assesment/frontend/src/views/ScanListView.vue` (modified)
- `Assesment/frontend/src/components/ClassificationCard.spec.ts`
- `Assesment/frontend/src/composables/useHostFilters.spec.ts` (modified)

**Acceptance.**

- Vitest: with an override set, both values are rendered; clearing shows the automatic value alone; the two new filters round-trip through the URL.
- Override, tags and notes can be set in the browser and are still there after a reload.

### TASK-058 — Lab suite: traceroute methods, hops and live path

- **dependencies:** TASK-049, TASK-050, TASK-051, TASK-053, TASK-054
- **risk_level:** medium

**Description.**

- On-demand lab tests against the router chain of TASK-049, started through the API.
- A capture-based test that no hop address outside the scope is ever a destination, during traceroutes and during a following scan of the target.
- A test at the event stream that hop events arrive one TTL at a time, before the run ends.

**expected_files.**

- `Assesment/backend/tests/lab/test_traceroute_methods.py`
- `Assesment/backend/tests/lab/test_hops_not_probed.py`
- `Assesment/backend/tests/lab/test_live_hops.py`

**Acceptance.**

- Check 3.7: ICMP, TCP and UDP each report the lab's known hop sequence with per-probe times; the silent router is recorded as a hop without an answer; UDP probes carry no payload.
- Check 3.8: no packet has an out-of-scope hop address as its destination (I2).
- Check 3.10, at the event stream: hop events arrive one TTL at a time, before the run ends, and the stored run holds method, parameters and vantage point.

### TASK-059 — Operator guide, demonstration and Phase 3 exit checks

- **dependencies:** TASK-057, TASK-058
- **risk_level:** medium

**Description.**

- Extend the operator guide with the configuration values Phase 3 adds: the concurrent-traceroute ceiling and the classification threshold.
- Write the demonstration script for check 3.13 and carry it out on the authorized public range.
- Confirm check 3.10 in a browser: the path is drawn and the segment across the silent hop is dashed.
- Record the share of `unknown` results on the authorized public range. It is information for the product owner, not a pass condition (R5).
- Go through the common criteria of delivery plan section 2.2 and record the result of each of checks 3.1 to 3.13.

**expected_files.**

- `Assesment/docs/operator-guide.md` (modified)
- `Assesment/docs/demo/phase-3.md`

**Acceptance.**

- Check 3.13: a traceroute by each method with enriched public hops on the map; a host's classification with evidence; an override that is still in place after a new scan.
- Every check from 3.1 to 3.13 is recorded as passed, with none skipped. AC 6 and AC 8 are met; AC 1 is met for traceroutes.
- The ruleset file carries a version and a licence record.

## 4. Coverage of Phase 3

| Phase 3 check | Task |
| --- | --- |
| 3.1 Rule evaluation | TASK-052 |
| 3.2 Ruleset regression | TASK-052 |
| 3.3 Rule inputs | TASK-052 |
| 3.4 Stored classification is stable | TASK-053 |
| 3.5 Override and annotations | TASK-055 |
| 3.6 Traceroute scope | TASK-054 (API), TASK-050 (scanner) |
| 3.7 Three methods | TASK-058 |
| 3.8 Hops are never probed directly | TASK-058 |
| 3.9 Hop storage and enrichment | TASK-051 |
| 3.10 Live hops and path | TASK-058 (event stream), TASK-056 (path), TASK-059 (browser) |
| 3.11 Concurrency | TASK-051 |
| 3.12 Hop rule | TASK-053 |
| 3.13 Demonstration | TASK-059 |

| Common criterion (delivery plan 2.2) | Task |
| --- | --- |
| 1. CI green | Every task |
| 2. Clean start and demonstration | TASK-059 |
| 3. Migrations apply, revert, apply; earlier data kept | TASK-048 |
| 4. Audit entry in the same transaction, with a test | TASK-054 (traceroute execution), TASK-055 (override) |
| 5. Deletion purge covers every new table | TASK-048 |
| 6. Route-enumeration test covers new endpoints | TASK-054, TASK-055 |
| 7. No failed or skipped check | TASK-059 |
| 8. Operator guide | TASK-059 |
