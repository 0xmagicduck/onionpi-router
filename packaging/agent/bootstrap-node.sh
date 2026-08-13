#!/usr/bin/env bash
# Télécharge depuis GitHub l'agent correspondant au dépôt, vérifie qu'il est
# exactement celui que la baie exécute, puis lance l'installateur natif Linux
# ou macOS.
#
# Rien de ce qui vient de GitHub n'est exécuté avant d'avoir été comparé à
# l'empreinte fournie par la baie. Cette empreinte vient d'une appliance
# installée depuis une publication signée: elle est la référence, le
# téléchargement ne l'est pas.
#
# Le jeton n'est pas un argument: l'installateur le lit sur le terminal, donc
# il n'apparaît ni dans « ps », ni dans l'historique du shell.
set -Eeuo pipefail

REPOSITORY="${ONIONPI_REPOSITORY:-0xmagicduck/onionpi-router}"
REF="${ONIONPI_REF:-main}"
BUNDLE_DIGEST="${ONIONPI_BUNDLE_DIGEST:-}"
UNVERIFIED=0
PLATFORM=""
PRINT_DIGEST=""
FORWARD=()

while (($#)); do
  case "$1" in
    --platform) PLATFORM="${2:-}"; shift 2 ;;
    --ref) REF="${2:-}"; shift 2 ;;
    --bundle-digest) BUNDLE_DIGEST="${2:-}"; shift 2 ;;
    --unverified-bundle) UNVERIFIED=1; shift ;;
    # Écrit l'empreinte d'un dossier et s'arrête. La baie calcule la même en
    # Python; un test compare les deux, parce que deux implémentations d'un
    # manifeste qui divergent, c'est une vérification qui refuse tout.
    --print-bundle-digest) PRINT_DIGEST="${2:-}"; shift 2 ;;
    *) FORWARD+=("$1"); shift ;;
  esac
done

[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || { printf 'Dépôt GitHub invalide.\n' >&2; exit 2; }
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ && "$REF" != *..* && "$REF" != -* ]] \
  || { printf 'Référence GitHub invalide.\n' >&2; exit 2; }
[[ -z "$BUNDLE_DIGEST" || "$BUNDLE_DIGEST" =~ ^[0-9a-f]{64}$ ]] \
  || { printf 'Empreinte de paquet invalide.\n' >&2; exit 2; }

# macOS livre shasum, Debian sha256sum. La sortie des deux est « empreinte  nom ».
if command -v sha256sum >/dev/null; then
  digest_of() { sha256sum "$1" | cut -d' ' -f1; }
  digest_of_stdin() { sha256sum | cut -d' ' -f1; }
elif command -v shasum >/dev/null; then
  digest_of() { shasum -a 256 "$1" | cut -d' ' -f1; }
  digest_of_stdin() { shasum -a 256 | cut -d' ' -f1; }
else
  printf 'sha256sum ou shasum est requis pour vérifier le téléchargement.\n' >&2
  exit 1
fi

# Le même manifeste que `bundle_digest` côté baie: une ligne « empreinte  chemin »
# par fichier, chemins relatifs triés en ordre d'octets, le tout condensé une
# fois. LC_ALL=C fait correspondre le tri du shell à celui de Python.
bundle_digest_of() {
  local root="$1" listing line
  listing="$(cd "$root" && find . -type f | sed 's|^\./||' | LC_ALL=C sort)"
  {
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      printf '%s  %s\n' "$(digest_of "$root/$line")" "$line"
    done <<<"$listing"
  } | digest_of_stdin
}

if [[ -n "$PRINT_DIGEST" ]]; then
  [[ -d "$PRINT_DIGEST" ]] || { printf 'Dossier introuvable: %s\n' "$PRINT_DIGEST" >&2; exit 2; }
  bundle_digest_of "$PRINT_DIGEST"
  exit 0
fi

if [[ -z "$BUNDLE_DIGEST" && "$UNVERIFIED" -eq 0 ]]; then
  cat >&2 <<'REFUS'
Aucune empreinte à vérifier.

Ce script installe un agent qui parle à votre baie: il ne s'exécute pas sur la
foi d'un téléchargement. Copiez la commande complète depuis « Baie virtuelle →
Préparer l'installation », elle porte l'empreinte du paquet.

Sans appliance de référence — développement, installation hors ligne — passez
explicitement --unverified-bundle.
REFUS
  exit 2
fi

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

if [[ -n "$BUNDLE_DIGEST" ]]; then
  FOUND="$(bundle_digest_of "$AGENT_DIR")"
  if [[ "$FOUND" != "$BUNDLE_DIGEST" ]]; then
    cat >&2 <<REFUS
L’agent téléchargé n’est pas celui de votre baie.

  attendu  $BUNDLE_DIGEST
  obtenu   $FOUND

Rien n’a été exécuté. La cause habituelle est une référence GitHub qui a bougé
depuis la version installée sur l’appliance: relancez la commande affichée par
« Préparer l’installation », ou utilisez l’archive hors ligne qu’elle propose.
REFUS
    exit 1
  fi
  printf '▸ Empreinte du paquet vérifiée\n'
else
  printf '⚠ Paquet non vérifié (--unverified-bundle)\n' >&2
fi

case "$PLATFORM" in
  linux) sudo "$AGENT_DIR/install-node-agent.sh" "${FORWARD[@]}" ;;
  macos) sudo "$AGENT_DIR/install-node-agent-macos.sh" "${FORWARD[@]}" ;;
esac
