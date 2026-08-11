#!/usr/bin/env bash
set -Eeuo pipefail

# VERSION is what the update client compares against a published release.
# package manifests and __init__.py exist for tooling, but a disagreement between
# them is how an appliance ends up believing it is up to date when it is not.

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

version="$(head -n 1 VERSION | tr -d '[:space:]')"
package="$(sed -n 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' frontend/package.json | head -n 1)"
lock="$(sed -n 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' frontend/package-lock.json | head -n 1)"
module="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' backend/onionpi/__init__.py | head -n 1)"

status=0
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  printf 'VERSION doit être du type 1.2.3, trouvé: %s\n' "$version" >&2
  status=1
fi
if [[ "$package" != "$version" ]]; then
  printf 'frontend/package.json annonce %s au lieu de %s\n' "$package" "$version" >&2
  status=1
fi
if [[ "$lock" != "$version" ]]; then
  printf 'frontend/package-lock.json annonce %s au lieu de %s\n' "$lock" "$version" >&2
  status=1
fi
if [[ "$module" != "$version" ]]; then
  printf 'backend/onionpi/__init__.py annonce %s au lieu de %s\n' "$module" "$version" >&2
  status=1
fi
(( status )) || printf 'Version cohérente: %s\n' "$version"
exit "$status"
