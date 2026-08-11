#!/usr/bin/env bash
set -Eeuo pipefail

# Replace the live table in one netlink transaction. A separate delete followed
# by a load would leave clients unprotected forever when the second command
# fails, precisely when a kill switch must fail closed.
transaction="$(mktemp /run/onionpi-firewall.XXXXXX)"
trap 'rm -f -- "$transaction"' EXIT
if nft list table inet onionpi >/dev/null 2>&1; then
  printf 'delete table inet onionpi\n' >>"$transaction"
fi
cat /etc/onionpi/firewall.nft >>"$transaction"
nft --check --file "$transaction"
nft --file "$transaction"
# The reload above recreates an empty blocked_clients set: repopulate it from
# the list the web interface maintains, or blocked devices come back online.
if [[ -x /usr/local/sbin/onionpi-devices-apply ]]; then
  /usr/local/sbin/onionpi-devices-apply
fi
