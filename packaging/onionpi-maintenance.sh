#!/usr/bin/env bash
set -Eeuo pipefail

STATE_FILE="/var/lib/onionpi-privileged/maintenance.state"
ACTION="status"
MINUTES=15

usage() {
  cat <<'EOF'
Mode maintenance physique OnionPi.

Usage: sudo onionpi-maintenance --open [minutes]
       sudo onionpi-maintenance --close
       onionpi-maintenance --status

La fenêtre (15 minutes par défaut, 30 au maximum) autorise la récupération du
compte depuis l’interface locale. Elle n’ouvre ni SSH ni port réseau.
EOF
}

while (($#)); do
  case "$1" in
    --open) ACTION="open"; shift; if (($#)) && [[ "$1" =~ ^[0-9]+$ ]]; then MINUTES="$1"; shift; fi ;;
    --close) ACTION="close"; shift ;;
    --status) ACTION="status"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Option inconnue: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$ACTION" in
  status)
    if [[ ! -r "$STATE_FILE" ]]; then
      printf 'Mode maintenance fermé.\n'
      exit 1
    fi
    expires="$(sed -n 's/.*"expires_at":[[:space:]]*\([0-9]*\).*/\1/p' "$STATE_FILE" | head -n 1)"
    if [[ "$expires" =~ ^[0-9]+$ ]] && (( expires > $(date -u +%s) )); then
      printf 'Mode maintenance actif jusqu’à %s.\n' "$(date -u -d "@$expires" '+%Y-%m-%d %H:%M:%S UTC')"
      exit 0
    fi
    printf 'Mode maintenance fermé.\n'
    exit 1
    ;;
  open|close)
    [[ "$EUID" -eq 0 ]] || { printf 'Utilisez sudo pour modifier le mode maintenance.\n' >&2; exit 1; }
    ;;
esac

if [[ "$ACTION" == "close" ]]; then
  rm -f -- "$STATE_FILE"
  printf 'Mode maintenance fermé.\n'
  exit 0
fi

[[ "$MINUTES" =~ ^[0-9]+$ ]] && (( MINUTES >= 1 && MINUTES <= 30 )) || {
  printf 'Durée invalide: choisissez de 1 à 30 minutes.\n' >&2
  exit 2
}
install -d -m 0750 -o root -g onionpi "$(dirname -- "$STATE_FILE")"
temporary="$(mktemp "$(dirname -- "$STATE_FILE")/.maintenance.XXXXXX")"
trap 'rm -f -- "$temporary"' EXIT
expires_at="$(( $(date -u +%s) + MINUTES * 60 ))"
printf '{"expires_at": %s, "source": "console"}\n' "$expires_at" >"$temporary"
chown root:onionpi "$temporary"
chmod 0640 "$temporary"
mv -fT "$temporary" "$STATE_FILE"
trap - EXIT
printf 'Mode maintenance actif pendant %s minute(s). Il se fermera automatiquement.\n' "$MINUTES"
