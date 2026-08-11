#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT

# Reproduce the installer transition that exposed both first-boot failures:
# mktemp creates a private root and venv console scripts retain that root in
# their shebang even after the complete tree is atomically renamed.
stage="$(mktemp -d "$work/.incoming-0.0.0.XXXXXX")"
python3 -m venv "$stage/venv"
grep -Fq "$stage/venv/bin/python" "$stage/venv/bin/pip"
chmod 0755 "$stage"
mv "$stage" "$work/release"

"$work/release/venv/bin/python" -m pip --version >/dev/null
[[ -x "$work/release" ]]

unit="$PROJECT_ROOT/packaging/systemd/onionpi.service"
grep -Fq 'ExecStart=/opt/onionpi/current/venv/bin/python -m uvicorn ' "$unit"
if grep -Fq 'ExecStart=/opt/onionpi/current/venv/bin/uvicorn ' "$unit"; then
  printf 'L’unité OnionPi utilise un console-script non relocalisable.\n' >&2
  exit 1
fi

grep -Fq 'chmod 0755 "$RELEASE_STAGE"' "$PROJECT_ROOT/packaging/install.sh"
if grep -qE 'grep -c.*\|\| echo 0' "$PROJECT_ROOT/packaging/verify.sh"; then
  printf 'grep -c produit déjà zéro; le repli ajouterait une seconde ligne.\n' >&2
  exit 1
fi

printf 'Promotion et lancement d’une version préparée validés.\n'
