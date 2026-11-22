# Release versions

Release: `1.0.0-rc.1`

Every image the stack runs, with its version. `deploy/compose.yaml` refers to each by exactly this reference; `backend/tests/unit/test_compose.py` checks that the two agree. The project's own images are built from the tagged commit `ndm-v1.0.0-rc.1` by `deploy/release/build-images.sh` or by the release workflow (`.github/workflows/ndm-release.yml`), and carry the labels `org.opencontainers.image.version` and `org.opencontainers.image.revision`.

## Images of the stack

| Image | Version | Built from / source | Used by |
| --- | --- | --- | --- |
| `ndm-backend` | `1.0.0-rc.1` | `deploy/Dockerfile.backend` | api, orchestrator, migrate |
| `ndm-scanner` | `1.0.0-rc.1` | `deploy/Dockerfile.scanner` | scanner |
| `ndm-proxy` | `1.0.0-rc.1` | `deploy/Dockerfile.proxy` | proxy |
| `ndm-scanner-netinit` | `1.0.0-rc.1` | `deploy/scanner-netinit/Dockerfile` | scanner-netinit |
| `rabbitmq` | `4.3.7-alpine` | Docker Hub, official image | rabbitmq |
| `postgis/postgis` | `17-3.5` | Docker Hub | postgres |

## Base images of the project's images

| Image | Version | Used in |
| --- | --- | --- |
| `python` | `3.14.4-slim-bookworm` | `Dockerfile.backend`, `Dockerfile.scanner` |
| `ghcr.io/astral-sh/uv` | `0.13.8` | `Dockerfile.backend`, `Dockerfile.scanner` (build stage only) |
| `node` | `26.12.0-alpine` | `Dockerfile.proxy` (build stage only) |
| `caddy` | `2.11.9-alpine` | `Dockerfile.proxy` |
| `alpine` | `3.24.4` | `scanner-netinit/Dockerfile` |

## Tags and digests

A tag names a version, but a registry can move it: `postgis/postgis:17-3.5` in particular follows the patch releases of PostgreSQL 17 and PostGIS 3.5, and the official images are rebuilt under the same tag for operating-system updates. The build script therefore records the digest of every image it built or pulled in `digests-<release>.txt` beside this file. That file, kept with the release, says exactly which images were tested. To run exactly those images, pull them by digest (`docker pull postgis/postgis@sha256:…`) and tag them as above.
