#!/usr/bin/env bash
# Inspection for check 1A.8 (secrets and exposure). Run it on the host, against
# the running stack:
#
#     cd Assesment/deploy && ./checks/inspect-1a.sh
#
# It checks that
#   1. no configured secret value occurs in any container log line;
#   2. no configured secret value occurs in any layer or in the configuration
#      of the images the stack built;
#   3. port 443 is the only port published to the outside (a port published
#      on the host's loopback address only, as RabbitMQ's is, does not count);
#   4. PostgreSQL is on internal networks only and does not answer from
#      outside them.
#
# The secret values are read from the environment file. They are compared, and
# never printed. Exit status: 0 if every check passed, 1 otherwise.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$DEPLOY_DIR/.env}"
PROJECT="${COMPOSE_PROJECT_NAME:-ndm}"

failures=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); }

compose() { docker compose --project-directory "$DEPLOY_DIR" --env-file "$ENV_FILE" "$@"; }

# --- The configured secret values -------------------------------------------
# Every variable in the environment file whose name says it is a secret.
[ -r "$ENV_FILE" ] || { echo "cannot read $ENV_FILE" >&2; exit 1; }
secrets=()
while IFS='=' read -r name value; do
    case "$name" in
        *PASSWORD* | *SECRET* | *KEY* | *TOKEN*)
            value="${value%\"}"; value="${value#\"}"
            [ -n "$value" ] && secrets+=("$value")
            ;;
    esac
done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE")
[ "${#secrets[@]}" -gt 0 ] || { echo "no secret found in $ENV_FILE" >&2; exit 1; }
echo "Checking ${#secrets[@]} secret values from $ENV_FILE."

# True if standard input holds any of the secret values.
holds_secret() {
    local patterns
    patterns="$(mktemp)"
    printf '%s\n' "${secrets[@]}" > "$patterns"
    local found=1
    if grep -a -q -F -f "$patterns"; then found=0; fi
    rm -f "$patterns"
    return "$found"
}

containers="$(docker ps -a -q --filter "label=com.docker.compose.project=$PROJECT")"
[ -n "$containers" ] || { echo "no container of project '$PROJECT' found; is the stack up?" >&2; exit 1; }

# --- 1. Logs ----------------------------------------------------------------
for container in $containers; do
    name="$(docker inspect -f '{{.Name}}' "$container")"
    if docker logs "$container" 2>&1 | holds_secret; then
        fail "a secret value occurs in the log of ${name#/}"
    else
        pass "no secret value in the log of ${name#/}"
    fi
done

# --- 2. Image layers and image configuration ---------------------------------
# "docker save" writes every layer and the image configuration; each file in
# it is searched, compressed or not.
images="$(for container in $containers; do docker inspect -f '{{.Config.Image}}' "$container"; done | sort -u)"
for image in $images; do
    workdir="$(mktemp -d)"
    docker save "$image" | tar -x -C "$workdir"
    hit=0
    while IFS= read -r -d '' file; do
        if gzip -t "$file" 2>/dev/null; then
            if gzip -dc "$file" | holds_secret; then hit=1; fi
        elif holds_secret < "$file"; then
            hit=1
        fi
    done < <(find "$workdir" -type f -print0)
    rm -rf "$workdir"
    if [ "$hit" -eq 1 ]; then
        fail "a secret value occurs in image $image"
    else
        pass "no secret value in any layer of image $image"
    fi
done

# --- 3. Published ports -----------------------------------------------------
# A published port shows as "host-address:PORT->container-port"; a port that
# is only exposed has no arrow.
published="$(docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Ports}}' \
    | tr ',' '\n' | grep -v '127\.0\.0\.1:' | sed -n 's/.*:\([0-9][0-9]*\)->.*/\1/p' \
    | sort -u | tr '\n' ' ')"
if [ "$published" = "443 " ]; then
    pass "port 443 is the only port published to the outside"
else
    fail "ports published to the outside are '${published% }', expected only 443"
fi

# --- 4. PostgreSQL is not reachable from outside the internal network --------
postgres="$(compose ps -q postgres)"
[ -n "$postgres" ] || { echo "the postgres service is not running" >&2; exit 1; }

if [ -z "$(docker port "$postgres")" ]; then
    pass "postgres publishes no port"
else
    fail "postgres publishes a port: $(docker port "$postgres" | tr '\n' ' ')"
fi

for network in $(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$postgres"); do
    if [ "$(docker network inspect -f '{{.Internal}}' "$network")" = "true" ]; then
        pass "postgres network $network is internal"
    else
        fail "postgres is attached to $network, which is not an internal network"
    fi
done

# A connection attempt from a container that is not on the internal network.
proxy_image="$(docker inspect -f '{{.Config.Image}}' "$(compose ps -q proxy)")"
postgres_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$postgres" | awk '{print $1}')"
if docker run --rm --network bridge --entrypoint nc "$proxy_image" -z -w 3 "$postgres_ip" 5432 2>/dev/null; then
    fail "postgres ($postgres_ip:5432) answers from outside the internal network"
else
    pass "postgres does not answer from outside the internal network"
fi

echo
if [ "$failures" -eq 0 ]; then
    echo "Check 1A.8: PASSED"
else
    echo "Check 1A.8: FAILED ($failures findings)"
    exit 1
fi
