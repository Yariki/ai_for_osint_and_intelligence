#!/usr/bin/env bash
# Inspection for check 2.14 (licences: NFR-LIC-1, NFR-LIC-3). Run it on the
# host, from a checkout, against the running stack:
#
#     cd Assesment/deploy && ./checks/inspect-2.sh
#
# It checks that
#   1. no GeoLite2 database is in the repository, tracked or not;
#   2. no GeoLite2 database is in the file system of any image the stack runs;
#   3. the application served by the proxy carries the MaxMind attribution,
#      and the tile attribution element of the map;
#   4. a tile source is configured together with its attribution, or not at
#      all; without one, the browser is given no tile URL.
#
# Exit status: 0 if every check passed, 1 otherwise.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$DEPLOY_DIR/.env}"
PROJECT="${COMPOSE_PROJECT_NAME:-ndm}"
ATTRIBUTION='This product includes GeoLite Data created by MaxMind'

failures=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; failures=$((failures + 1)); }

# A GeoLite2 database: a MaxMind DB file, or anything named after GeoLite2.
is_database() { grep -a -i -E '(\.mmdb$|geolite2?[-_]?(city|asn|country)[^/]*$)'; }

# --- 1. The repository --------------------------------------------------------
found="$(cd "$REPO_DIR" && find . \( -name node_modules -o -name .venv -o -name .git \) -prune -o -type f -print | is_database || true)"
if [ -z "$found" ]; then
    pass "no GeoLite2 database in the repository"
else
    fail "GeoLite2 database in the repository: $(echo "$found" | head -5 | tr '\n' ' ')"
fi

# --- 2. The images --------------------------------------------------------------
containers="$(docker ps -a -q --filter "label=com.docker.compose.project=$PROJECT")"
[ -n "$containers" ] || { echo "no container of project '$PROJECT' found; is the stack up?" >&2; exit 1; }
images="$(docker inspect --format '{{.Config.Image}}' $containers | sort -u)"
for image in $images; do
    # The flattened file system of a container made from the image, not started.
    container="$(docker create "$image" true)"
    listing="$(docker export "$container" | tar -t 2>/dev/null || true)"
    docker rm "$container" > /dev/null
    hits="$(echo "$listing" | is_database || true)"
    if [ -z "$hits" ]; then
        pass "no GeoLite2 database in image $image"
    else
        fail "GeoLite2 database in image $image: $(echo "$hits" | head -3 | tr '\n' ' ')"
    fi
done

# --- 3. Attribution in the application ----------------------------------------------
proxy="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=proxy" | head -1)"
if [ -z "$proxy" ]; then
    fail "the proxy is not running; the application cannot be inspected"
else
    bundle="$(docker exec "$proxy" sh -c 'cat /srv/assets/*.js')"
    if grep -q -F "$ATTRIBUTION" <<< "$bundle"; then
        pass "the application shows the MaxMind attribution"
    else
        fail "the MaxMind attribution is not in the application"
    fi
    if grep -q -F 'tile-attribution' <<< "$bundle"; then
        pass "the map shows the tile provider's attribution"
    else
        fail "the tile attribution element is not in the application"
    fi
fi

# --- 4. Tile source ------------------------------------------------------------------
value() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
url="$(value NDM_TILE_URL_TEMPLATE)"
credit="$(value NDM_TILE_ATTRIBUTION)"
api="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=api" | head -1)"
seen="$(docker exec "$api" printenv NDM_TILE_URL_TEMPLATE 2>/dev/null || true)"
if [ -z "$url" ]; then
    if [ -z "$seen" ]; then
        pass "no tile source configured: the map is off and contacts no tile server"
    else
        fail "the API has a tile URL that .env does not set"
    fi
elif [ -n "$credit" ] && [ "$seen" = "$url" ]; then
    pass "tile source configured with its attribution: ${url%%\?*}"
else
    fail "a tile URL is set without its attribution, or the API does not have it"
fi

echo
if [ "$failures" -eq 0 ]; then echo "All checks passed."; else echo "$failures check(s) failed."; fi
[ "$failures" -eq 0 ]
