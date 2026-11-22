# Phase 1A — Demonstration Script and Exit Checks

This document has two parts. Part 1 is the written demonstration for check 1A.9: a person follows it on a clean host. Part 2 is the record of checks 1A.1 to 1A.9.

## Part 1. Demonstration (check 1A.9)

**You need.** A clean Linux host with Docker Engine and the Compose plugin, a clean checkout, and a browser (current Chromium or Firefox). "Clean" means no containers, images or volumes of this project exist on the host.

Follow the operator guide ([../operator-guide.md](../operator-guide.md)) for the installation steps; do not use knowledge that is not in it. Anything the guide does not say is a defect of the guide.

| # | Step | Expected result |
| --- | --- | --- |
| 1 | In `Assesment/deploy`: create `.env` from `.env.example`, set the two passwords, run `docker compose up -d --build`. | The build succeeds. |
| 2 | `docker compose ps` | `proxy`, `api` and `postgres` are `healthy`; `migrate` has exited with status 0. |
| 3 | `curl --insecure https://localhost/healthz` | `{"status":"ok"}` |
| 4 | `docker compose logs api \| grep setup_required` | One line with a `setup_token` value. |
| 5 | Open `https://localhost/` in the browser. | After the certificate warning (or with the root certificate installed, without one), the **First-run setup** page. The address bar shows HTTPS. |
| 6 | Enter a wrong token, a username and a password. | An error; no account is created. |
| 7 | Enter the token from step 4, username `analyst` and a password of at least 12 characters. | The **Log in** page. |
| 8 | Log in with a wrong password. | An error message; you stay on the login page. |
| 9 | Log in with the right password. | The **Projects** page, with `analyst` shown in the bar. |
| 10 | Create a project named `Demo`. | The project is in the list as **Active**. |
| 11 | Open the project. Add the entry `8.8.8.0/24` and choose **Save scope**. | The **Confirm authorization** dialog. The save button is disabled. |
| 12 | Cancel the dialog. Reload the page. | The scope is still empty: nothing was saved. |
| 13 | Add `8.8.8.0/24` again, choose **Save scope**, tick the confirmation and save. | "The scope was saved." The scope history shows one entry with your username and the time. |
| 14 | Add a second entry `192.168.1.0/24`, save and confirm. | An error on the second entry that names the range `192.168.0.0/16`. The history still shows one entry. |
| 15 | Open **Audit log**. | Newest first: `scope.change`, `project.create`, `login`, `login.failed`, `account.setup`. There is no entry for the rejected save of step 14. |
| 16 | Filter the audit log by action `scope.change`. | One entry, whose details list `8.8.8.0/24` and an attestation identifier. |
| 17 | Open `https://localhost/setup` again. | The login or the projects page: setup cannot be repeated. |
| 18 | Log out. Open `https://localhost/projects`. | The login page. |
| 19 | In `Assesment/deploy`: `./checks/inspect-1a.sh` | Every line starts with `PASS`; the last line is `Check 1A.8: PASSED`. |

## Part 2. Record of exit checks

Fill in the result of each check when it is run. A check that cannot be run is recorded with its reason and needs the product owner's acceptance (delivery plan 2.2, item 7).

| Check | How it is run | Result | Date | Note |
| --- | --- | --- | --- | --- |
| 1A.1 Public-address rule | `pytest tests/unit/core/test_scope.py` | Passed | 2026-11-15 | |
| 1A.2 Strict parsing | `pytest tests/unit/core/test_scope.py` | Passed | 2026-11-15 | |
| 1A.3 Authentication fails closed | `pytest tests/integration/api/test_route_auth.py` | Passed | 2026-11-18 | On the pinned images; first run with a reservation, see below. |
| 1A.4 First run and sessions | `pytest tests/integration/api/test_access.py` | Passed | 2026-11-18 | On the pinned images; first run with a reservation, see below. |
| 1A.5 Scope and attestation | `pytest tests/integration/api/test_scope_attestation.py` | Passed | 2026-11-18 | On the pinned images; first run with a reservation, see below. |
| 1A.6 Audit is append-only | `pytest tests/integration/db/test_audit_append_only.py` and `test_scope_attestation.py` | Passed | 2026-11-18 | On the pinned images; first run with a reservation, see below. |
| 1A.7 Project states | `pytest tests/integration/api/test_projects.py` | Passed | 2026-11-18 | On the pinned images; first run with a reservation, see below. |
| 1A.8 Secrets and exposure | `deploy/checks/inspect-1a.sh` on the running stack | Passed | 2026-11-19 | On the lab host with the release images; also run by `inspect-release.sh`. |
| 1A.9 Demonstration | Part 1 of this document | **Not run** | | Needs a clean host with Docker and a browser. |

**Reservation on checks 1A.3 to 1A.7.** The integration tests are written to start PostgreSQL through `testcontainers` with the pinned image `postgis/postgis:17-3.5`. The machine they were run on had no access to Docker, so on 2026-11-15 they ran against a locally started PostgreSQL 16 server that stood in for the container. All 206 backend tests passed there. The run must be repeated with Docker, which CI does on every change.

**Also done on 2026-11-15, without containers.** `init-roles.sh`, `alembic upgrade head`, and the API started with `uvicorn --factory ndm.api.app:production_app` were run in sequence against that PostgreSQL server, and steps 3, 4, 7, 9, 10, 13, 14 and 15 of Part 1 were carried out over HTTP against the API directly. All gave the expected results; every log line was valid JSON and no configured password occurred in any of them. This run did not include the proxy, TLS, the container images or a browser, so it does not replace checks 1A.8 and 1A.9.

**The reservation is lifted.** On 2026-11-18 the whole backend suite, checks 1A.3 to 1A.7 among it, ran with Docker on the pinned images: 1,478 passed, none failed.

**Phase 1A is not complete until 1A.9, the demonstration in a browser, is recorded as passed.**
