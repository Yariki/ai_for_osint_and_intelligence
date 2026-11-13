# Network Discovery Map — Technical Specification

| | |
|---|---|
| Version | 1.0 |
| Date | 2026-11-14 |
| Status | Requirements baseline, ready for architecture design |
| Audience | Software architect |

## 1. Purpose

Network Discovery Map is a self-hosted web application that lets a security analyst discover, analyze and visualize public IPv4 infrastructure they are authorized to scan. It finds reachable hosts and open TCP ports, enriches each address with registration and approximate location data, classifies the likely device type, shows the result on an interactive map, and keeps full history so that changes between scans can be identified.

This document states what the system must do and the constraints it must respect. It does not prescribe the internal design; where a design decision is deliberately left to the architect, it is listed in section 11.

Requirement priorities for version 1: **M** = must, **S** = should, **C** = could (only if cheap).

## 2. Decisions and assumptions

### 2.1 Decisions confirmed with the product owner

| # | Topic | Decision |
|---|---|---|
| D1 | Users | One analyst per installation. One local account, no roles, no multi-tenancy. |
| D2 | Target authorization | The analyst declares the authorized ranges when creating a project. No contract, letter or ticket reference is required. Scans are restricted to the declared ranges. |
| D3 | Port scanning | Selectable port profiles (top-100, top-1000, full 1–65535, custom list). TCP SYN (half-open) when raw-socket privileges are available, TCP connect as fallback. |
| D4 | Deployment | Docker Compose on a single Linux host with internet access. Online map tiles and automatic reference-data updates are acceptable. |
| D5 | Enrichment sources | MaxMind GeoLite2 (City and ASN, free edition) plus bulk registration data from the five RIRs, all queried locally. |
| D6 | Classification | Built-in versioned ruleset that outputs type, confidence and evidence. The analyst can override the result manually. Rules are not editable in the UI. |
| D7 | Change detection | Manual comparison of any two scans, plus an automatic diff after every scheduled run, surfaced as in-app alerts. No email or webhook. |
| D8 | Export | CSV and JSON only. No supported external API, no PDF report, no GeoJSON/KML in v1. |
| D9 | Traceroute | On demand, method selectable (ICMP, TCP, UDP), results persisted. |
| D10 | Technology stack | Fixed: Python / FastAPI (async), PostgreSQL with PostGIS, RabbitMQ, Vue 3 + Vuetify 3 + TypeScript, Docker Compose. |
| D11 | Scan engine | Native implementation only. No external scanner or diagnostic binaries (nmap, masscan, naabu, zmap, system `ping`/`traceroute`). |
| D12 | Future readiness | Agent-ready: the scanner runs as a separate worker, and every observation records the vantage point it was taken from. |

### 2.2 Assumptions made by the analyst

These were not discussed explicitly. Each is the default the specification is written against; the product owner can overturn any of them before design starts.

| # | Assumption |
|---|---|
| A1 | The 256-address limit applies to a single scan. A project's authorized scope may be larger (default ceiling 65,536 addresses per project, configurable) and is scanned in several scans. |
| A2 | Although there is one user, the application requires login. An unauthenticated instance that can launch scans is not acceptable. |
| A3 | Scan history is kept indefinitely. Data is removed only by explicit deletion of a scan or a project. |
!!!!| A4 | A host is identified by its IP address within a project. Data is never merged across projects. |
| A5 | The UI is English only and targets current desktop Chromium and Firefox. |
| A6 | Automatic diffs run for scheduled scans only. Manual scans are compared on demand. |
| A7 | Sizing envelope: up to 100 projects, 20 active schedules, 50 million host observations and 250 million open-port observations over the life of an installation. |
| A8 | Numeric defaults in this document (rates, timeouts, concurrency) are proposals to be tuned during implementation. The ceilings and the fact that they are configurable are requirements. |

## 3. Scope

### 3.1 In scope for version 1

- Projects with a declared authorized scope of public IPv4 addresses and ranges
- Manual and scheduled scans of up to 256 addresses each
- Host discovery by ICMP and TCP probes; open TCP port detection
- Offline enrichment: ASN, network operator, registered organization, country, city, coordinates, accuracy radius, reverse DNS
- Rule-based device classification with evidence and manual override
- Interactive map with clustering, filtering and live updates during a scan
- On-demand traceroute with hop enrichment and a path on the map
- Immutable scan history, scan comparison, automatic change alerts
- Interrupted-scan recovery, concurrent scans, configurable scan parameters
- CSV and JSON export, audit log

### 3.2 Out of scope for version 1

- Service, banner, version or operating-system identification; vulnerability detection
- UDP port scanning (UDP is used only as a traceroute method)
- IPv6
- Private, reserved or otherwise non-public address space
- Multiple users, roles, SSO, multi-tenancy
- Outbound notifications (email, webhook, chat)
- Supported public API and API tokens
- PDF/HTML reports, GeoJSON/KML export
- User-editable classification rules
- Offline or air-gapped operation (self-hosted map tiles, manual database import)
- Distributed scanning agents and graph topology visualization (section 10)

## 4. Glossary

| Term | Meaning |
|---|---|
| Project | An investigation. Owns a scope, scans, schedules, annotations and alerts. |
| Scope | The set of public IPv4 addresses and ranges the analyst has declared as authorized for a project. |
| Attestation | The analyst's recorded confirmation that they are authorized to scan a scope. |
| Scan | One execution of discovery and port probing against a target set of at most 256 addresses. |
| Target set | The addresses a scan probes. Always a subset of the project scope. |
| Observation | A fact recorded during a scan: a host responded, a port was open, a PTR record existed. |
| Evidence | The raw observation that supports a derived claim (reachability, classification, enrichment). |
| Enrichment | Registration and location data attached to an address from local reference datasets. |
| Vantage point | The location a scan or traceroute was executed from. Version 1 has exactly one. |
| Reference dataset | A versioned copy of GeoLite2 or RIR data held locally. |

## 5. Functional requirements

### 5.1 Access

- **FR-ACC-1 (M)** Every UI page and API endpoint except health checks requires an authenticated session.
- **FR-ACC-2 (M)** The single account is created during first-run setup. There is no self-registration.
- **FR-ACC-3 (M)** The analyst can change the password. Sessions expire after a configurable idle period.
- **FR-ACC-4 (S)** Repeated failed logins are throttled.

### 5.2 Projects and authorized scope

- **FR-PRJ-1 (M)** The analyst can create, rename, describe, archive and delete projects. An archived project is read-only and its schedules are disabled. Deleting a project requires explicit confirmation and removes all of its data.
- **FR-PRJ-2 (M)** A project has a scope made of entries, each a single IPv4 address, a CIDR block or a start–end range.
- **FR-PRJ-3 (M)** A scope entry is rejected if any address in it is non-public: RFC 1918 private space, loopback, link-local, CGNAT (100.64.0.0/10), multicast, documentation ranges, 0.0.0.0/8, 240.0.0.0/4 and broadcast. Entries that partly overlap such space are rejected as a whole.
- **FR-PRJ-4 (M)** Saving a new or changed scope requires the analyst to confirm that they are authorized to scan the listed ranges. The attestation is stored with the exact scope it covers and a timestamp. No supporting document is requested.
- **FR-PRJ-5 (M)** Scope changes take effect for future scans only. Historical data for removed ranges is kept.
- **FR-PRJ-6 (S)** The operator can configure a global never-scan list. Addresses on it are rejected in any project scope.
- **FR-PRJ-7 (M)** Scope enforcement fails closed. A target is probed only if it is inside the project scope at the moment of execution; the check is applied when a scan is created, when a scheduled run starts, and again by the scanner worker before it sends a probe to a host. If the scope cannot be verified, nothing is sent.

### 5.3 Scan configuration

- **FR-CFG-1 (M)** A scan's target set is chosen from the project scope and contains at most 256 unique addresses after expansion. A /24 counts as 256 addresses, including the .0 and .255 addresses.
- **FR-CFG-2 (M)** Host discovery methods are selectable: ICMP echo and TCP probes to a configurable list of ports. At least one method must be enabled, unless discovery is skipped (FR-CFG-3).
- **FR-CFG-3 (M)** The analyst can choose to port-scan only hosts that answered discovery (default) or all targets regardless of discovery result.
- **FR-CFG-4 (M)** Port profiles: top-100, top-1000, full 1–65535, and a custom list or ranges entered by the analyst.
- **FR-CFG-5 (M)** Probe technique: automatic (SYN if privileges allow, otherwise connect), forced SYN, or forced connect. The technique actually used is recorded on the scan.
- **FR-CFG-6 (M)** Tunable parameters with defaults and enforced ceilings: probe timeout, retries, packets per second for the scan, number of hosts probed in parallel.
- **FR-CFG-7 (M)** Operator-level ceilings (environment configuration, not editable in the UI): global packets per second across all scans, maximum concurrent scans, maximum concurrent traceroutes, minimum schedule interval.
- **FR-CFG-8 (S)** Parameter sets can be saved as named scan profiles and reused in manual scans and schedules.
- **FR-CFG-9 (S)** Before launch, the UI shows the number of probes the configuration implies and an estimated duration.

### 5.4 Scan execution

- **FR-SCN-1 (M)** A host is reachable if it produces any of: an ICMP echo reply, a SYN/ACK or RST to a discovery probe, or an open port during the port scan. The probes that established reachability are stored as evidence with timestamp and round-trip time.
- **FR-SCN-2 (M)** For each probed host the system records every open port individually, and the count of closed and of unanswered (filtered) ports.
- **FR-SCN-3 (M)** No service identification. The scanner sends no application-layer payload and reads no application data. A connect-mode probe closes the connection as soon as it is established. The UI may display the IANA-registered service name for a port number, labelled as the nominal assignment and not as a detected service.
- **FR-SCN-4 (M)** Scan states: queued, running, completed, cancelled, failed, interrupted. A running scan exposes its current phase (discovery, port scan, enrichment, classification, diff), progress and probe counters.
- **FR-SCN-5 (M)** The analyst can cancel a queued or running scan. Results collected so far are kept and the scan is marked partial.
- **FR-SCN-6 (M)** Several scans can run concurrently up to the configured maximum; further scans wait in the queue. The global rate ceiling is shared across all running scans.
- **FR-SCN-7 (M)** Interrupted-scan recovery: after a crash or restart of any component, scans that were running are detected and resumed automatically from the last checkpoint. Work completed before the interruption is not repeated beyond one checkpoint unit, and resumption produces no duplicate observations.
- **FR-SCN-8 (M)** A scan stores the resolved target list, the full parameter set, the scope attestation it was validated against, the vantage point, the reference-dataset versions used, and start and end times.
- **FR-SCN-9 (M)** A completed scan is immutable. Annotations (section 5.7) are stored separately and do not alter scan data.
- **FR-SCN-10 (S)** Target and port order are randomized within a scan.

### 5.5 Scheduled scans

- **FR-SCH-1 (M)** A schedule belongs to a project and combines a target set, scan parameters and a recurrence expressed as an interval or a cron expression with a time zone.
- **FR-SCH-2 (M)** Schedules can be enabled, disabled, edited and deleted. The UI shows the next planned run and the outcome of the last run.
- **FR-SCH-3 (M)** If the previous run of a schedule is still active when the next one is due, the new run is skipped and the skip is recorded.
- **FR-SCH-4 (M)** After downtime, a schedule runs at most once to catch up; missed runs are not replayed.
- **FR-SCH-5 (M)** If a schedule's targets are no longer fully inside the project scope, the run is rejected, the schedule is flagged, and an alert is raised.
- **FR-SCH-6 (M)** Intervals shorter than the configured minimum (proposed default: 1 hour) are rejected.

### 5.6 Enrichment

- **FR-ENR-1 (M)** Each discovered host is enriched with: ASN, AS holder name, registered network name and organization, registry (RIR), registration country, and geolocation (country, city, latitude, longitude, accuracy radius).
- **FR-ENR-2 (M)** Enrichment uses only local reference datasets. No per-address query is sent to a third-party enrichment service.
- **FR-ENR-3 (M)** The "ISP" shown to the analyst is the AS holder name. GeoLite2 has no dedicated ISP field; that field exists only in the paid GeoIP2 ISP database. "Organization" is taken from the most specific RIR registration covering the address where available, and falls back to the AS holder.
- **FR-ENR-4 (M)** Reverse DNS (PTR) is looked up for each discovered host through a configurable resolver and stored with the scan. This is the one enrichment step that sends target addresses to an external system, and the documentation must say so.
- **FR-ENR-5 (M)** Enrichment values are stored as a snapshot with each scan, together with the dataset versions, so that past scans remain reproducible after datasets are updated. Enrichment values belongs to the host they describe and are not shared across hosts.Each scan saves a separate snapshot of enrichment data for all its discovered hosts with version and timestamps.
- **FR-ENR-6 (M)** Reference datasets update automatically on a schedule, and the analyst can see the version and age of each one. The MaxMind license key is supplied by the operator; the databases are not bundled with the application.
- **FR-ENR-7 (M)** A missing or stale dataset does not block scanning. Affected fields are marked unavailable, and the analyst can re-run enrichment for a scan later; the re-run is stored as an additional snapshot, not as a replacement.
- **FR-ENR-8 (M)** The UI presents location as approximate everywhere it appears and shows the accuracy radius.

### 5.7 Device classification and annotations

- **FR-CLS-1 (M)** Each discovered host is classified into one of: router, gateway/firewall/VPN endpoint, DNS server, IP camera/DVR, server (with a sub-role where the evidence supports it: web, mail, database, remote access, file), or unknown. The taxonomy is extensible without data migration.
- **FR-CLS-2 (M)** Rule inputs are limited to: the set of open ports, reverse DNS names, enrichment attributes, and whether the address has been observed as an intermediate traceroute hop.
- **FR-CLS-3 (M)** The result contains the device type, a confidence score, the rules that fired with the evidence each one matched, and the ruleset version. Below a configurable confidence threshold the type is unknown.
- **FR-CLS-4 (M)** The ruleset is versioned and shipped with the application. The classification stored with a scan is not changed when the ruleset is upgraded.
- **FR-CLS-5 (M)** The analyst can override the device type for a host. The override is stored separately from the automatic result, applies to that address across all scans in the project until cleared, and both values remain visible.
- **FR-CLS-6 (M)** The analyst can attach tags and free-text notes to a host within a project.
- **FR-CLS-7 (S)** The analyst can re-run classification of a past scan with the current ruleset; the result is stored alongside the original.

### 5.8 Map and host views

- **FR-MAP-1 (M)** Discovered hosts with coordinates are shown on an interactive map with pan and zoom and automatic clustering.
- **FR-MAP-2 (M)** Hosts sharing identical coordinates must remain individually accessible (list or fan-out on the cluster). City-level geolocation places most addresses of one range on the same point, so this is the normal case, not an edge case.
- **FR-MAP-3 (M)** Hosts without coordinates are listed in a separate "unlocated" panel and are never dropped silently.
- **FR-MAP-4 (M)** The map shows a selected scan, by default the most recent one in the project.
- **FR-MAP-5 (S)** The map can show the latest known state of the whole project: the most recent observation of every address across scans.
- **FR-MAP-6 (M)** Filters: device type, open port, ASN, organization, country, reverse-DNS text, tag, and change status relative to the previous scan. Filters apply to the map and the host table together.
- **FR-MAP-7 (M)** While a scan runs, new hosts, new open ports and progress appear without a page reload, within 2 seconds of being recorded.
- **FR-MAP-8 (M)** A host table shows the same data as the map with sorting and the same filters.
- **FR-MAP-9 (M)** Selecting a host opens a detail view: reachability evidence, open ports, enrichment with dataset versions, reverse DNS, classification with evidence, override, tags, notes, traceroute history, and a timeline of the address across scans (first seen, last seen, port changes).
- **FR-MAP-10 (M)** Selecting a host draws its accuracy radius on the map.
- **FR-MAP-11 (M)** Map tiles come from a configurable online tile source, with the attribution the provider requires.

### 5.9 Traceroute

- **FR-TRC-1 (M)** From the host detail view the analyst can run a traceroute to that address. The target must be inside the project scope.
- **FR-TRC-2 (M)** Methods: ICMP (default), TCP to a chosen port (defaulting to an open port of the host), and UDP. Parameters: maximum hops, probes per hop, timeout.
- **FR-TRC-3 (M)** For each hop the result records the TTL, every responding address (several are possible on load-balanced paths), per-probe round-trip times, and loss. Hops that do not answer are recorded as such.
- **FR-TRC-4 (M)** Hop addresses are enriched like hosts (section 5.6, including reverse DNS). Private and CGNAT hops are shown and marked as unlocatable.
!!!!- **FR-TRC-5 (M)** Intermediate hops are observed only through their replies to the traceroute. They are never port-scanned or probed directly unless they are themselves in the project scope and part of a scan.
- **FR-TRC-6 (M)** Hops appear in the UI as they are discovered. The path is drawn on the map through the hops that have coordinates, and segments that skip unlocated or silent hops are visually distinct.
- **FR-TRC-7 (M)** Every traceroute is stored with its method, parameters, vantage point and timestamp, and is listed in the host's history.
- **FR-TRC-8 (C)** Two traceroutes to the same address can be compared side by side.

### 5.10 History, comparison and alerts

- **FR-HIS-1 (M)** All scans of a project are listed with status, time, target set, parameters and summary counts.
- **FR-HIS-2 (M)** Any two scans of the same project can be compared. The diff covers addresses targeted by both scans; addresses targeted by only one are reported as not comparable.
- **FR-HIS-3 (M)** The diff reports: hosts that appeared, hosts that disappeared, ports opened, ports closed, and changes in reverse DNS, classification and enrichment.
- **FR-HIS-4 (M)** A port is reported as opened or closed only if it was probed in both scans. When the port profiles differ, the diff states which ports were compared.
- **FR-HIS-5 (M)** An enrichment change that coincides with a reference-dataset version change is labelled as such, so that database updates are not mistaken for network changes.
- **FR-HIS-6 (M)** After each completed scheduled run, a diff against the previous completed run of the same schedule is computed automatically. Cancelled, failed and partial runs are not used as a baseline.
- **FR-HIS-7 (M)** If the automatic diff contains changes, an in-app alert is raised with a summary and a link to the diff. Alerts are also raised for failed, skipped and rejected scheduled runs.
- **FR-HIS-8 (M)** The application has an alert list with read/unread state and an unread indicator visible from every page.
- **FR-HIS-9 (M)** Individual scans can be deleted with confirmation. Deletion is recorded in the audit log.

### 5.11 Export

- **FR-EXP-1 (M)** Scan results (hosts, open ports, enrichment, reverse DNS, classification, override, tags) can be exported as CSV and as JSON, honouring the active filters.
- **FR-EXP-2 (M)** Scan diffs can be exported as CSV and as JSON.
- **FR-EXP-3 (S)** Traceroute results can be exported as CSV and as JSON.
- **FR-EXP-4 (M)** Each export carries the scan identifier, scan time, vantage point and dataset versions.

### 5.12 Audit log

- **FR-AUD-1 (M)** An append-only audit log records: login and failed login, project creation, archival and deletion, every scope change with its attestation, scan launch, cancellation and deletion, schedule creation and change, traceroute execution, classification override, reference-dataset updates, and any scan or run rejected by a scope check.
- **FR-AUD-2 (M)** Each entry has a UTC timestamp, the actor (the account, or the system for scheduled actions), the action, and the affected object.
- **FR-AUD-3 (M)** The audit log is viewable and filterable in the UI and cannot be edited or deleted through the application.

## 6. Information model

Conceptual entities the design must be able to represent. This is not a schema.

| Entity | Notes |
|---|---|
| Account | The single analyst. |
| Project | Name, description, state (active, archived). |
| Scope entry | Address, CIDR or range belonging to a project. |
| Attestation | Scope snapshot, timestamp, account. |
| Scan profile | Named parameter set. |
| Schedule | Target set, parameters, recurrence, state. |
| Vantage point | Name, public egress address, location. One in v1. |
| Scan | Target list, parameters, state, phase, timings, vantage point, attestation, dataset versions, originating schedule if any. |
| Host observation | An address within a scan: reachable or not, with evidence. |
| Port observation | An open port of a host observation. Closed and filtered ports are counts on the host observation. |
| Enrichment snapshot | Enrichment values and dataset versions for a host observation or a traceroute hop. |
| Classification result | Type, confidence, fired rules with evidence, ruleset version. |
| Host annotation | Per project and address: type override, tags, notes. |
| Traceroute run and hops | Method, parameters, vantage point; per hop TTL, responders, RTTs, loss. |
| Scan diff and change events | The comparison of two scans and its individual changes. |
| Alert | Type, summary, link, read state. |
| Reference dataset | Source, version, load time, status. |
| Audit event | See section 5.12. |

All timestamps are stored in UTC and displayed in the analyst's local time zone.

## 7. Non-functional requirements

### 7.1 Security

- **NFR-SEC-1** Scope enforcement and authentication fail closed (FR-PRJ-7, FR-ACC-1).
- **NFR-SEC-2** The scanner worker is the only component holding raw-socket capability. It runs as a non-root user with that capability alone. The web/API component has no raw-socket capability.
- **NFR-SEC-3** The password is stored with a slow hash (argon2). Secrets (database and broker credentials, MaxMind license key) come from the environment and never appear in source, images or logs.
- **NFR-SEC-4** The application is served over TLS, terminated at a reverse proxy included in the deployment.
- **NFR-SEC-5** All input is validated at the API boundary. Address and range parsing is strict; malformed or ambiguous input is rejected, never normalized silently.
- **NFR-SEC-6** If a configuration switch exists to permit non-public targets for automated testing, it is settable only through the environment, is off by default, cannot be changed from the UI, and its use is written to the log and the audit log at startup.

### 7.2 Performance and capacity

- **NFR-PRF-1** Probe volume the engine must handle for one scan of 256 hosts, before retries: 25,600 probes (top-100), 256,000 (top-1000), 16,776,960 (full range). At 1,000 packets per second these take about 26 seconds, 4.3 minutes and 4.7 hours; at 5,000 packets per second about 5 seconds, 51 seconds and 56 minutes.
- **NFR-PRF-2** Proposed defaults: 1,000 packets per second per scan, 5,000 globally, 3 concurrent scans, 5 concurrent traceroutes. The engine must sustain the configured rate without dropping its own replies.
- **NFR-PRF-3** Scan accuracy: against a controlled set of lab targets with known open ports and no packet loss, the scan reports every open port and no false open ports.
- **NFR-PRF-4** The map stays interactive with 10,000 hosts loaded. Host table, filter and scan-list queries return within 1 second at the sizing envelope of assumption A7.
- **NFR-PRF-5** Comparing two scans completes within 5 seconds.

### 7.3 Reliability

- **NFR-REL-1** Scan job delivery is at-least-once and result recording is idempotent, so redelivery and resumption are safe.
- **NFR-REL-2** An interrupted scan resumes within 2 minutes of the services being available again, without analyst action.
- **NFR-REL-3** A failure in enrichment, classification or diff does not lose probe results. The failed phase can be retried on its own.
- **NFR-REL-4** A message that repeatedly fails processing is set aside after a bounded number of retries and reported as a failed scan or run; it does not loop indefinitely.

### 7.4 Operability

- **NFR-OPS-1** Deployment is one Docker Compose stack with pinned image versions, health checks and resource limits. Configuration comes from the environment.
- **NFR-OPS-2** Logs are structured JSON with a correlation identifier per scan and per traceroute.
- **NFR-OPS-3** Metrics are exposed for probe rate, queue depth, scan duration, scan outcomes and reference-dataset age.
- **NFR-OPS-4** Database schema changes are versioned and reversible.
- **NFR-OPS-5** The documentation describes backup and restore of the database.

### 7.5 Compliance and licensing

- **NFR-LIC-1** GeoLite2 use must follow MaxMind's licence: the operator's own licence key, attribution in the UI, timely updates, and no redistribution of the databases with the application.
- **NFR-LIC-2** Every bundled data file (top-port lists, IANA port names, classification rules) must have a licence that permits its inclusion.
- **NFR-LIC-3** Map tile usage must comply with the chosen provider's usage policy.

### 7.6 Testability

- **NFR-TST-1** Integration tests run against real PostgreSQL and RabbitMQ instances.
- **NFR-TST-2** The scan engine and traceroute are tested end to end against lab targets in an isolated network (see NFR-SEC-6), covering both SYN and connect techniques and recovery after a forced worker restart.

## 8. Constraints

- **C-1** Technology stack as in decision D10. Any deviation, such as a different language for the scanner worker, needs the product owner's agreement.
- **C-2** No external scanner or network-diagnostic binaries (D11). Probing, host discovery and traceroute are implemented in the application's own code. Use of libraries is the architect's decision.
- **C-3** The scanner is a separate worker that receives work and returns results through the message broker. It has no direct dependency on the web/API process, so further workers in other locations can be added later.
- **C-4** Every scan, host observation and traceroute references a vantage point (D12).
- **C-5** IPv4 only. The data model should not make a later IPv6 extension needlessly hard, but no IPv6 behaviour is required.

## 9. Suggested delivery phases

A proposal for incremental delivery. The architect may re-cut it.

| Phase | Content |
|---|---|
| 1 | Login, projects, scope with attestation and fail-closed enforcement, audit log, scanner worker with discovery and port scan, checkpointing and recovery, manual scans, host table. |
| 2 | Reference datasets and enrichment, reverse DNS, map with clustering, filters and live updates. |
| 3 | Classification with override and annotations, traceroute with path on map. |
| 4 | Schedules, scan comparison, automatic diff and alerts, CSV/JSON export. |

## 10. Future versions

Not part of version 1; listed so the design does not block them.

- **Distributed scanning agents.** Workers in several geographic locations, each a vantage point. The same target scanned from several vantage points; results comparable per vantage point. Implies agent registration, authentication and scope enforcement at the agent.
- **Graph topology.** A graph built from stored traceroutes: addresses and ASNs as nodes, observed adjacency as edges, with evolution over time. Version 1 already stores the hop-level data this needs.

## 11. Open issues and risks for the architect

| # | Issue | Why it matters |
|---|---|---|
| R1 | Raw-socket probing in Python at 1,000–5,000 packets per second | The stack is fixed to Python and external scanners are excluded. Whether asynchronous raw-socket code reaches the required rate with accurate reply matching must be validated early. If it cannot, a native-language worker is a stack deviation under C-1. |
| R2 | Container networking for SYN scans | Half-open scanning from a bridged, NATed container interacts with connection tracking and with the kernel resetting unexpected SYN/ACKs. Host networking may be needed, which affects isolation (NFR-SEC-2). |
| R3 | Uneven RIR bulk data | RIPE, APNIC and AFRINIC publish registration objects in bulk. ARIN and LACNIC provide detailed bulk data only under an access agreement. All five publish delegation statistics (prefix, country, date, status) without organization names. Organization detail for ARIN and LACNIC space will fall back to the AS holder unless the operator obtains bulk access. The exact terms should be verified. |
| R4 | Source of the top-100 and top-1000 port lists | The best-known frequency data ships with nmap under its own licence. A licence-compatible source or an own curated list is required (NFR-LIC-2). |
| R5 | Classification quality without service identification | Rules see only port numbers, reverse DNS and registration data. DNS servers are inferred from TCP port 53 only, since UDP is not scanned. Expect a large "unknown" share; confidence and evidence must be shown honestly. |
| R6 | Tile provider | Public OpenStreetMap tile servers have a restrictive usage policy. A provider and a configuration approach must be chosen. |
| R7 | Abuse exposure | Scans originate from the host's own public address. Rate ceilings, the never-scan list and the audit log are the mitigations; operators should expect occasional abuse reports even for authorized targets. |
| R8 | Checkpoint granularity | FR-SCN-7 requires bounded rework after interruption. The unit (per host, per port batch) trades recovery precision against write volume, most visibly for full-range scans. |
| R9 | Live update transport | Server-sent events or WebSocket for FR-MAP-7 and FR-TRC-6. |
| R10 | Reverse DNS exposure | PTR lookups reveal target addresses to the resolver and to authoritative servers. Resolver choice and an option to disable the lookup should be considered. |

## 12. Acceptance criteria for version 1

1. A scan cannot be started, manually or by schedule, against any address outside the project scope or outside public IPv4 space, and every rejected attempt appears in the audit log.
2. A scan of a /24 with the top-1000 profile against lab targets reports exactly the known open ports, using SYN where privileged and connect otherwise, and the technique used is recorded.
3. Packet captures of a scan show no application-layer payload sent to any target.
4. Killing the scanner worker or restarting the whole stack during a scan results in the scan completing automatically with no duplicate and no missing observations.
5. Each discovered host shows ASN, AS holder, organization where available, country, city, coordinates and accuracy radius, with the dataset versions used, and no enrichment request leaves the host other than reverse DNS.
6. Each classification shows its type, confidence and the evidence behind it; a manual override persists across scans and the automatic result stays visible.
7. During a running scan, hosts appear on the map within 2 seconds of discovery; co-located hosts are individually selectable; unlocated hosts are listed.
8. A traceroute by each of the three methods shows hops as they arrive, draws the path on the map, and is retrievable from the host's history.
9. A scheduled scan runs at its planned time, is skipped when the previous run is still active, and raises an in-app alert when its automatic diff contains changes.
10. Comparing two scans with different port profiles reports port changes only for ports probed in both.
11. Scan results and diffs export to CSV and JSON with scan metadata included.
