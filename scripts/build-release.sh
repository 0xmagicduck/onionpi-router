#!/usr/bin/env bash
set -Eeuo pipefail

# Builds the archive an OnionPi downloads when it updates itself.
#
# The same script runs in the release workflow and on a laptop, so what is
# published can be reproduced and inspected before it reaches an appliance.
#
#   ./scripts/build-release.sh 0.2.0
#   dist/onionpi-0.2.0.tar.gz
#   dist/SHA256SUMS

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

VERSION="${1:-$(head -n 1 VERSION | tr -d '[:space:]')}"
if [[ ! "$VERSION" =~ ^[0-9A-Za-z.-]{1,40}$ ]]; then
  printf 'Version invalide: %s\n' "$VERSION" >&2
  exit 1
fi

if [[ ! -s frontend/dist/index.html ]]; then
  printf 'frontend/dist absent: lancez « npm ci && npm run build » dans frontend/.\n' >&2
  exit 1
fi

STAGE="$(mktemp -d)"
TREE="$STAGE/onionpi-$VERSION"
trap 'rm -rf "$STAGE"' EXIT

install -d "$TREE"
# The updater re-runs packaging/install.sh --upgrade from this tree, so it must
# hold everything that script reads, and nothing else: no test suite, no source
# of the interface, no design mockups, no local state.
rsync -a --exclude '__pycache__' --exclude '.pytest_cache' --exclude 'tests' \
  backend "$TREE/"
rsync -a packaging "$TREE/"
install -d "$TREE/frontend"
rsync -a frontend/dist "$TREE/frontend/"
install -m 0644 README.md LICENSE "$TREE/"
# The version travels inside the archive: onionpi-update refuses an archive
# whose VERSION disagrees with the file name it downloaded.
printf '%s\n' "$VERSION" >"$TREE/VERSION"
chmod 0755 "$TREE"/packaging/*.sh "$TREE"/packaging/assets/*.sh 2>/dev/null || true

rm -rf dist
install -d dist

# Reproducible: same input, same bytes, whoever builds it. Only GNU tar can do
# that, so a macOS laptop without gtar produces a usable but not byte-identical
# archive — the release itself is always built on Linux.
TAR="tar"
command -v gtar >/dev/null && TAR="gtar"
if "$TAR" --version 2>/dev/null | grep -q GNU; then
  "$TAR" --sort=name \
    --owner=0 --group=0 --numeric-owner \
    --mtime="@${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date -u +%s)}" \
    -czf "dist/onionpi-$VERSION.tar.gz" -C "$STAGE" "onionpi-$VERSION"
else
  printf 'GNU tar absent: archive non reproductible (brew install gnu-tar).\n' >&2
  "$TAR" -czf "dist/onionpi-$VERSION.tar.gz" -C "$STAGE" "onionpi-$VERSION"
fi

(cd dist && sha256sum "onionpi-$VERSION.tar.gz" >SHA256SUMS)

printf 'dist/onionpi-%s.tar.gz\n' "$VERSION"
cat dist/SHA256SUMS
