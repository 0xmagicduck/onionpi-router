#!/usr/bin/env bash
set -Eeuo pipefail

# Started by onionpi-relay.path when /var/lib/onionpi/relay.state changes.
# The file is written by the unprivileged onionpi user and is treated as
# untrusted input: only the two literal words below do anything.

STATE_FILE="/var/lib/onionpi/relay.state"
SERVICE="snowflake-proxy.service"

if [[ ! -x /usr/bin/snowflake-proxy ]]; then
  printf 'snowflake-proxy absent, rien à faire.\n'
  exit 0
fi

state="$(head -c 16 "$STATE_FILE" 2>/dev/null | tr -d '[:space:]' || true)"

case "$state" in
  enabled)
    systemctl enable --now "$SERVICE"
    printf 'Proxy Snowflake activé.\n'
    ;;
  disabled)
    systemctl disable --now "$SERVICE"
    printf 'Proxy Snowflake arrêté.\n'
    ;;
  *)
    printf 'État inattendu dans %s, aucune action.\n' "$STATE_FILE" >&2
    exit 1
    ;;
esac
