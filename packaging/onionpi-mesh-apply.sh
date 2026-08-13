#!/usr/bin/env bash
set -Eeuo pipefail

# Builds the layer-2 mesh after NetworkManager has authenticated the dedicated
# 802.11s radio. The web service never calls this helper: only systemd runs it
# as root, and every value is revalidated from root-owned install.conf.

CONFIG=/etc/onionpi/install.conf
[[ -r "$CONFIG" ]] || { printf 'Configuration OnionPi absente: %s\n' "$CONFIG" >&2; exit 1; }
# shellcheck disable=SC1090
source "$CONFIG"

MESH_INTERFACE="${ONIONPI_MESH_INTERFACE:-}"
MESH_ADDRESS="${ONIONPI_MESH_ADDRESS:-}"
MESH_DEVICE="${ONIONPI_MESH_DEVICE:-bat0}"
ACTION="${1:-start}"

if [[ -z "$MESH_INTERFACE" ]]; then
  # The unit is installed on every appliance so upgrades are identical, but a
  # node without a dedicated mesh radio has deliberately nothing to activate.
  exit 0
fi
if [[ ! "$MESH_INTERFACE" =~ ^[a-zA-Z0-9_.:-]{1,32}$ ]]; then
  printf 'Interface mesh invalide: %s\n' "$MESH_INTERFACE" >&2
  exit 1
fi
if [[ "$MESH_DEVICE" != bat0 ]]; then
  printf 'Interface virtuelle mesh invalide: %s\n' "$MESH_DEVICE" >&2
  exit 1
fi
if [[ ! "$MESH_ADDRESS" =~ ^10\.43\.([0-9]{1,3})\.([0-9]{1,3})/16$ ]]; then
  printf 'Adresse mesh invalide: %s\n' "$MESH_ADDRESS" >&2
  exit 1
fi
for octet in "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"; do
  ((10#$octet <= 255)) || { printf 'Adresse mesh hors plage: %s\n' "$MESH_ADDRESS" >&2; exit 1; }
done
[[ "$MESH_ADDRESS" != "10.43.0.0/16" && "$MESH_ADDRESS" != "10.43.255.255/16" ]] || {
  printf 'Adresse mesh réservée: %s\n' "$MESH_ADDRESS" >&2
  exit 1
}

stop_mesh() {
  if [[ -d "/sys/class/net/$MESH_INTERFACE" ]]; then
    ip link set dev "$MESH_INTERFACE" nomaster 2>/dev/null || true
  fi
  if [[ -d "/sys/class/net/$MESH_DEVICE" ]]; then
    ip link delete dev "$MESH_DEVICE" 2>/dev/null || true
  fi
  nmcli --wait 10 connection down onionpi-mesh 2>/dev/null || true
}

case "$ACTION" in
  stop)
    stop_mesh
    exit 0
    ;;
  start)
    ;;
  *)
    printf 'Action mesh inconnue: %s (start ou stop).\n' "$ACTION" >&2
    exit 2
    ;;
esac

[[ -d "/sys/class/net/$MESH_INTERFACE" ]] || {
  printf 'Radio mesh absente: %s\n' "$MESH_INTERFACE" >&2
  exit 1
}
nmcli -t -f NAME connection show | grep -Fqx onionpi-mesh || {
  printf 'Profil NetworkManager onionpi-mesh absent.\n' >&2
  exit 1
}

modprobe batman-adv
# A stale bat0 can survive a hard interruption of an earlier start. Reusing a
# device of an unknown kind would be unsafe, so only the batman sysfs shape is
# accepted; otherwise startup fails without touching it.
if [[ -d "/sys/class/net/$MESH_DEVICE" && ! -d "/sys/class/net/$MESH_DEVICE/mesh" ]]; then
  printf '%s existe mais n’est pas une interface batman-adv.\n' "$MESH_DEVICE" >&2
  exit 1
fi

nmcli --wait 30 connection up onionpi-mesh
if [[ ! -d "/sys/class/net/$MESH_DEVICE" ]]; then
  ip link add name "$MESH_DEVICE" type batadv
fi

# The physical 802.11s interface carries only batman frames. IP lives on bat0,
# which is the stable virtual switch spanning every reachable mesh peer.
ip address flush dev "$MESH_INTERFACE" scope global
ip link set dev "$MESH_INTERFACE" master "$MESH_DEVICE"
ip link set dev "$MESH_INTERFACE" up
ip address replace "$MESH_ADDRESS" dev "$MESH_DEVICE"
ip link set dev "$MESH_DEVICE" up
sysctl -q -w "net.ipv6.conf.$MESH_INTERFACE.disable_ipv6=1"
sysctl -q -w "net.ipv6.conf.$MESH_DEVICE.disable_ipv6=1"

# No route outside 10.43.0.0/16 is installed. nftables independently drops any
# forwarded packet entering or leaving bat0, so a peer cannot turn this node's
# Ethernet uplink into a non-Tor gateway.
ip route replace 10.43.0.0/16 dev "$MESH_DEVICE" scope link src "${MESH_ADDRESS%/*}"
