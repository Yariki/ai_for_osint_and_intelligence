# Network Discovery Map — Task Graph, Phase 1A

| | |
| --- | --- |
| Version | 1.0 |
| Date | 2026-11-14 |
| Status | Proposed; ready for assignment |
| Based on | Network Discovery Map — Delivery Plan v1.0, section 5 (Phase 1A) and section 2.2; Architecture v1.0 |
| Audience | Implementation team |

This document breaks Phase 1A (Foundation) of the delivery plan into tasks that can be assigned and run. Requirement identifiers (FR-…, NFR-…, C-…, A…) refer to the specification, AD… and I… to the architecture, and "check 1A.n" to the validation table of Phase 1A in the delivery plan.

## 1. Scope and conventions

**Slice.** Phase 1A is the first vertical slice: database, API, user interface and a deployable stack, ending with an analyst who logs in over TLS, creates projects with attested scopes and reads the audit log. Phase 0 (scanner spike) runs in parallel with these tasks and has its own file. The index of all phases is [network-discovery-map-task-graph.md](network-discovery-map-task-graph.md).

**Rules the graph follows.**

- A task depends only on tasks with a lower identifier, so the graph has no cycles.
- Two tasks where neither depends on the other, directly or through other tasks, may run at the same time. Such tasks never list the same file.
- `expected_files` are paths relative to the git repository root. The product lives under `Assesment/`; the CI workflow sits at the repository root.
- `risk_level` is `high` for anything that touches authentication, authorization, secrets, migrations or user data.

**Assumptions.**

- CI runs on GitHub Actions, because the remote is on GitHub.
- The directories follow architecture section 17. The file names inside them are proposed here, since no code exists yet.
- Interpretation I3 (deletion hides at once, the purge runs later, audit entries stay) holds. The delivery plan asks for it to be confirmed before Phase 1A starts; TASK-004 and TASK-006 build on it.

## 2. Overview

| Task | Title | Depends on | Risk | Checks closed |
| --- | --- | --- | --- | --- |
| TASK-001 | Repository scaffold, pinned dependencies and CI pipeline | — | low | Plan 2.2 item 1 |
| TASK-002 | Settings and JSON logging with secret redaction | TASK-001 | high | NFR-SEC-3, NFR-SEC-6, NFR-OPS-2 |
| TASK-003 | Scope library | TASK-001 | high | 1A.1, 1A.2 |
| TASK-004 | Schema, first migration, append-only audit table | TASK-002 | high | 1A.6 (database half) |
| TASK-005 | API skeleton and access | TASK-004 | high | 1A.3, 1A.4 |
| TASK-006 | Projects, scope with attestation, audit list API | TASK-003, TASK-005 | high | 1A.5, 1A.6 (API half), 1A.7 |
| TASK-007 | Frontend access | TASK-005 | high | — |
| TASK-008 | Frontend projects, scope editor, audit log | TASK-006, TASK-007 | medium | — |
| TASK-009 | Compose stack | TASK-005 | high | Plan 2.2 item 2 |
| TASK-010 | Operator guide, demonstration, exit checks | TASK-008, TASK-009 | medium | 1A.8, 1A.9 |

```text
001 ──► 002 ──► 004 ──► 005 ──┬──► 006 ──┐
 │                            │          ├──► 008 ──┐
 │                            ├──► 007 ──┘          ├──► 010
 │                            └──► 009 ─────────────┘
 └────► 003 ─────────────────────► 006
```

TASK-006 appears twice in the diagram: it needs both TASK-005 and TASK-003.

| Wave | Tasks that run in parallel |
| --- | --- |
| 1 | TASK-001 |
| 2 | TASK-002, TASK-003 |
| 3 | TASK-004 (TASK-003 may still be running) |
| 4 | TASK-005 |
| 5 | TASK-006, TASK-007, TASK-009 |
| 6 | TASK-008 (TASK-009 may still be running) |
| 7 | TASK-010 |

Three files are listed by more than one task: `api/app.py` (TASK-005, TASK-006), `router/index.ts` (TASK-001, TASK-007, TASK-008) and `api/schema.d.ts` (TASK-007, TASK-008). In each case the later task depends on the earlier one, so no two concurrent tasks share a file.

## 3. Tasks

### TASK-001 — Repository scaffold, pinned dependencies and CI pipeline

- **dependencies:** none
- **risk_level:** low

**Description.**

- Create the layout of architecture section 17: one Python package `ndm`, managed with uv and pinned through `uv.lock`, and the Vue 3, Vuetify 3, Pinia, Vue Router and Vite application in strict TypeScript.
- Pin every dependency Phase 1A needs now, so that no later task edits a manifest or a lockfile:
  - backend: FastAPI, SQLAlchemy 2 async, asyncpg, Alembic, pydantic-settings, argon2-cffi, structlog, testcontainers, ruff, mypy;
  - frontend: Vitest, ESLint, vue-tsc, openapi-typescript.
- Add an `npm run gen:api` script that generates API types from the OpenAPI schema.
- CI runs ruff, mypy, vue-tsc, ESLint, unit tests, integration tests and a dependency scan on every change.
- Configure pytest with the importlib import mode, so test directories need no `__init__.py` files.

**expected_files.**

- `.github/workflows/ndm-ci.yml`
- `Assesment/backend/pyproject.toml`
- `Assesment/backend/uv.lock`
- `Assesment/backend/src/ndm/__init__.py`
- `Assesment/backend/src/ndm/core/__init__.py`
- `Assesment/backend/tests/unit/test_smoke.py`
- `Assesment/frontend/package.json`
- `Assesment/frontend/package-lock.json`
- `Assesment/frontend/vite.config.ts`
- `Assesment/frontend/tsconfig.json`
- `Assesment/frontend/eslint.config.js`
- `Assesment/frontend/index.html`
- `Assesment/frontend/src/main.ts`
- `Assesment/frontend/src/App.vue`
- `Assesment/frontend/src/router/index.ts`
- `Assesment/frontend/src/plugins/vuetify.ts`

**Acceptance.**

- CI is green on a smoke test for the backend and for the frontend.
- All seven CI stages of delivery plan section 2.2, item 1, exist and run.

### TASK-002 — Settings from `NDM_` environment variables and JSON logging with secret redaction

- **dependencies:** TASK-001
- **risk_level:** high (secrets)

**Description.**

- Pydantic settings with the prefix `NDM_` for the groups Phase 1A uses:
  - database URLs for the migration owner and for the application role, and the application role's name;
  - session idle timeout;
  - project scope ceiling, default 65,536 (A1);
  - never-scan list, kept as raw strings;
  - the test switch `NDM_ALLOW_NON_PUBLIC_TARGETS`, settable through the environment only and off by default.
- structlog with JSON output. Every configured secret value is redacted at the logging boundary, whatever field or message it appears in.
- The never-scan list is not parsed here. Parsing needs `ndm.core.scope` and is done at API start-up in TASK-006.

**expected_files.**

- `Assesment/backend/src/ndm/core/settings.py`
- `Assesment/backend/src/ndm/core/logging.py`
- `Assesment/backend/tests/unit/core/test_settings.py`
- `Assesment/backend/tests/unit/core/test_logging.py`

**Acceptance.**

- A test logs each configured secret in a message, in a bound field and in an exception, and finds none of them in the output (NFR-SEC-3).
- The test switch is false unless it is set in the environment (NFR-SEC-6).
- Every log line is valid JSON (NFR-OPS-2).

### TASK-003 — Scope library: strict parsing, non-public table, containment, never-scan list, ceiling

- **dependencies:** TASK-001
- **risk_level:** high (decides which addresses may be scanned; must fail closed)

**Description.**

- `ndm.core.scope` consists of pure functions with no I/O. It imports nothing from `ndm.db` or `ndm.api`, because the scanner imports it in Phase 1B (C-3).
- Parse a single IPv4 address, a CIDR block or a start–end range. Host bits set, leading zeros, reversed ranges and mixed notations are each rejected with an error and never normalized.
- Keep an explicit table of non-public ranges: those of FR-PRJ-3 and the further special-purpose blocks. Do not rely on `ipaddress.is_private` or `ipaddress.is_global`.
- An entry that partly overlaps a non-public range is rejected as a whole.
- Provide containment of a target in a scope, the never-scan check and the project scope ceiling.
- The never-scan list, the ceiling and the allow-non-public switch are parameters of the functions, not settings lookups.

**expected_files.**

- `Assesment/backend/src/ndm/core/scope.py`
- `Assesment/backend/tests/unit/core/test_scope.py`

**Acceptance.**

- Check 1A.1: for every non-public range the first and last address are rejected and the addresses just outside are accepted; a partial overlap is rejected as a whole.
- Check 1A.2: each malformed form is rejected with an error and never normalized (NFR-SEC-5).

### TASK-004 — Schema, first migration, append-only audit table and audit writer

- **dependencies:** TASK-002
- **risk_level:** high (migrations, user data)

**Description.**

- ORM models and one Alembic migration for `account`, `session`, `project`, `attestation`, `scope_entry` and `audit_event`, as in architecture section 10.1:
  - `bigint` identities (AD7) and `timestamptz` in UTC;
  - `session` keyed by the token hash;
  - `project` with state, current attestation and `deleted_at`;
  - `scope_entry` with kind, original text, and start and end address as `inet`;
  - `audit_event` with no foreign keys, so entries survive deletions.
- The migration revokes UPDATE, DELETE and TRUNCATE on `audit_event` from the application role and adds triggers that reject the same for any role.
- The application role is created outside the migration, because its password is a secret: by a test fixture here and by an init script in TASK-009.
- An async session factory, and `record_audit(session, ...)`, which writes inside the caller's transaction.
- Integration fixtures start PostgreSQL through `testcontainers` with the same pinned PostGIS image tag that the Compose stack uses.

**expected_files.**

- `Assesment/backend/alembic.ini`
- `Assesment/backend/src/ndm/db/__init__.py`
- `Assesment/backend/src/ndm/db/models.py`
- `Assesment/backend/src/ndm/db/session.py`
- `Assesment/backend/src/ndm/db/audit.py`
- `Assesment/backend/src/ndm/db/migrations/env.py`
- `Assesment/backend/src/ndm/db/migrations/versions/0001_foundation.py`
- `Assesment/backend/tests/integration/conftest.py`
- `Assesment/backend/tests/integration/db/test_migrations.py`
- `Assesment/backend/tests/integration/db/test_audit_append_only.py`

**Acceptance.**

- The migration applies, reverts and applies again (delivery plan 2.2 item 3, NFR-OPS-4).
- Check 1A.6, database half: update, delete and truncate on `audit_event` fail for the application role and at the trigger.
- A transaction that rolls back leaves no audit entry.

### TASK-005 — API skeleton and access: first-run setup, login, sessions, password change, throttling

- **dependencies:** TASK-004
- **risk_level:** high (authentication)

**Description.**

- FastAPI application under `/api/v1` with one database session per request, one error shape that exposes no internals, and pagination with default limits.
- Authentication is a dependency on the parent router, so a new endpoint is protected by default. The only public routes are `/healthz`, login and first-run setup.
- First run: while no account exists, the API prints a one-time setup token to its log. Setup requires the token and works once.
- Sessions:
  - a random 256-bit token in an `HttpOnly`, `Secure`, `SameSite=Strict` cookie, with only its hash stored;
  - configurable idle expiry;
  - no CORS allowance.
- Passwords are hashed with argon2id. A password change ends all other sessions.
- Failed logins are audit events. The login endpoint counts recent ones and delays or refuses accordingly (FR-ACC-4).
- Audit entries, each written in the same transaction as its action: login, failed login, and the test switch at start-up.
- A script that exports the OpenAPI schema, for frontend type generation.

**expected_files.**

- `Assesment/backend/src/ndm/api/__init__.py`
- `Assesment/backend/src/ndm/api/app.py`
- `Assesment/backend/src/ndm/api/deps.py`
- `Assesment/backend/src/ndm/api/errors.py`
- `Assesment/backend/src/ndm/api/pagination.py`
- `Assesment/backend/src/ndm/api/auth.py`
- `Assesment/backend/src/ndm/api/routers/__init__.py`
- `Assesment/backend/src/ndm/api/routers/health.py`
- `Assesment/backend/src/ndm/api/routers/access.py`
- `Assesment/backend/src/ndm/api/schemas/__init__.py`
- `Assesment/backend/src/ndm/api/schemas/common.py`
- `Assesment/backend/src/ndm/api/schemas/access.py`
- `Assesment/backend/scripts/export_openapi.py`
- `Assesment/backend/tests/integration/api/conftest.py`
- `Assesment/backend/tests/integration/api/test_route_auth.py`
- `Assesment/backend/tests/integration/api/test_access.py`

**Acceptance.**

- Check 1A.3: a test enumerates every registered route, and each one except the three public routes answers 401 without a session. The test needs no change when an endpoint is added.
- Check 1A.4: setup needs the token and works once; a password change ends other sessions; an idle session expires; repeated failed logins are delayed or refused.
- Login, failed login and the test switch at start-up each write an audit entry, and a test proves it.

### TASK-006 — Projects, scope with attestation, and audit list API

- **dependencies:** TASK-003, TASK-005
- **risk_level:** high (authorization, user data)

**Description.**

- Projects: create, rename, describe, archive and delete.
  - An archived project rejects writes.
  - Deletion needs explicit confirmation, sets `deleted_at` and hides the project from every endpoint at once. Audit entries stay (I3). The physical purge arrives in Phase 1B.
- Scope: read the current scope, replace it, read its history.
  - A replace is refused without the authorization confirmation.
  - Each save creates a new immutable attestation that holds the exact entries and a timestamp, and leaves earlier attestations untouched (FR-PRJ-4, FR-PRJ-5).
  - Entries are validated with `ndm.core.scope`: non-public ranges, never-scan addresses and scopes above the ceiling are rejected.
- The never-scan list is parsed at API start-up. The API refuses to start on an invalid entry.
- Audit list endpoint, filterable and paginated.
- Audit entries, each written in the same transaction as its action: project creation, archival and deletion, and scope change with its attestation.
- Register the new routers in `api/app.py`.

**expected_files.**

- `Assesment/backend/src/ndm/api/app.py` (modified)
- `Assesment/backend/src/ndm/api/routers/projects.py`
- `Assesment/backend/src/ndm/api/routers/audit.py`
- `Assesment/backend/src/ndm/api/schemas/projects.py`
- `Assesment/backend/src/ndm/api/schemas/audit.py`
- `Assesment/backend/tests/integration/api/test_projects.py`
- `Assesment/backend/tests/integration/api/test_scope_attestation.py`
- `Assesment/backend/tests/integration/api/test_audit_list.py`

**Acceptance.**

- Check 1A.5: a scope cannot be saved without the confirmation; each save creates a new immutable attestation with the exact entries; never-scan addresses and scopes above the ceiling are rejected.
- Check 1A.6, API half: an action that rolls back leaves no audit entry.
- Check 1A.7: an archived project rejects writes; a deleted project disappears from every endpoint and its audit entries remain.
- Check 1A.3 still passes, unchanged, with the new routes.

### TASK-007 — Frontend access: setup, login, password change, session store and route guard

- **dependencies:** TASK-005
- **risk_level:** high (authentication)

**Description.**

- Generate the API types from the OpenAPI schema of TASK-005.
- A typed client that sends the session cookie and maps the one error shape.
- A session store (Pinia) and a router guard. Without a session, the guard sends every route except setup and login to the login view, and it handles a 401 from any call the same way.
- Views: first-run setup with the one-time token; login, including the delayed or refused state; password change.

**expected_files.**

- `Assesment/frontend/src/api/schema.d.ts` (generated)
- `Assesment/frontend/src/api/client.ts`
- `Assesment/frontend/src/stores/session.ts`
- `Assesment/frontend/src/router/index.ts` (modified)
- `Assesment/frontend/src/views/SetupView.vue`
- `Assesment/frontend/src/views/LoginView.vue`
- `Assesment/frontend/src/views/PasswordChangeView.vue`
- `Assesment/frontend/src/stores/session.spec.ts`
- `Assesment/frontend/src/router/guard.spec.ts`

**Acceptance.**

- Vitest: the guard redirects without a session and after a 401; the session store follows login, logout and expiry.
- `vue-tsc` and ESLint are clean against the generated types.
- Setup, login and password change work against a running API.

### TASK-008 — Frontend projects, scope editor with attestation, and audit log

- **dependencies:** TASK-006, TASK-007
- **risk_level:** medium (the server enforces the scope and attestation rules; the interface cannot weaken them)

**Description.**

- Regenerate the API types after TASK-006.
- Project list with create, rename, describe and archive, and delete behind an explicit confirmation. Archived projects are shown as read-only.
- Scope editor for address, CIDR and range entries. It shows the server's rejection on the entry concerned and does no validation or normalization of its own.
- Saving a scope opens the attestation confirmation. Without the confirmation nothing is sent.
- Scope history view.
- Audit log view with filters and paging.
- Add the new routes to the router.

**expected_files.**

- `Assesment/frontend/src/api/schema.d.ts` (regenerated)
- `Assesment/frontend/src/router/index.ts` (modified)
- `Assesment/frontend/src/stores/projects.ts`
- `Assesment/frontend/src/views/ProjectListView.vue`
- `Assesment/frontend/src/views/ProjectDetailView.vue`
- `Assesment/frontend/src/views/AuditLogView.vue`
- `Assesment/frontend/src/components/ScopeEditor.vue`
- `Assesment/frontend/src/components/AttestationDialog.vue`
- `Assesment/frontend/src/stores/projects.spec.ts`
- `Assesment/frontend/src/components/ScopeEditor.spec.ts`

**Acceptance.**

- Vitest: the scope editor sends nothing until the attestation is confirmed, and shows a rejected private range as an error on its entry.
- In the browser: create a project, save a scope, see a rejected private range and the matching audit entries.

### TASK-009 — Compose stack: proxy with TLS, api, postgres, migrate

- **dependencies:** TASK-005
- **risk_level:** high (secrets, network exposure, migrations at deployment)

**Description.**

- Compose file with four services:
  - `proxy`: Caddy with TLS from its internal CA; serves the built SPA and proxies `/api`;
  - `api`;
  - `postgres`: pinned PostGIS image with a named volume;
  - `migrate`: one-shot; `api` starts after it succeeds.
- Every image is pinned. Every service has a health check and a resource limit, runs as non-root with `no-new-privileges`, and has a read-only root filesystem where possible.
- Only the proxy publishes a port, 443. PostgreSQL is on the internal network only.
- A PostgreSQL init script creates the application role, separate from the migration owner.
- Secrets come from the environment at run time and are in no build argument and no image layer.
- An example environment file with placeholder values only.

**expected_files.**

- `Assesment/.dockerignore`
- `Assesment/deploy/compose.yaml`
- `Assesment/deploy/Caddyfile`
- `Assesment/deploy/Dockerfile.backend`
- `Assesment/deploy/Dockerfile.proxy`
- `Assesment/deploy/postgres/init-roles.sh`
- `Assesment/deploy/.env.example`

**Acceptance.**

- `docker compose up` from a clean checkout on a clean host brings every service to healthy (delivery plan 2.2 item 2).
- `/healthz` answers through the proxy over TLS, and port 443 is the only published port.
- The API connects as the application role and cannot alter `audit_event`.

### TASK-010 — Operator guide, demonstration script and Phase 1A exit checks

- **dependencies:** TASK-008, TASK-009
- **risk_level:** medium (handles secret values while inspecting; changes no secret handling)

**Description.**

- Operator guide covering every `NDM_` value and every host step Phase 1A adds, including first-run setup, the internal CA certificate and the two database roles.
- A written demonstration script for check 1A.9.
- An inspection script for check 1A.8. It searches container logs and image layers for the configured secret values, lists the published ports and confirms that PostgreSQL is not reachable from outside the internal network.
- Run both on a clean host, go through the common criteria of delivery plan section 2.2, and record the result of each of checks 1A.1 to 1A.9.

**expected_files.**

- `Assesment/docs/operator-guide.md`
- `Assesment/docs/demo/phase-1a.md`
- `Assesment/deploy/checks/inspect-1a.sh`

**Acceptance.**

- Check 1A.8 passes: no secret value in any log line or image layer; only port 443 published; PostgreSQL not reachable from outside.
- Check 1A.9 is carried out on a clean host: setup over TLS, login, a project with a scope, a rejected private range, the matching audit entries.
- Every check from 1A.1 to 1A.9 is recorded as passed, with none skipped.

## 4. Coverage of Phase 1A

| Phase 1A check | Task |
| --- | --- |
| 1A.1 Public-address rule | TASK-003 |
| 1A.2 Strict parsing | TASK-003 |
| 1A.3 Authentication fails closed | TASK-005; re-run unchanged in TASK-006 |
| 1A.4 First run and sessions | TASK-005 |
| 1A.5 Scope and attestation | TASK-006 |
| 1A.6 Audit is append-only | TASK-004 (database), TASK-006 (rollback through the API) |
| 1A.7 Project states | TASK-006 |
| 1A.8 Secrets and exposure | TASK-010, on the stack of TASK-009 and the redaction of TASK-002 |
| 1A.9 Demonstration | TASK-010 |

| Common criterion (delivery plan 2.2) | Task |
| --- | --- |
| 1. CI green | TASK-001 sets it up; every task keeps it green |
| 2. Clean start and demonstration | TASK-009, TASK-010 |
| 3. Migrations apply, revert, apply | TASK-004 |
| 4. Audit entry in the same transaction, with a test | TASK-005, TASK-006 |
| 5. Deletion purge | Applies from Phase 1B |
| 6. Route-enumeration test covers new endpoints | TASK-005, TASK-006 |
| 7. No failed or skipped check | TASK-010 |
| 8. Operator guide | TASK-010 |
