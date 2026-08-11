#!/usr/bin/env bash
set -Eeuo pipefail

# Moves VERSION, package manifests and __init__.py together.
#
#   ./scripts/set-version.sh 0.2.0     # prepare a release
#   git tag v0.2.0 && git push --tags  # publish it
#   ./scripts/set-version.sh 0.3.0     # right after: edge builds now lead to 0.3.0
#
# That last step matters: an edge build is published as <VERSION>-edge.<n>, a
# pre-release of the version being prepared. Leaving VERSION on the version
# just released would make every edge build older than what is already
# installed, and the edge channel would go silent.

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

VERSION="${1:-}"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  printf 'Usage: %s MAJEUR.MINEUR.CORRECTIF\n' "$0" >&2
  exit 2
fi

printf '%s\n' "$VERSION" >VERSION
# Only the top-level "version" key, never a dependency range.
python3 - "$VERSION" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path("frontend/package.json")
document = json.loads(path.read_text())
document["version"] = sys.argv[1]
path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")

path = pathlib.Path("frontend/package-lock.json")
document = json.loads(path.read_text())
document["version"] = sys.argv[1]
document["packages"][""]["version"] = sys.argv[1]
path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
PY
python3 - "$VERSION" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path("backend/onionpi/__init__.py")
path.write_text(
    re.sub(r'^__version__ = ".*"$', f'__version__ = "{sys.argv[1]}"',
           path.read_text(), flags=re.M)
)
PY

./scripts/check-version.sh
printf 'Pensez à ajouter une entrée dans CHANGELOG.md.\n'
