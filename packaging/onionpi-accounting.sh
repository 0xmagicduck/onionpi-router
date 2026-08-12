#!/usr/bin/env bash
set -Eeuo pipefail

# Publishes the per-client nftables counters for the web interface.
#
# Reading an nftables set needs CAP_NET_ADMIN, which the application will never
# have. This runs as root from onionpi-accounting.timer and drops a JSON
# snapshot in the privileged state directory, where the onionpi group may read
# it and nothing else. No argument is taken from anywhere: the two set names
# below are the whole interface.

STATE_FILE="/var/lib/onionpi-privileged/traffic.json"

for set_name in client_upload client_download; do
  if ! nft list set inet onionpi "$set_name" >/dev/null 2>&1; then
    # Firewall not loaded, or an installation that predates the counters. The
    # interface reads an absent file as "accounting unavailable".
    printf 'Ensemble nftables %s absent, rien à échantillonner.\n' "$set_name"
    exit 0
  fi
done

# Root-created files never live in the application-owned state directory:
# onionpi could pre-position a symlink there and redirect this write.
temporary="$(mktemp /var/lib/onionpi-privileged/.traffic.json.XXXXXX)"
trap 'rm -f -- "$temporary"' EXIT

# nft -j prints one JSON object per invocation, so wrapping the two of them in
# an envelope keeps the file valid JSON without needing jq on the appliance.
{
  printf '{"updated_at":%d,"upload":' "$(date +%s)"
  nft -j list set inet onionpi client_upload
  printf ',"download":'
  nft -j list set inet onionpi client_download
  printf '}\n'
} >"$temporary"

chown root:onionpi "$temporary"
chmod 0640 "$temporary"
mv -fT "$temporary" "$STATE_FILE"
trap - EXIT
