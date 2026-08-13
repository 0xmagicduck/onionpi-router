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
# Same exclusions for packaging: the test suite loads packaging/agent/*.py by
# path, so a build that follows a test run finds bytecode caches beside the
# installer. They would travel into /opt/onionpi/current/agent, land in the
# digest the appliance pins the node download to, and no GitHub archive would
# ever match it — every node install refusing to run, correctly, for a reason
# nobody could see.
rsync -a --exclude '__pycache__' --exclude '.pytest_cache' packaging "$TREE/"

# Cheap, and it fails the build rather than an operator's install.
if find "$TREE/packaging" "$TREE/backend" -name '__pycache__' -print -quit | grep -q .; then
  printf 'Cache de bytecode dans l’arborescence de publication.\n' >&2
  exit 1
fi
install -d "$TREE/frontend"
rsync -a frontend/dist "$TREE/frontend/"
install -m 0644 README.md LICENSE "$TREE/"
# The version travels inside the archive: onionpi-update refuses an archive
# whose VERSION disagrees with the file name it downloaded.
printf '%s\n' "$VERSION" >"$TREE/VERSION"
chmod 0755 "$TREE"/packaging/*.sh "$TREE"/packaging/assets/*.sh 2>/dev/null || true

# Bookworm uses CPython 3.11 and Trixie uses 3.13. Both arm64 wheelhouses are
# built before the archive is signed, so install.sh --upgrade never reaches a
# package index after the OpenPGP check.
install -d "$TREE/wheelhouse"
for python_tag in cp311 cp313; do
  destination="$TREE/wheelhouse/$python_tag-arm64"
  install -d "$destination"
  python3 -m pip download \
    --disable-pip-version-check \
    --dest "$destination" \
    --no-deps \
    --only-binary=:all: \
    --platform manylinux_2_28_aarch64 \
    --platform manylinux2014_aarch64 \
    --implementation cp \
    --python-version "${python_tag#cp}" \
    --abi "$python_tag" --abi abi3 --abi none \
    -r backend/requirements.txt
done
(cd "$TREE" && find wheelhouse -type f -name '*.whl' -print0 \
  | sort -z | xargs -0 sha256sum >wheelhouse/SHA256SUMS)

python3 scripts/generate-sbom.py "$VERSION" "$TREE/SBOM.spdx.json"

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

install -m 0644 "$TREE/SBOM.spdx.json" "dist/onionpi-$VERSION.spdx.json"
(cd dist && sha256sum "onionpi-$VERSION.tar.gz" "onionpi-$VERSION.spdx.json" >SHA256SUMS)

printf 'dist/onionpi-%s.tar.gz\n' "$VERSION"
cat dist/SHA256SUMS
