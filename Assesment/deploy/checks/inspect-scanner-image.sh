#!/usr/bin/env bash
# Inspection of the scanner image and of the running stack (TASK-028; C-2, C-3,
# NFR-SEC-2). Run it on the host, with the stack up:
#
#     cd Assesment/deploy && ./checks/inspect-scanner-image.sh
#
# It checks that
#   1. the scanner image holds no database driver and no web framework, and
#      none of the application's database, web or orchestrator code (C-3);
#   2. the scanner image holds no external scanner or diagnostic binary (C-2);
#   3. the scanner process runs with a non-zero user ID and exactly one
#      effective capability, NET_RAW;
#   4. "no-new-privileges" is set on every container except the scanner;
#   5. RabbitMQ is published on the host's loopback address only, and port 443
#      is the only port published to the outside.
#
# Exit status: 0 if every check passed, 1 otherwise.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$DEPLOY_DIR/.env}"
PROJECT="${COMPOSE_PROJECT_NAME:-ndm}"
IMAGE="${SCANNER_IMAGE:-ndm-scanner:${NDM_VERSION:-1.0.0-rc.1}}"

failures=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); }

compose() { docker compose --project-directory "$DEPLOY_DIR" --env-file "$ENV_FILE" "$@"; }
in_image() { docker run --rm --network none --entrypoint "$1" "$IMAGE" "${@:2}"; }

# --- 1. No database driver, no web framework, no code of the other processes ---
forbidden_modules="sqlalchemy asyncpg psycopg psycopg2 alembic fastapi starlette uvicorn ndm.db ndm.api ndm.orchestrator"
for module in $forbidden_modules; do
    if in_image python -c "import importlib, sys; importlib.import_module('$module')" > /dev/null 2>&1; then
        fail "the scanner image can import $module"
    else
        pass "the scanner image has no $module"
    fi
done
if in_image python -c "import ndm.scanner.main, ndm.core.scope" > /dev/null 2>&1; then
    pass "the scanner image can import its own code"
else
    fail "the scanner image cannot import ndm.scanner.main"
fi

# --- 2. No external scanner or diagnostic binary ---------------------------------
binaries="nmap masscan zmap naabu unicornscan ping ping6 fping arping traceroute tracepath mtr hping3 nping nc ncat netcat socat tcpdump tshark dig nslookup curl wget"
found="$(in_image sh -c "for b in $binaries; do command -v \"\$b\" || true; done")"
if [ -z "$found" ]; then
    pass "the scanner image holds none of: $binaries"
else
    fail "the scanner image holds: $(echo "$found" | tr '\n' ' ')"
fi

# --- 3. The scanner process: non-root, exactly one capability --------------------
scanner="$(compose ps -q scanner)"
[ -n "$scanner" ] || { echo "the scanner service is not running" >&2; exit 1; }
# Process 1 is Docker's init (init: true); the scanner is its child. Found by
# its command line, by Python, the one tool the image has for it.
pid="$(docker exec "$scanner" python -c 'import os
print(next(p for p in os.listdir("/proc") if p.isdigit() and p != str(os.getpid())
      and b"ndm.scanner.main" in open(f"/proc/{p}/cmdline", "rb").read()))')"
status="$(docker exec "$scanner" cat "/proc/$pid/status")"
uid="$(echo "$status" | awk '/^Uid:/ { print $3 }')"   # the effective user ID
effective="$(echo "$status" | awk '/^CapEff:/ { print $2 }')"
if [ "$uid" != "0" ]; then
    pass "the scanner process runs as user $uid"
else
    fail "the scanner process runs as root"
fi
# Bit 13 is CAP_NET_RAW: 1 << 13 = 0x2000.
if [ "$effective" = "0000000000002000" ]; then
    pass "the scanner process has exactly one effective capability, NET_RAW"
else
    fail "the scanner process has the effective capabilities $effective, expected 0000000000002000"
fi

# --- 4. no-new-privileges everywhere but on the scanner --------------------------
for container in $(docker ps -q --filter "label=com.docker.compose.project=$PROJECT"); do
    service="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$container")"
    options="$(docker inspect -f '{{.HostConfig.SecurityOpt}}' "$container")"
    case "$service:$options" in
        scanner:*no-new-privileges*) fail "the scanner has no-new-privileges, which switches its file capability off" ;;
        scanner:*) pass "the scanner runs without no-new-privileges, as its file capability needs" ;;
        *:*no-new-privileges*) pass "$service has no-new-privileges" ;;
        *) fail "$service runs without no-new-privileges" ;;
    esac
done

# --- 5. Published ports ----------------------------------------------------------
rabbitmq="$(compose ps -q rabbitmq)"
bindings="$(docker port "$rabbitmq")"
if [ -n "$bindings" ] && ! echo "$bindings" | grep -v -q -- '-> 127\.0\.0\.1:'; then
    pass "RabbitMQ is published on the loopback address only"
else
    fail "RabbitMQ is published as: $(echo "$bindings" | tr '\n' ' ')"
fi

outside="$(docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Ports}}' \
    | tr ',' '\n' | grep -v '127\.0\.0\.1:' | sed -n 's/.*:\([0-9][0-9]*\)->.*/\1/p' \
    | sort -u | tr '\n' ' ')"
if [ "$outside" = "443 " ]; then
    pass "port 443 is the only port published to the outside"
else
    fail "ports published to the outside are '${outside% }', expected only 443"
fi

echo
if [ "$failures" -eq 0 ]; then
    echo "Scanner image and stack inspection: PASSED"
else
    echo "Scanner image and stack inspection: FAILED ($failures findings)"
    exit 1
fi
