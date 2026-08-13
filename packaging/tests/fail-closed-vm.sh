#!/usr/bin/env bash
set -Eeuo pipefail

[[ "$EUID" -eq 0 ]] || { printf 'Ce test de VM doit être exécuté avec sudo.\n' >&2; exit 1; }
for command in ip nft python3 sed; do
  command -v "$command" >/dev/null || { printf '%s est requis.\n' "$command" >&2; exit 1; }
done

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
suffix="${BASHPID: -5}"
client="opc$suffix"
router="opr$suffix"
wan="opw$suffix"
work="$(mktemp -d)"
server_pid=""

cleanup() {
  [[ -z "$server_pid" ]] || kill "$server_pid" 2>/dev/null || true
  ip netns del "$client" 2>/dev/null || true
  ip netns del "$router" 2>/dev/null || true
  ip netns del "$wan" 2>/dev/null || true
  rm -rf -- "$work"
}
trap cleanup EXIT

ip netns add "$client"
ip netns add "$router"
ip netns add "$wan"
ip link add "c$suffix" type veth peer name "rl$suffix"
ip link add "rw$suffix" type veth peer name "w$suffix"
ip link set "c$suffix" netns "$client"
ip link set "rl$suffix" netns "$router"
ip link set "rw$suffix" netns "$router"
ip link set "w$suffix" netns "$wan"
ip -n "$client" link set "c$suffix" name eth0
ip -n "$router" link set "rl$suffix" name lan0
ip -n "$router" link set "rw$suffix" name wan0
ip -n "$wan" link set "w$suffix" name eth0

for namespace in "$client" "$router" "$wan"; do ip -n "$namespace" link set lo up; done
ip -n "$client" address add 10.42.0.2/24 dev eth0
ip -n "$client" link set eth0 up
ip -n "$client" route add default via 10.42.0.1
ip -n "$router" address add 10.42.0.1/24 dev lan0
ip -n "$router" address add 192.0.2.1/24 dev wan0
ip -n "$router" link set lan0 up
ip -n "$router" link set wan0 up
ip netns exec "$router" sysctl -q -w net.ipv4.ip_forward=1
ip -n "$wan" address add 192.0.2.2/24 dev eth0
ip -n "$wan" link set eth0 up

sed \
  -e 's/__WIFI_INTERFACE__/lan0/g' \
  -e 's|__LAN_NETWORK__|10.42.0.0/24|g' \
  -e 's|__LAN_SSH_RULE__|# SSH désactivé|g' \
  -e 's|__MESH_SSH_RULE__|# SSH mesh désactivé|g' \
  "$PROJECT_ROOT/packaging/templates/onionpi.nft" >"$work/onionpi.nft"
ip netns exec "$router" nft --file "$work/onionpi.nft"

# With Tor and dnsmasq deliberately absent, none of these packets may reach
# the upstream namespace: TCP/80 is redirected to the missing Tor TransPort,
# TCP/53 to the missing DNS listener, and arbitrary UDP hits the kill switch.
ip netns exec "$wan" python3 - "$work/leak" <<'PY' &
import selectors
import socket
import sys

marker = sys.argv[1]
selector = selectors.DefaultSelector()
for port in (53, 80):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("192.0.2.2", port))
    sock.listen()
    selector.register(sock, selectors.EVENT_READ)
for port in (53, 4444):
    udp = socket.socket(type=socket.SOCK_DGRAM)
    udp.bind(("192.0.2.2", port))
    selector.register(udp, selectors.EVENT_READ)
if selector.select(3):
    open(marker, "w", encoding="utf-8").write("trafic direct observé\n")
PY
server_pid=$!
sleep 0.2
ip netns exec "$client" python3 - <<'PY'
import socket

for port in (53, 80):
    try:
        socket.create_connection(("192.0.2.2", port), timeout=0.5).close()
    except OSError:
        pass
udp = socket.socket(type=socket.SOCK_DGRAM)
udp.sendto(b"dns-probe", ("192.0.2.2", 53))
udp.sendto(b"probe", ("192.0.2.2", 4444))
PY
wait "$server_pid" || true
server_pid=""
[[ ! -e "$work/leak" ]] || { cat "$work/leak" >&2; exit 1; }

# onionpi-firewall-apply validates a complete transaction before applying it.
# A rejected reload therefore leaves the live kill switch in place.
{
  printf 'delete table inet onionpi\n'
  printf 'instruction volontairement invalide\n'
} >"$work/invalid.nft"
if ip netns exec "$router" nft --check --file "$work/invalid.nft" 2>/dev/null; then
  printf 'La transaction nftables invalide aurait dû être refusée.\n' >&2
  exit 1
fi
ip netns exec "$router" nft list chain inet onionpi kill_switch | grep -q 'iifname "lan0".*drop'

# The access point cannot start independently after an initial firewall load
# failure; this dependency is part of the fail-closed contract.
grep -q '^Requires=.*onionpi-firewall.service' "$PROJECT_ROOT/packaging/systemd/onionpi-ap.service"
grep -q '^BindsTo=onionpi-firewall.service' "$PROJECT_ROOT/packaging/systemd/onionpi-ap.service"
grep -q '^Requires=.*onionpi-firewall.service' "$PROJECT_ROOT/packaging/systemd/onionpi-mesh.service"
grep -q '^BindsTo=onionpi-firewall.service' "$PROJECT_ROOT/packaging/systemd/onionpi-mesh.service"
grep -q '^Requires=onionpi-mesh.service' "$PROJECT_ROOT/packaging/systemd/nginx-mesh.conf"
grep -q 'iifname \$onionpi_mesh_if counter drop' "$PROJECT_ROOT/packaging/templates/onionpi.nft"
printf 'Matrice fail-closed Tor/DNS/nftables validée.\n'
