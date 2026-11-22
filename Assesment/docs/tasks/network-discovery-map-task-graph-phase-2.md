# Network Discovery Map — Task Graph, Phase 2

| | |
| --- | --- |
| Version | 1.0 |
| Date | 2026-11-15 |
| Status | Proposed; ready for assignment |
| Based on | Network Discovery Map — Delivery Plan v1.0, section 5 (Phase 2) and section 2.2; Architecture v1.0, sections 10, 11.1, 12 and 13 |
| Audience | Implementation team |

This document breaks Phase 2 (Enrichment and map) of the delivery plan into tasks. The conventions, the field meanings and the rules of the graph are in the index, [network-discovery-map-task-graph.md](network-discovery-map-task-graph.md).

## 1. Scope and entry conditions

**Goal of the phase.** Every discovered host carries registration and location data from local datasets and reverse DNS, and appears on the map within 2 seconds while the scan runs.

**Entry conditions.**

- Phase 1B is complete (TASK-032).
- I5, I6 and P1 should be confirmed by the product owner. I5 has a medium cost to reverse once TASK-040 has started.
- The MaxMind account and licence key are available for TASK-037.
- A tile provider is chosen for development and test, for TASK-044.
- The authorized public range is available for TASK-047.

## 2. Overview

| Task | Title | Depends on | Risk | Checks closed |
| --- | --- | --- | --- | --- |
| TASK-033 | Registry data sources verified | TASK-032 | low | R3 |
| TASK-034 | Groundwork: dependencies, settings, messages, Compose | TASK-032 | high | — |
| TASK-035 | Schema for enrichment and reference datasets | TASK-032 | high | — |
| TASK-036 | Test tools: replay harness, seeding tool, lab resolver | TASK-034, TASK-035 | medium | — |
| TASK-037 | Dataset management and GeoLite2 | TASK-034, TASK-035 | high | 2.2 (GeoLite2) |
| TASK-038 | Registry loaders and lookup | TASK-033, TASK-037 | medium | 2.1, 2.2 (registries) |
| TASK-039 | Post-probe phase runner and retry of a failed phase | TASK-034, TASK-035 | medium | Mechanism of 2.5 |
| TASK-040 | Enrichment: inline, enrichment phase, reverse DNS | TASK-036, TASK-038, TASK-039 | high | 2.3 to 2.7 |
| TASK-041 | Host query builder, filters, project-wide view, timeline | TASK-035 | medium | 2.9 |
| TASK-042 | Dataset, tile-configuration and scan-action endpoints | TASK-034, TASK-035 | medium | — |
| TASK-043 | Frontend project workspace, filter bar and scan selector | TASK-041, TASK-042 | medium | — |
| TASK-044 | Frontend map | TASK-043 | medium | Part of 2.11, part of 2.14 |
| TASK-045 | Frontend host detail extension and dataset settings page | TASK-043 | medium | — |
| TASK-046 | Live-map, egress and load checks | TASK-040, TASK-044, TASK-045 | medium | 2.8, 2.10, 2.12, 2.13 |
| TASK-047 | Operator guide, demonstration and Phase 2 exit checks | TASK-046 | medium | 2.11, 2.14, 2.15 |

| Wave | Tasks that run in parallel |
| --- | --- |
| 1 | TASK-033, TASK-034, TASK-035 |
| 2 | TASK-036, TASK-037, TASK-039, TASK-041, TASK-042 |
| 3 | TASK-038, TASK-043 |
| 4 | TASK-040, TASK-044, TASK-045 |
| 5 | TASK-046 |
| 6 | TASK-047 |

## 3. Tasks

### TASK-033 — Registry data sources verified

- **dependencies:** TASK-032
- **risk_level:** low

**Description.**

- Verify the download location, the format and the terms of use of the bulk data of the five registries (R3): delegation statistics for all five, detailed objects for RIPE, APNIC and AFRINIC, and the bulk-access files an operator can obtain from ARIN and LACNIC.
- Record the result per registry. It decides which loaders TASK-038 builds; where a source is not available on acceptable terms, the organization falls back to the AS holder.

**expected_files.**

- `Assesment/docs/datasets/registry-sources.md`

**Acceptance.**

- For each of the five registries the document gives the location, the format, the terms and a decision: load automatically, load from an operator-supplied file, or do not load.

### TASK-034 — Groundwork: dependencies, settings, messages and Compose

- **dependencies:** TASK-032
- **risk_level:** high (MaxMind licence key; outbound network access for the orchestrator)

**Description.**

- Pin the dependencies Phase 2 adds: `maxminddb`, `dnspython`, `httpx`; MapLibre GL JS and a browser test runner for the frontend.
- Settings: MaxMind account and licence key, dataset update intervals, the path for operator-supplied registry files, the PTR resolver and its on/off switch, the tile URL template and its attribution. There is no default tile source (R6). The licence key is covered by the redaction of TASK-002.
- Messages: commands for a dataset update, a re-run of enrichment and a retry of a failed phase.
- Compose: a reference-data volume that only the orchestrator mounts, and outbound network access for the orchestrator.

**expected_files.**

- `Assesment/backend/pyproject.toml` (modified)
- `Assesment/backend/uv.lock` (modified)
- `Assesment/frontend/package.json` (modified)
- `Assesment/frontend/package-lock.json` (modified)
- `Assesment/backend/src/ndm/core/settings.py` (modified)
- `Assesment/backend/src/ndm/core/messages.py` (modified)
- `Assesment/backend/tests/unit/core/test_settings.py` (modified)
- `Assesment/deploy/compose.yaml` (modified)
- `Assesment/deploy/.env.example` (modified)

**Acceptance.**

- The licence key occurs in no log line when it is logged on purpose in a test.
- The stack starts without a MaxMind key and without a tile source.
- The reference-data volume is mounted by the orchestrator only.

### TASK-035 — Schema for enrichment and reference datasets

- **dependencies:** TASK-032
- **risk_level:** high (migration)

**Description.**

- One migration and the ORM models for `reference_dataset`, `rir_inetnum`, `rir_delegation` and `enrichment_snapshot`, the reverse-DNS columns on `host_observation`, and the dataset versions on `scan`.
- `rir_inetnum` has a GiST index on its IPv4 range column.
- `enrichment_snapshot` has the run number, the latest flag and a partial unique index on (scan, address) where the flag is set. Its location is a PostGIS point with no spatial index.
- Register `enrichment_snapshot` for the deletion purge.

**expected_files.**

- `Assesment/backend/src/ndm/db/models.py` (modified)
- `Assesment/backend/src/ndm/db/migrations/versions/0003_enrichment.py`
- `Assesment/backend/src/ndm/orchestrator/purge.py` (modified)
- `Assesment/backend/tests/integration/db/test_migration_0003.py`
- `Assesment/backend/tests/integration/orchestrator/test_purge.py` (modified)

**Acceptance.**

- The migration applies, reverts and applies again; a database left by Phase 1B migrates forward and keeps its data.
- The purge test passes with snapshots present, and the registry test of TASK-023 passes.

### TASK-036 — Test tools: replay harness, seeding tool and lab resolver

- **dependencies:** TASK-034, TASK-035
- **risk_level:** medium (the seeding tool writes bulk data into a database)

**Description.**

- Findings-replay harness: publishes recorded findings for public addresses to the orchestrator. Lab targets sit in non-public space and have no enrichment data; the harness lets enrichment and the map be tested without probing anything.
- Seeding tool: fills a database to the A7 envelope for the tables that exist in this phase. It refuses to run against a database that already holds projects, unless forced.
- Lab resolver with known PTR records for the lab addresses.

**expected_files.**

- `Assesment/backend/tests/tools/replay_findings.py`
- `Assesment/backend/tests/fixtures/replay/public_scan.json`
- `Assesment/backend/tests/integration/tools/test_replay_findings.py`
- `Assesment/backend/scripts/seed_envelope.py`
- `Assesment/deploy/lab/resolver/Corefile`
- `Assesment/deploy/lab/resolver/lab.zone`
- `Assesment/deploy/lab/compose.lab.yaml` (modified)

**Acceptance.**

- A replayed scan reaches `completed` with no packet sent to any target.
- The seeding tool reaches the A7 row counts and reports them; it stops with an error on a database that holds projects.
- The lab resolver answers PTR queries for the lab addresses with the recorded names.

### TASK-037 — Dataset management and GeoLite2

- **dependencies:** TASK-034, TASK-035
- **risk_level:** high (handles the licence key; downloads from the Internet)

**Description.**

- A dataset manager that loaders plug into: update schedule, manual trigger, version, age and status per dataset in `reference_dataset`, and one audit entry per update.
- GeoLite2 City and ASN: download with the operator's key, verify the published checksum, replace the file atomically on the reference-data volume, reopen the reader. The version is the build date in the file.
- A daily check keeps the installation inside the licence's update window (NFR-LIC-1).
- A failed download or a checksum mismatch keeps the previous version and records the failure.
- No GeoLite2 file is put in the repository or in an image.
- The manager exposes one entry point for the orchestrator loop; TASK-040 wires it.

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/datasets/__init__.py`
- `Assesment/backend/src/ndm/orchestrator/datasets/manager.py`
- `Assesment/backend/src/ndm/orchestrator/datasets/geolite.py`
- `Assesment/backend/tests/integration/orchestrator/test_dataset_manager.py`
- `Assesment/backend/tests/integration/orchestrator/test_geolite_update.py`

**Acceptance.**

- Check 2.2, GeoLite2 part: a reader during an update sees the complete old file; a failed download or a checksum mismatch keeps the previous version and records the failure.
- Each update writes one audit entry and sets the version and load time.
- The licence key appears in no log line and no stored URL.

### TASK-038 — Registry loaders and lookup

- **dependencies:** TASK-033, TASK-037
- **risk_level:** medium

**Description.**

- Loaders, as decided in TASK-033: delegation statistics for all five registries; detailed objects for RIPE, APNIC and AFRINIC; operator-supplied bulk files for ARIN and LACNIC from the configured path.
- Each registry is parsed and loaded in one transaction that replaces that registry's rows, so a reader never sees a half-loaded set.
- Lookup: the most specific range that covers the address gives network name and organization. Without one, the organization falls back to the AS holder and the source is recorded (FR-ENR-3).
- Register the loaders with the dataset manager.

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/datasets/rir_delegation.py`
- `Assesment/backend/src/ndm/orchestrator/datasets/rir_objects.py`
- `Assesment/backend/src/ndm/orchestrator/datasets/rir_lookup.py`
- `Assesment/backend/src/ndm/orchestrator/datasets/__init__.py` (modified)
- `Assesment/backend/tests/fixtures/rir/` (one fixture file per registry and format)
- `Assesment/backend/tests/unit/orchestrator/test_rir_parsing.py`
- `Assesment/backend/tests/integration/orchestrator/test_rir_load.py`

**Acceptance.**

- Check 2.1: fixture files of each registry parse; the most specific covering range wins; without one, the organization falls back to the AS holder and the source is recorded.
- Check 2.2, registry part: a reader during a load sees the complete old set.

### TASK-039 — Post-probe phase runner and retry of a failed phase

- **dependencies:** TASK-034, TASK-035
- **risk_level:** medium (changes the scan lifecycle)

**Description.**

- A runner for the phases that follow probing. It runs them in order, one scan at a time per phase, and moves the scan to `completed` after the last one.
- A phase that fails marks the scan failed with the phase recorded and leaves all observations intact (NFR-REL-3).
- "Retry failed phase" runs that phase alone, started by the command of TASK-034.
- A phase reads only stored data and writes idempotently, so a phase interrupted by a crash is run again.
- The runner takes phases from a registry. This task registers none; the tests use a fake phase.

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/phases.py`
- `Assesment/backend/src/ndm/orchestrator/lifecycle.py` (modified)
- `Assesment/backend/src/ndm/orchestrator/main.py` (modified)
- `Assesment/backend/tests/integration/orchestrator/test_phase_runner.py`

**Acceptance.**

- A forced failure in a fake phase marks the scan failed with the phase recorded and leaves all observations intact; retrying the phase completes the scan.
- With no phase registered, a scan completes as it did in Phase 1B.
- An orchestrator restart in the middle of a phase runs that phase again and ends in the same state.

### TASK-040 — Enrichment: inline, enrichment phase and reverse DNS

- **dependencies:** TASK-036, TASK-038, TASK-039
- **risk_level:** high (writes scan data; reverse DNS sends target addresses to a resolver)

**Description.**

- Local enrichment of one address from GeoLite2 and the registry tables. A field whose dataset is missing or failed is marked unavailable. Every snapshot records the dataset versions it used; the scan records the versions current when it started.
- Inline enrichment when a host is first seen, inside result recording (I5). Redelivered findings create no second snapshot.
- The enrichment phase: reverse DNS for every reachable host through the configured resolver, then local enrichment for any host that missed it inline.
- A global switch turns reverse DNS off. With the switch off, no DNS query is sent.
- A re-run adds a snapshot with the next run number and moves the latest flag; earlier snapshots are unchanged (FR-ENR-7).
- Wire the dataset manager of TASK-037 into the orchestrator loop, and add the dataset-age metric.
- Enrichment makes no per-address request to any service except the configured resolver.

**expected_files.**

- `Assesment/backend/src/ndm/orchestrator/enrichment.py`
- `Assesment/backend/src/ndm/orchestrator/rdns.py`
- `Assesment/backend/src/ndm/orchestrator/recording.py` (modified)
- `Assesment/backend/src/ndm/orchestrator/phases.py` (modified)
- `Assesment/backend/src/ndm/orchestrator/main.py` (modified)
- `Assesment/backend/src/ndm/orchestrator/metrics.py` (modified)
- `Assesment/backend/tests/integration/orchestrator/test_enrichment_availability.py`
- `Assesment/backend/tests/integration/orchestrator/test_enrichment_snapshots.py`
- `Assesment/backend/tests/integration/orchestrator/test_inline_redelivery.py`
- `Assesment/backend/tests/lab/test_reverse_dns.py`

**Acceptance.**

- Check 2.3: with no datasets a scan completes and the fields are marked unavailable; a re-run after loading adds a second snapshot and leaves the first unchanged.
- Check 2.4: each reachable host has one snapshot per scan with its dataset versions; a dataset update does not change the snapshots of earlier scans (FR-ENR-5).
- Check 2.5: a forced failure in enrichment marks the scan failed with the phase recorded and leaves all observations intact; retrying the phase completes the scan.
- Check 2.6: redelivered findings create no second snapshot.
- Check 2.7: PTR names from the lab resolver are stored with the scan; with the switch off, a capture shows no DNS query.

### TASK-041 — Host query builder, filters, project-wide view and timeline

- **dependencies:** TASK-035
- **risk_level:** medium (reads scan data through the existing authentication)

**Description.**

- One query builder for table rows and map points, so a filter always applies to both (FR-MAP-6, FR-MAP-8).
- Filters: open port, ASN, organization, country and reverse-DNS text.
- The map-points endpoint for the same filters.
- The project-wide view: the latest state of every address in the project, through `project_host` (FR-MAP-5).
- Host detail extended with enrichment, dataset versions, reverse DNS and the timeline across scans.

**expected_files.**

- `Assesment/backend/src/ndm/api/queries/__init__.py`
- `Assesment/backend/src/ndm/api/queries/hosts.py`
- `Assesment/backend/src/ndm/api/routers/hosts.py` (modified)
- `Assesment/backend/src/ndm/api/schemas/hosts.py` (modified)
- `Assesment/backend/tests/integration/api/test_host_filters.py`
- `Assesment/backend/tests/integration/api/test_project_view.py`
- `Assesment/backend/tests/integration/api/test_host_timeline.py`

**Acceptance.**

- Check 2.9: for generated filter combinations the table endpoint and the map endpoint return the same set of addresses.
- Map points plus hosts without coordinates equal the rows of the table for the same filters.
- The timeline lists every scan that targeted the address, in order.

### TASK-042 — Dataset, tile-configuration and scan-action endpoints

- **dependencies:** TASK-034, TASK-035
- **risk_level:** medium

**Description.**

- Dataset endpoints: versions, ages and status; trigger an update. The trigger commits and then publishes a command.
- Tile configuration: the URL template and the attribution, or the fact that none is configured.
- Scan actions: retry the failed phase, re-run enrichment.
- No endpoint returns the MaxMind key or any other secret.

**expected_files.**

- `Assesment/backend/src/ndm/api/routers/datasets.py`
- `Assesment/backend/src/ndm/api/schemas/datasets.py`
- `Assesment/backend/src/ndm/api/routers/config.py` (modified)
- `Assesment/backend/src/ndm/api/schemas/config.py` (modified)
- `Assesment/backend/src/ndm/api/routers/scans.py` (modified)
- `Assesment/backend/src/ndm/api/schemas/scans.py` (modified)
- `Assesment/backend/src/ndm/api/commands.py` (modified)
- `Assesment/backend/src/ndm/api/app.py` (modified)
- `Assesment/backend/tests/integration/api/test_datasets.py`
- `Assesment/backend/tests/integration/api/test_scan_actions.py`

**Acceptance.**

- The dataset list shows version and age per dataset; a trigger publishes one command.
- "Retry failed phase" is refused for a scan that has not failed in a phase.
- No response body holds a configured secret value.
- Check 1A.3 still passes, unchanged, with the new routes.

### TASK-043 — Frontend project workspace, filter bar and scan selector

- **dependencies:** TASK-041, TASK-042
- **risk_level:** medium

**Description.**

- Regenerate the API types.
- The project workspace view: host table, filter bar and scan selector. The selector defaults to the latest scan and offers the project-wide view.
- `useHostFilters`: the filters are mirrored in the URL, so a view survives a reload.
- Live updates: with no filter active, changed hosts are applied to the store directly. With a filter active, the client refetches the filtered set, debounced to 500 ms. Filter logic exists only on the server.

**expected_files.**

- `Assesment/frontend/src/api/schema.d.ts` (regenerated)
- `Assesment/frontend/src/router/index.ts` (modified)
- `Assesment/frontend/src/stores/workspace.ts` (modified)
- `Assesment/frontend/src/composables/useHostFilters.ts`
- `Assesment/frontend/src/views/ProjectWorkspaceView.vue`
- `Assesment/frontend/src/components/FilterBar.vue`
- `Assesment/frontend/src/components/ScanSelector.vue`
- `Assesment/frontend/src/composables/useHostFilters.spec.ts`
- `Assesment/frontend/src/stores/workspace.spec.ts` (modified)

**Acceptance.**

- Vitest: filters round-trip through the URL; with a filter active a hosts-changed event causes one debounced refetch and no local filtering.
- The workspace opens on the latest scan.

### TASK-044 — Frontend map

- **dependencies:** TASK-043
- **risk_level:** medium

**Description.**

- `useMap` and the map component: MapLibre GL with a raster tile source taken from configuration and a clustered GeoJSON source, fed by the map-points endpoint with the active filters.
- Clicking a cluster that cannot be split further opens a list of its hosts (FR-MAP-2).
- Hosts without coordinates go to the "unlocated" panel (FR-MAP-3).
- Selecting a host draws its accuracy radius as a circle (FR-MAP-10).
- Location is labelled approximate wherever it is shown, with the radius (FR-ENR-8). MaxMind and tile attribution are visible (NFR-LIC-1, NFR-LIC-3).
- Without a configured tile source the map shows a configuration notice and contacts no tile server.
- Live updates reach the map as they reach the table.

**expected_files.**

- `Assesment/frontend/src/composables/useMap.ts`
- `Assesment/frontend/src/components/WorkspaceMap.vue`
- `Assesment/frontend/src/components/ClusterHostList.vue`
- `Assesment/frontend/src/components/UnlocatedPanel.vue`
- `Assesment/frontend/src/views/ProjectWorkspaceView.vue` (modified)
- `Assesment/frontend/src/composables/useMap.spec.ts`
- `Assesment/frontend/src/components/ClusterHostList.spec.ts`

**Acceptance.**

- Check 2.11, frontend test: with 300 hosts on one coordinate each can be selected from the cluster list; hosts without coordinates are in the "unlocated" panel; hosts on the map plus unlocated hosts equal the rows in the table.
- Check 2.14, in part: without a tile source the notice is shown and no tile request is made; both attributions are visible.

### TASK-045 — Frontend host detail extension and dataset settings page

- **dependencies:** TASK-043
- **risk_level:** medium

**Description.**

- Host detail panel extended with ASN, AS holder, organization and its source, country, city, coordinates, radius, dataset versions, reverse DNS and the timeline across scans. Unavailable fields are shown as unavailable, not as empty.
- Dataset settings page: version, age and status per dataset, and the update trigger.
- Scan actions in the scan list: retry the failed phase, re-run enrichment.

**expected_files.**

- `Assesment/frontend/src/components/HostDetailPanel.vue` (modified)
- `Assesment/frontend/src/components/HostTimeline.vue`
- `Assesment/frontend/src/views/DatasetSettingsView.vue`
- `Assesment/frontend/src/stores/datasets.ts`
- `Assesment/frontend/src/views/ScanListView.vue` (modified)
- `Assesment/frontend/src/router/index.ts` (modified)
- `Assesment/frontend/src/stores/datasets.spec.ts`
- `Assesment/frontend/src/components/HostTimeline.spec.ts`

**Acceptance.**

- Vitest: an unavailable field is rendered as unavailable; the timeline orders scans by time.
- The dataset page shows the versions the API reports, and the trigger starts an update.

### TASK-046 — Live-map, egress and load checks

- **dependencies:** TASK-040, TASK-044, TASK-045
- **risk_level:** medium

**Description.**

- Lab test with the replay harness for the live map, with and without an active filter.
- A capture-based test of the orchestrator's outbound traffic during a replayed scan with dataset updates paused.
- A browser test of the map with 10,000 hosts in current Chromium and Firefox.
- Query-time tests on a database seeded to the A7 envelope.
- If the project-wide view misses its limit, apply the fallback of architecture section 10.2 (copy the display columns onto `project_host` at scan completion) and repeat the check. The fallback changes the schema and the query builder, so it is raised as a new task.

**expected_files.**

- `Assesment/backend/tests/lab/test_live_map.py`
- `Assesment/backend/tests/lab/test_enrichment_egress.py`
- `Assesment/backend/tests/load/test_queries_envelope.py`
- `Assesment/frontend/playwright.config.ts`
- `Assesment/frontend/e2e/map-load.spec.ts`

**Acceptance.**

- Check 2.8: the only traffic leaving the host from the orchestrator is DNS to the configured resolver.
- Check 2.10: every host is drawn within 2 seconds of its finding's timestamp, with and without an active filter.
- Check 2.12: with 10,000 hosts loaded, pan and zoom hold at least 30 frames per second on the reference workstation and a filter change redraws within 1 second, in Chromium and Firefox (P1).
- Check 2.13: host table, each filter and the scan list answer within 1 second; the project-wide view answers within 1 second for 65,536 hosts.

### TASK-047 — Operator guide, demonstration and Phase 2 exit checks

- **dependencies:** TASK-046
- **risk_level:** medium

**Description.**

- Extend the operator guide: obtaining and configuring the MaxMind key, choosing a tile provider, the registry sources of TASK-033 with the path for operator-supplied files, and the fact that reverse DNS sends target addresses to the resolver (FR-ENR-4).
- An inspection script for check 2.14.
- Write the demonstration script for check 2.15 and carry it out on the authorized public range.
- Confirm check 2.11 by hand.
- Go through the common criteria of delivery plan section 2.2 and record the result of each of checks 2.1 to 2.15.

**expected_files.**

- `Assesment/docs/operator-guide.md` (modified)
- `Assesment/docs/demo/phase-2.md`
- `Assesment/deploy/checks/inspect-2.sh`

**Acceptance.**

- Check 2.14: no GeoLite2 file is in the repository or in any image; attribution is visible; without a configured tile source the map contacts no tile server.
- Check 2.15: on a scan of the authorized public range, hosts appear on the map as they are found, with ASN, AS holder, organization, country, city, coordinates, radius and dataset versions in the detail view.
- Every check from 2.1 to 2.15 is recorded as passed, with none skipped. AC 5 and AC 7 are met.

## 4. Coverage of Phase 2

| Phase 2 check | Task |
| --- | --- |
| 2.1 Registry parsing and lookup | TASK-038 |
| 2.2 Dataset update | TASK-037 (GeoLite2), TASK-038 (registries) |
| 2.3 Scanning without datasets | TASK-040 |
| 2.4 Reproducible snapshots | TASK-040 |
| 2.5 Phase failure is contained | TASK-039 (mechanism), TASK-040 (enrichment) |
| 2.6 Inline enrichment under redelivery | TASK-040 |
| 2.7 Reverse DNS | TASK-040 |
| 2.8 No per-address egress | TASK-046 |
| 2.9 Filters agree | TASK-041 |
| 2.10 Live map | TASK-046 |
| 2.11 Co-located and unlocated hosts | TASK-044 (frontend test), TASK-047 (manual) |
| 2.12 Map under load | TASK-046 |
| 2.13 Queries at the envelope | TASK-046 |
| 2.14 Licences | TASK-044 (tile notice), TASK-047 (inspection) |
| 2.15 Demonstration | TASK-047 |

| Common criterion (delivery plan 2.2) | Task |
| --- | --- |
| 1. CI green | Every task |
| 2. Clean start and demonstration | TASK-034, TASK-047 |
| 3. Migrations apply, revert, apply; earlier data kept | TASK-035 |
| 4. Audit entry in the same transaction, with a test | TASK-037, TASK-038 (dataset updates) |
| 5. Deletion purge covers every new table | TASK-035 |
| 6. Route-enumeration test covers new endpoints | TASK-041, TASK-042 |
| 7. No failed or skipped check | TASK-047 |
| 8. Operator guide | TASK-047 |
