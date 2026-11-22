#!/usr/bin/env bash
# Build the release images locally, as the release workflow does.
#
#   deploy/release/build-images.sh [RELEASE]
#
# RELEASE defaults to the one in deploy/release/VERSIONS.md. Run it from a
# checkout of the tagged commit (ndm-v<RELEASE>); it refuses a working tree
# with uncommitted changes unless ALLOW_DIRTY=1, because the images would then
# not be those of the tag. It builds the four images of the project, pulls the
# third-party images the Compose file names, checks the version label of each
# built image, and writes the digest of every image to
# deploy/release/digests-<RELEASE>.txt.
#
# Needs: docker, git. Run from anywhere.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # the Assesment/ directory
cd "$ROOT"

VERSIONS="deploy/release/VERSIONS.md"
RELEASE="${1:-$(sed -n 's/^Release: `\(.*\)`$/\1/p' "$VERSIONS")}"
[ -n "$RELEASE" ] || { echo "no release given and none found in $VERSIONS" >&2; exit 1; }
if ! grep -q "^Release: \`$RELEASE\`$" "$VERSIONS"; then
    echo "release $RELEASE is not the one in $VERSIONS" >&2
    exit 1
fi

if [ "${ALLOW_DIRTY:-0}" != "1" ] && [ -n "$(git status --porcelain -- .)" ]; then
    echo "the working tree has uncommitted changes; commit them or set ALLOW_DIRTY=1" >&2
    exit 1
fi
REVISION="$(git rev-parse HEAD)"
if ! git tag --points-at HEAD | grep -qx "ndm-v$RELEASE"; then
    echo "note: HEAD is not tagged ndm-v$RELEASE; the images are labelled with commit $REVISION" >&2
fi

build() {   # build <image> <dockerfile> <context>
    echo "building $1:$RELEASE"
    docker build --pull \
        --build-arg "NDM_VERSION=$RELEASE" \
        --build-arg "NDM_REVISION=$REVISION" \
        --file "$2" --tag "$1:$RELEASE" "$3"
    label="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$1:$RELEASE")"
    if [ "$label" != "$RELEASE" ]; then
        echo "$1:$RELEASE has the version label '$label'" >&2
        exit 1
    fi
}

build ndm-backend deploy/Dockerfile.backend .
build ndm-scanner deploy/Dockerfile.scanner .
build ndm-proxy deploy/Dockerfile.proxy .
build ndm-scanner-netinit deploy/scanner-netinit/Dockerfile deploy/scanner-netinit

# The third-party images of the stack: every image: line that is not the project's own.
third_party="$(sed -n 's/^ *image: *\([^ ]*\) *$/\1/p' deploy/compose.yaml | grep -v '^ndm-' | sort -u)"
for image in $third_party; do
    echo "pulling $image"
    docker pull --quiet "$image" > /dev/null
done

DIGESTS="deploy/release/digests-$RELEASE.txt"
{
    echo "# Images of release $RELEASE, commit $REVISION, built $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    for image in ndm-backend ndm-scanner ndm-proxy ndm-scanner-netinit; do
        echo "$image:$RELEASE $(docker image inspect --format '{{.Id}}' "$image:$RELEASE")"
    done
    for image in $third_party; do
        echo "$image $(docker image inspect --format '{{join .RepoDigests " "}}' "$image")"
    done
} > "$DIGESTS"
echo "wrote $DIGESTS"
