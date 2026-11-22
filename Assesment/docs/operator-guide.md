# Network Discovery Map — Operator Guide

This guide is for the person who installs and runs Network Discovery Map on a host. It covers the product as of Phase 3: login over TLS, projects with attested scopes, the audit log, manual scans, the enrichment of discovered hosts with registration and location data shown on a map, traceroutes, and the classification of hosts by device type. Schedules, change history and alerts arrive in later phases, and this guide grows with each phase.

## 1. What you run

One Docker Compose stack on one Linux host.

| Service | What it does | Reachable from |
| --- | --- | --- |
| `proxy` | Caddy. Terminates TLS, serves the web application, passes `/api` to the API. | The network, on port 443 |
| `api` | The application server. Validates requests; never probes anything. | The proxy only |
| `orchestrator` | Runs every scan from queue to completion, records and enriches the results, and keeps the reference datasets up to date. The only service besides the proxy with a way to the outside: for dataset downloads and reverse DNS (section 11). | Nothing; it only connects out |
| `scanner` | The only service that sends packets to targets. Runs on the host network. | Nothing; it has no listening port |
| `postgres` | PostgreSQL with PostGIS. Holds all data. | The API, the orchestrator and the migration job only |
| `rabbitmq` | The message broker between the three services above. | The internal network, and the host's loopback address |
| `migrate` | Applies database migrations, then exits. Runs on every start. | — |
| `scanner-netinit` | Installs two firewall rules on the host, then exits (section 4). | — |

Port 443 is the only port published to the outside. RabbitMQ is published on `127.0.0.1` only, because the scanner runs on the host network and reaches it there.

Two volumes hold state besides the database: `caddy-state` (section 6) and `reference-data`, the downloaded datasets, which only the orchestrator mounts.

## 2. Requirements

- A Linux host with Docker Engine and the Compose plugin (`docker compose version` must work). A desktop Docker installation is not suitable: the scanner needs the real host network.
- Port 443 free on the host, and port 5672 free on its loopback address.
- Disk for the database: it grows with the scans kept. At the sizing envelope (100 projects, 50 million host observations, 250 million open ports) plan about 80 GB, plus as much again free for a backup (section 16). A /24 whose hosts answer on every port, scanned hourly with the top-1000 profile, adds about 0.6 GB a day on its own (`docs/release/capacity-report.md`).
- The host preparation of section 4.
- Authorization to scan the ranges you will enter. Scans leave from this host's own public address.
- Optional, for enrichment and the map (section 11): a MaxMind account with a licence key, a tile provider, and outgoing HTTPS from the host to the dataset sources.

## 3. Install

All commands run in `Assesment/deploy`.

1. Create the environment file.

   ```sh
   cp .env.example .env
   chmod 600 .env
   ```

2. Generate three passwords and put them into `.env`.

   ```sh
   openssl rand -hex 24   # NDM_DB_OWNER_PASSWORD
   openssl rand -hex 24   # NDM_DB_APP_PASSWORD
   openssl rand -hex 24   # NDM_BROKER_PASSWORD
   ```

   Use letters, digits and `-`, `_`, `.`, `~` only. The passwords are placed inside connection URLs, and other characters break them.

3. Set `NDM_SITE_ADDRESS` to the host name or IP address the analyst will type into the browser. The certificate is issued for exactly this name.

4. Prepare the host (section 4).

5. Start the stack.

   ```sh
   docker compose up -d --build
   docker compose ps -a
   ```

   Every service must show `healthy`, except `migrate` and `scanner-netinit`, which show `exited (0)`.

   `--build` builds the images of the release this checkout belongs to (`release/VERSIONS.md`). If you were given the release images instead, as the archive the release workflow produces, load them and start without building:

   ```sh
   gunzip -c ndm-images-1.0.0-rc.1.tar.gz | docker load
   docker compose up -d
   ```

   `release/build-images.sh` builds the same images from a checkout of the release tag and records the digest of every image in `release/digests-<release>.txt`.

Set the passwords before the first start. PostgreSQL and RabbitMQ create their users once, when their data volumes are first initialized; changing a password in `.env` afterwards does not change it in the service (see section 8 for the database).

## 4. Host preparation

Three things on the host, once. The scanner checks the first two when it starts and writes what it finds to its log (`docker compose logs scanner | grep host_preparation`).

**1. Receive buffer limit.** The scanner enlarges its receive buffer so that a burst of replies is not dropped. The kernel allows that only up to `net.core.rmem_max`. Raise it to at least 8 MiB:

```sh
echo 'net.core.rmem_max = 16777216' | sudo tee /etc/sysctl.d/90-ndm.conf
sudo sysctl --system
```

If the limit is lower, scans still run, and the scanner logs a warning. Replies that the kernel dropped are counted and shown on the scan as a warning.

**2. Source-port range.** SYN probes leave from a fixed range of source ports, 61000–61999 by default (`NDM_SOURCE_PORT_RANGE`). This range must lie outside the range the kernel uses for ordinary outgoing connections:

```sh
cat /proc/sys/net/ipv4/ip_local_port_range     # usually: 32768 60999
```

If the two ranges overlap, the scanner switches SYN scanning off and scans with ordinary connections instead; the launch dialog then shows "SYN not available". Either choose another `NDM_SOURCE_PORT_RANGE` or narrow the kernel's range.

**3. Connection-tracking bypass.** Docker makes the kernel track connections, and it then also tracks every SYN probe. Unanswered probes stay in that table for about two minutes; at 5,000 packets per second it fills up, and a full table disturbs all networking on the host. Two firewall rules exempt the scanner's probes. The `scanner-netinit` service installs them at every start and then exits. It needs the `NET_ADMIN` capability on the host network for that.

To apply the rules yourself instead, set `NDM_NETINIT_SKIP=true` in `.env` and run, as root, at every boot:

```sh
iptables -t raw -A OUTPUT     -p tcp --sport 61000:61999 -m comment --comment ndm-scanner-notrack -j CT --notrack
iptables -t raw -A PREROUTING -p tcp --dport 61000:61999 -m comment --comment ndm-scanner-notrack -j CT --notrack
```

If the host uses the legacy iptables back end (`iptables --version` prints `(legacy)`), set `NDM_IPTABLES=iptables-legacy`. More detail, and how to remove the rules, is in `scanner-netinit/README.md`.

## 5. First-run setup

A new installation has no account. Until one exists, nobody can log in, and the application shows the setup page.

1. Read the one-time setup token from the API log.

   ```sh
   docker compose logs api | grep setup_required
   ```

   The line holds a `setup_token` value.

2. Open `https://<NDM_SITE_ADDRESS>/` and accept or install the certificate (section 6).
3. Enter the token, a username and a password of at least 12 characters.

The token proves that the person creating the account can read the server's log. It works once. If the API restarts before setup is done, it prints a new token and the old one stops working. After setup, the setup page is closed for good.

There is one account per installation. There is no password reset: keep the password safe. The analyst changes it under **Password** in the application; doing so ends every other session of the account.

## 6. The TLS certificate

The proxy issues its certificate from its own certificate authority, so browsers warn on the first visit. To remove the warning, install the authority's root certificate on the analyst's workstation.

1. Copy the root certificate out of the proxy.

   ```sh
   docker compose cp proxy:/var/lib/caddy/data/caddy/pki/authorities/local/root.crt ./ndm-root.crt
   ```

2. Import `ndm-root.crt` as a trusted authority into the browser or the operating system of the workstation.

The authority lives in the `caddy-state` volume. Keep that volume: if it is removed, the proxy creates a new authority and every workstation must import the new root certificate.

## 7. Configuration

All configuration is in `deploy/.env`. After a change, run `docker compose up -d`.

### Secrets and address

| Variable | Default | Meaning |
| --- | --- | --- |
| `NDM_DB_OWNER_PASSWORD` | none, required | Password of the database role that owns the schema and runs migrations. |
| `NDM_DB_APP_PASSWORD` | none, required | Password of the database role the API and the orchestrator use. |
| `NDM_BROKER_PASSWORD` | none, required | Password of the message broker. |
| `NDM_DB_OWNER` | `ndm_owner` | Name of the owning role. Takes effect only when the data volume is first initialized. |
| `NDM_DB_APP_ROLE` | `ndm_app` | Name of the application role. Takes effect only when the data volume is first initialized. |
| `NDM_SITE_ADDRESS` | `localhost` | Host name or IP address of the application. |

### Access and scope

| Variable | Default | Meaning |
| --- | --- | --- |
| `NDM_SESSION_IDLE_TIMEOUT_SECONDS` | `1800` | A session ends after this many seconds without a request. |
| `NDM_PROJECT_SCOPE_CEILING` | `65536` | Largest number of addresses one project scope may hold. |
| `NDM_NEVER_SCAN` | empty | Addresses that are never scanned. Comma-separated single addresses, CIDR blocks or `start-end` ranges. |

### Scanning: operator ceilings

These cannot be changed in the application.

| Variable | Default | Meaning |
| --- | --- | --- |
| `NDM_GLOBAL_RATE_PPS` | `5000` | Packets per second across all running scans. Also the highest rate one scan may ask for. |
| `NDM_MAX_CONCURRENT_SCANS` | `3` | Scans that run at the same time. Further scans wait in the queue. |
| `NDM_MAX_CONCURRENT_TRACEROUTES` | `5` | Traceroutes that run at the same time, at most 50. Further ones wait. Traceroutes do not wait for scans. |
| `NDM_MIN_SCHEDULE_INTERVAL_SECONDS` | `3600` | Shortest time between two runs of a schedule, at least 60. A schedule whose interval, or whose cron expression at any of its coming runs, is shorter is refused (section 13). |

### Scanner

| Variable | Default | Meaning |
| --- | --- | --- |
| `NDM_VANTAGE_POINT` | `default` | Name of the place scans are made from. Lower-case letters, digits, `-` and `_`. |
| `NDM_EGRESS_ADDRESS` | unset | The public address probes leave from. Unset, the scanner uses the address the host routes from. |
| `NDM_SOURCE_PORT_RANGE` | `61000-61999` | Source ports of SYN probes, as `low-high` (section 4). |
| `NDM_NETINIT_SKIP` | `false` | `true` if you apply the two firewall rules yourself (section 4). |
| `NDM_IPTABLES` | `iptables` | `iptables-legacy` on a host with the legacy back end. |

### Enrichment: reference datasets

All optional. Without them, scans run as before and the fields the datasets would fill are shown as unavailable. Section 11 explains each.

| Variable | Default | Meaning |
| --- | --- | --- |
| `NDM_MAXMIND_ACCOUNT_ID` | unset | Your MaxMind account ID. Set it together with the key. |
| `NDM_MAXMIND_LICENSE_KEY` | unset | Your MaxMind licence key. A secret: it is kept out of every log line and every response. |
| `NDM_GEOLITE_UPDATE_INTERVAL_HOURS` | `24` | Hours between checks for a new GeoLite2 release. |
| `NDM_RIR_UPDATE_INTERVAL_HOURS` | `168` | Hours between downloads of the registries' data. |
| `NDM_RIR_OBJECT_REGISTRIES` | empty | Registries whose registration records are downloaded: any of `ripe`, `apnic`, `afrinic`, comma-separated. List one only if your use meets its terms (section 11). |
| `NDM_RIR_FILES_DIR` | `./registry-files` | Directory on the host with bulk files you obtained from ARIN (`arin_db.xml`) and LACNIC (`lacnic_db.txt`). Mounted read-only into the orchestrator. |
| `NDM_PTR_ENABLED` | `true` | `false` switches reverse DNS off: no DNS query is sent. |
| `NDM_PTR_RESOLVER` | unset | The resolver for reverse DNS, as `address` or `address:port`. Unset: the host's resolver. |

### Classification

| Variable | Default | Meaning |
| --- | --- | --- |
| `NDM_CLASSIFICATION_THRESHOLD` | `0.5` | Confidence from 0 to 1 below which a host's device type is `unknown` (section 12). |

### Map

| Variable | Default | Meaning |
| --- | --- | --- |
| `NDM_TILE_URL_TEMPLATE` | unset | Raster tile URL with `{z}`, `{x}` and `{y}`. There is no default. Unset, the map is switched off. |
| `NDM_TILE_ATTRIBUTION` | unset | The attribution the tile provider requires. Required with a tile URL; shown on the map. |

### Other

| Variable | Default | Meaning |
| --- | --- | --- |
| `NDM_LOG_LEVEL` | `INFO` | Log level of every service. |
| `NDM_ALLOW_NON_PUBLIC_TARGETS` | `false` | Test switch. See below. |
| `NDM_VERSION` | the release of the checkout | Which release of the project's images Compose runs (`release/VERSIONS.md`). Leave unset. |
| `NDM_REVISION` | `unknown` | The commit Compose labels the images with when it builds them. `release/build-images.sh` sets it. |

Compose builds further values from these and passes them to the containers; you do not set them yourself: `NDM_DATABASE_URL`, `NDM_MIGRATION_DATABASE_URL`, `NDM_APP_DB_ROLE`, `NDM_BROKER_URL`, and for the orchestrator `NDM_REFERENCE_DATA_PATH` and `NDM_RIR_FILES_PATH`.

**Never-scan list.** Entries are written strictly: no spaces inside an entry, no leading zeros, and a CIDR block must not have host bits set (`198.52.0.0/24`, not `198.52.0.1/24`). If an entry is not valid, the API does not start and its log names the entry. The list is checked in three places, each with its own copy: by the API when a scope is saved and when a scan is created, by the orchestrator before each part of a scan is sent out, and by the scanner before it sends anything.

**Test switch.** `NDM_ALLOW_NON_PUBLIC_TARGETS=true` lets private and other non-public addresses be scanned. It exists for the test lab. Leave it unset on every real installation. It can be set only here, never in the application. It is passed to the API, the orchestrator and the scanner, and it has an effect only when all three have it. Each start with the switch on writes entries to the logs and to the audit log.

## 8. The two database roles

| Role | Used by | May do |
| --- | --- | --- |
| Owner (`ndm_owner`) | `migrate` | Create and change the schema. |
| Application (`ndm_app`) | `api`, `orchestrator` | Read and write data. On the audit log: read and add entries only. |

The scanner has no database access at all.

The audit log cannot be changed through the application. The application role has no right to update, delete or truncate it, and a database trigger rejects those statements from every role, the owner included.

The application role is created by `deploy/postgres/init-roles.sh`, which PostgreSQL runs once, when the data volume is first initialized. To change a password later, change it in the database and in `.env`, then restart:

```sh
docker compose exec postgres psql -U ndm_owner -d ndm -c "ALTER ROLE ndm_app PASSWORD 'new-password'"
# put the same value into .env as NDM_DB_APP_PASSWORD, then:
docker compose up -d
```

## 9. Sessions and login

- A session ends after the idle timeout, at logout, or when the password is changed from another session.
- After 5 failed logins within 5 minutes, further logins are refused until the oldest of them is 5 minutes old. This applies to the installation as a whole, so someone who can reach the login page can lock the analyst out for a while. Limit who can reach port 443.
- Every login and every failed login is in the audit log.

## 10. Scans

- **Where a scan is checked.** A target is probed only if it is inside the project scope, is a public address and is not on the never-scan list. This is checked when the scan is created, again before each part of it is sent to the scanner, and a third time by the scanner itself. A scan that fails a check stops; what it had found is kept, and the rejection is in the audit log.
- **A scope that changes during a scan.** If a target of a running scan is no longer in the project scope, the scan stops and is marked failed with the reason `scope_changed`.
- **After a restart.** A scan that was running when a service or the whole stack stopped continues by itself when the services are back. Until the scanner is heard from again, the scan shows as `interrupted`.
- **Deleting.** A deleted scan or project disappears at once. Its rows are removed in the background within about a minute; audit entries stay.
- **SYN and connect.** With its raw-socket capability and a prepared host (section 4), the scanner sends SYN probes. Without them it opens ordinary connections and closes each as soon as it is established. Neither sends any data to a target. The technique used is shown on each scan.
- **Discovery without the raw-socket capability.** Without it the scanner cannot send ICMP echo requests either, so hosts are discovered by the TCP probes alone. A host that answers ping but drops the discovery ports is then not found, and its ports are not scanned. Such a scan shows a warning, and its export carries `icmp_discovery_unavailable`. To include those hosts, scan all targets regardless of discovery, or give the scanner its capability.

## 11. Enrichment and the map

Each host a scan finds is looked up in reference datasets kept on this installation: its ASN and AS holder, the registry and registration country of its address, the network name and organization it is registered to, and an approximate location. What a host was found to be is stored with the scan, with the dataset versions used, and is never changed afterwards; a later scan of the same host makes its own snapshot. **Datasets** in the top bar of the application shows the version, age and status of every dataset and can start an update.

No address is sent to another service for this, with one exception: reverse DNS (below).

### GeoLite2: location, ASN and AS holder

The location and the AS data come from MaxMind's GeoLite2 City and GeoLite2 ASN databases. They are not part of the application: each installation downloads them with its own account.

1. Create a free MaxMind account at maxmind.com and accept the GeoLite2 end-user licence agreement.
2. In the account, create a licence key.
3. Put the account ID and the key into `.env` as `NDM_MAXMIND_ACCOUNT_ID` and `NDM_MAXMIND_LICENSE_KEY`, then `docker compose up -d`.

The orchestrator downloads both databases within a minute, checks the published checksum, and replaces the file on the `reference-data` volume. It looks for a new release once a day. The licence requires an old release to be replaced within 30 days of a new one; a database that is older than that is no longer used, its status shows "too old, not used", and its fields become unavailable until an update succeeds. A failed download keeps the previous database in use and shows the reason with the dataset.

The application credits MaxMind wherever location data is shown, as the licence requires. A location is the centre of an area, not the place of a host; the application says so wherever it shows one, together with the radius of the area. Do not use the data to locate people or households: the licence forbids it.

### The registries: registration and organization

The registry and registration country of an address come from the delegation statistics of the five regional registries, which are published freely and downloaded every week.

The network name and the organization come from the registries' registration records. The terms differ per registry; they are summarized in `docs/datasets/registry-sources.md`:

| Registry | How its records are loaded |
| --- | --- |
| RIPE NCC, APNIC, AFRINIC | Downloaded, but only for the registries you list in `NDM_RIR_OBJECT_REGISTRIES`. Each publishes the records under terms that limit their use (purposes such as network operations and research; no commercial service built on them). APNIC requires a signed acceptable-use agreement, and AFRINIC a request for bulk access. List a registry only if your use meets its terms. The default is none. |
| ARIN | From a file you obtain from ARIN under its Bulk Whois terms of use. Put `arin_db.xml` (the `nets` and `orgs` objects are enough) into `NDM_RIR_FILES_DIR`. |
| LACNIC | From a file you obtain from LACNIC under its bulk whois agreement. Put it into `NDM_RIR_FILES_DIR` as `lacnic_db.txt`. |

Where no record is loaded for an address, its organization is the AS holder from GeoLite2, and the host detail says so.

A file you put into `NDM_RIR_FILES_DIR` is loaded at the next weekly check. To load it at once, press **Update now** for its dataset under Datasets.

### Reverse DNS

For every host a scan discovers, the orchestrator asks a resolver for the host's names (PTR records). **This sends the address of every discovered host to that resolver.** It is the one part of enrichment that does: whoever runs the resolver learns which addresses you scanned.

- Choose the resolver with `NDM_PTR_RESOLVER`. Unset, the host's own resolver is used.
- Set `NDM_PTR_ENABLED=false` to send no query at all. The host detail then says that reverse DNS is switched off.

### The map

The map draws its background from a tile provider. **There is no default**: public tile servers such as OpenStreetMap's own forbid this kind of use, and every provider has a usage policy. Choose a provider whose policy permits your use, or run your own tile server, and set:

```sh
NDM_TILE_URL_TEMPLATE=https://tiles.example.org/{z}/{x}/{y}.png?key=...
NDM_TILE_ATTRIBUTION=© Example Maps, © OpenStreetMap contributors
```

The attribution is the text the provider requires; it is shown on the map. The tile URL reaches the browser, so a key in it is visible to the analyst; use a key the provider restricts to your site.

Without a tile source, the map is switched off: the workspace shows a notice instead, the browser contacts no tile server, and the hosts are in the table and the list beside it as usual.

The browser loads tiles directly from the provider. The analyst's workstation, not this host, needs to reach it.

## 12. Traceroutes and device types

### Traceroutes

From the detail of a host, **Trace route** traces the path to it by ICMP (the default), by TCP to a port (by default an open port the latest scan found), or by UDP. The hops appear one by one while the trace runs, and the path is drawn on the map through the hops that have a location; where it skips a silent or unlocated hop, the line is dashed.

- **Scope.** The target must be inside the project scope, public, and not on the never-scan list. This is checked when the traceroute is started, again before it is sent to the scanner, and a third time by the scanner. A refusal is in the audit log, and so is every traceroute that runs.
- **Hops are never probed.** Probes go to the target only. A router on the way is seen through the ICMP message it sends back, and nothing is sent to it. To scan a router, add its address to the scope with a new attestation and scan it like any target.
- **Hops are recorded and enriched.** Every address that answers goes into the host table. A public one is enriched like a scanned host, reverse DNS included (section 11). A private or carrier-grade NAT address is stored and marked unlocatable; it is not enriched and its name is not looked up.
- **Rate.** Each traceroute sends at most 50 probes per second, and its probes count against `NDM_GLOBAL_RATE_PPS` like those of scans.
- **Privileges.** Traceroute needs the scanner's raw sockets (section 4). Without them the scanner refuses traceroutes, and the run shows `rejected: no_raw_sockets`.

### Device types

After enrichment, every host a scan found is classified as a router; a gateway, firewall or VPN endpoint; a DNS server; an IP camera or DVR; a server (with a sub-role: web, mail, database, remote access, file); or `unknown`. The host detail shows the type, its confidence, and each rule that matched with what it matched.

- **Evidence is weak by design.** Rules see only open TCP ports, reverse-DNS names, enrichment data, and whether the address was seen as a traceroute hop. No service is identified and no banner is read. Many hosts stay `unknown`; that is honest, not a fault.
- **The threshold.** `NDM_CLASSIFICATION_THRESHOLD` sets the confidence below which the type is `unknown`. A change applies to scans classified afterwards.
- **The ruleset is versioned.** It ships with the application (`ndm/core/ruleset/`). A result stored with a scan names the ruleset version that produced it and is never changed. After an update with a new ruleset, **Classify again** in the list of scans adds a result made with the new ruleset beside the old one.
- **Overrides, tags and notes.** The analyst can set a host's type by hand. It applies to that address in every scan of the project, including later ones, until cleared, and the automatic result stays visible beside it. Tags and notes belong to the address in the project. None of these changes anything a scan observed. Setting and clearing an override are in the audit log.

## 13. Schedules, comparison, alerts and export

### Schedules

A project's **Schedules** page lists each schedule with its next run, how its last run went, and whether the scheduler stopped it. A schedule has targets, scan parameters (typed in or taken from a scan profile), and either an interval in hours or a cron expression with a time zone. A cron expression keeps its wall-clock time across daylight-saving changes; on the night clocks go forward, a time that does not exist moves to the next one that does.

- **Minimum interval.** `NDM_MIN_SCHEDULE_INTERVAL_SECONDS` (default one hour) is the shortest time allowed between two runs. A cron expression is checked over its coming runs, so `0,30 9 * * *` is refused under the default even though most of its runs are a day apart. Lowering the minimum lets analysts schedule more often; every run is a full scan of its targets.
- **When a run is due.** The orchestrator looks every 15 seconds. A scheduled scan joins the same queue as manual scans and counts against `NDM_MAX_CONCURRENT_SCANS`.
- **Skipped runs.** If the previous run of the schedule is still queued or running, the new run is skipped, recorded, and an alert is raised.
- **After downtime** a schedule runs once to catch up, not once for every run it missed.
- **Archived projects.** Their schedules do not run.

### A flagged schedule

Before every run the scheduler checks the targets against the project scope as it is then, the public-address rule and the never-scan list. If a target fails, the run is **rejected**: no scan is created and nothing reaches the scanner. The schedule is **flagged**, and the operator or analyst sees:

- on the Schedules page, the schedule marked "stopped: targets_out_of_scope", with no next run;
- an alert "the run was rejected and the schedule flagged", naming the targets;
- an audit entry `scan.rejected` with the actor `system` and the point `scheduler`.

A flagged schedule does not run again until it is saved again. Edit it, narrow the targets or widen the scope with a new attestation, and save: the flag is cleared, the next run is counted from then, and the change is in the audit log. A schedule is also flagged `invalid_parameters` if its stored parameters can no longer be read after an update; saving it again fixes that too.

### Comparison and automatic diffs

**Compare** in the list of scans compares any two finished scans of a project: hosts that appeared or disappeared, ports opened or closed, and changes in reverse DNS, device type and enrichment. A port is reported only if both scans probed it, and the page says which ports were compared. Addresses that only one scan targeted are listed as not comparable. An enrichment change made when the reference dataset behind it had a new version is marked "dataset updated": it may be a database update, not a network change.

After each scheduled run that completes, the orchestrator compares it with the previous run of the same schedule that completed in full; cancelled, failed and partial runs are passed over. That diff is stored, so that its alert keeps showing what was found at the time.

### Alerts

The bell in the top bar shows the number of unread alerts on every page and counts a new alert as it is raised. Alerts are raised for changes found by a scheduled run, for failed, skipped and rejected scheduled runs, and for a message the system could not process (a message moved to the dead-letter queue; the orchestrator's log, section 14, says which one and why). Opening an alert marks it read and goes to its diff, scan or schedules.

### Export

The host table, a comparison and a traceroute have an **Export** menu with CSV and JSON. The host export holds every host that passes the active filters, in the table's order, not only the page shown. Each export carries the scan identifier, scan time, vantage point and dataset versions; in a CSV file they are columns of every row. Text that a spreadsheet would read as a formula (it begins with `=`, `+`, `-`, `@`, a tab or a carriage return) is written with a leading apostrophe; reverse-DNS names come from outside and must not run as formulas. Exports need a logged-in session like every other page.

## 14. Logs, metrics and health

Every service writes JSON lines to its standard output; read them with `docker compose logs <service>`. Configured secret values are replaced with `[REDACTED]` before a line is written. Lines about a scan carry its `scan_id`.

The orchestrator serves metrics in Prometheus format and its health check on port 9108 of the internal network: `/metrics` and `/healthz`. The port is not published. The metrics cover the scanner's probe rate and counters, the depth of each broker queue, the outcomes and durations of scans, and the age of each reference dataset (`ndm_dataset_age_seconds`). To read them from the host:

```sh
docker compose exec orchestrator python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9108/metrics').read().decode())"
```

## 15. Updating

Back up the database first (section 16). Then, in a checkout of the new release tag:

```sh
git fetch --tags
git checkout ndm-v<release>
docker compose up -d --build
```

The `migrate` service applies new migrations before the API and the orchestrator start. If a migration fails, they do not start; `docker compose logs migrate` shows why. A scan that is running during an update continues afterwards.

The update to Phase 2 adds the PostGIS extension to the database if it is not there yet; the pinned database image has it. The updates to Phases 3 and 4 change no existing data.

## 16. Backup and restore

Everything the analysts produced is in the database: projects, scopes and attestations, scans and what they found, schedules, alerts and the audit log. The reference datasets are not: the orchestrator downloads them again. Back up the database, and keep a copy of `.env` in a safe place apart from it: it holds the passwords.

### Back up

`pg_dump` takes a consistent copy while the stack runs. In `Assesment/deploy`:

```sh
docker compose exec -T postgres sh -c \
  'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom' \
  > ndm-$(date -u +%Y%m%dT%H%M%SZ).dump
```

The file holds the analysts' data, the audit log included; store it like the data itself. Check that a backup can be read:

```sh
docker compose exec -T postgres pg_restore --list < ndm-<time>.dump | head
```

If you use registry object files (section 11), back up `registry-files/` too.

### Restore

On the host that is to hold the data, a new one or the same one after a loss:

1. Install as in section 3, with the `.env` you kept (or a new one with new passwords), but start the database only:

   ```sh
   docker compose up -d postgres
   ```

   Its first start creates both database roles (section 8).

2. Replace the empty database with the backup. The database is dropped and created again empty first, because the image puts extensions into a new database that the backup brings with it:

   ```sh
   docker compose exec -T postgres sh -c \
     'dropdb --username "$POSTGRES_USER" "$POSTGRES_DB" && createdb --username "$POSTGRES_USER" "$POSTGRES_DB"'
   docker compose exec -T postgres sh -c \
     'pg_restore --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-owner --single-transaction --exit-on-error' \
     < ndm-<time>.dump
   ```

   `--single-transaction` and `--exit-on-error`: a restore that fails leaves an empty database, never half of the data. `--no-owner`: everything belongs to the owner role of this installation, whatever its name was before. The application role gets its privileges from the backup.

3. Start the rest:

   ```sh
   docker compose up -d
   ```

   `migrate` brings a backup of an older release up to the current schema.

4. Compare with the original. The counts must be those of the source at the time of the backup:

   ```sh
   docker compose exec -T postgres sh -c 'psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c \
     "SELECT (SELECT count(*) FROM project) AS projects, (SELECT count(*) FROM scan) AS scans,
             (SELECT count(*) FROM audit_event) AS audit_entries, (SELECT max(id) FROM audit_event) AS last_entry"'
   ```

   Log in; the projects, scans and the audit log are as they were. The accounts are those of the backup, with their passwords. Sessions are in the backup too; they expire as usual.

## 17. Abuse reports

Scans leave from this host's own public address. The owners of the targets, their providers and automated systems see that address, and some of them report it to your provider's abuse contact, **even when the scan was authorized**. Expect occasional reports; they are part of running a scanner.

- **Keep the evidence.** For every scan the audit log has who launched it, when, its targets, and the attestation of the scope it ran under; the project's scope history has the authorization text. A report names an address and a time: find the scan in the audit log and answer with the authorization.
- **Make the source identifiable.** A reverse-DNS name and a web page on the scanning address that say who scans and how to reach you turn many reports into a mail instead of a complaint.
- **Stop scanning a complainant at once.** Add the address or network to `NDM_NEVER_SCAN` and restart; it is refused in every project from then on, by the API, the orchestrator and the scanner each. Remove it from the project scopes as well, with a new attestation.
- **Keep the rate low.** `NDM_GLOBAL_RATE_PPS` limits the probes of all scans together. Lower rates draw fewer automated reports.
- Tell your provider before the first scans. Some providers forbid scanning in their terms; reading them is the operator's job, not the application's.

## 18. Checking an installation

Three scripts check a running stack. Each prints one line per check and exits with status 0 only if all passed.

```sh
./checks/inspect-1a.sh               # no secret in logs or images; ports; PostgreSQL not reachable
./checks/inspect-scanner-image.sh    # scanner image contents; scanner privileges; RabbitMQ on loopback only
./checks/inspect-2.sh                # licences: no GeoLite2 file in the repository or an image; attribution; tiles
```
