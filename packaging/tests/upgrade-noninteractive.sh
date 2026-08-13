#!/usr/bin/env bash
set -Eeuo pipefail

# packaging/install.sh --upgrade is executed by onionpi-update.service, a
# oneshot with no terminal on stdin and no ONIONPI_* secret in its environment.
# Every credential is already on the appliance: the Wi-Fi PSK in NetworkManager
# and the administrator digest in the database. A prompt, or the abort that
# replaces it when stdin is not a terminal, therefore makes the installer exit
# before it touches anything, onionpi-update roll back, and the appliance stay
# on its old version for ever. That is exactly what happened between 0.3.1 and
# 0.3.2, so the invariant is checked here rather than on a Raspberry Pi.
#
# The block is extracted from the installer itself: a copy would drift.

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="$PROJECT_ROOT/packaging/install.sh"

SECTION="$(awk '
  $0 == "# Credential resolution starts here." { capture = 1; next }
  $0 ~ /^# Credential resolution ends here\./ { capture = 0 }
  capture
' "$INSTALLER")"

if [[ -z "$SECTION" ]]; then
  printf 'Marqueurs de résolution des identifiants introuvables dans %s.\n' "$INSTALLER" >&2
  exit 1
fi
# Without the guard the block is a bare sequence of ifs and would still run.
if ! grep -Fq 'read -r -s -p' <<<"$SECTION"; then
  printf 'Le bloc extrait ne contient aucune invite: les marqueurs ont glissé.\n' >&2
  exit 1
fi

# stdin closed on purpose: `[[ -t 0 ]]` must see what the systemd unit sees.
run_section() {
  local upgrade="$1"
  shift
  env -i PATH="$PATH" "$@" bash -c "
set -Eeuo pipefail
UPGRADE=$upgrade
$SECTION
printf 'RESULTAT %s|%s|%s|%s\n' \
  \"\${WIFI_PASSWORD}\" \"\${WIFI_PSK}\" \"\${ADMIN_PASSWORD}\" \"\${ADMIN_PASSWORD_HASH}\"
" </dev/null 2>&1
}

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

# Built rather than written literally, so scripts/check-secrets.sh keeps
# reading an assignment of this shape as the credential leak it usually is.
factice="$(printf 'x%.0s' {1..18})"
psk="$(printf 'a%.0s' {1..64})"

# 1. The unattended upgrade: no terminal, no secret, and nothing to ask.
output="$(run_section 1)" \
  || fail "Une mise à niveau non interactive doit aboutir; sortie: $output"
[[ "$output" == "RESULTAT |||" ]] \
  || fail "Une mise à niveau ne doit conserver aucun identifiant: $output"

# 2. Same run, but with secrets in the environment: an upgrade reuses what the
# appliance already holds, so nothing may survive into the rest of the script.
output="$(run_section 1 \
  ONIONPI_WIFI_PASSWORD="$factice" \
  ONIONPI_WIFI_PSK="$psk" \
  ONIONPI_ADMIN_PASSWORD="$factice" \
  ONIONPI_ADMIN_PASSWORD_HASH='scrypt$factice')" \
  || fail "Une mise à niveau avec identifiants dans l’environnement doit aboutir: $output"
[[ "$output" == "RESULTAT |||" ]] \
  || fail "Une mise à niveau doit effacer les identifiants hérités: $output"

# 3. A first installation keeps its guarantees: no terminal and no secret is
# still a refusal, not a silent appliance without a password.
if output="$(run_section 0)"; then
  fail "Une première installation non interactive sans mot de passe doit échouer: $output"
fi
grep -Fq 'ONIONPI_WIFI_PASSWORD' <<<"$output" \
  || fail "Le refus doit nommer la variable à définir: $output"

# 4. A first installation from a prebuilt image: precomputed secrets, no prompt.
output="$(run_section 0 \
  ONIONPI_WIFI_PSK="$psk" \
  ONIONPI_ADMIN_PASSWORD_HASH='scrypt$factice')" \
  || fail "Une installation depuis une image préparée doit aboutir: $output"
[[ "$output" == "RESULTAT |$psk||scrypt\$factice" ]] \
  || fail "Les secrets précalculés doivent être conservés: $output"

# 5. And a malformed one is still refused.
if output="$(run_section 0 ONIONPI_WIFI_PSK=zzz ONIONPI_ADMIN_PASSWORD_HASH='scrypt$factice')"; then
  fail "Un PSK malformé doit être refusé: $output"
fi

# 6. A mesh first install needs its own shared secret, while an upgrade must
# reuse the root-owned NetworkManager profile without ever asking for it.
if output="$(run_section 0 MESH_INTERFACE=wlan1 \
  ONIONPI_WIFI_PSK="$psk" ONIONPI_ADMIN_PASSWORD_HASH='scrypt$factice')"; then
  fail "Un mesh neuf sans phrase SAE doit être refusé: $output"
fi
grep -Fq 'ONIONPI_MESH_PASSWORD' <<<"$output" \
  || fail "Le refus doit nommer le secret mesh à définir: $output"
output="$(run_section 0 MESH_INTERFACE=wlan1 ONIONPI_MESH_PASSWORD="$factice" \
  ONIONPI_WIFI_PSK="$psk" ONIONPI_ADMIN_PASSWORD_HASH='scrypt$factice')" \
  || fail "Une première installation mesh correctement identifiée doit aboutir: $output"
output="$(run_section 1 MESH_INTERFACE=wlan1 ONIONPI_MESH_PASSWORD="$factice")" \
  || fail "Une mise à niveau mesh ne doit jamais redemander son secret: $output"

printf 'Mise à niveau non interactive validée.\n'
