# Licence inventory

| | |
| --- | --- |
| Release | 1.0.0-rc.1 |
| Date | 2026-11-18 |
| Requirement | NFR-LIC-2 |

Every data file the product ships, every dataset it uses without shipping it, and every package in the two lockfiles (`backend/uv.lock`, `frontend/package-lock.json`) with its licence. `backend/tests/unit/test_licences.py` fails when a locked package is missing here, so a dependency change must update this file.

## 1. Summary

- **No GPL, LGPL or AGPL code** is in a lockfile.
- **MPL-2.0**, a file-level copyleft, applies to three packages, used unmodified: `certifi` (CA bundle, in `ndm-backend`), `pathspec` (development only) and `lightningcss` (CSS processing in the frontend build; nothing of it is in the built application). Using MPL-2.0 files unmodified places no obligation on the rest of the product; a modified MPL file would have to be published.
- **PSF-2.0, Python-2.0, BlueOak-1.0.0, 0BSD, ISC, MIT-0** and the BSD and Apache licences are permissive.
- **The application's own licence** applies to everything under `Assesment/` that is not listed below as third-party, the classification ruleset and the two port lists included.

## 2. Data files shipped with the product

| File | What it is | Source | Licence |
| --- | --- | --- | --- |
| `backend/src/ndm/core/data/iana-service-names.csv` | Nominal service name of each TCP port | IANA Service Name and Transport Protocol Port Number Registry, 2026-11-15 | CC0 1.0 |
| `backend/src/ndm/core/data/top-100-tcp.txt` | Port list of the top-100 profile | This project's own selection; port numbers are facts from the IANA registry | This project's licence |
| `backend/src/ndm/core/data/top-1000-tcp.txt` | Port list of the top-1000 profile | As above; not derived from nmap's `nmap-services` (R4) | This project's licence |
| `backend/src/ndm/core/ruleset/v1.yaml` | Classification ruleset, version 1 | Written for this project; no third-party rules or fingerprints | This project's licence |
| `frontend` icon font (`@mdi/font`) | Material Design Icons, in the built application | npm package, listed in section 5 | As listed in section 5 |

The provenance of each data file is recorded beside it: `ndm/core/data/PROVENANCE.md` and `ndm/core/ruleset/LICENCE.md`.

## 3. Data the product uses but does not ship

None of these is in the repository or in an image; the operator obtains them under the provider's terms (operator guide, section 11).

| Data | Licence and terms | Who obtains it |
| --- | --- | --- |
| MaxMind GeoLite2 City and ASN | GeoLite2 End User License Agreement; attribution "This product includes GeoLite2 data created by MaxMind", shown on the map | The operator's MaxMind account; downloaded by the orchestrator |
| Delegation statistics of AFRINIC, APNIC, ARIN, LACNIC, RIPE NCC | Published for free use by each registry | Downloaded by the orchestrator |
| Registry object data (RIPE, APNIC, AFRINIC bulk files) | Each registry's own terms; opt-in per registry (`docs/datasets/registry-sources.md`) | The operator, when the terms are met |
| Map tiles | The chosen tile provider's terms and attribution | The analyst's browser, from the provider the operator configures |

## 4. Python packages (`backend/uv.lock`)

"Image" says where a package is installed: `backend` is the image of the api, orchestrator and migrate; `scanner` the image of the scanner; `development` means tests, linting and type checking only, in no image.

| Package | Version | Licence | Image |
| --- | --- | --- | --- |
| `aio-pika` | 10.2.3 | Apache-2.0 | backend, scanner |
| `aiormq` | 7.2.5 | Apache-2.0 | backend, scanner |
| `alembic` | 1.20.1 | MIT | backend |
| `annotated-doc` | 0.0.6 | MIT | backend |
| `annotated-types` | 0.8.0 | MIT | backend, scanner |
| `anyio` | 4.16.0 | MIT | backend |
| `argon2-cffi` | 25.1.0 | MIT | backend |
| `argon2-cffi-bindings` | 26.2.0 | MIT | backend |
| `ast-serialize` | 0.13.1 | MIT | development |
| `asyncpg` | 0.32.0 | Apache-2.0 | backend |
| `boolean-py` | 5.0 | BSD-2-Clause | development |
| `cachecontrol` | 0.14.4 | Apache-2.0 | development |
| `certifi` | 2026.10.23 | MPL-2.0 | backend |
| `cffi` | 2.1.1 | MIT-0 | backend |
| `charset-normalizer` | 3.5.2 | MIT | development |
| `click` | 8.5.1 | BSD-3-Clause | backend |
| `colorama` | 0.4.6 | BSD-3-Clause (Windows only; not installed on Linux) | development, Windows only |
| `cronsim` | 2.8 | BSD License | backend |
| `cyclonedx-python-lib` | 11.12.0 | Apache-2.0 | development |
| `defusedxml` | 0.7.1 | PSFL | development |
| `dnspython` | 2.9.2 | ISC | backend |
| `docker` | 7.2.0 | Apache-2.0 | development |
| `fastapi` | 0.163.0 | MIT | backend |
| `filelock` | 4.2.10 | MIT | development |
| `greenlet` | 3.5.6 | MIT AND PSF-2.0 | backend |
| `h11` | 0.16.0 | MIT | backend |
| `httpcore` | 1.0.9 | BSD-3-Clause | backend |
| `httpx` | 0.28.1 | BSD-3-Clause | backend |
| `idna` | 3.20 | BSD-3-Clause | backend, scanner |
| `iniconfig` | 2.3.1 | MIT | development |
| `librt` | 0.16.0 | MIT | development |
| `license-expression` | 30.4.4 | Apache-2.0 | development |
| `mako` | 1.4.3 | MIT | backend |
| `markdown-it-py` | 4.3.0 | MIT License | development |
| `markupsafe` | 3.0.4 | BSD-3-Clause | backend |
| `maxminddb` | 3.2.0 | Apache-2.0 | backend |
| `mdurl` | 0.1.2 | MIT License | development |
| `mmdb-writer` | 0.2.7 | MIT License | development |
| `msgpack` | 1.2.4 | Apache-2.0 | development |
| `multidict` | 7.2.1 | Apache-2.0 | backend, scanner |
| `mypy` | 2.4.1 | MIT | development |
| `mypy-extensions` | 1.2.0 | MIT | development |
| `netaddr` | 1.3.0 | BSD License | development |
| `opentelemetry-api` | 1.45.2 | Apache-2.0 | backend |
| `packageurl-python` | 0.17.6 | MIT | development |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause | development |
| `pamqp` | 5.0.0 | BSD-3-Clause | backend, scanner |
| `pathspec` | 1.1.1 | Mozilla Public License 2.0 (MPL 2.0) | development |
| `pika` | 1.4.4 | BSD-3-Clause | development |
| `pip` | 26.3 | MIT | development |
| `pip-api` | 0.0.35 | Apache Software License | development |
| `pip-audit` | 2.10.1 | Apache Software License | development |
| `pip-requirements-parser` | 32.0.1 | MIT | development |
| `platformdirs` | 4.14.5 | MIT | development |
| `pluggy` | 1.7.0 | MIT | development |
| `prometheus-client` | 0.26.0 | Apache-2.0 AND BSD-2-Clause | backend |
| `propcache` | 0.5.4 | Apache-2.0 | backend, scanner |
| `py-serializable` | 2.2.0 | Apache-2.0 | development |
| `pycparser` | 3.12 | BSD-3-Clause | backend |
| `pydantic` | 2.14.3 | MIT | backend, scanner |
| `pydantic-core` | 2.50.4 | MIT | backend, scanner |
| `pydantic-settings` | 2.16.3 | MIT | backend, scanner |
| `pygments` | 2.21.0 | BSD-2-Clause | development |
| `pyparsing` | 3.3.3 | MIT | development |
| `pytest` | 9.2.0 | MIT | development |
| `pytest-asyncio` | 1.5.0 | Apache-2.0 | development |
| `python-dotenv` | 1.2.4 | BSD-3-Clause | backend, scanner |
| `pywin32` | 312 | PSF-2.0 (Windows only; not installed on Linux) | development, Windows only |
| `pyyaml` | 6.0.3 | MIT | backend |
| `requests` | 2.34.2 | Apache-2.0 | development |
| `rich` | 15.0.0 | MIT | development |
| `ruff` | 0.17.5 | MIT | development |
| `sortedcontainers` | 2.4.0 | Apache 2.0 | development |
| `sqlalchemy` | 2.1.8 | MIT | backend |
| `starlette` | 1.10.0 | BSD-3-Clause | backend |
| `structlog` | 26.1.0 | MIT OR Apache-2.0 | backend, scanner |
| `testcontainers` | 4.15.0 | Apache-2.0 | development |
| `tomli` | 2.5.0 | MIT | development |
| `tomli-w` | 1.2.0 | MIT License | development |
| `types-pyyaml` | 6.0.12.20261113 | Apache-2.0 | development |
| `typing-extensions` | 4.16.0 | PSF-2.0 | backend, scanner |
| `typing-inspection` | 0.4.5 | MIT | backend, scanner |
| `urllib3` | 2.8.0 | MIT | development |
| `uvicorn` | 0.54.0 | BSD-3-Clause | backend |
| `wrapt` | 2.5.2 | BSD-2-Clause | development |
| `yarl` | 1.25.1 | Apache-2.0 | backend, scanner |

## 5. npm packages (`frontend/package-lock.json`)

"Use" is `application` for a package whose code can end in the built web application served by the proxy, and `build and test` for one that only builds or tests it. A package that appears more than once in the lockfile, in different versions, is listed once per version.

| Package | Version | Licence | Use |
| --- | --- | --- | --- |
| `@babel/code-frame` | 7.29.7 | MIT | build and test |
| `@babel/helper-string-parser` | 7.29.14 | MIT | application |
| `@babel/helper-string-parser` | 8.1.2 | MIT | application |
| `@babel/helper-validator-identifier` | 7.29.7 | MIT | application |
| `@babel/helper-validator-identifier` | 8.0.6 | MIT | application |
| `@babel/parser` | 7.29.14 | MIT | application |
| `@babel/parser` | 8.1.2 | MIT | application |
| `@babel/types` | 7.29.14 | MIT | application |
| `@babel/types` | 8.1.2 | MIT | application |
| `@cacheable/memory` | 2.2.0 | MIT | build and test |
| `@cacheable/utils` | 2.5.0 | MIT | build and test |
| `@emnapi/core` | 2.0.0-beta.2 | MIT | application |
| `@emnapi/runtime` | 2.0.0-beta.2 | MIT | application |
| `@emnapi/wasi-threads` | 2.2.0 | MIT | application |
| `@eslint-community/eslint-utils` | 4.10.1 | MIT | build and test |
| `@eslint-community/regexpp` | 4.12.3 | MIT | build and test |
| `@eslint/config-array` | 0.23.6 | Apache-2.0 | build and test |
| `@eslint/config-helpers` | 0.7.0 | Apache-2.0 | build and test |
| `@eslint/core` | 1.2.1 | Apache-2.0 | build and test |
| `@eslint/js` | 10.0.1 | MIT | build and test |
| `@eslint/object-schema` | 3.0.5 | Apache-2.0 | build and test |
| `@eslint/plugin-kit` | 0.7.3 | Apache-2.0 | build and test |
| `@exodus/bytes` | 1.16.0 | MIT | build and test |
| `@humanfs/core` | 0.19.2 | Apache-2.0 | build and test |
| `@humanfs/node` | 0.16.8 | Apache-2.0 | build and test |
| `@humanfs/types` | 0.15.0 | Apache-2.0 | build and test |
| `@humanwhocodes/module-importer` | 1.0.1 | Apache-2.0 | build and test |
| `@humanwhocodes/retry` | 0.4.3 | Apache-2.0 | build and test |
| `@isaacs/nonrecursive-glob-match` | 1.1.0 | BlueOak-1.0.0 | build and test |
| `@jridgewell/gen-mapping` | 0.3.13 | MIT | application |
| `@jridgewell/remapping` | 2.3.5 | MIT | application |
| `@jridgewell/resolve-uri` | 3.1.3 | MIT | application |
| `@jridgewell/sourcemap-codec` | 1.6.0 | MIT | application |
| `@jridgewell/trace-mapping` | 0.3.31 | MIT | application |
| `@keyv/bigmap` | 1.3.1 | MIT | build and test |
| `@keyv/serialize` | 1.1.1 | MIT | build and test |
| `@mapbox/jsonlint-lines-primitives` | 2.0.3 | MIT | application |
| `@mapbox/point-geometry` | 1.1.0 | ISC | application |
| `@mapbox/tiny-sdf` | 2.3.0 | BSD-2-Clause | application |
| `@mapbox/unitbezier` | 1.0.0 | BSD-2-Clause | application |
| `@mapbox/vector-tile` | 3.0.0 | BSD-3-Clause | application |
| `@maplibre/geojson-vt` | 6.3.1 | ISC | application |
| `@maplibre/maplibre-gl-style-spec` | 26.8.0 | ISC | application |
| `@maplibre/mlt` | 1.3.0 | (MIT OR Apache-2.0) | application |
| `@maplibre/vt-pbf` | 4.3.3 | MIT | application |
| `@mdi/font` | 7.4.47 | Apache-2.0 | application |
| `@napi-rs/wasm-runtime` | 1.2.6 | MIT | application |
| `@one-ini/wasm` | 0.2.1 | MIT | build and test |
| `@oxc-project/types` | 0.156.0 | MIT | application |
| `@playwright/test` | 1.65.1 | Apache-2.0 | build and test |
| `@redocly/ajv` | 8.11.2 | MIT | build and test |
| `@redocly/config` | 0.22.0 | MIT | build and test |
| `@redocly/openapi-core` | 1.34.24 | MIT | build and test |
| `@rolldown/binding-android-arm-eabi` | 1.2.19 | MIT | application |
| `@rolldown/binding-android-arm64` | 1.2.19 | MIT | application |
| `@rolldown/binding-darwin-arm64` | 1.2.19 | MIT | application |
| `@rolldown/binding-darwin-x64` | 1.2.19 | MIT | application |
| `@rolldown/binding-freebsd-x64` | 1.2.19 | MIT | application |
| `@rolldown/binding-linux-arm-gnueabihf` | 1.2.19 | MIT | application |
| `@rolldown/binding-linux-arm64-gnu` | 1.2.19 | MIT | application |
| `@rolldown/binding-linux-arm64-musl` | 1.2.19 | MIT | application |
| `@rolldown/binding-linux-ppc64-gnu` | 1.2.19 | MIT | application |
| `@rolldown/binding-linux-s390x-gnu` | 1.2.19 | MIT | application |
| `@rolldown/binding-linux-x64-gnu` | 1.2.19 | MIT | application |
| `@rolldown/binding-linux-x64-musl` | 1.2.19 | MIT | application |
| `@rolldown/binding-openharmony-arm64` | 1.2.19 | MIT | application |
| `@rolldown/binding-wasm32-wasi` | 1.2.19 | MIT | application |
| `@rolldown/binding-win32-arm64-msvc` | 1.2.19 | MIT | application |
| `@rolldown/binding-win32-x64-msvc` | 1.2.19 | MIT | application |
| `@rolldown/pluginutils` | 1.1.1 | MIT | application |
| `@tybys/wasm-util` | 0.10.4 | MIT | application |
| `@types/chai` | 5.2.3 | MIT | build and test |
| `@types/deep-eql` | 4.0.2 | MIT | build and test |
| `@types/esrecurse` | 4.3.1 | MIT | build and test |
| `@types/estree` | 1.0.10 | MIT | application |
| `@types/geojson` | 7946.0.16 | MIT | application |
| `@types/json-schema` | 7.0.15 | MIT | build and test |
| `@types/node` | 26.6.6 | MIT | application |
| `@typescript-eslint/eslint-plugin` | 8.72.2 | MIT | build and test |
| `@typescript-eslint/parser` | 8.72.2 | MIT | build and test |
| `@typescript-eslint/project-service` | 8.72.2 | MIT | build and test |
| `@typescript-eslint/scope-manager` | 8.72.2 | MIT | build and test |
| `@typescript-eslint/tsconfig-utils` | 8.72.2 | MIT | build and test |
| `@typescript-eslint/type-utils` | 8.72.2 | MIT | build and test |
| `@typescript-eslint/types` | 8.72.2 | MIT | build and test |
| `@typescript-eslint/typescript-estree` | 8.72.2 | MIT | build and test |
| `@typescript-eslint/utils` | 8.72.2 | MIT | build and test |
| `@typescript-eslint/visitor-keys` | 8.72.2 | MIT | build and test |
| `@vitejs/plugin-vue` | 6.0.11 | MIT | build and test |
| `@vitest/mocker` | 5.1.0 | MIT | build and test |
| `@vitest/spy` | 5.1.0 | MIT | build and test |
| `@volar/language-core` | 2.4.30 | MIT | build and test |
| `@volar/language-service` | 2.4.30 | MIT | build and test |
| `@volar/source-map` | 2.4.30 | MIT | build and test |
| `@volar/typescript` | 2.4.30 | MIT | build and test |
| `@vue-macros/common` | 3.1.4 | MIT | application |
| `@vue/compiler-core` | 3.5.45 | MIT | application |
| `@vue/compiler-dom` | 3.5.45 | MIT | application |
| `@vue/compiler-sfc` | 3.5.45 | MIT | application |
| `@vue/compiler-ssr` | 3.5.45 | MIT | application |
| `@vue/devtools-api` | 8.2.1 | MIT | application |
| `@vue/devtools-kit` | 8.2.1 | MIT | application |
| `@vue/devtools-shared` | 8.2.1 | MIT | application |
| `@vue/language-core` | 3.3.14 | MIT | build and test |
| `@vue/reactivity` | 3.5.45 | MIT | application |
| `@vue/runtime-core` | 3.5.45 | MIT | application |
| `@vue/runtime-dom` | 3.5.45 | MIT | application |
| `@vue/server-renderer` | 3.5.45 | MIT | application |
| `@vue/shared` | 3.5.45 | MIT | application |
| `@vue/test-utils` | 2.5.1 | MIT | build and test |
| `@vuetify/loader-shared` | 2.1.2 | MIT | application |
| `abbrev` | 5.0.0 | ISC | build and test |
| `acorn` | 8.19.0 | MIT | application |
| `acorn-jsx` | 5.3.2 | MIT | build and test |
| `agent-base` | 7.1.4 | MIT | build and test |
| `ajv` | 6.15.0 | MIT | build and test |
| `alien-signals` | 3.2.1 | MIT | build and test |
| `ansi-colors` | 4.1.3 | MIT | build and test |
| `argparse` | 2.0.1 | Python-2.0 | build and test |
| `assertion-error` | 2.0.1 | MIT | build and test |
| `ast-kit` | 2.2.0 | MIT | application |
| `ast-kit` | 3.0.1 | MIT | application |
| `ast-walker-scope` | 0.10.0 | MIT | application |
| `balanced-match` | 1.0.2 | MIT | build and test |
| `balanced-match` | 4.0.4 | MIT | build and test |
| `bcp-47` | 2.1.2 | MIT | build and test |
| `bcp-47-match` | 2.0.3 | MIT | build and test |
| `bidi-js` | 1.1.0 | MIT | application |
| `birpc` | 2.9.0 | MIT | application |
| `boolbase` | 1.0.0 | ISC | build and test |
| `brace-expansion` | 2.1.8 | MIT | build and test |
| `brace-expansion` | 5.0.13 | MIT | build and test |
| `cacheable` | 2.5.0 | MIT | build and test |
| `chai` | 6.3.0 | MIT | build and test |
| `change-case` | 5.4.4 | MIT | build and test |
| `chokidar` | 6.0.0 | MIT | application |
| `colorette` | 1.4.0 | MIT | build and test |
| `commander` | 14.0.3 | MIT | build and test |
| `confbox` | 0.1.8 | MIT | application |
| `confbox` | 0.3.2 | MIT | application |
| `config-chain` | 1.1.13 | MIT | build and test |
| `cross-spawn` | 7.0.6 | MIT | build and test |
| `csstype` | 3.2.3 | MIT | application |
| `data-urls` | 8.0.0 | MIT | build and test |
| `debug` | 4.4.3 | MIT | application |
| `decimal.js` | 10.6.0 | MIT | build and test |
| `deep-is` | 0.1.4 | MIT | build and test |
| `detect-libc` | 2.1.2 | Apache-2.0 | application |
| `earcut` | 3.2.4 | ISC | application |
| `editorconfig` | 3.0.2 | MIT | build and test |
| `entities` | 7.0.1 | BSD-2-Clause | application |
| `entities` | 9.0.0 | BSD-2-Clause | build and test |
| `es-module-lexer` | 2.3.2 | MIT | build and test |
| `escape-string-regexp` | 4.0.0 | MIT | build and test |
| `eslint` | 10.15.0 | MIT | build and test |
| `eslint-plugin-vue` | 10.12.1 | MIT | build and test |
| `eslint-scope` | 9.1.2 | BSD-2-Clause | build and test |
| `eslint-visitor-keys` | 3.4.3 | Apache-2.0 | build and test |
| `eslint-visitor-keys` | 5.0.1 | Apache-2.0 | build and test |
| `espree` | 11.2.0 | BSD-2-Clause | build and test |
| `esquery` | 1.7.0 | BSD-3-Clause | build and test |
| `esrecurse` | 4.3.0 | BSD-2-Clause | build and test |
| `estraverse` | 5.3.0 | BSD-2-Clause | build and test |
| `estree-walker` | 2.0.2 | MIT | application |
| `estree-walker` | 3.0.3 | MIT | build and test |
| `esutils` | 2.0.3 | BSD-2-Clause | build and test |
| `expect-type` | 1.4.0 | Apache-2.0 | build and test |
| `exsolve` | 1.2.0 | MIT | application |
| `fast-deep-equal` | 3.1.3 | MIT | build and test |
| `fast-json-stable-stringify` | 2.1.0 | MIT | build and test |
| `fast-levenshtein` | 2.0.6 | MIT | build and test |
| `fdir` | 6.5.0 | MIT | application |
| `file-entry-cache` | 11.1.5 | MIT | build and test |
| `find-up` | 5.0.0 | MIT | build and test |
| `flat-cache` | 6.1.23 | MIT | build and test |
| `flatted` | 3.4.4 | ISC | build and test |
| `fsevents` | 2.3.3 | MIT | application |
| `gl-matrix` | 3.4.4 | MIT | application |
| `glob` | 13.0.7 | BlueOak-1.0.0 | build and test |
| `glob-parent` | 6.0.2 | ISC | build and test |
| `globals` | 17.14.0 | MIT | build and test |
| `hashery` | 1.5.1 | MIT | build and test |
| `hookable` | 5.5.3 | MIT | application |
| `hookified` | 1.15.1 | MIT | build and test |
| `hookified` | 2.2.0 | MIT | build and test |
| `html-encoding-sniffer` | 7.0.0 | MIT | build and test |
| `https-proxy-agent` | 7.0.6 | MIT | build and test |
| `ignore` | 5.3.2 | MIT | build and test |
| `ignore` | 7.0.14 | MIT | build and test |
| `imurmurhash` | 0.1.4 | MIT | build and test |
| `index-to-position` | 1.2.0 | MIT | build and test |
| `ini` | 1.3.8 | ISC | build and test |
| `is-alphabetical` | 2.0.1 | MIT | build and test |
| `is-alphanumerical` | 2.0.1 | MIT | build and test |
| `is-decimal` | 2.0.1 | MIT | build and test |
| `is-extglob` | 2.1.1 | MIT | build and test |
| `is-glob` | 4.0.3 | MIT | build and test |
| `is-potential-custom-element-name` | 1.0.1 | MIT | build and test |
| `isexe` | 2.0.0 | ISC | build and test |
| `js-beautify` | 2.0.3 | MIT | build and test |
| `js-cookie` | 3.0.8 | MIT | build and test |
| `js-levenshtein` | 1.1.6 | MIT | build and test |
| `js-tokens` | 4.0.0 | MIT | build and test |
| `js-yaml` | 4.3.2 | MIT | build and test |
| `jsdom` | 31.0.1 | MIT | build and test |
| `json-schema-traverse` | 0.4.1 | MIT | build and test |
| `json-schema-traverse` | 1.0.0 | MIT | build and test |
| `json-stable-stringify-without-jsonify` | 1.0.1 | MIT | build and test |
| `json-stringify-pretty-compact` | 4.0.0 | MIT | application |
| `kdbush` | 4.1.0 | ISC | application |
| `keyv` | 5.6.0 | MIT | build and test |
| `levn` | 0.4.1 | MIT | build and test |
| `lightningcss` | 1.33.0 | MPL-2.0 | build and test |
| `lightningcss-android-arm64` | 1.33.0 | MPL-2.0 | build and test (platform binary) |
| `lightningcss-darwin-arm64` | 1.33.0 | MPL-2.0 | build and test (platform binary) |
| `lightningcss-darwin-x64` | 1.33.0 | MPL-2.0 | build and test (platform binary) |
| `lightningcss-freebsd-x64` | 1.33.0 | MPL-2.0 | build and test (platform binary) |
| `lightningcss-linux-arm-gnueabihf` | 1.33.0 | MPL-2.0 | build and test (platform binary) |
| `lightningcss-linux-arm64-gnu` | 1.33.0 | MPL-2.0 | build and test (platform binary) |
| `lightningcss-linux-arm64-musl` | 1.33.0 | MPL-2.0 | build and test (platform binary) |
| `lightningcss-linux-x64-gnu` | 1.33.0 | MPL-2.0 | build and test (platform binary) |
| `lightningcss-linux-x64-musl` | 1.33.0 | MPL-2.0 | build and test (platform binary) |
| `lightningcss-win32-arm64-msvc` | 1.33.0 | MPL-2.0 | build and test (platform binary) |
| `lightningcss-win32-x64-msvc` | 1.33.0 | MPL-2.0 | build and test (platform binary) |
| `local-pkg` | 1.2.1 | MIT | application |
| `local-pkg` | 2.0.0 | MIT | application |
| `locate-path` | 6.0.0 | MIT | build and test |
| `lru-cache` | 11.5.3 | BlueOak-1.0.0 | build and test |
| `magic-string` | 0.30.21 | MIT | application |
| `magic-string` | 1.4.5 | MIT | build and test |
| `magic-string-ast` | 1.0.3 | MIT | application |
| `maplibre-gl` | 6.18.0 | BSD-3-Clause | application |
| `minimatch` | 10.2.6 | BlueOak-1.0.0 | build and test |
| `minimatch` | 11.0.8 | BlueOak-1.0.0 | build and test |
| `minimatch` | 5.1.9 | ISC | build and test |
| `minimist` | 1.2.8 | MIT | application |
| `minipass` | 7.1.3 | BlueOak-1.0.0 | build and test |
| `mlly` | 1.8.2 | MIT | application |
| `ms` | 2.1.3 | MIT | application |
| `muggle-string` | 0.4.1 | MIT | application |
| `murmurhash-js` | 1.0.0 | MIT | application |
| `nanoid` | 3.3.20 | MIT | application |
| `natural-compare` | 1.4.0 | MIT | build and test |
| `nopt` | 10.0.1 | ISC | build and test |
| `nostics` | 1.3.0 | MIT | application |
| `nth-check` | 2.1.1 | BSD-2-Clause | build and test |
| `obug` | 3.0.0 | MIT | build and test |
| `openapi-typescript` | 7.13.0 | MIT | build and test |
| `optionator` | 0.9.4 | MIT | build and test |
| `p-limit` | 3.1.0 | MIT | build and test |
| `p-locate` | 5.0.0 | MIT | build and test |
| `parse-json` | 8.3.0 | MIT | build and test |
| `parse5` | 8.1.1 | MIT | build and test |
| `parse5-parser-stream` | 8.1.1 | MIT | build and test |
| `path-browserify` | 1.0.1 | MIT | build and test |
| `path-exists` | 4.0.0 | MIT | build and test |
| `path-key` | 3.1.1 | MIT | build and test |
| `path-scurry` | 2.0.2 | BlueOak-1.0.0 | build and test |
| `pathe` | 2.0.3 | MIT | application |
| `pbf` | 5.1.2 | BSD-3-Clause | application |
| `perfect-debounce` | 2.1.0 | MIT | application |
| `picocolors` | 1.1.1 | ISC | application |
| `picomatch` | 4.0.7 | MIT | application |
| `pinia` | 4.1.0 | MIT | application |
| `pkg-types` | 1.3.1 | MIT | application |
| `pkg-types` | 2.4.0 | MIT | application |
| `playwright` | 1.65.1 | Apache-2.0 | build and test |
| `playwright-core` | 1.65.1 | Apache-2.0 | build and test |
| `pluralize` | 8.0.0 | MIT | build and test |
| `postcss` | 8.5.33 | MIT | application |
| `postcss-selector-parser` | 7.2.1 | MIT | build and test |
| `potpack` | 2.1.0 | ISC | application |
| `prelude-ls` | 1.2.1 | MIT | build and test |
| `proto-list` | 1.2.4 | ISC | build and test |
| `protocol-buffers-schema` | 3.6.1 | MIT | application |
| `punycode` | 2.3.1 | MIT | build and test |
| `qified` | 0.10.1 | MIT | build and test |
| `quansync` | 0.2.11 | MIT | application |
| `quansync` | 1.0.0 | MIT | application |
| `quickselect` | 3.0.0 | ISC | application |
| `readdirp` | 5.1.1 | MIT | application |
| `require-from-string` | 2.0.2 | MIT | application |
| `resolve-protobuf-schema` | 2.1.0 | MIT | application |
| `rolldown` | 1.2.19 | MIT | application |
| `saxes` | 6.0.0 | ISC | build and test |
| `scule` | 1.3.0 | MIT | application |
| `semver` | 7.8.6 | ISC | build and test |
| `shebang-command` | 2.0.0 | MIT | build and test |
| `shebang-regex` | 3.0.0 | MIT | build and test |
| `source-map-js` | 1.2.2 | BSD-3-Clause | application |
| `std-env` | 4.3.0 | MIT | build and test |
| `supports-color` | 10.2.2 | MIT | build and test |
| `tinybench` | 6.2.3 | MIT | build and test |
| `tinyexec` | 1.3.1 | MIT | build and test |
| `tinyglobby` | 0.2.17 | MIT | application |
| `tinyqueue` | 3.0.0 | ISC | application |
| `tldts` | 7.5.3 | MIT | build and test |
| `tldts-core` | 7.5.3 | MIT | build and test |
| `tough-cookie` | 6.1.0 | BSD-3-Clause | build and test |
| `tr46` | 7.0.0 | MIT | build and test |
| `ts-api-utils` | 2.5.0 | MIT | build and test |
| `tslib` | 2.9.1 | 0BSD | application |
| `type-check` | 0.4.0 | MIT | build and test |
| `type-fest` | 4.41.0 | (MIT OR CC0-1.0) | build and test |
| `typescript` | 5.9.3 | Apache-2.0 | application |
| `typescript-eslint` | 8.72.2 | MIT | build and test |
| `ufo` | 1.6.4 | MIT | application |
| `undici` | 8.12.4 | MIT | build and test |
| `undici-types` | 8.9.0 | MIT | application |
| `unplugin` | 3.7.0 | MIT | application |
| `unplugin-utils` | 0.3.2 | MIT | application |
| `upath` | 2.0.1 | MIT | application |
| `uri-js` | 4.4.1 | BSD-2-Clause | build and test |
| `uri-js-replace` | 1.0.2 | MIT | build and test |
| `util-deprecate` | 1.0.2 | MIT | build and test |
| `vite` | 8.3.8 | MIT | application |
| `vite-plugin-vuetify` | 2.1.3 | MIT | application |
| `vitest` | 5.1.0 | MIT | build and test |
| `vscode-jsonrpc` | 10.0.0 | MIT | build and test |
| `vscode-languageserver-protocol` | 3.18.5 | MIT | build and test |
| `vscode-languageserver-textdocument` | 1.0.16 | MIT | build and test |
| `vscode-languageserver-types` | 3.18.5 | MIT | build and test |
| `vscode-uri` | 3.2.0 | MIT | build and test |
| `vue` | 3.5.45 | MIT | application |
| `vue-component-type-helpers` | 3.3.14 | MIT | build and test |
| `vue-eslint-parser` | 10.4.2 | MIT | build and test |
| `vue-router` | 5.5.1 | MIT | application |
| `vue-tsc` | 3.3.14 | MIT | build and test |
| `vuetify` | 3.13.7 | MIT | application |
| `w3c-xmlserializer` | 6.0.0 | MIT | build and test |
| `webidl-conversions` | 8.0.1 | BSD-2-Clause | build and test |
| `webpack-virtual-modules` | 0.6.2 | MIT | application |
| `whatwg-mimetype` | 5.0.0 | MIT | build and test |
| `whatwg-url` | 17.3.0 | MIT | build and test |
| `which` | 2.0.2 | ISC | build and test |
| `why-is-node-running` | 3.2.1 | MIT | build and test |
| `word-wrap` | 1.2.5 | MIT | build and test |
| `xml-name-validator` | 5.0.0 | Apache-2.0 | build and test |
| `xmlchars` | 2.2.0 | MIT | build and test |
| `yaml-ast-parser` | 0.0.43 | Apache-2.0 | build and test |
| `yargs-parser` | 21.1.1 | ISC | build and test |
| `yocto-queue` | 0.1.0 | MIT | build and test |
