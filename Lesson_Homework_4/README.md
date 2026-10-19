# Pitwall RSS Reader

A single-user dashboard for scheduled, incremental RSS and Atom collection. The
four Autosport feeds are included on first initialization. Add other public feeds
without changing the crawler.

## Start with Docker

Requirements: Docker Engine and Docker Compose v2 or later.

```bash
cp .env.example .env
docker compose up -d --build
```

Open **http://localhost:8080**. Initialization applies database migrations and
seeds Formula 1, MotoGP, National, and Sportscars without overwriting existing
source settings. Beat checks for work every 30 seconds. The first crawl imports
all articles currently in each feed; subsequent crawls save only new articles.

The application has no accounts. Its only published port binds to `127.0.0.1`.
Redis and the API are internal to Compose. Use a trusted access boundary if you
change that binding. Private-network feeds and URLs containing credentials are
not supported. A feed URL is immutable after creation; pause it and add a new
source to change URLs while preserving collection history.

```bash
docker compose ps
docker compose logs -f worker beat
docker compose down
```

`down` preserves the SQLite database and Redis volumes. Rebuilding or restarting
does not remove articles. Run only **one Beat scheduler** for this deployment.

## Interface

- **Overview:** source totals, unique articles, jobs, active jobs, and jobs with
  recorded errors. Refreshes every 15 seconds.
- **Sources:** add a name, feed URL, and period in minutes (default 60); edit the
  name/period, pause or resume scheduling, or request a crawl manually. A manual
  crawl also works for paused sources. Existing active jobs are reused.
- **Articles:** category tags, source, title search, inclusive UTC publication
  date filters, and server-side pagination. Multiple selected tags use **any
  match**. Displayed timestamps use the browser's timezone. Missing dates remain
  empty. Descriptions render as plain text; article links open the publisher.
- **Crawl jobs:** scheduled/manual trigger, status, attempts, counts, HTTP status,
  duration of the latest attempt, next retry, and error details. Refreshes every
  15 seconds. `seen` is the duplicate count, not the total items in the feed.

Error statistics count jobs with recorded issues, including recovered retries.
Invalid-entry totals count encounters, so an invalid item encountered repeatedly
can contribute more than once. Unique articles are counted from the article
table, independently of feed/source associations.

## How incremental collection works

RSS is a snapshot format: there is no standard `after_id` parameter and GUIDs
are opaque, not necessarily sequential. Each source stores the first entry ID
from its last fully successful document as an observable checkpoint. Correctness
uses a persisted `(source_id, entry_id)` identity for every saved entry rather
than stopping at that checkpoint, so late or reordered entries are still found.
An entry without a GUID/Atom ID uses its canonical URL as its identity.

All article content lives in `articles`. A globally unique canonical URL prevents
cross-feed duplicates. Normalization removes fragments and `utm_*`, `fbclid`,
`gclid`, and `msclkid`; meaningful query parameters are retained. The original
feed link is also stored. Different publisher URLs are not guessed to represent
the same story. Previously saved article content is preserved.

`source_entries` maps each source identity to its article. Categories are Unicode
normalized, whitespace normalized, and matched case-insensitively in `tags`;
`article_tags` reuses the same tag rows. Different feeds can add categories to the
same article. Required missing title/link fields skip that entry without losing
valid siblings. Missing publication dates are nullable.

Fetching/parsing happens outside database transactions. Saving valid entries,
job counts, and a successful checkpoint is atomic. A partial document never
advances the checkpoint or retains HTTP validators, so skipped invalid entries
can be retried at the next source check. A truncated/malformed XML document is
retried as a whole. DTD/entity declarations are rejected.

Conditional requests use publisher `ETag`/`Last-Modified` headers when available.
`304` is a successful check with no inserts. The Autosport feeds did not provide
these headers during initial inspection; ordinary deduplication still applies.

Only feed content is fetched, without article-page scraping or historical
backfill. Items removed from the feed during extended downtime cannot be
recovered unless the publisher offers a separate archive.

## Request policy and failure handling

- A descriptive user agent is used for both robots and feed requests.
- Robots policies are cached for at most 24 hours. Protego handles wildcard and
  user-agent rules. Every feed redirect is checked against its destination's
  policy. Robots redirects are followed with bounded hops and public-IP checks.
- Robots `404`/`410` means no policy; `401`/`403` means deny. Other failures defer
  collection. There is no stale-policy fallback after cache expiry.
- Minimum spacing and jitter apply **per origin**, across all worker processes,
  using atomic Redis reservations. Robots delays can increase the interval.
  `Request-rate` is handled conservatively by allowing one request per entire
  specified window. This trades throughput for respecting publisher limits.
- `429` and `5xx` responses apply a shared origin cooldown, honoring both forms
  of `Retry-After`. Retries use exponential backoff plus jitter and a bounded
  attempt count. A pacing wait over five seconds is deferred without consuming
  an attempt. Workers do not sleep through long cooldowns.
- HTTP timeouts, response size limits, redirect limits, and a crawl execution
  time limit bound individual attempts. Redis coordination failure postpones
  requests rather than bypassing pacing.
- Public DNS answers are validated and connections are pinned to the validated
  IP, preserving HTTP Host and TLS SNI. Private, loopback, link-local, and mixed
  public/private DNS results are rejected. Only standard HTTP/HTTPS ports work.
  A configured outbound proxy is trusted infrastructure and receives the pinned
  destination address. TLS verification stays enabled.
- Jobs and errors are durable in SQLite. One source failure does not cancel
  other sources. A unique partial index permits one active job per source;
  claims use atomic updates and a run token. Duplicate Celery deliveries cannot
  repeat a finished job. Expired worker/dispatch leases are recovered by the
  dispatcher, including a crash between committing a job and publishing it.

The scheduler creates overdue jobs and dispatches queued/retrying jobs. Manual
requests also wait for its next tick. A delayed scheduler creates one catch-up
crawl per source rather than replaying every missed interval. Source periods
measure from queueing, so worker availability and publisher cooldowns can delay
actual execution. Retries are scheduled by the dispatcher rather than sleeping
or accumulating long Celery ETA tasks.

## Configuration

Copy `.env.example` to the project root as `.env`; it is ignored by Git. Compose
loads it into the backend services. Restart those services after changing it.
Source periods/enabled state are stored in SQLite and can be changed live in the
UI. Startup seeding never overwrites those values.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/rss.db` | SQLite path relative to the backend working directory |
| `REDIS_URL` | `redis://redis:6379/0` | Broker and shared request policy cache |
| `WORKER_CONCURRENCY` | `1` | Celery worker processes |
| `DEFAULT_PERIOD_MINUTES` | `60` | Default for newly created/seeded sources |
| `SCHEDULER_INTERVAL_SECONDS` | `30` | Due-source and recovery check interval |
| `REQUEST_DELAY_SECONDS` | `3` | Minimum interval per origin |
| `REQUEST_JITTER_SECONDS` | `1` | Maximum extra random delay |
| `REQUEST_TIMEOUT_SECONDS` | `20` | HTTP connect/read/write timeout |
| `MAX_RESPONSE_BYTES` | `5242880` | Maximum decoded HTTP body size |
| `MAX_ATTEMPTS` | `3` | Maximum failed attempts per job |
| `RETRY_BASE_SECONDS` | `30` | Base exponential retry/cooldown delay |
| `ROBOTS_CACHE_SECONDS` | `86400` | Robots cache TTL, maximum 24 hours |
| `JOB_LEASE_SECONDS` | `300` | Crash recovery lease; task limit is 15 seconds shorter |
| `DISPATCH_LEASE_SECONDS` | `120` | Republish timeout for unclaimed jobs |
| `USER_AGENT` | `RespectfulRSSReader/1.0` | Identifiable crawler product token |
| `PROXY_URL` | empty | Optional fixed HTTP(S) proxy URL |
| `WEB_PORT` | `8080` | Local dashboard port |

SQLite uses WAL, a 15-second busy timeout, foreign keys, and short write
transactions. Keep the data volume on a local filesystem on **one host**. A
single worker is suitable for this project. Raising concurrency preserves shared
pacing but SQLite still serializes writes; substantial scaling needs a different
database/deployment design. Changing `DATABASE_URL` in Compose requires mounting
its new directory persistently.

## Local development

Requirements: uv, Python 3.14 (uv can install it), Node 24.15+, and Redis.

Start the Compose Redis service, or use your own local Redis. Compose's Redis
does not publish a host port; local backend processes need a separate reachable
Redis instance. In separate terminals, from the repository root:

```bash
cd backend
uv sync --locked
# Set REDIS_URL to your local Redis address for both worker and Beat.
export REDIS_URL=redis://127.0.0.1:6379/0
uv run python -m rss_reader.cli init
uv run uvicorn rss_reader.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

```bash
cd backend
REDIS_URL=redis://127.0.0.1:6379/0 uv run celery -A rss_reader.tasks:celery_app worker --loglevel=INFO
```

```bash
cd backend
REDIS_URL=redis://127.0.0.1:6379/0 uv run celery -A rss_reader.tasks:celery_app beat --schedule=./data/celerybeat-schedule --loglevel=INFO
```

```bash
cd frontend
npm ci
npm run dev
```

Open the Vite URL printed in the terminal. Vite proxies `/api` to the local
backend. Backend Settings reads `.env` from its working directory; local backend
defaults work without a file, or place a local ignored `.env` in `backend/`.
The root `.env` is used by Compose. API documentation is available at
`http://127.0.0.1:8000/docs` during local development.

## Verification

```bash
cd backend
uv sync --locked
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
# Include real shared-pacing/cooldown tests against a reachable Redis:
TEST_REDIS_URL=redis://127.0.0.1:6379/15 uv run pytest
uv run alembic check
```

Redis tests use isolated random keys and delete only their own keys. They are
skipped when `TEST_REDIS_URL` is absent. All other tests use temporary SQLite
files and mocked publisher responses; they do not contact Autosport.

```bash
cd frontend
npm ci
npm run typecheck
npm run lint
npm run test
npm run build
npm audit
```

GitHub Actions runs these checks, including Redis coordination and migration
consistency. Tests cover repeated/reordered feeds, canonicalization, tag reuse,
RSS/Atom parsing, partial failures, HTTP cache checkpoints, retry limits, robots
rules, redirects, rate limiting, job recovery, API filtering, and UI forms.

## Back up and restore

Use SQLite's online backup rather than copying a live WAL database:

```bash
docker compose exec api python -m rss_reader.cli backup /app/data/backup.db
docker compose cp api:/app/data/backup.db ./backup.db
```

The command refuses to overwrite an existing destination. To restore, stop the
API, worker, and Beat; preserve the current database separately; replace the
database file with the backup and remove its old `-wal`/`-shm` files while all
database users remain stopped. Preserve ownership for container UID 10001, then
restart. Restored active jobs may be retried; database constraints keep inserts
idempotent. Redis is a broker/cache, not the authoritative article store.
