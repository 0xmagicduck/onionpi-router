#!/usr/bin/env bash
# Console dashboard: the same information the web interface shows, for
# somebody sitting in front of the Pi with a keyboard or on SSH.
#
#   onionpi-status          full report
#   onionpi-status --motd   short version printed at login
#   onionpi-status --plain  no colours, for piping into a file
set -uo pipefail

# The labels below are accented, and column widths are computed from
# ${#label}: bash only counts characters rather than bytes under a UTF-8
# locale. A login shell does not always have one.
export LC_ALL="${LC_ALL:-C.UTF-8}"

BANNER="/usr/local/lib/onionpi/onionpi-banner.sh"
[[ -r "$BANNER" ]] || BANNER="$(dirname -- "${BASH_SOURCE[0]}")/onionpi-banner.sh"
# shellcheck disable=SC1090
source "$BANNER"

MODE="full"
PLAIN=""
for argument in "$@"; do
  case "$argument" in
    --motd) MODE="motd" ;;
    --plain) PLAIN="--plain" ;;
    -h|--help) printf 'Usage: onionpi-status [--motd] [--plain]\n'; exit 0 ;;
  esac
done

# A login banner is not a terminal in every PAM setup, but it is still shown
# on one, so keep the colours unless the caller asked for plain text.
[[ "$MODE" == "motd" && -z "$PLAIN" ]] && export ONIONPI_FORCE_COLOR=1
onionpi_palette "$PLAIN"
OK=$'\033[38;5;114m'; BAD=$'\033[38;5;203m'; KEY="$ONIONPI_DIM"; RESET="$ONIONPI_RESET"
[[ -n "$PLAIN" ]] && { OK=""; BAD=""; }

if [[ -r /etc/onionpi/install.conf ]]; then
  # shellcheck disable=SC1091
  source /etc/onionpi/install.conf
fi
GATEWAY_IP="${ONIONPI_GATEWAY_IP:-10.42.0.1}"
WIFI_INTERFACE="${ONIONPI_WIFI_INTERFACE:-wlan0}"
WAN_INTERFACE="${ONIONPI_WAN_INTERFACE:-eth0}"

# printf pads on bytes, and half the labels here carry an accent or a typographic
# apostrophe. ${#label} counts characters, so pad by hand to keep the column.
row() {
  local pad=$(( 22 - ${#1} ))
  (( pad < 1 )) && pad=1
  printf '  %s%s%*s%s %s\n' "$KEY" "$1" "$pad" "" "$RESET" "$2"
}
state() {
  if systemctl is-active --quiet "$1" 2>/dev/null; then
    printf '%sactif%s' "$OK" "$RESET"
  else
    printf '%sarrêté%s' "$BAD" "$RESET"
  fi
}

tor_line() {
  local bootstrap
  bootstrap="$(journalctl -u tor -n 400 --no-pager 2>/dev/null \
    | grep -o 'Bootstrapped [0-9]*%' | tail -n 1)"
  if [[ "$bootstrap" == "Bootstrapped 100%" ]]; then
    printf '%sconnecté (100%%)%s' "$OK" "$RESET"
  elif [[ -n "$bootstrap" ]]; then
    printf '%s%s%s' "$BAD" "${bootstrap/Bootstrapped /démarrage }" "$RESET"
  else
    state tor
  fi
}

clients() {
  local count=0
  if [[ -r /var/lib/misc/dnsmasq.leases ]]; then
    count="$(wc -l </var/lib/misc/dnsmasq.leases 2>/dev/null | tr -d ' ')"
  fi
  printf '%s' "${count:-0}"
}

blocked_domains() {
  local file=/etc/onionpi/dns/block.hosts count=0
  [[ -r "$file" ]] && count="$(grep -c '^0\.0\.0\.0 ' "$file" 2>/dev/null)"
  printf '%s' "${count:-0}"
}

blocked_devices() {
  local file=/var/lib/onionpi/blocked-macs.txt
  if [[ -r "$file" ]]; then
    grep -cE '^[0-9a-f]{2}(:[0-9a-f]{2}){5}$' "$file" 2>/dev/null || printf '0'
  else
    printf '?'
  fi
}

onionpi_banner "$PLAIN"

printf '  %sÉtat%s\n' "$ONIONPI_WORD" "$RESET"
row 'Tor' "$(tor_line)"
row 'Point d’accès' "$(state NetworkManager) · $(nmcli -g 802-11-wireless.ssid connection show onionpi-ap 2>/dev/null || echo 'SSID inconnu')"
row 'Coupe-circuit' "$(state onionpi-firewall)"
row 'Interface web' "https://onionpi.local  ·  https://$GATEWAY_IP"

if [[ "$MODE" == "motd" ]]; then
  row 'Clients connectés' "$(clients)"
  printf '\n  %sonionpi-status%s pour le détail, %sonionpi-verify%s pour un diagnostic.\n\n' \
    "$ONIONPI_ACCENT" "$RESET" "$ONIONPI_ACCENT" "$RESET"
  exit 0
fi

row 'DNS local' "$(state dnsmasq)"
row 'Application' "$(state onionpi)"
printf '\n  %sRéseau%s\n' "$ONIONPI_WORD" "$RESET"
row 'Amont' "$WAN_INTERFACE  $(ip -4 -o addr show "$WAN_INTERFACE" 2>/dev/null | awk '{print $4}' | paste -sd' ' -)"
row 'Point d’accès' "$WIFI_INTERFACE  $GATEWAY_IP"
row 'Clients connectés' "$(clients)"
row 'Appareils bloqués' "$(blocked_devices)"
row 'Domaines filtrés' "$(blocked_domains)"

printf '\n  %sMachine%s\n' "$ONIONPI_WORD" "$RESET"
row 'Hôte' "$(hostname)"
row 'Disponibilité' "$(uptime -p 2>/dev/null || uptime)"
if [[ -r /sys/class/thermal/thermal_zone0/temp ]]; then
  row 'Température' "$(awk '{printf "%.1f °C", $1/1000}' /sys/class/thermal/thermal_zone0/temp)"
fi
row 'Stockage' "$(df -h /var/lib/onionpi 2>/dev/null | awk 'NR==2{print $3" / "$2"  ("$5")"}')"
printf '\n'
