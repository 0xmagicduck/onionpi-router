#!/usr/bin/env bash
set -Eeuo pipefail

# Started by onionpi-agent.path when /var/lib/onionpi/agent.request changes.
# The web application runs with NoNewPrivileges=true and cannot restart a
# service or reload nftables; it writes "<nonce> <verb>" here instead.
#
# The request file is untrusted input. Only the verbs listed below do
# anything, no argument is ever taken from the file, and the answer is written
# back so the interface can tell success from silence.

REQUEST_FILE="/var/lib/onionpi/agent.request"
RESULT_FILE="/var/lib/onionpi/agent.result"

reply() {
  local status="$1" message="$2"
  printf '%s %s %s\n' "$nonce" "$status" "$message" >"$RESULT_FILE.tmp"
  chmod 0644 "$RESULT_FILE.tmp"
  mv "$RESULT_FILE.tmp" "$RESULT_FILE"
  printf '%s: %s\n' "$status" "$message"
}

request="$(head -c 64 "$REQUEST_FILE" 2>/dev/null | head -n 1 || true)"
nonce="${request%% *}"
action="${request#* }"
action="${action//[[:space:]]/}"

if [[ ! "$nonce" =~ ^[0-9a-f]{8,32}$ ]]; then
  printf 'Requête illisible dans %s.\n' "$REQUEST_FILE" >&2
  exit 1
fi

case "$action" in
  devices)
    if /usr/local/sbin/onionpi-devices-apply; then
      reply ok "Blocages appliqués"
    else
      reply fail "nftables a refusé la liste"
      exit 1
    fi
    ;;
  dns)
    # SIGHUP makes dnsmasq re-read addn-hosts without dropping its cache or
    # the DHCP leases, so connected clients never notice the change.
    if systemctl reload dnsmasq || systemctl restart dnsmasq; then
      reply ok "Filtrage DNS rechargé"
    else
      reply fail "dnsmasq a refusé le rechargement"
      exit 1
    fi
    ;;
  restart-tor|restart-dnsmasq|restart-network|restart-firewall)
    declare -A units=(
      [restart-tor]=tor
      [restart-dnsmasq]=dnsmasq
      [restart-network]=NetworkManager
      [restart-firewall]=onionpi-firewall
    )
    unit="${units[$action]}"
    if systemctl restart "$unit"; then
      # Restarting the firewall recreates an empty set.
      [[ "$action" == "restart-firewall" ]] && /usr/local/sbin/onionpi-devices-apply || true
      reply ok "$unit redémarré"
    else
      reply fail "$unit n’a pas redémarré"
      exit 1
    fi
    ;;
  update-check)
    # Bounded on purpose: this runs inside a oneshot unit whose own timeout is
    # 120 s, and a check over Tor should never take longer than that.
    if ONIONPI_UPDATE_TIMEOUT=60 timeout 100 /usr/local/sbin/onionpi-update --check --quiet; then
      reply ok "Vérification terminée"
    else
      reply fail "Vérification impossible (réseau ou Tor indisponible)"
      exit 1
    fi
    ;;
  update)
    # Installing takes minutes. Starting the unit without blocking keeps this
    # oneshot short; the interface follows the progress in update.state.
    if systemctl start --no-block onionpi-update.service; then
      reply ok "Mise à jour lancée"
    else
      reply fail "Le service de mise à jour n’a pas démarré"
      exit 1
    fi
    ;;
  update-schedule)
    if /usr/local/sbin/onionpi-update --write-timer --quiet; then
      reply ok "Horaire de mise à jour appliqué"
    else
      reply fail "Horaire refusé"
      exit 1
    fi
    ;;
  reboot)
    reply ok "Redémarrage en cours"
    systemctl --no-block reboot
    ;;
  poweroff)
    reply ok "Extinction en cours"
    systemctl --no-block poweroff
    ;;
  *)
    reply fail "Action refusée"
    exit 1
    ;;
esac
