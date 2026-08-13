#!/usr/bin/env bash
# Installation native macOS de l'agent de baie OnionPi.
set -Eeuo pipefail

NODE_ID=""
TOKEN=""
PORT=9080
CLIENT_KEY=""
CLIENT_NAME="baie"
ASSUME_YES=0

usage() {
  cat <<'USAGE'
Usage: sudo ./install-node-agent-macos.sh --node <id> --token <jeton> [options]

  --node <id>          Identifiant du nœud, 16 caractères hexadécimaux.
  --token <jeton>      Jeton partagé, 64 caractères hexadécimaux.
  --port <port>        Port local de l'agent (défaut: 9080).
  --client-key <clé>   Clé publique x25519 de la baie, en base32.
  --client-name <nom>  Nom de l'autorisation (défaut: baie).
  --yes                Ne pas demander de confirmation.
USAGE
}

while (($#)); do
  case "$1" in
    --node) NODE_ID="${2:-}"; shift 2 ;;
    --token) TOKEN="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --client-key) CLIENT_KEY="${2:-}"; shift 2 ;;
    --client-name) CLIENT_NAME="${2:-}"; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Option inconnue: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

(( EUID == 0 )) || { printf 'À lancer avec sudo.\n' >&2; exit 1; }
[[ "$(uname -s)" == Darwin ]] || { printf 'Cet installateur exige macOS.\n' >&2; exit 1; }
[[ "$NODE_ID" =~ ^[0-9a-f]{16}$ ]] || { printf '%s\n' '--node invalide.' >&2; exit 2; }
[[ "$TOKEN" =~ ^[0-9a-f]{64}$ ]] || { printf '%s\n' '--token invalide.' >&2; exit 2; }
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( PORT >= 1 && PORT <= 65535 )) \
  || { printf '%s\n' '--port invalide.' >&2; exit 2; }
[[ -z "$CLIENT_KEY" || "$CLIENT_KEY" =~ ^[A-Z2-7]{52}$ ]] \
  || { printf '%s\n' '--client-key invalide.' >&2; exit 2; }
[[ "$CLIENT_NAME" =~ ^[A-Za-z0-9_-]{1,32}$ ]] \
  || { printf '%s\n' '--client-name invalide.' >&2; exit 2; }

if (( ! ASSUME_YES )); then
  printf 'Installer Tor, l’agent OnionPi et son coupe-circuit PF ? [o/N] '
  read -r REPLY
  [[ "$REPLY" =~ ^[oOyY]$ ]] || { printf 'Abandon.\n'; exit 0; }
fi

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BASE='/Library/Application Support/OnionPi Node'
LIB="$BASE/lib"
STATE="$BASE/state"
RESULT="$BASE/result"
TOR_DATA="$BASE/tor"
HS_DIR="$BASE/hidden-service"
LOG_DIR="$BASE/log"
CONFIG="$BASE/agent.env"
TOR_SOCKS_FILE="$BASE/tor-socks-port"
TOR_TRANS_FILE="$BASE/tor-trans-port"
TOR_DNS_FILE="$BASE/tor-dns-port"
PF_CONF=/etc/pf.conf
SERVICE_USER=_onionpi-node
SERVICE_GROUP=staff

CONSOLE_USER="$(stat -f '%Su' /dev/console)"
[[ -n "$CONSOLE_USER" && "$CONSOLE_USER" != root && "$CONSOLE_USER" != loginwindow ]] \
  || { printf 'Ouvrez une session macOS avant l’installation.\n' >&2; exit 1; }

# Stop a previous installation before probing ports or replacing its files.
# launchctl bootout is asynchronous, hence the bounded wait before continuing.
for LABEL in agent apply tor; do
  /bin/launchctl bootout "system/com.onionpi.node.$LABEL" 2>/dev/null || true
done
for _ in $(seq 1 50); do
  RUNNING=0
  for LABEL in agent apply tor; do
    /bin/launchctl print "system/com.onionpi.node.$LABEL" >/dev/null 2>&1 && RUNNING=1
  done
  (( RUNNING == 0 )) && break
  sleep 0.2
done
(( RUNNING == 0 )) || { printf 'Les anciens services OnionPi ne se sont pas arrêtés.\n' >&2; exit 1; }
# Releases before 0.4.2 loaded a filter-only anchor which could leave every
# application offline. Reinstallation is also recovery: clear it before curl,
# Homebrew or Tor need the network. The anchor attachment stays in pf.conf and
# is upgraded later, after the replacement rules have been validated.
/sbin/pfctl -a com.onionpi.node -F all 2>/dev/null || true

port_is_free() {
  ! /usr/sbin/lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | grep -q LISTEN
}

find_free_port() {
  local START="$1" EXCLUDED="${2:-}" CANDIDATE
  (( START >= 1024 && START <= 64535 )) || START=19050
  for OFFSET in $(seq 0 999); do
    CANDIDATE=$((START + OFFSET))
    if [[ "$CANDIDATE" != "$PORT" && "$CANDIDATE" != "$EXCLUDED" ]] \
      && port_is_free "$CANDIDATE"; then
      printf '%s' "$CANDIDATE"
      return 0
    fi
  done
  return 1
}

port_is_free "$PORT" || {
  printf 'Le port agent %s est déjà utilisé par un autre programme.\n' "$PORT" >&2
  /usr/sbin/lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2 || true
  exit 1
}
TOR_SOCKS_PORT="$(find_free_port "$((PORT + 1))")" \
  || { printf 'Aucun port SOCKS libre trouvé.\n' >&2; exit 1; }
TOR_CONTROL_PORT="$(find_free_port "$((TOR_SOCKS_PORT + 1))" "$TOR_SOCKS_PORT")" \
  || { printf 'Aucun port de contrôle Tor libre trouvé.\n' >&2; exit 1; }
TOR_TRANS_PORT="$(find_free_port "$((TOR_CONTROL_PORT + 1))" "$TOR_CONTROL_PORT")" \
  || { printf 'Aucun TransPort Tor libre trouvé.\n' >&2; exit 1; }
TOR_DNS_PORT="$(find_free_port "$((TOR_TRANS_PORT + 1))" "$TOR_TRANS_PORT")" \
  || { printf 'Aucun DNSPort Tor libre trouvé.\n' >&2; exit 1; }

printf '▸ Homebrew, Tor et Python\n'
BREW="$(sudo -u "$CONSOLE_USER" -H /bin/zsh -lc 'command -v brew' 2>/dev/null || true)"
if [[ -z "$BREW" ]]; then
  BREW_INSTALLER="$(mktemp "${TMPDIR:-/tmp}/onionpi-brew.XXXXXX")"
  trap 'rm -f -- "$BREW_INSTALLER"' EXIT
  curl --proto '=https' --tlsv1.2 -fsSL \
    https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$BREW_INSTALLER"
  sudo -u "$CONSOLE_USER" -H env NONINTERACTIVE=1 /bin/bash "$BREW_INSTALLER"
  rm -f -- "$BREW_INSTALLER"
  trap - EXIT
  for CANDIDATE in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [[ -x "$CANDIDATE" ]] && BREW="$CANDIDATE"
  done
fi
[[ -x "$BREW" ]] || { printf 'Homebrew est introuvable après installation.\n' >&2; exit 1; }
sudo -u "$CONSOLE_USER" -H "$BREW" install tor python
BREW_PREFIX="$(sudo -u "$CONSOLE_USER" -H "$BREW" --prefix)"
TOR_BINARY="$BREW_PREFIX/bin/tor"
PYTHON_BINARY="$BREW_PREFIX/bin/python3"
[[ -x "$TOR_BINARY" && -x "$PYTHON_BINARY" ]] \
  || { printf 'Tor ou Python est introuvable après installation.\n' >&2; exit 1; }

printf '▸ Compte de service isolé\n'
if ! dscl . -read "/Users/$SERVICE_USER" >/dev/null 2>&1; then
  SERVICE_UID=499
  while dscl . -search /Users UniqueID "$SERVICE_UID" 2>/dev/null | grep -q .; do
    (( SERVICE_UID-- ))
    (( SERVICE_UID >= 400 )) || { printf 'Aucun identifiant de service disponible.\n' >&2; exit 1; }
  done
  dscl . -create "/Users/$SERVICE_USER"
  dscl . -create "/Users/$SERVICE_USER" UserShell /usr/bin/false
  dscl . -create "/Users/$SERVICE_USER" RealName 'Agent OnionPi'
  dscl . -create "/Users/$SERVICE_USER" UniqueID "$SERVICE_UID"
  dscl . -create "/Users/$SERVICE_USER" PrimaryGroupID 20
  dscl . -create "/Users/$SERVICE_USER" NFSHomeDirectory "$STATE"
  dscl . -create "/Users/$SERVICE_USER" IsHidden 1
fi

install -d -m 0755 "$BASE" "$LIB" "$RESULT"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$STATE"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$TOR_DATA" "$HS_DIR"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$LOG_DIR"
install -m 0755 "$SOURCE_DIR/onionpi-node-agent.py" "$LIB/onionpi-node-agent.py"
install -m 0755 "$SOURCE_DIR/render-policy-macos.py" "$LIB/render-policy-macos.py"
install -m 0755 "$SOURCE_DIR/onionpi-node-apply-macos.sh" "$LIB/onionpi-node-apply-macos.sh"
ln -sf "$PYTHON_BINARY" "$BASE/python"
printf '%s\n' "$TOR_SOCKS_PORT" >"$TOR_SOCKS_FILE"
printf '%s\n' "$TOR_TRANS_PORT" >"$TOR_TRANS_FILE"
printf '%s\n' "$TOR_DNS_PORT" >"$TOR_DNS_FILE"
chown root:"$SERVICE_GROUP" "$TOR_SOCKS_FILE" "$TOR_TRANS_FILE" "$TOR_DNS_FILE"
chmod 0644 "$TOR_SOCKS_FILE" "$TOR_TRANS_FILE" "$TOR_DNS_FILE"

printf '▸ Identité et service onion\n'
umask 027
cat >"$CONFIG" <<EOF
NODE_ID=$NODE_ID
TOKEN=$TOKEN
PORT=$PORT
EOF
chown root:"$SERVICE_GROUP" "$CONFIG"
chmod 0640 "$CONFIG"

[[ -f "$STATE/apply.request" ]] || : >"$STATE/apply.request"
chown "$SERVICE_USER":"$SERVICE_GROUP" "$STATE/apply.request"
chmod 0640 "$STATE/apply.request"
[[ -f "$RESULT/apply.result" ]] || : >"$RESULT/apply.result"
chmod 0644 "$RESULT/apply.result"

install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$HS_DIR/authorized_clients"
if [[ -n "$CLIENT_KEY" ]]; then
  printf 'descriptor:x25519:%s\n' "$CLIENT_KEY" >"$HS_DIR/authorized_clients/$CLIENT_NAME.auth"
  chown "$SERVICE_USER":"$SERVICE_GROUP" "$HS_DIR/authorized_clients/$CLIENT_NAME.auth"
  chmod 0600 "$HS_DIR/authorized_clients/$CLIENT_NAME.auth"
else
  rm -rf -- "$HS_DIR/authorized_clients"
fi

cat >"$BASE/torrc" <<EOF
DataDirectory "$TOR_DATA"
SocksPort 127.0.0.1:$TOR_SOCKS_PORT
TransPort 127.0.0.1:$TOR_TRANS_PORT IsolateClientAddr IsolateDestAddr IsolateDestPort
DNSPort 127.0.0.1:$TOR_DNS_PORT
AutomapHostsOnResolve 1
VirtualAddrNetworkIPv4 10.192.0.0/10
ControlPort 127.0.0.1:$TOR_CONTROL_PORT
CookieAuthentication 1
CookieAuthFile "$TOR_DATA/control.authcookie"
HiddenServiceDir "$HS_DIR"
HiddenServiceVersion 3
HiddenServicePort $PORT 127.0.0.1:$PORT
Log notice stdout
EOF
chown "$SERVICE_USER":"$SERVICE_GROUP" "$BASE/torrc"
chmod 0600 "$BASE/torrc"

printf '▸ Services launchd\n'
cat >/Library/LaunchDaemons/com.onionpi.node.tor.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.onionpi.node.tor</string>
<key>UserName</key><string>$SERVICE_USER</string>
<key>ProgramArguments</key><array><string>$TOR_BINARY</string><string>-f</string><string>$BASE/torrc</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$LOG_DIR/tor.stdout.log</string>
<key>StandardErrorPath</key><string>$LOG_DIR/tor.stderr.log</string>
</dict></plist>
EOF

cat >/Library/LaunchDaemons/com.onionpi.node.agent.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.onionpi.node.agent</string>
<key>UserName</key><string>$SERVICE_USER</string>
<key>ProgramArguments</key><array><string>$PYTHON_BINARY</string><string>$LIB/onionpi-node-agent.py</string></array>
<key>EnvironmentVariables</key><dict>
<key>ONIONPI_NODE_CONFIG</key><string>$CONFIG</string>
<key>ONIONPI_NODE_STATE</key><string>$STATE</string>
<key>ONIONPI_NODE_RESULT</key><string>$RESULT</string>
<key>ONIONPI_NODE_TOR_COOKIE</key><string>$TOR_DATA/control.authcookie</string>
<key>ONIONPI_NODE_TOR_CONTROL_PORT</key><string>$TOR_CONTROL_PORT</string>
<key>ONIONPI_NODE_LOG_DIR</key><string>$LOG_DIR</string>
<key>ONIONPI_NODE_PORT</key><string>$PORT</string>
</dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$LOG_DIR/agent.log</string>
<key>StandardErrorPath</key><string>$LOG_DIR/agent.log</string>
</dict></plist>
EOF

cat >/Library/LaunchDaemons/com.onionpi.node.apply.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.onionpi.node.apply</string>
<key>ProgramArguments</key><array><string>$LIB/onionpi-node-apply-macos.sh</string></array>
<key>WatchPaths</key><array><string>$STATE/apply.request</string></array>
</dict></plist>
EOF
chmod 0644 /Library/LaunchDaemons/com.onionpi.node.*.plist
chown root:wheel /Library/LaunchDaemons/com.onionpi.node.*.plist

MARK_START='# >>> OnionPi node agent'
MARK_END='# <<< OnionPi node agent'
if grep -Fq "$MARK_START" "$PF_CONF"; then
  # Upgrade the old filter-only attachment: transparent redirection rules in
  # the same anchor also need the explicit rdr attachment point.
  PF_TEMP="$(mktemp /tmp/onionpi-pf.XXXXXX)"
  /usr/bin/awk -v start="$MARK_START" -v end="$MARK_END" '
    $0 == start {
      print start
      print "rdr-anchor \"com.onionpi.node\""
      print "anchor \"com.onionpi.node\""
      replacing = 1
      next
    }
    replacing && $0 == end { print end; replacing = 0; next }
    !replacing { print }
  ' "$PF_CONF" >"$PF_TEMP"
  /sbin/pfctl -nf "$PF_TEMP"
  install -m 0644 "$PF_TEMP" "$PF_CONF"
  rm -f -- "$PF_TEMP"
else
  cat >>"$PF_CONF" <<EOF
$MARK_START
rdr-anchor "com.onionpi.node"
anchor "com.onionpi.node"
$MARK_END
EOF
fi
/sbin/pfctl -nf "$PF_CONF"
/sbin/pfctl -f "$PF_CONF"
/sbin/pfctl -E 2>/dev/null || true

/bin/launchctl bootstrap system /Library/LaunchDaemons/com.onionpi.node.tor.plist

printf '▸ Publication\n'
ADDRESS=""
for _ in $(seq 1 60); do
  if [[ -s "$HS_DIR/hostname" ]]; then
    ADDRESS="$(tr -d '[:space:]' <"$HS_DIR/hostname")"
    break
  fi
  sleep 1
done
if [[ -z "$ADDRESS" ]]; then
  printf 'Tor n’a pas publié l’adresse. Derniers messages :\n' >&2
  tail -30 "$LOG_DIR/tor.stdout.log" "$LOG_DIR/tor.stderr.log" 2>/dev/null >&2 || true
  exit 1
fi

/bin/launchctl bootstrap system /Library/LaunchDaemons/com.onionpi.node.apply.plist
/bin/launchctl bootstrap system /Library/LaunchDaemons/com.onionpi.node.agent.plist

cat <<EOF

Agent installé sur macOS.

  Adresse du nœud : $ADDRESS
  Port de l’agent : $PORT

Recopiez cette adresse dans « Baie virtuelle », puis actualisez le nœud.
Le coupe-circuit PF sera activé lors de la première synchronisation des règles.
EOF
