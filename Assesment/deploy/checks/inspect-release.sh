#!/usr/bin/env bash
# Inspection of the release candidate for checks 5.8 (operability) and 5.9
# (container and network posture). Run it on the host, with the stack of the
# release candidate up and at least one scan and one traceroute done since it
# started (the acceptance run leaves both):
#
#     cd Assesment/deploy && ./checks/inspect-release.sh [--dependencies]
#
# It checks that
#   1. every container runs the release image named in release/VERSIONS.md,
#      with that version in its label;
#   2. every log line about a scan or a traceroute carries its identifier
#      (scan_id or run_id), in the api, the orchestrator and the scanner;
#   3. the orchestrator's metrics include every metric of NFR-OPS-3;
#   4. every long-running container is healthy and every container has a
#      memory and a CPU limit;
#   5. users, capabilities, no-new-privileges and read-only root file systems
#      are those of architecture section 14 (the declared exceptions:
#      scanner-netinit runs as root with NET_ADMIN and exits; the scanner has
#      NET_RAW and no no-new-privileges; RabbitMQ's root is writable);
#   6. it runs the existing inspections of the published ports, the secrets and
#      the scanner image (inspect-1a.sh, inspect-scanner-image.sh);
#   7. with --dependencies, the dependency scans of both lockfiles report no
#      finding of high or critical severity, the blocking level proposed in
#      docs/release/posture-report.md. They need network access.
#
# Exit status: 0 if every check passed, 1 otherwise.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ASSESMENT="$(cd "$DEPLOY_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$DEPLOY_DIR/.env}"
PROJECT="${COMPOSE_PROJECT_NAME:-ndm}"
RELEASE="$(sed -n 's/^Release: `\(.*\)`$/\1/p' "$DEPLOY_DIR/release/VERSIONS.md")"

failures=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); }
compose() { docker compose --project-directory "$DEPLOY_DIR" --env-file "$ENV_FILE" "$@"; }

containers="$(docker ps -a -q --filter "label=com.docker.compose.project=$PROJECT")"
[ -n "$containers" ] || { echo "no container of project '$PROJECT' found; is the stack up?" >&2; exit 1; }
service_of() { docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$1"; }

# --- 1. Release images ----------------------------------------------------------------
for container in $containers; do
    service="$(service_of "$container")"
    image="$(docker inspect -f '{{.Config.Image}}' "$container")"
    case "$image" in
        ndm-*)
            label="$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' "$container")"
            if [ "$image" = "${image%%:*}:$RELEASE" ] && [ "$label" = "$RELEASE" ]; then
                pass "$service runs $image, labelled $label"
            else
                fail "$service runs $image labelled '$label', expected release $RELEASE"
            fi
            ;;
        *)
            if grep -q "| \`${image%%:*}\` | \`${image#*:}\` |" "$DEPLOY_DIR/release/VERSIONS.md"; then
                pass "$service runs $image, listed in VERSIONS.md"
            else
                fail "$service runs $image, which VERSIONS.md does not list"
            fi
            ;;
    esac
done

# --- 2. Log identifiers ---------------------------------------------------------------
# A line is about a scan or a traceroute when its event names one; such a line
# must carry scan_id or run_id. The lines are read by Python in the
# orchestrator's container, so the host needs nothing but Docker.
for service in api orchestrator scanner; do
    result="$(compose logs --no-log-prefix --no-color "$service" 2>/dev/null | compose exec -T orchestrator python -c '
import json, sys
PREFIXES = ("scan_", "chunk", "findings", "phase", "enrich", "classif", "diff_", "traceroute", "hop")
GLOBAL = ("scanner_", "scan_profile")
about, missing = 0, []
for line in sys.stdin:
    try:
        entry = json.loads(line)
    except ValueError:
        continue
    event = str(entry.get("event", ""))
    if not event.startswith(PREFIXES) or event.startswith(GLOBAL):
        continue
    about += 1
    if entry.get("scan_id") is None and entry.get("run_id") is None:
        missing.append(event)
print(about, len(missing), ",".join(sorted(set(missing))[:5]))
')"
    read -r about missing events <<< "$result"
    if [ "$about" -eq 0 ]; then
        fail "$service has logged nothing about a scan or a traceroute yet; run one first"
    elif [ "$missing" -eq 0 ]; then
        pass "$service: all $about log lines about scans and traceroutes carry their identifier"
    else
        fail "$service: $missing of $about lines lack scan_id or run_id (events: $events)"
    fi
done

# --- 3. Metrics of NFR-OPS-3 ----------------------------------------------------------
metrics="$(compose exec -T orchestrator python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9108/metrics').read().decode())")"
for metric in ndm_scanner_probe_rate_pps ndm_queue_depth ndm_scan_duration_seconds \
              ndm_scan_outcomes ndm_dataset_age_seconds; do
    if grep -q "^$metric" <<< "$metrics"; then
        pass "metric $metric is exported"
    else
        fail "metric $metric is missing"
    fi
done

# --- 4. Health and resource limits ----------------------------------------------------
for container in $containers; do
    service="$(service_of "$container")"
    state="$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' "$container")"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container")"
    case "$service" in
        migrate | scanner-netinit)
            [ "$state" = "exited 0" ] && pass "$service ran and exited 0" || fail "$service is '$state'"
            ;;
        *)
            [ "$health" = "healthy" ] && pass "$service is healthy" || fail "$service health is '$health'"
            ;;
    esac
    limits="$(docker inspect -f '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}' "$container")"
    read -r memory cpus <<< "$limits"
    if [ "$memory" -gt 0 ] && [ "$cpus" -gt 0 ]; then
        pass "$service has a memory limit ($memory bytes) and a CPU limit"
    else
        fail "$service has no memory or no CPU limit ($limits)"
    fi
done

# --- 5. Users, capabilities, privileges, read-only file systems -------------------------
for container in $containers; do
    service="$(service_of "$container")"
    user="$(docker inspect -f '{{.Config.User}}' "$container")"
    drop="$(docker inspect -f '{{json .HostConfig.CapDrop}}' "$container")"
    add="$(docker inspect -f '{{json .HostConfig.CapAdd}}' "$container")"
    add="${add//CAP_/}"   # Docker 29 reports CAP_NET_RAW for NET_RAW
    options="$(docker inspect -f '{{json .HostConfig.SecurityOpt}}' "$container")"
    readonly_root="$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$container")"

    case "$service" in
        scanner-netinit) [ -z "$user" ] || [ "$user" = "root" ] || [ "$user" = "0" ] \
            && pass "$service runs as root, as declared" || fail "$service runs as '$user'" ;;
        *) case "$user" in
               "" | root | 0 | 0:*) fail "$service runs as root" ;;
               *) pass "$service runs as user $user" ;;
           esac ;;
    esac

    case "$drop" in *ALL*) pass "$service drops all capabilities" ;; *) fail "$service cap_drop is $drop" ;; esac
    case "$service" in
        scanner) expected='["NET_RAW"]' ;;
        scanner-netinit) expected='["NET_ADMIN"]' ;;
        *) expected='null' ;;
    esac
    if [ "$add" = "$expected" ] || { [ "$expected" = "null" ] && [ "$add" = "[]" ]; }; then
        pass "$service adds capabilities $add"
    else
        fail "$service adds $add, expected $expected"
    fi

    if [ "$service" = "scanner" ]; then
        case "$options" in *no-new-privileges*) fail "scanner has no-new-privileges, which removes NET_RAW" ;;
                           *) pass "scanner has no no-new-privileges, as declared" ;; esac
    else
        case "$options" in *no-new-privileges*) pass "$service has no-new-privileges" ;;
                           *) fail "$service lacks no-new-privileges" ;; esac
    fi

    if [ "$service" = "rabbitmq" ]; then
        pass "rabbitmq has a writable root, as declared"
    elif [ "$readonly_root" = "true" ]; then
        pass "$service has a read-only root file system"
    else
        fail "$service has a writable root file system"
    fi
done

# --- 6. The earlier inspections: ports, secrets, the scanner image ---------------------
for check in inspect-1a.sh inspect-scanner-image.sh; do
    if "$DEPLOY_DIR/checks/$check" > /tmp/ndm-$check.log 2>&1; then
        pass "$check passed"
    else
        fail "$check failed; see /tmp/ndm-$check.log"
    fi
done

# --- 7. Dependency scans ----------------------------------------------------------------
if [ "${1:-}" = "--dependencies" ]; then
    requirements="$(mktemp)"
    (cd "$ASSESMENT/backend" && uv export --locked --all-extras --no-emit-project \
        --format requirements-txt --output-file "$requirements" > /dev/null)
    if (cd "$ASSESMENT/backend" && uv run --no-sync pip-audit --requirement "$requirements" \
            --require-hashes --disable-pip); then
        pass "pip-audit: no known vulnerability in uv.lock"
    else
        fail "pip-audit reports vulnerabilities in uv.lock"
    fi
    rm -f "$requirements"
    if (cd "$ASSESMENT/frontend" && npm audit --audit-level=high); then
        pass "npm audit: nothing of high or critical severity"
    else
        fail "npm audit reports findings of high or critical severity"
    fi
fi

echo
if [ "$failures" -eq 0 ]; then
    echo "Checks 5.8 and 5.9: PASSED"
else
    echo "Checks 5.8 and 5.9: FAILED ($failures findings)"
    exit 1
fi
