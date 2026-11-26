# Network Discovery Map

A self-hosted tool for authorized network reconnaissance. An analyst defines a project with an attested scope, scans it for live hosts and open TCP ports, and sees the results in a table and on a map, enriched with registration, location, reverse DNS and a device type. Scans can be scheduled, compared, exported and traced.

| Document | For |
| --- | --- |
| This README | Getting started: running it, settings, architecture, first scan, the map |
| [docs/operator-guide.md](docs/operator-guide.md) | Everything an operator needs: every setting, host preparation, backup, updates |
| [network-discovery-map-spec.md](network-discovery-map-spec.md), [-architecture.md](network-discovery-map-architecture.md) | Requirements and design |
| [deploy/lab/README.md](deploy/lab/README.md) | The Compose lab of test targets |
| [docs/release/](docs/release/) | Release checklist, capacity, posture and acceptance records |

## Contents

1. [Components](#1-components)
2. [Running it](#2-running-it): production, lab, development, tests
3. [Settings](#3-settings)
4. [Architecture](#4-architecture): sequence diagram, class diagram
5. [Creating a project and running a scan](#5-creating-a-project-and-running-a-scan)
6. [Checking the results on the map](#6-checking-the-results-on-the-map)

## 1. Components

| Service | Code | What it does |
| --- | --- | --- |
| `proxy` | `deploy/Caddyfile`, `frontend/` | Caddy: TLS on port 443, serves the Vue application, passes `/api` to the API |
| `api` | `backend/src/ndm/api` | FastAPI. Validates requests and checks scope; never sends a probe |
| `orchestrator` | `backend/src/ndm/orchestrator` | Runs scans from queue to completion: plans chunks, records results, enriches, classifies, schedules, compares |
| `scanner` | `backend/src/ndm/scanner` | The only process that sends packets to targets. Host network, `NET_RAW` only |
| `postgres` | `backend/src/ndm/db` | PostgreSQL 17 with PostGIS: all data |
| `rabbitmq` | `backend/src/ndm/core/broker.py` | Messages between the API, orchestrator and scanner |
| `migrate`, `scanner-netinit` | | One-shot jobs: schema migrations; two firewall rules on the host |

```text
Assesment/
├── backend/    Python 3.14 package `ndm` (api, orchestrator, scanner, db, core), tests, scripts
├── frontend/   Vue 3 + Vuetify + MapLibre application
├── deploy/     Compose file, Dockerfiles, lab, inspection scripts, release tooling
├── docs/       Operator guide, phase records, release records
└── spike/      Phase 0 scanner spike
```

## 2. Running it

### 2.1 Production: one Compose stack on a Linux host

Needs a Linux host with Docker Engine and the Compose plugin (not Docker Desktop: the scanner needs the real host network), port 443 free, and authorization to scan the ranges you will enter. Full detail: [operator guide, sections 2–6](docs/operator-guide.md#2-requirements).

```sh
cd Assesment/deploy
cp .env.example .env && chmod 600 .env
openssl rand -hex 24      # three times: NDM_DB_OWNER_PASSWORD, NDM_DB_APP_PASSWORD, NDM_BROKER_PASSWORD
$EDITOR .env              # the three passwords, and NDM_SITE_ADDRESS (the name typed in the browser)

# Host preparation, once (operator guide, section 4)
echo 'net.core.rmem_max = 16777216' | sudo tee /etc/sysctl.d/90-ndm.conf && sudo sysctl --system

docker compose up -d --build
docker compose ps -a      # all healthy; migrate and scanner-netinit "exited (0)"
docker compose logs api | grep setup_required     # the one-time setup token
```

Then open `https://<NDM_SITE_ADDRESS>/`, accept the certificate (the proxy has its own authority; [section 6](docs/operator-guide.md#6-the-tls-certificate) shows how to trust it) and create the account with the setup token. There is one account per installation and no password reset.

Updating, backup and restore are in [sections 15 and 16](docs/operator-guide.md#15-updating). `deploy/release/build-images.sh` builds the release images and records their digests.

### 2.2 Lab: the production stack against test targets

The lab ([deploy/lab](deploy/lab/README.md)) runs hosts with known open ports in private address space (10.77.3.0/24), one routed hop away. The product refuses private addresses, so the stack runs with the test switch from `lab/lab.env`. **Never use `lab.env` on a real installation.**

```sh
cd Assesment/deploy
docker compose -f lab/compose.lab.yaml up -d --build
docker compose --env-file .env --env-file lab/lab.env up -d --build
```

After a reboot the lab's host routes are gone; restore them with `docker compose -f lab/compose.lab.yaml up -d --force-recreate --wait host-route`. A project with the scope `10.77.3.0/24` then finds the hosts of [deploy/lab/manifest.yaml](deploy/lab/manifest.yaml).

### 2.3 Development in Docker, with hot reload

One command runs everything in containers, from your working copy ([deploy/compose.dev.yaml](deploy/compose.dev.yaml)). From `Assesment/`:

```sh
docker compose -f deploy/lab/compose.lab.yaml up -d            # the lab targets, once
docker compose -f deploy/compose.dev.yaml up --build --watch
```

Open **<http://localhost:5173>** and enter the setup token from the output (`setup_required`, field `setup_token`). A project with the scope `10.77.3.0/24` scans the lab.

- **Hot reload:** `backend/src` is mounted into every Python container. The API reloads itself on a change, and `--watch` restarts the orchestrator and the scanner. The frontend runs on Vite's dev server with hot module replacement. After a change of dependencies (`uv.lock`, `package-lock.json`), run `up --build` again. After adding a migration, run `docker compose -f deploy/compose.dev.yaml up migrate`.
- **Like production where it matters:** the scanner runs on the host network with `NET_RAW`, so SYN scans, ICMP discovery and traceroutes work. It has its own source ports (62000–62999) and its own two firewall rules.
- **Separate from the other stacks:** project `ndm-dev`, its own volumes, fixed throwaway passwords, and ports on `127.0.0.1` only: frontend 5173, API 8000 (`/docs`), PostgreSQL 5433 (`postgresql://ndm_owner:dev-owner@127.0.0.1:5433/ndm`), RabbitMQ 5673.
- **Settings:** the test switch is on, probes leave from the lab's transit address, and reverse DNS asks the lab resolver. `NDM_DEV_EGRESS_ADDRESS=` and `NDM_DEV_PTR_RESOLVER=` (empty) switch the last two back to the host's own. MaxMind, tiles and the other optional settings are read from `deploy/.env` if set. **Never use this file for a real installation.**
- **Stop:** Ctrl-C, or `docker compose -f deploy/compose.dev.yaml down`; `down -v` also deletes the dev database.

### 2.4 Development: local processes with hot reload

Each service runs as a local process; only PostgreSQL and RabbitMQ run in containers. Needs Docker, Python 3.14 with [uv](https://docs.astral.sh/uv/), and Node.js 26. The ports below (5433, 5673, 8000, 5173) do not clash with a production or lab stack on the same host.

**1. Database and broker.** The pinned images, with the product's own start-up scripts. The passwords are local throwaways.

```sh
cd Assesment/deploy
docker run -d --name ndm-dev-postgres --user postgres \
  -e POSTGRES_USER=ndm_owner -e POSTGRES_PASSWORD=dev-owner -e POSTGRES_DB=ndm \
  -e NDM_APP_DB_ROLE=ndm_app -e NDM_APP_DB_PASSWORD=dev-app \
  -v "$PWD/postgres/no-postgis-init.sh:/docker-entrypoint-initdb.d/10_postgis.sh:ro" \
  -v "$PWD/postgres/init-roles.sh:/docker-entrypoint-initdb.d/20-init-roles.sh:ro" \
  -p 127.0.0.1:5433:5432 postgis/postgis:17-3.5
docker run -d --name ndm-dev-rabbitmq \
  -e RABBITMQ_DEFAULT_USER=ndm -e RABBITMQ_DEFAULT_PASS=dev-broker \
  -p 127.0.0.1:5673:5672 rabbitmq:4.3.7-alpine
```

**2. Backend: dependencies and schema.**

```sh
cd Assesment/backend
uv sync --all-extras
NDM_MIGRATION_DATABASE_URL=postgresql+asyncpg://ndm_owner:dev-owner@127.0.0.1:5433/ndm \
NDM_APP_DB_ROLE=ndm_app uv run alembic upgrade head
```

**3. The three services**, each in its own terminal, with the same settings. Put them in a file, for example `dev.env`:

```sh
NDM_DATABASE_URL=postgresql+asyncpg://ndm_app:dev-app@127.0.0.1:5433/ndm
NDM_BROKER_URL=amqp://ndm:dev-broker@127.0.0.1:5673/
NDM_REFERENCE_DATA_PATH=/tmp/ndm-reference      # where the orchestrator keeps downloaded datasets
NDM_VANTAGE_POINT=dev
# To scan the lab (section 2.2) from a development setup; never elsewhere:
NDM_ALLOW_NON_PUBLIC_TARGETS=true
NDM_EGRESS_ADDRESS=10.77.0.1
NDM_PTR_RESOLVER=10.77.0.1:1053
```

```sh
set -a; . ./dev.env; set +a
uv run uvicorn --factory ndm.api.app:production_app --port 8000 --reload   # terminal 1: the API
uv run python -m ndm.orchestrator.main                                    # terminal 2
uv run python -m ndm.scanner.start                                        # terminal 3
```

The scanner run this way has no `NET_RAW`, so it scans with ordinary TCP connections ("connect" mode), and ICMP discovery is unavailable; every scan says so. For SYN scans and traceroutes, run the scanner image instead (`docker compose up -d scanner`, or see the engine smoke test in `tests/lab/test_engine_smoke.py`). The orchestrator downloads the registries' delegation files at start, as in production.

**4. Frontend** with hot reload. Vite passes `/api` to `localhost:8000`.

```sh
cd Assesment/frontend
npm ci
npm run dev               # http://localhost:5173
```

The first page asks for the setup token: it is in the API's output (`setup_required`, field `setup_token`).

**To stop:** end the four processes, then `docker rm -f ndm-dev-postgres ndm-dev-rabbitmq`.

### 2.5 Tests and checks

```sh
cd Assesment/backend
uv run pytest                          # unit and integration tests; the integration tests start
                                       # the pinned PostgreSQL and RabbitMQ through testcontainers
uv run ruff check . && uv run ruff format --check . && uv run mypy .

cd ../frontend
npm run lint && npm run typecheck && npm test && npm run build

# Lab checks, against the lab stack (section 2.2):
cd ../backend
NDM_LAB=1 NDM_LAB_USERNAME=<account> NDM_LAB_PASSWORD=<password> uv run pytest tests/lab

# Load checks: docs/release/capacity-report.md, section 7.
# Inspections of a running stack:
cd ../deploy && ./checks/inspect-release.sh
```

## 3. Settings

All settings are environment variables beginning with `NDM_`. In Compose they live in `deploy/.env`; after a change, run `docker compose up -d`. The complete list, with every default, is in [operator guide, section 7](docs/operator-guide.md#7-configuration).

### Required

| Variable | Description |
| --- | --- |
| `NDM_DB_OWNER_PASSWORD` | Password of the database role that owns the schema and runs migrations. |
| `NDM_DB_APP_PASSWORD` | Password of the database role the API and the orchestrator use. It has no right to change or delete audit entries. |
| `NDM_BROKER_PASSWORD` | Password of RabbitMQ. |
| `NDM_SITE_ADDRESS` | Host name or IP address the analyst types into the browser (default `localhost`). The TLS certificate is issued for exactly this name. |

Set the passwords before the first start: PostgreSQL and RabbitMQ create their users once, when their volumes are first initialized. Use letters, digits and `-`, `_`, `.`, `~` only, because the passwords go into connection URLs.

### Needed for enrichment and the map

| Variable | Description |
| --- | --- |
| `NDM_MAXMIND_ACCOUNT_ID`, `NDM_MAXMIND_LICENSE_KEY` | A free MaxMind account and licence key. The orchestrator downloads GeoLite2 City and ASN with them: location, ASN and AS holder. Without them those fields are unavailable and no host has a location. |
| `NDM_TILE_URL_TEMPLATE` | Raster tile URL with `{z}`, `{x}`, `{y}`, from a provider whose terms permit this use. There is no default; unset, the map is switched off. |
| `NDM_TILE_ATTRIBUTION` | The attribution text the tile provider requires. Required with a tile URL. |
| `NDM_RIR_OBJECT_REGISTRIES` | Registries whose registration records are downloaded (network name, organization): any of `ripe`, `apnic`, `afrinic`. Only if your use meets their terms. ARIN and LACNIC files go into `NDM_RIR_FILES_DIR`. |
| `NDM_PTR_RESOLVER`, `NDM_PTR_ENABLED` | The resolver for reverse DNS (default: the host's), or `false` to send no DNS query. Reverse DNS is the one lookup that reveals the scanned addresses to a third party. |

### Operator ceilings and the scanner (defaults shown)

| Variable | Default | Description |
| --- | --- | --- |
| `NDM_GLOBAL_RATE_PPS` | `5000` | Packets per second across all running scans, and the most one scan may ask for. |
| `NDM_MAX_CONCURRENT_SCANS` | `3` | Scans that run at once; more wait in the queue. |
| `NDM_MAX_CONCURRENT_TRACEROUTES` | `5` | Traceroutes that run at once. |
| `NDM_PROJECT_SCOPE_CEILING` | `65536` | Most addresses one project scope may hold. |
| `NDM_NEVER_SCAN` | empty | Addresses never scanned, whatever the scope: comma-separated addresses, CIDR blocks or ranges. |
| `NDM_MIN_SCHEDULE_INTERVAL_SECONDS` | `3600` | Shortest time between two runs of a schedule. |
| `NDM_VANTAGE_POINT` | `default` | Name of the place scans are made from. |
| `NDM_EGRESS_ADDRESS` | unset | The public address probes leave from; unset, the address the host routes from. |
| `NDM_SOURCE_PORT_RANGE` | `61000-61999` | Source ports of SYN probes; must lie outside the kernel's ephemeral range. |
| `NDM_SESSION_IDLE_TIMEOUT_SECONDS` | `1800` | A session ends after this long without a request. |
| `NDM_CLASSIFICATION_THRESHOLD` | `0.5` | Confidence below which a host's device type is `unknown`. |
| `NDM_LOG_LEVEL` | `INFO` | Log level of every service. |
| `NDM_ALLOW_NON_PUBLIC_TARGETS` | `false` | Test switch for the lab only: allows private addresses. Never on a real installation. |

### Set by Compose, or by you in development

Compose builds these from the settings above. A local process (section 2.3) needs them set directly.

| Variable | Used by | Description |
| --- | --- | --- |
| `NDM_DATABASE_URL` | api, orchestrator | `postgresql+asyncpg://` URL of the application role. |
| `NDM_MIGRATION_DATABASE_URL`, `NDM_APP_DB_ROLE` | migrations | URL of the owning role, and the name of the application role to grant rights to. |
| `NDM_BROKER_URL` | api, orchestrator, scanner | `amqp://` URL of RabbitMQ. |
| `NDM_REFERENCE_DATA_PATH` | orchestrator | Writable directory for the downloaded datasets (default `/var/lib/ndm/reference`). |
| `NDM_RIR_FILES_PATH` | orchestrator | Directory with the ARIN and LACNIC bulk files, if any. |

## 4. Architecture

The design is in [network-discovery-map-architecture.md](network-discovery-map-architecture.md). The two diagrams below show how a scan travels through the services, and what is stored.

### 4.1 Sequence diagram: a scan from launch to the map

The scope is checked at three independent points (API, orchestrator, scanner). Every message is idempotent, so a redelivery after a crash changes nothing. Live updates are not stored: after a reconnect the browser reloads its view.

```mermaid
sequenceDiagram
    autonumber
    actor Analyst
    participant UI as Browser (Vue app)
    participant API as api
    participant DB as PostgreSQL
    participant MQ as RabbitMQ
    participant Orch as orchestrator
    participant Scan as scanner

    Analyst->>UI: New scan (targets, port profile, rate)
    UI->>API: POST /api/v1/projects/{id}/scans
    API->>DB: targets inside the attested scope, public, not never-scan?
    API->>DB: INSERT scan (queued) + audit entry, COMMIT
    API->>MQ: ScanCreated (ndm.commands)
    API-->>UI: 201 scan

    loop every tick, or woken by a command
        Orch->>DB: promote queued scans while a scanner is alive and slots are free
        Orch->>DB: scan running: technique (SYN or connect), one observation per target
        Orch->>DB: plan chunks: discovery first, then port scan (about 10,000 probes each)
        Orch->>DB: check scope again
        Orch->>MQ: Chunk (ndm.scanner, queue of the vantage point)
    end

    MQ->>Scan: Chunk
    Scan->>Scan: check scope a third time, probe at the shared rate
    Scan->>MQ: Findings (every ~0.5 s), then ChunkDone (ndm.results)
    MQ->>Orch: Findings / ChunkDone
    Orch->>DB: record hosts and open ports, inline enrichment, project copy
    Orch->>MQ: hosts_changed, scan_progress (ndm.events)
    MQ->>API: events
    API-->>UI: server-sent events
    UI-->>Analyst: table and map update live

    Note over Orch,DB: after the last chunk
    Orch->>DB: enrichment phase: reverse DNS, GeoLite2, registries (snapshot per host)
    Orch->>DB: classification phase: device type per host
    Orch->>DB: diff with the previous run (scheduled scans), alerts
    Orch->>MQ: scan_progress (completed)
    API-->>UI: completed
```

### 4.2 Class diagram: the data model

The main entities of `backend/src/ndm/db/models.py`. What a scan observed is never changed afterwards: re-running enrichment or classification adds a row with the next run number and moves its `latest` flag.

```mermaid
classDiagram
    direction LR

    class Account {
        username
        password_hash
    }
    class UserSession {
        token_hash
        expires_at
    }
    class Project {
        name
        state
        current_attestation_id
    }
    class Attestation {
        account_id
        created_at
    }
    class ScopeEntry {
        kind
        start_address
        end_address
    }
    class Scan {
        state
        phase
        targets
        parameters
        technique_used
        icmp_discovery_unavailable
        dataset_versions
    }
    class ScanChunk {
        chunk_seq
        phase
        state
    }
    class VantagePoint {
        name
        capabilities
        last_heartbeat_at
    }
    class HostObservation {
        address
        reachable
        evidence
        closed_count
        filtered_count
        ptr_names
    }
    class PortObservation {
        address
        port
    }
    class EnrichmentSnapshot {
        run
        latest
        asn
        organization
        country
        location
    }
    class ClassificationResult {
        run
        latest
        device_type
        confidence
    }
    class ProjectHost {
        address
        latest_scan_id
        type_override
        tags
        notes
        copy of the latest observation
    }
    class Host {
        address
        first_seen_at
        last_seen_at
    }
    class Schedule {
        interval_seconds
        cron
        next_run_at
    }
    class ScheduleRun {
        planned_at
        outcome
    }
    class ScanDiff {
        summary
    }
    class ChangeEvent {
        kind
        address
        port
    }
    class Alert {
        type
        read_at
    }
    class TracerouteRun {
        target
        method
        state
    }
    class TracerouteProbe {
        ttl
        responder
        rtt_ms
    }
    class AuditEvent {
        actor
        action
        object_type
        object_id
    }
    class ReferenceDataset {
        name
        version
        status
    }

    Account "1" --> "*" UserSession
    Account "1" --> "*" Attestation : attests
    Project "1" --> "*" Attestation : scope history
    Attestation "1" --> "*" ScopeEntry
    Project "1" --> "*" Scan
    Scan "*" --> "1" Attestation : validated against
    Scan "*" --> "0..1" VantagePoint : made from
    Scan "1" --> "*" ScanChunk
    Scan "1" --> "*" HostObservation : one per target
    HostObservation "1" --> "*" PortObservation : open ports
    HostObservation "1" --> "*" EnrichmentSnapshot : runs
    HostObservation "1" --> "*" ClassificationResult : runs
    Project "1" --> "*" ProjectHost : addresses
    ProjectHost "*" --> "0..1" Scan : latest scan
    ProjectHost ..> Host : same address
    Project "1" --> "*" Schedule
    Schedule "1" --> "*" ScheduleRun
    ScheduleRun "*" --> "0..1" Scan
    ScanDiff "*" --> "2" Scan : base, target
    ScanDiff "1" --> "*" ChangeEvent
    Project "1" --> "*" Alert
    Project "1" --> "*" TracerouteRun
    TracerouteRun "1" --> "*" TracerouteProbe : hops
    TracerouteRun "1" --> "*" EnrichmentSnapshot : hop snapshots
```

Not shown: `ScanProfile` (saved scan parameters), `TracerouteResponder`, and the registry tables `RirDelegation` and `RirInetnum`. `AuditEvent` and `ReferenceDataset` stand alone: the audit log is append-only (the database rejects updates and deletes), and the datasets are reference data of the installation. `host_observation` and `port_observation` are hash-partitioned by scan.

## 5. Creating a project and running a scan

Only scan ranges you are authorized to scan. Probes leave from the host's own address.

1. **Log in** at `https://<NDM_SITE_ADDRESS>/` (or `http://localhost:5173` in development). On the very first visit, create the account with the setup token (section 2.1).
2. **Create a project.** **Projects → New project**: a name and, optionally, a description. **Save**.
3. **Set its scope.** Open the project, which opens its **Scope** page. **Add entry** once per range: a single address, a CIDR block (`198.51.100.0/24`) or a range (`198.51.100.10-198.51.100.20`). The scope may hold at most `NDM_PROJECT_SCOPE_CEILING` addresses, all public (in the lab, `10.77.3.0/24`). **Save scope** opens **Confirm authorization**: tick "I confirm that I am authorized to scan the listed ranges." and **Save scope**. The attestation is stored with that exact scope, your account and the time, and every later change to the scope needs a new one.
4. **Start a scan.** **Scans → New scan**:

   | Field | Default | Meaning |
   | --- | --- | --- |
   | Targets, one per line | | Addresses, CIDR blocks or ranges inside the scope |
   | Ports | top-1000 | `top-100`, `top-1000`, `full` (all 65,535), a saved profile, or custom (`22,80,8000-8100`) |
   | Discovery | ICMP echo and TCP probes to 80, 443 | Which hosts count as up. Only those are port-scanned, unless **Skip discovery and port-scan every target** is ticked |
   | Technique | Automatic | SYN if the scanner has its raw-socket capability, otherwise connect |
   | Packets per second | 1000 | At most `NDM_GLOBAL_RATE_PPS`; scans share that ceiling |
   | Probe timeout, Retries, Hosts in parallel | 1500 ms, 1, 64 | |

   The dialog shows the number of probes and an estimate of the duration. **Start scan**.
5. **Follow it.** The scan list shows each scan's state, progress, technique and what it found. **Workspace** shows the selected scan live: hosts appear in the table and on the map as they are found. A scan runs on after a restart of any service, and shows as `interrupted` while the scanner is away.
6. **After probing**, the scan is enriched and classified, then `completed`. From the scan list: **Cancel** a running scan; **Enrich again** or **Classify again** after a dataset or ruleset update; **Compare** two scans; **Delete** a scan. **Schedules** runs scans on an interval or a cron expression.

The same through the API, as the browser does it (the session cookie comes from `POST /api/v1/login`):

```sh
curl -b cookies -X POST https://<site>/api/v1/projects -H 'content-type: application/json' \
  -d '{"name": "Edge routers"}'
curl -b cookies -X PUT https://<site>/api/v1/projects/1/scope -H 'content-type: application/json' \
  -d '{"entries": ["198.51.100.0/24"], "authorized": true}'
curl -b cookies -X POST https://<site>/api/v1/projects/1/scans -H 'content-type: application/json' \
  -d '{"targets": ["198.51.100.0/24"], "port_profile": "top-100"}'
curl -b cookies https://<site>/api/v1/scans/1
```

The full API is described in `backend/openapi.json`.

## 6. Checking the results on the map

### What the map needs

- **A tile source**: `NDM_TILE_URL_TEMPLATE` and `NDM_TILE_ATTRIBUTION` (section 3). Without one the map is switched off: the workspace shows a notice instead, the browser contacts no tile server, and the hosts are in the table as usual. Your browser loads the tiles directly from the provider.
- **Locations**: GeoLite2, from `NDM_MAXMIND_ACCOUNT_ID` and `NDM_MAXMIND_LICENSE_KEY`. **Datasets** in the top bar shows whether it is loaded, its version and age, and can start an update.
- **Public addresses.** Private addresses, such as those of the lab, have no location. They are listed beside the map, not drawn on it.

### Reading the map

Open a project's **Workspace**.

- **What is shown.** The selector **Shown** picks one scan, or **Whole project: latest state of every address**: each address as the latest scan that targeted it saw it. The map always shows the same hosts as the table below it.
- **Filters.** The filter bar narrows table and map together: **Open port**, **ASN**, **Organization contains**, **Country code**, **Reverse DNS contains**, **Device type**, **Tag**, and, for one scan, **Change status** against the previous scan (changed, appeared, disappeared, unchanged). The filters are kept in the URL, so a view can be bookmarked or shared.
- **Hosts and clusters.** A red dot is one host; a blue circle is a cluster, with its count. Clicking a cluster zooms into it. Hosts at the same coordinate, which is common, cannot be split by zooming: clicking such a cluster lists its hosts.
- **Host detail.** Selecting a host, on the map, in a cluster's list or in the table, opens its detail: reachability evidence, open ports with their nominal IANA service names, registration and location, reverse DNS, the device type with the rules that matched, tags and notes, and its history across scans. The area of the location's accuracy radius is drawn on the map.
- **Locations are approximate.** A host is drawn at the centre of an area, not where it is; the detail gives the radius. Do not use it to locate people: the GeoLite2 licence forbids it.
- **Without a location.** The panel **Without a location** lists every host that is shown but has no coordinates, with its own pages.
- **Paths.** **Trace route** in the host detail traces the path to the host (ICMP, TCP or UDP). Hops appear as they answer, and the path is drawn through the hops that have a location; a dashed segment skips a silent or unlocated hop. It needs the scanner's raw sockets.
- **Export.** **Export** gives the hosts of the current view and filters, all pages, as CSV or JSON, with the scan identifier, time, vantage point and dataset versions in every row.

### Trying the map without scanning real networks

The lab's addresses are private and have no location. To see located hosts without probing anything, the findings-replay harness ([backend/tests/tools/replay_findings.py](backend/tests/tools/replay_findings.py)) takes the scanner's place and answers every chunk from a recorded scan of public addresses; no packet is sent. With a tile source and GeoLite2 configured: stop the scanner, start the harness as its docstring shows, create a project with the scope it prints, and scan it.
