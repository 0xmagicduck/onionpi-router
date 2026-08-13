#!/usr/bin/env bash
# Télécharge depuis GitHub l'agent correspondant au dépôt, puis lance
# l'installateur natif Linux ou macOS. Les identifiants restent des arguments
# locaux: ils ne sont jamais ajoutés à l'URL téléchargée.
set -Eeuo pipefail

REPOSITORY="${ONIONPI_REPOSITORY:-0xmagicduck/onionpi-router}"
REF="${ONIONPI_REF:-main}"
PLATFORM=""
FORWARD=()

while (($#)); do
  case "$1" in
    --platform) PLATFORM="${2:-}"; shift 2 ;;
    --ref) REF="${2:-}"; shift 2 ;;
    *) FORWARD+=("$1"); shift ;;
  esac
done

[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || { printf 'Dépôt GitHub invalide.\n' >&2; exit 2; }
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ && "$REF" != *..* && "$REF" != -* ]] \
  || { printf 'Référence GitHub invalide.\n' >&2; exit 2; }

if [[ -z "$PLATFORM" ]]; then
  case "$(uname -s)" in
    Linux) PLATFORM=linux ;;
    Darwin) PLATFORM=macos ;;
    *) printf 'Système non pris en charge par ce bootstrap.\n' >&2; exit 1 ;;
  esac
fi
[[ "$PLATFORM" == linux || "$PLATFORM" == macos ]] \
  || { printf 'Plateforme invalide: %s\n' "$PLATFORM" >&2; exit 2; }

command -v curl >/dev/null || { printf 'curl est requis.\n' >&2; exit 1; }
command -v tar >/dev/null || { printf 'tar est requis.\n' >&2; exit 1; }

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/onionpi-node.XXXXXX")"
cleanup() { rm -rf -- "$WORK_DIR"; }
trap cleanup EXIT

ARCHIVE_URL="https://github.com/$REPOSITORY/archive/$REF.tar.gz"
printf '▸ Téléchargement de l’agent OnionPi (%s)\n' "$REF"
curl --proto '=https' --tlsv1.2 -fsSL "$ARCHIVE_URL" -o "$WORK_DIR/source.tar.gz"
tar -xzf "$WORK_DIR/source.tar.gz" -C "$WORK_DIR"

shopt -s nullglob
AGENT_DIRS=("$WORK_DIR"/*/packaging/agent)
shopt -u nullglob
(( ${#AGENT_DIRS[@]} == 1 )) \
  || { printf 'Archive GitHub inattendue: dossier agent introuvable.\n' >&2; exit 1; }
AGENT_DIR="${AGENT_DIRS[0]}"

case "$PLATFORM" in
  linux) sudo "$AGENT_DIR/install-node-agent.sh" "${FORWARD[@]}" ;;
  macos) sudo "$AGENT_DIR/install-node-agent-macos.sh" "${FORWARD[@]}" ;;
esac
