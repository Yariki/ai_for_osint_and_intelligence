# Phase 2 — Demonstration Script and Exit Checks

This document has two parts. Part 1 is the written demonstration for check 2.15: a person follows it on the lab host against the authorized public range. Part 2 is the record of checks 2.1 to 2.15 and of the common criteria of delivery plan section 2.2.

## Part 1. Demonstration (check 2.15)

**You need.** The lab host, prepared as in the operator guide; the product stack running with:

- a MaxMind account ID and licence key in `.env` (operator guide, section 11);
- a tile provider in `.env` (`NDM_TILE_URL_TEMPLATE`, `NDM_TILE_ATTRIBUTION`);
- `NDM_RIR_OBJECT_REGISTRIES` set to the registries whose terms the installation meets, if any;
- the authorized public range (delivery plan, entry conditions of Phase 2), and a browser.

The test switch is **off**: this is a scan of public addresses.

| # | Step | Expected result |
| --- | --- | --- |
| 1 | Open **Datasets** in the top bar. | GeoLite2 City and ASN show a version (a build date) and an age under a day, status "in use". The five delegation statistics are in use. Registries that are not configured say why, for example "Not enabled. Add ripe to NDM_RIR_OBJECT_REGISTRIES…". |
| 2 | Create a project, save the authorized range as its scope with the confirmation. | "The scope was saved." |
| 3 | Open the project's **Workspace**. | "This project has no scan yet." |
| 4 | From **Scans**, start a scan of the range with the profile top-100. Return to the workspace. | The workspace opens on the new scan. The map shows the tile provider's background, its attribution, and the MaxMind attribution under it. |
| 5 | Watch the map while the scan runs. | Hosts appear on the map as they are found, without a reload, grouped into clusters. Hosts with no known location appear in "Without a location" beside the map. The host table fills at the same time. |
| 6 | Click a cluster of hosts that share a city. | If zooming can separate them, the map zooms in. If it cannot, a list of every host at that location opens. Select one. |
| 7 | Look at the host's detail. | ASN, AS holder, organization with its source, registry, registration country, country, city, coordinates and accuracy radius, each marked approximate where it is a location. The dataset versions of the snapshot are listed. Reverse DNS shows names, or says why there are none. The map draws a circle with the accuracy radius. |
| 8 | Enter a filter, for example an open port and a country, and press **Filter**. | Table and map show only the matching hosts. The URL holds the filter; reloading the page shows the same view. |
| 9 | Choose "Whole project" in the selector. | Every address of the project as its latest scan saw it. |
| 10 | When the scan has completed, open **Scans** and press **Enrich again** on it. | Within seconds the host detail shows snapshot 2. The first snapshot is kept. |
| 11 | Run `./checks/inspect-2.sh` in `Assesment/deploy`. | Every line starts with `PASS`. |
| 12 | Open **Audit log**. | Entries for the dataset updates (`dataset.update`), the scope, the launch, and the re-run (`scan.enrich`). |

## Part 2. Record of exit checks

A check that cannot be run is recorded with its reason and needs the product owner's acceptance (delivery plan 2.2, item 7).

| Check | How it is run | Result | Date | Note |
| --- | --- | --- | --- | --- |
| 2.1 Registry parsing and lookup | `tests/unit/orchestrator/test_rir_parsing.py`, `tests/integration/orchestrator/test_rir_load.py` | Passed, reservations A and C | 2026-11-17 | One fixture per registry and format. The parsers were also run over the real statistics files of all five registries and the first 60 KB of the RIPE, APNIC and AFRINIC dumps, downloaded on 2026-11-16. |
| 2.2 Dataset update | `test_geolite_update.py`, `test_dataset_manager.py`, `test_rir_load.py` | Passed, reservations A and D | 2026-11-17 | A reader keeps the complete old file or the complete old rows during an update; a failed download or checksum keeps the previous version and records the failure. |
| 2.3 Scanning without datasets | `test_enrichment_availability.py` | Passed, reservation A | 2026-11-17 | |
| 2.4 Reproducible snapshots | `test_enrichment_snapshots.py` | Passed, reservation A | 2026-11-17 | |
| 2.5 Phase failure is contained | `test_phase_runner.py`, `test_enrichment_snapshots.py`, `tests/integration/api/test_scan_actions.py` | Passed, reservation A | 2026-11-17 | |
| 2.6 Inline enrichment under redelivery | `test_inline_redelivery.py` | Passed, reservations A and B | 2026-11-17 | |
| 2.7 Reverse DNS | `test_reverse_dns.py` (integration), `tests/lab/test_reverse_dns.py` | Passed | 2026-11-19 | On the lab: the names of the lab resolver's zone stored with the scan, `no_name` for the others; queries only to the configured resolver; with the switch off, no query in the capture. |
| 2.8 No per-address egress | `tests/lab/test_enrichment_egress.py` | Passed | 2026-11-19 | A replayed scan of public addresses with dataset updates paused: everything the orchestrator sent was DNS to the configured resolver. |
| 2.9 Filters agree | `tests/integration/api/test_host_filters.py`, `test_project_view.py` | Passed, reservation A | 2026-11-17 | Every pair of six filters in full and a sample of the full product: over 250 combinations. |
| 2.10 Live map | `tests/lab/test_live_map.py`; frontend `workspace.spec.ts` | Passed | 2026-11-19 | On the lab, at the API: every host within 2 seconds of its finding without a filter, and with a filter (port 53) including the browser's 500 ms debounce. Frontend: new hosts are put on the map from the event; with a filter, one debounced refetch. |
| 2.11 Co-located and unlocated hosts | frontend `useMap.spec.ts`, `ClusterHostList.spec.ts`; by hand | **Partly run** | 2026-11-17 | Frontend test: each of 300 hosts on one coordinate can be selected; unlocated hosts are listed; map plus unlocated equals the table. The check by hand in a browser: not run. |
| 2.12 Map under load | `frontend/e2e/map-load.spec.ts` (Playwright) | **Not run** | | Needs the browsers and the reference workstation (P1). |
| 2.13 Queries at the envelope | `tests/load/test_queries_envelope.py` | Passed | 2026-11-19 | At the full envelope (A7: 195,315 scans, 50.0 million host observations, 250.4 million open ports, 100 projects, the largest of 65,536 addresses), on the pinned database image with the product's limits (1 GB, 2 CPUs). It **first failed**: the project-wide view took 17.7–42.4 s and its map points 8.0 s (at 0.4 % it had been 0.63 s). Fixed by TASK-078, the fallback of architecture 10.2: the view now reads a copy on `project_host`. After the fix: project-wide view 0.04–0.16 s for every filter, last page 0.21 s, map points of the project 0.46 s, host table of a scan 0.02–0.05 s, scan list 0.30 s, host detail 0.04 s. |
| 2.14 Licences | `deploy/checks/inspect-2.sh`; frontend `useMap.spec.ts` | Passed | 2026-11-19 | Frontend (2026-11-17): without a tile source the notice is shown and no map is created, so no tile is requested; both attributions are shown. `inspect-2.sh` on the lab host with the release images: no GeoLite2 database in the repository or in any of the six images; the served application carries both attributions; no tile source configured, so no tile URL is given. A deployment with a tile source configured was not inspected; the acceptance run (5.1) runs the same script on its deployment. |
| 2.15 Demonstration | Part 1 | **Not run** | | Needs the lab host, a MaxMind key, a tile provider and the authorized range. |

**Reservations A and B, lifted on 2026-11-18.** The whole backend suite, the tests of this phase among them, ran on the pinned `postgis/postgis:17-3.5` and RabbitMQ images: 1,478 passed, none failed. The rows above that still name A or B passed again on the real images.

**Reservation A: the database.** The integration tests start PostgreSQL through `testcontainers` with the pinned image `postgis/postgis:17-3.5`. Without Docker, they ran against PostgreSQL 18 with PostGIS 3.6, unpacked from the distribution's packages.

**Reservation B: the broker.** No RabbitMQ was available. The orchestrator's result handling was called directly. The tests that need a real broker, among them two new ones for the replay harness, were written and not run; CI runs them.

**Reservation C: ARIN and LACNIC.** Their bulk files are given out under agreements the team does not have. The loaders follow the registries' published descriptions; the first real file must confirm them. LACNIC does not publish the format at all.

**Reservation D: MaxMind.** The download was tested against a stand-in for MaxMind's service, with databases built in the test. The URL form was confirmed against the real service, which answers 401 without credentials; no real download was made.

**What was run, on 2026-11-17.** 1,020 backend tests and 144 frontend tests passed; the 25 load checks passed at 0.4 % of the envelope; ruff, mypy, ESLint, vue-tsc, the frontend build, pip-audit and npm audit were clean.

### Common criteria (delivery plan 2.2)

| Criterion | State |
| --- | --- |
| 1. CI green | Not observed: the workflow has not run on this branch. Locally, every step that needs no Docker passes. |
| 2. Clean start and demonstration | Not run: needs Docker. |
| 3. Migrations apply, revert, apply; earlier data kept | Passed (reservation A): `test_migration_0003.py`, also for a database that already had PostGIS. |
| 4. Audit entry in the same transaction, with a test | Passed: `test_dataset_manager.py` shows that the loader's rows, the version and the audit entry are kept or lost together. |
| 5. Deletion purge covers every new table | Passed: `test_purge.py`, with snapshots present, and the registry test. |
| 6. Route-enumeration test covers new endpoints | Passed: `test_route_auth.py` enumerates every route, the new ones included. |
| 7. No failed or skipped check | **Not met**: see the table above. |
| 8. Operator guide | Done: sections 7, 11, 12, 14 and 15 (numbered as of Phase 4). |

### Decisions the product owner is asked to confirm

- **Registry records are opt-in.** Architecture 11.1 has RIPE, APNIC and AFRINIC records loaded by default. Their terms (APNIC: signed agreement; AFRINIC: request, no commercial service; RIPE: listed purposes only) make that a decision of each operator, so the default is none (`docs/datasets/registry-sources.md`).
- **I5, I6 and P1**, as listed in the entry conditions of Phase 2. I5 (inline enrichment) is built as specified.

**Also open from earlier phases.** Gate 0, checks 1A.8 and 1A.9, and checks 1B.10 to 1B.19 have not been run.

**Phase 2 is not complete until every check above is recorded as passed.**
