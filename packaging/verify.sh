#!/usr/bin/env bash
set -Eeuo pipefail

if [[ -r /etc/onionpi/install.conf ]]; then
  # shellcheck disable=SC1091
  source /etc/onionpi/install.conf
fi
GATEWAY_IP="${ONIONPI_GATEWAY_IP:-10.42.0.1}"
WIFI_INTERFACE="${ONIONPI_WIFI_INTERFACE:-wlan0}"
HTTP_WAIT_SECONDS="${ONIONPI_VERIFY_HTTP_WAIT_SECONDS:-0}"
failed=0

[[ "$HTTP_WAIT_SECONDS" =~ ^[0-9]+$ ]] || {
  printf 'ONIONPI_VERIFY_HTTP_WAIT_SECONDS doit être un entier positif.\n' >&2
  exit 2
}

ok()    { printf '  [OK] %s\n' "$1"; }
warn()  { printf '  [ATTENTION] %s\n' "$1"; }
fail()  { printf '  [ERREUR] %s\n' "$1"; failed=1; }

printf 'Services\n'
for service in tor dnsmasq nftables onionpi-firewall onionpi-ap nginx onionpi NetworkManager; do
  if systemctl is-active --quiet "$service"; then ok "$service"; else fail "$service est arrêté"; fi
done

printf 'Noyau\n'
[[ "$(sysctl -n net.ipv4.ip_forward)" == "1" ]] \
  && ok 'routage IPv4 actif' || fail 'net.ipv4.ip_forward est désactivé'
if [[ -e "/proc/sys/net/ipv6/conf/$WIFI_INTERFACE/disable_ipv6" ]]; then
  [[ "$(cat "/proc/sys/net/ipv6/conf/$WIFI_INTERFACE/disable_ipv6")" == "1" ]] \
    && ok "IPv6 désactivé sur $WIFI_INTERFACE" || fail "IPv6 actif sur $WIFI_INTERFACE"
fi

printf 'Règles réseau\n'
if rules="$(nft list table inet onionpi 2>/dev/null)"; then
  ok 'table nftables inet onionpi'
  grep -q 'redirect to :9040' <<<"$rules" \
    && ok 'TCP client redirigé vers le TransPort Tor' || fail 'redirection TransPort absente'
  grep -q 'redirect to :53' <<<"$rules" \
    && ok 'DNS client capturé localement' || fail 'redirection DNS absente'
  grep -qE "iifname \"$WIFI_INTERFACE\" counter packets [0-9]+ bytes [0-9]+ drop" <<<"$rules" \
    && ok 'coupe-circuit de routage en place' || fail 'coupe-circuit de routage absent'
  if grep -q 'set blocked_clients' <<<"$rules"; then
    blocked="$(grep -cE '^[0-9a-f]{2}(:[0-9a-f]{2}){5}$' /var/lib/onionpi/blocked-macs.txt 2>/dev/null || echo 0)"
    ok "blocage d’appareils opérationnel ($blocked bloqué(s))"
  else
    fail 'jeu blocked_clients absent: le blocage d’appareils ne fonctionnera pas'
  fi
else
  fail 'table nftables absente'
fi

printf 'Actions privilégiées\n'
systemctl is-active --quiet onionpi-agent.path \
  && ok 'onionpi-agent.path en écoute' \
  || fail 'onionpi-agent.path est arrêté: redémarrages et blocages seront refusés'
[[ -x /usr/local/sbin/onionpi-agent-apply ]] \
  && ok 'onionpi-agent-apply installé' || fail 'onionpi-agent-apply absent'

printf 'Mises à jour\n'
if [[ -x /usr/local/sbin/onionpi-update ]]; then
  version="$(head -n 1 /opt/onionpi/current/VERSION 2>/dev/null | tr -d '[:space:]')"
  ok "version installée ${version:-inconnue}"
  if systemctl is-active --quiet onionpi-update.timer; then
    next="$(systemctl show onionpi-update.timer -p NextElapseUSecRealtime --value 2>/dev/null)"
    ok "minuterie active${next:+, prochaine exécution $next}"
  else
    warn 'minuterie de mise à jour arrêtée: aucune vérification programmée'
  fi
  if [[ -r /etc/onionpi/update.conf ]]; then
    ok 'politique de mise à jour présente'
    require_signature="$(sed -n 's/^ONIONPI_UPDATE_REQUIRE_SIGNATURE=//p' /etc/onionpi/update.conf | head -n 1)"
    keyring="$(sed -n 's/^ONIONPI_UPDATE_GPG_KEYRING=//p' /etc/onionpi/update.conf | head -n 1)"
    keyring="${keyring:-/etc/onionpi/update-signing-key.gpg}"
    if [[ -s "$keyring" ]]; then
      fingerprint="$(gpg --show-keys --with-colons "$keyring" 2>/dev/null \
        | awk -F: '/^fpr:/ {print $10; exit}')"
      ok "clé de signature installée ${fingerprint:0:16}…"
      [[ "$require_signature" == "1" ]] \
        && ok 'signature exigée avant toute installation' \
        || warn 'signature présente mais non exigée (ONIONPI_UPDATE_REQUIRE_SIGNATURE=0)'
    elif [[ "$require_signature" == "1" ]]; then
      fail "signature exigée mais aucun trousseau en $keyring: toute mise à jour sera refusée"
    else
      warn 'aucune clé de signature: les archives ne sont vérifiées que par empreinte'
    fi
  else
    fail '/etc/onionpi/update.conf absent: onionpi-update utilisera ses valeurs par défaut'
  fi
else
  warn 'onionpi-update absent: aucune mise à jour automatique'
fi

printf 'Filtrage DNS\n'
if [[ -r /etc/onionpi/dns/block.hosts ]]; then
  domains="$(grep -c '^0\.0\.0\.0 ' /etc/onionpi/dns/block.hosts 2>/dev/null || echo 0)"
  if (( domains > 0 )); then
    ok "$domains domaines filtrés"
  else
    ok 'filtrage DNS désactivé'
  fi
else
  fail 'fichier /etc/onionpi/dns/block.hosts absent: dnsmasq refusera de démarrer'
fi

printf 'Politique Tor\n'
if [[ -r /etc/onionpi/tor/policy.conf ]]; then
  exit_country="$(sed -n 's/^ExitNodes {\(..\)}/\1/p' /etc/onionpi/tor/policy.conf | head -n 1)"
  ok "${exit_country:+sortie forcée en ${exit_country^^}}${exit_country:-aucun pays de sortie imposé}"
else
  fail 'fichier /etc/onionpi/tor/policy.conf absent: Tor refusera de démarrer'
fi

printf 'Tor\n'
bootstrap="$(journalctl -u tor -n 200 --no-pager 2>/dev/null | grep -o 'Bootstrapped [0-9]*%' | tail -n 1 || true)"
if [[ "$bootstrap" == "Bootstrapped 100%" ]]; then
  ok 'circuit Tor établi (100%)'
elif [[ -n "$bootstrap" ]]; then
  warn "Tor démarre encore: $bootstrap"
else
  warn 'progression Tor inconnue'
fi
if ss -ltnH 2>/dev/null | grep -q "${GATEWAY_IP}:9040"; then
  ok "TransPort en écoute sur ${GATEWAY_IP}:9040"
else
  fail "TransPort absent sur ${GATEWAY_IP}:9040"
fi

printf 'Contournement\n'
if [[ -r /etc/onionpi/tor/bridges.conf ]]; then
  ok 'fichier de ponts présent'
  if grep -qx 'UseBridges 1' /etc/onionpi/tor/bridges.conf; then
    transport="$(sed -n 's/^ClientTransportPlugin \([^ ]*\) .*/\1/p' /etc/onionpi/tor/bridges.conf | head -n 1)"
    ok "ponts actifs (${transport:-transport inconnu})"
  else
    ok 'connexion directe (aucun pont)'
  fi
else
  fail 'fichier /etc/onionpi/tor/bridges.conf absent: Tor refusera de démarrer'
fi
for binary in /usr/bin/obfs4proxy /usr/bin/snowflake-client; do
  [[ -x "$binary" ]] && ok "$binary" || warn "$binary absent, transport indisponible"
done
if [[ -x /usr/bin/snowflake-proxy ]]; then
  if systemctl is-active --quiet snowflake-proxy; then
    ok 'proxy Snowflake hébergé (actif)'
  else
    ok 'proxy Snowflake installé, arrêté'
  fi
  systemctl is-active --quiet onionpi-relay.path \
    && ok 'commutateur du proxy Snowflake en écoute' \
    || fail 'onionpi-relay.path est arrêté: l’interface ne pourra pas piloter le proxy'
else
  warn 'snowflake-proxy absent, hébergement indisponible'
fi

printf 'Résolution DNS\n'
if ! command -v dig >/dev/null 2>&1; then
  warn 'dig absent, contrôle DNS ignoré'
elif [[ -n "$(dig +short +time=5 +tries=1 @"$GATEWAY_IP" example.com A 2>/dev/null)" ]]; then
  ok 'dnsmasq répond via le DNSPort Tor'
else
  warn 'aucune réponse DNS (Tor démarre peut-être encore)'
fi

printf 'Interface web\n'
wait_for_http_status() {
  local expected="$1" path="$2" attempt status=""
  for ((attempt = 0; attempt <= HTTP_WAIT_SECONDS; attempt++)); do
    status="$(curl --silent --insecure --connect-timeout 1 --max-time 2 \
      --output /dev/null --write-out '%{http_code}' \
      --resolve "onionpi.local:443:${GATEWAY_IP}" \
      "https://onionpi.local${path}" || true)"
    if [[ "$status" == "$expected" ]]; then
      printf '%s' "$status"
      return 0
    fi
    (( attempt < HTTP_WAIT_SECONDS )) && sleep 1
  done
  printf '%s' "$status"
}

status="$(wait_for_http_status 200 /api/v1/health)"
if [[ "$status" == "200" ]]; then
  ok 'HTTPS local opérationnel'
else
  fail "réponse HTTPS inattendue sur /api/v1/health: $status"
fi
if ss -ltnH 2>/dev/null | grep -q '127\.0\.0\.1:8081'; then
  ok 'entrée locale du service onion en écoute'
else
  warn 'nginx n’écoute pas sur 127.0.0.1:8081: le service onion serait injoignable'
fi

status="$(wait_for_http_status 401 /api/v1/status)"
if [[ "$status" == "401" ]]; then
  ok 'API protégée par authentification'
else
  fail "/api/v1/status devrait répondre 401, obtenu: $status"
fi

exit "$failed"
