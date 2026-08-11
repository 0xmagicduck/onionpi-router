#!/usr/bin/env bash
set -Eeuo pipefail

# Loads /var/lib/onionpi/blocked-macs.txt into the nftables set that drops
# every packet coming from those clients. Run by onionpi-agent-apply when the
# web interface changes the list, and by onionpi-firewall-apply after a rule
# reload, since flushing the table also empties the set.
#
# The file is written by the unprivileged onionpi user and treated as
# untrusted: only well-formed MAC addresses are ever passed to nft.

STATE_FILE="/var/lib/onionpi/blocked-macs.txt"
MAX_ENTRIES=512

if ! nft list set inet onionpi blocked_clients >/dev/null 2>&1; then
  printf 'Table nftables onionpi absente, rien à appliquer.\n'
  exit 0
fi

macs=()
if [[ -r "$STATE_FILE" ]]; then
  while IFS= read -r line; do
    line="${line%%#*}"
    line="${line//[[:space:]]/}"
    line="${line,,}"
    [[ "$line" =~ ^[0-9a-f]{2}(:[0-9a-f]{2}){5}$ ]] || continue
    macs+=("$line")
    (( ${#macs[@]} >= MAX_ENTRIES )) && break
  done <"$STATE_FILE"
fi

nft flush set inet onionpi blocked_clients

if ((${#macs[@]} == 0)); then
  printf 'Aucun appareil bloqué.\n'
  exit 0
fi

elements="$(IFS=,; printf '%s' "${macs[*]}")"
nft add element inet onionpi blocked_clients "{ $elements }"
printf '%d appareil(s) bloqué(s).\n' "${#macs[@]}"
