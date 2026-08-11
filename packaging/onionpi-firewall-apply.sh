#!/usr/bin/env bash
set -Eeuo pipefail

# Debian Bookworm's nft does not yet provide `destroy table`. Delete only our
# own table when it exists, then load one clean copy of the rules.
if nft list table inet onionpi >/dev/null 2>&1; then
  nft delete table inet onionpi
fi
nft --file /etc/onionpi/firewall.nft
# The reload above recreates an empty blocked_clients set: repopulate it from
# the list the web interface maintains, or blocked devices come back online.
if [[ -x /usr/local/sbin/onionpi-devices-apply ]]; then
  /usr/local/sbin/onionpi-devices-apply
fi

