#!/usr/bin/env bash
# Second stage of the prebuilt image: runs once with the network up and
# performs the regular OnionPi installation without any operator input.
set -Eeuo pipefail

SETUP=/var/lib/onionpi-setup
LOG=/var/log/onionpi-firstboot.log
# Root-only: this log follows an installer that handles the Wi-Fi PSK and the
# administrator digest, and nothing it records is anyone else's business.
: >>"$LOG"
chmod 0600 "$LOG"
exec > >(tee -a "$LOG") 2>&1

scrub() {
  # The environment file holds the Wi-Fi PSK and the admin password digest. It
  # is only destroyed once the install succeeded, otherwise a retry would have
  # nothing left to work with. Until then it stays root-only in /var/lib.
  if [[ -f "$SETUP/install.env" ]]; then
    shred -u "$SETUP/install.env" 2>/dev/null || rm -f "$SETUP/install.env"
  fi
}

printf '\n=== OnionPi première installation: %s ===\n' "$(date -u +%FT%TZ)"

# No RTC on a Raspberry Pi: Tor and TLS both reject a 1970 clock.
timedatectl set-ntp true 2>/dev/null || true
for _ in $(seq 1 60); do
  [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" == "yes" ]] && break
  sleep 5
done

online=0
for _ in $(seq 1 60); do
  if getent hosts deb.debian.org >/dev/null 2>&1; then online=1; break; fi
  sleep 5
done
if (( ! online )); then
  printf 'Aucun accès réseau après 5 minutes. Branchez le câble Ethernet puis relancez:\n' >&2
  printf '  sudo systemctl start onionpi-firstboot\n' >&2
  exit 1
fi

install -d -m 0700 "$SETUP/src"
tar -xzf "$SETUP/payload.tar.gz" -C "$SETUP/src"
INSTALLER="$SETUP/src/onionpi-router/packaging/install.sh"
[[ -x "$INSTALLER" ]] || chmod +x "$INSTALLER"

# shellcheck disable=SC1091
set -a; source "$SETUP/install.env"; set +a

arguments=(--yes
  --wan "${ONIONPI_WAN_INTERFACE:-eth0}"
  --wifi "${ONIONPI_WIFI_INTERFACE:-wlan0}"
  --ssid "${ONIONPI_SSID:-OnionPi Wi-Fi}"
  --country "${ONIONPI_COUNTRY:-BE}"
  --band "${ONIONPI_BAND:-bg}"
  --channel "${ONIONPI_CHANNEL:-7}")
[[ "${ONIONPI_LAN_SSH:-1}" == "1" ]] || arguments+=(--no-lan-ssh)

if "$INSTALLER" "${arguments[@]}"; then
  # Do not remove/reload the unit while its own start job is still running:
  # doing so can deadlock the job and turn a successful install into a timeout.
  # The missing payload condition and disabled symlink make the retained unit
  # inert on every subsequent boot.
  systemctl disable --no-reload onionpi-firstboot.service || true
  scrub
  rm -rf "$SETUP/src" "$SETUP/payload.tar.gz"
  printf '\nOnionPi est installé. Journal complet: %s\n' "$LOG"
else
  status=$?
  printf '\nÉchec de l’installation (code %s). Journal: %s\n' "$status" "$LOG" >&2
  printf 'Relancez avec: sudo systemctl start onionpi-firstboot\n' >&2
  exit "$status"
fi
