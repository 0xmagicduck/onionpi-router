#!/usr/bin/env bash
set -Eeuo pipefail

# Proves the per-device accounting on a real nftables, in network namespaces:
# the two counter sets exist, they count in both directions, the privileged
# sampler writes a document the application can parse, and that document turns
# into totals per MAC address.
#
# Linux and root only, like fail-closed-vm.sh. Everything the appliance needs
# is exercised here except systemd's timer. The sampler writes to the same
# /var/lib/onionpi-privileged/traffic.json as a real installation, on purpose:
# the path is hard-coded in the root script and taking it from the environment
# would be one more thing to validate as root.

[[ "$EUID" -eq 0 ]] || { printf 'Ce test doit être exécuté avec sudo.\n' >&2; exit 1; }
for command in ip nft python3 sed ping; do
  command -v "$command" >/dev/null || { printf '%s est requis.\n' "$command" >&2; exit 1; }
done

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
suffix="${BASHPID: -5}"
client="tac$suffix"
router="tar$suffix"
work="$(mktemp -d)"

cleanup() {
  ip netns del "$client" 2>/dev/null || true
  ip netns del "$router" 2>/dev/null || true
  rm -rf -- "$work"
}
trap cleanup EXIT

ip netns add "$client"
ip netns add "$router"
ip link add "c$suffix" type veth peer name "rl$suffix"
ip link set "c$suffix" netns "$client"
ip link set "rl$suffix" netns "$router"
ip -n "$client" link set "c$suffix" name eth0
ip -n "$router" link set "rl$suffix" name lan0
for namespace in "$client" "$router"; do ip -n "$namespace" link set lo up; done
ip -n "$client" address add 10.42.0.2/24 dev eth0
ip -n "$client" link set eth0 up
ip -n "$router" address add 10.42.0.1/24 dev lan0
ip -n "$router" link set lan0 up
client_mac="$(ip -n "$client" -brief link show eth0 | awk '{print $3}')"

sed \
  -e 's/__WIFI_INTERFACE__/lan0/g' \
  -e 's|__LAN_NETWORK__|10.42.0.0/24|g' \
  -e 's|__LAN_SSH_RULE__|# SSH désactivé|g' \
  "$PROJECT_ROOT/packaging/templates/onionpi.nft" >"$work/onionpi.nft"
ip netns exec "$router" nft --file "$work/onionpi.nft"

# ICMP is accepted by lan_input, so the echo replies exercise the postrouting
# half of the accounting as well as the prerouting one.
ip netns exec "$client" ping -c 4 -s 512 10.42.0.1 >/dev/null 2>&1 || true

# The sampler as the appliance runs it, against the state directory it owns.
getent group onionpi >/dev/null || groupadd --system onionpi
install -d -m 0750 -o root -g onionpi /var/lib/onionpi-privileged
ip netns exec "$router" bash "$PROJECT_ROOT/packaging/onionpi-accounting.sh"
[[ -s /var/lib/onionpi-privileged/traffic.json ]] \
  || { printf 'Le relevé privilégié n’a pas été écrit.\n' >&2; exit 1; }
[[ "$(stat -c '%U:%G %a' /var/lib/onionpi-privileged/traffic.json)" == "root:onionpi 640" ]] \
  || { printf 'Le relevé privilégié est lisible par n’importe qui.\n' >&2; exit 1; }

PYTHONPATH="$PROJECT_ROOT/backend" python3 - "$client_mac" "$work" <<'PY'
import sys
from pathlib import Path

from onionpi.accounting import TrafficAccountant
from onionpi.database import Database

mac, work = sys.argv[1].lower(), Path(sys.argv[2])
database = Database(work / "accounting.db")
database.initialize()
accountant = TrafficAccountant(
    database, Path("/var/lib/onionpi-privileged/traffic.json")
)
totals = accountant.update([{"ip": "10.42.0.2", "mac": mac}])

if mac not in totals:
    raise SystemExit(f"aucun total pour {mac}: {totals}")
if totals[mac]["upload"] <= 0:
    raise SystemExit(f"téléversement non compté: {totals[mac]}")
if totals[mac]["download"] <= 0:
    raise SystemExit(f"téléchargement non compté: {totals[mac]}")
print(f"  {mac}: ↑ {totals[mac]['upload']} o ↓ {totals[mac]['download']} o")
PY

printf 'Comptabilité du trafic par appareil validée.\n'
