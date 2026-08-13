#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="$PROJECT_ROOT/packaging/install.sh"
VERIFY="$PROJECT_ROOT/packaging/verify.sh"

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

security_value() {
  local property="$1"
  sed -n "/^nmcli connection modify onionpi-ap \\\\$/,/^[^[:space:]]/p" "$INSTALLER" \
    | sed -n "s/.*802-11-wireless-security\.${property} \([^ \\\\]*\).*/\\1/p" \
    | tail -n 1
}

[[ "$(security_value key-mgmt)" == "wpa-psk" ]] \
  || fail 'Le point d’accès doit utiliser WPA2 personnel (wpa-psk).'
[[ "$(security_value proto)" == "rsn" ]] \
  || fail 'Le point d’accès doit exclure WPA1 en limitant le protocole à RSN.'
[[ "$(security_value pairwise)" == "ccmp" ]] \
  || fail 'Le chiffrement pairwise doit être limité à AES-CCMP.'
[[ "$(security_value group)" == "ccmp" ]] \
  || fail 'Le chiffrement de groupe doit être limité à AES-CCMP.'

# The security command must remain outside the first-install guard so upgrades
# harden profiles created by older releases without replacing their saved PSK.
first_install_close="$(awk '
  /^# An upgrade preserves the PSK/ { access_point = 1 }
  access_point && /^if \(\( ! UPGRADE \)\); then$/ { first_install = 1; next }
  first_install && /^fi$/ { print NR; exit }
' "$INSTALLER")"
security_line="$(grep -n '^nmcli connection modify onionpi-ap \\$' "$INSTALLER" | tail -n 1 | cut -d: -f1)"
[[ -n "$first_install_close" && -n "$security_line" && "$security_line" -gt "$first_install_close" ]] \
  || fail 'Le durcissement WPA2 doit aussi être appliqué pendant une mise à niveau.'

for property in key-mgmt proto pairwise group; do
  grep -Fq "802-11-wireless-security.$property connection show onionpi-ap" "$VERIFY" \
    || fail "Le diagnostic ne vérifie pas le réglage Wi-Fi $property."
done

printf 'Sécurité WPA2 du point d’accès validée.\n'
