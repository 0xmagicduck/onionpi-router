#!/usr/bin/env bash
set -Eeuo pipefail

# Removes the OnionPi configuration and restores plain routing. User data in
# /var/lib/onionpi is kept unless --purge is given.

PURGE=0
ASSUME_YES=0
while (($#)); do
  case "$1" in
    --purge) PURGE=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help)
      cat <<'EOF'
Usage: sudo onionpi-uninstall [--purge] [--yes]
  --purge   supprime aussi /var/lib/onionpi (fichiers partagés, base, comptes)
  --yes     ne pas demander de confirmation
EOF
      exit 0 ;;
    *) printf 'Option inconnue: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if [[ "$EUID" -ne 0 ]]; then
  printf 'Exécutez cette commande avec sudo.\n' >&2
  exit 1
fi

if [[ -r /etc/onionpi/install.conf ]]; then
  # shellcheck disable=SC1091
  source /etc/onionpi/install.conf
fi

printf 'OnionPi va être désinstallé.\n'
if (( PURGE )); then
  printf 'ATTENTION: --purge supprime définitivement /var/lib/onionpi (fichiers partagés, messages, compte admin).\n'
fi
printf 'Sauvegarde de la configuration d’origine: %s\n' "${ONIONPI_BACKUP_DIR:-inconnue}"
if (( ! ASSUME_YES )); then
  read -r -p 'Continuer ? [o/N] ' answer
  [[ "$answer" =~ ^[oOyY]$ ]] || exit 0
fi

systemctl disable --now onionpi onionpi-ap onionpi-firewall onionpi-relay.path 2>/dev/null || true
systemctl disable --now onionpi-agent.path onionpi-boot-banner.service 2>/dev/null || true
systemctl disable --now onionpi-update.timer 2>/dev/null || true
systemctl disable --now onionpi-update-recover.service 2>/dev/null || true
systemctl disable --now onionpi-accounting.timer 2>/dev/null || true
# Hosting a Snowflake proxy was opt-in; leaving it running after an uninstall
# would keep lending the uplink without any interface to stop it.
systemctl disable --now snowflake-proxy.service 2>/dev/null || true
nft delete table inet onionpi 2>/dev/null || true
nmcli connection down onionpi-ap 2>/dev/null || true
nmcli connection delete onionpi-ap 2>/dev/null || true

rm -f /etc/systemd/system/onionpi.service /etc/systemd/system/onionpi-ap.service
rm -f /etc/systemd/system/onionpi-firewall.service
rm -f /etc/systemd/system/onionpi-relay.service /etc/systemd/system/onionpi-relay.path
rm -f /etc/systemd/system/onionpi-agent.service /etc/systemd/system/onionpi-agent.path
rm -f /etc/systemd/system/onionpi-boot-banner.service
rm -f /etc/systemd/system/onionpi-update.service /etc/systemd/system/onionpi-update.timer
rm -f /etc/systemd/system/onionpi-update-recover.service
rm -f /etc/systemd/system/onionpi-accounting.service /etc/systemd/system/onionpi-accounting.timer
rm -rf /etc/systemd/system/onionpi-update.timer.d
rm -f /usr/local/sbin/onionpi-firewall-apply /usr/local/sbin/onionpi-verify
rm -f /usr/local/sbin/onionpi-relay-apply /usr/local/sbin/onionpi-devices-apply
rm -f /usr/local/sbin/onionpi-agent-apply /usr/local/sbin/onionpi-update
rm -f /usr/local/sbin/onionpi-accounting
rm -f /usr/local/sbin/onionpi-maintenance
rm -f /etc/dnsmasq.d/onionpi.conf /etc/sysctl.d/70-onionpi.conf
rm -f /etc/tor/torrc.d/onionpi.conf /etc/onionpi/firewall.nft
# Removed before tor and dnsmasq restart: neither file is included any more.
rm -rf /etc/onionpi/tor /etc/onionpi/dns

# Console identity: restore the stock login banner and prompt.
rm -f /etc/update-motd.d/10-onionpi /etc/profile.d/onionpi.sh
rm -f /usr/local/bin/onionpi-status
rm -rf /usr/local/lib/onionpi
for file in /etc/issue /etc/issue.net /etc/motd; do
  if [[ -f "${ONIONPI_BACKUP_DIR:-}$file" ]]; then
    install -m 0644 "$ONIONPI_BACKUP_DIR$file" "$file"
  elif [[ "$file" != /etc/motd ]]; then
    printf 'Debian GNU/Linux \\n \\l\n\n' >"$file"
  fi
done
rm -f /etc/nginx/sites-enabled/onionpi /etc/nginx/sites-available/onionpi
rm -f /etc/modprobe.d/onionpi-regdom.conf
rm -rf /opt/onionpi
rm -rf /var/cache/onionpi-update /var/lib/onionpi-privileged

if [[ -f /etc/tor/torrc ]]; then
  sed -i '\|^%include /etc/tor/torrc\.d/onionpi\.conf$|d' /etc/tor/torrc
fi
if [[ -f /etc/avahi/hosts ]]; then
  sed -i '/[[:space:]]onionpi\.local$/d' /etc/avahi/hosts
fi

systemctl daemon-reload
sysctl --system >/dev/null 2>&1 || true
for service in tor dnsmasq nginx avahi-daemon; do
  systemctl restart "$service" 2>/dev/null || true
done

rm -f /etc/onionpi/update.conf
# Copies of /opt/onionpi kept by the update client; the original configuration
# backup in /var/backups/onionpi-* is deliberately left in place.
rm -rf /var/backups/onionpi-update-*

if (( PURGE )); then
  rm -rf /var/lib/onionpi /etc/onionpi
  userdel onionpi 2>/dev/null || true
  groupdel onionpi 2>/dev/null || true
  printf 'Données OnionPi supprimées.\n'
else
  rm -f /etc/onionpi/install.conf
  printf 'Données conservées dans /var/lib/onionpi.\n'
fi

# Removed last so a failure above still leaves the operator a way to re-run.
rm -f /usr/local/sbin/onionpi-uninstall
printf 'Désinstallation terminée. Redémarrez pour repartir d’un réseau propre.\n'
