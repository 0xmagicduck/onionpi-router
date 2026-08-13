#!/usr/bin/env bash
# Exécutant root macOS: revalide le verbe et la politique avant PF/launchd.
set -Eeuo pipefail

BASE='/Library/Application Support/OnionPi Node'
STATE="$BASE/state"
RESULT_DIR="$BASE/result"
REQUEST="$STATE/apply.request"
RESULT="$RESULT_DIR/apply.result"
POLICY="$STATE/policy.json"
APPLIED="$RESULT_DIR/policy.applied"
RULES="$RESULT_DIR/policy.pf"
RENDER="$BASE/lib/render-policy-macos.py"
PYTHON="$BASE/python"
SOCKS_PORT_FILE="$BASE/tor-socks-port"
LOG="$BASE/log/apply.log"

install -d -m 0755 "$RESULT_DIR" "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

answer() {
  printf '%s %s %s\n' "$1" "$2" "$3" >"$RESULT.tmp"
  chmod 0644 "$RESULT.tmp"
  mv -f "$RESULT.tmp" "$RESULT"
}

[[ -r "$REQUEST" ]] || exit 0
read -r NONCE ACTION _ <"$REQUEST" || exit 0
[[ "$NONCE" =~ ^[0-9a-f]{8,32}$ ]] || exit 0

case "$ACTION" in policy|restart-tor|reboot) ;; *) answer "$NONCE" error "Action refusée"; exit 0 ;; esac

case "$ACTION" in
  policy)
    [[ -s "$POLICY" ]] || { answer "$NONCE" error "Aucune politique à appliquer"; exit 0; }
    SOCKS_PORT="$(tr -d '[:space:]' <"$SOCKS_PORT_FILE" 2>/dev/null || true)"
    [[ "$SOCKS_PORT" =~ ^[0-9]{1,5}$ ]] && (( SOCKS_PORT >= 1 && SOCKS_PORT <= 65535 )) \
      || { answer "$NONCE" error "Port SOCKS invalide"; exit 0; }
    if ! RENDERED="$("$PYTHON" "$RENDER" "$POLICY" "$RULES" "$SOCKS_PORT")"; then
      answer "$NONCE" error "Politique refusée"
      exit 0
    fi
    read -r DIGEST EGRESS <<<"$RENDERED"
    if [[ "$EGRESS" == direct ]]; then
      /sbin/pfctl -a com.onionpi.node -F rules
    elif ! /sbin/pfctl -a com.onionpi.node -nf "$RULES" \
      || ! /sbin/pfctl -a com.onionpi.node -f "$RULES"; then
      answer "$NONCE" error "Application du coupe-circuit refusée"
      exit 0
    fi
    printf '{"digest":"%s","egress":"%s","applied_at":%s}\n' \
      "$DIGEST" "$EGRESS" "$(date +%s)" >"$APPLIED.tmp"
    chmod 0644 "$APPLIED.tmp"
    mv -f "$APPLIED.tmp" "$APPLIED"
    answer "$NONCE" ok "Coupe-circuit PF appliqué"
    ;;
  restart-tor)
    if /bin/launchctl kickstart -k system/com.onionpi.node.tor; then
      answer "$NONCE" ok "Tor redémarré"
    else
      answer "$NONCE" error "Redémarrage de Tor refusé"
    fi
    ;;
  reboot)
    answer "$NONCE" ok "Redémarrage en cours"
    /sbin/shutdown -r now
    ;;
esac
