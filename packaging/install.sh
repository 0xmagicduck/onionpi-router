#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_VERSION="$(head -n 1 "$PROJECT_ROOT/VERSION" 2>/dev/null | tr -d '[:space:]')"
if [[ ! "$RELEASE_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+-]{0,39}$ || "$RELEASE_VERSION" == *..* ]]; then
  printf 'Version de publication invalide: %s\n' "$RELEASE_VERSION" >&2
  exit 1
fi
INSTALL_BASE="/opt/onionpi"
RELEASES_ROOT="$INSTALL_BASE/releases"
RELEASE_DIR="$RELEASES_ROOT/$RELEASE_VERSION"
CURRENT_LINK="$INSTALL_BASE/current"
WAN_INTERFACE="eth0"
WIFI_INTERFACE="wlan0"
SSID="OnionPi Wi-Fi"
COUNTRY="BE"
GATEWAY_IP="10.42.0.1"
GATEWAY_CIDR="10.42.0.1/24"
LAN_NETWORK="10.42.0.0/24"
DHCP_START="10.42.0.20"
DHCP_END="10.42.0.200"
WIFI_BAND="bg"
WIFI_CHANNEL=""
LAN_SSH=1
ASSUME_YES=0
UPGRADE=0
UPDATE_REPOSITORY="0xmagicduck/onionpi-router"
UPDATE_CHANNEL="stable"
UPDATE_SCHEDULE="04:30"
UPDATE_ENABLED=1
UPDATE_APPLY=1

usage() {
  cat <<'EOF'
Installation OnionPi pour Raspberry Pi OS (Bookworm/Trixie).

Usage: sudo ./packaging/install.sh [options]
  --wan INTERFACE      interface reliée à Internet (défaut: eth0)
  --wifi INTERFACE     interface du point d'accès (défaut: wlan0)
  --ssid NOM           nom du réseau Wi-Fi (défaut: OnionPi Wi-Fi)
  --country CODE       code pays Wi-Fi ISO à deux lettres (défaut: BE)
  --band bg|a          bande du point d'accès, bg=2,4 GHz a=5 GHz (défaut: bg)
  --channel N          canal du point d'accès (défaut: 7 en bg, 36 en a)
  --no-lan-ssh         interdire SSH depuis le Wi-Fi OnionPi
  --yes                ne pas demander la confirmation finale
  -h, --help           afficher cette aide

Mises à jour automatiques:
  --update-repo OWNER/NOM  dépôt GitHub des publications (défaut: 0xmagicduck/onionpi-router)
  --update-channel CANAL   stable (publications) ou edge (branche main)
  --update-time HH:MM      heures de vérification, séparées par des virgules
  --update-check-only      télécharge l'information, n'installe pas tout seul
  --no-updates             installe le service mais laisse la minuterie arrêtée

Mise à niveau immuable (utilisée par onionpi-update):
  --upgrade            conserve mots de passe, Wi-Fi et comptes existants

Les mots de passe ne sont jamais acceptés en argument. Utilisez les invites
interactives, ou ONIONPI_WIFI_PASSWORD et ONIONPI_ADMIN_PASSWORD en mode automatisé.
EOF
}

while (($#)); do
  case "$1" in
    --wan) WAN_INTERFACE="${2:?interface manquante}"; shift 2 ;;
    --wifi) WIFI_INTERFACE="${2:?interface manquante}"; shift 2 ;;
    --ssid) SSID="${2:?SSID manquant}"; shift 2 ;;
    --country) COUNTRY="${2:?code pays manquant}"; shift 2 ;;
    --band) WIFI_BAND="${2:?bande manquante}"; shift 2 ;;
    --channel) WIFI_CHANNEL="${2:?canal manquant}"; shift 2 ;;
    --no-lan-ssh) LAN_SSH=0; shift ;;
    --update-repo) UPDATE_REPOSITORY="${2:?dépôt manquant}"; shift 2 ;;
    --update-channel) UPDATE_CHANNEL="${2:?canal manquant}"; shift 2 ;;
    --update-time) UPDATE_SCHEDULE="${2:?horaire manquant}"; shift 2 ;;
    --update-check-only) UPDATE_APPLY=0; shift ;;
    --no-updates) UPDATE_ENABLED=0; shift ;;
    --upgrade) UPGRADE=1; ASSUME_YES=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Option inconnue: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

# An upgrade reinstalls the code over a working appliance: every choice made at
# first install is read back instead of being asked again, and nothing that
# holds a secret is touched.
if (( UPGRADE )); then
  if [[ ! -r /etc/onionpi/install.conf ]]; then
    printf 'Aucune installation OnionPi à mettre à jour (/etc/onionpi/install.conf absent).\n' >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  source /etc/onionpi/install.conf
  WAN_INTERFACE="${ONIONPI_WAN_INTERFACE:-$WAN_INTERFACE}"
  WIFI_INTERFACE="${ONIONPI_WIFI_INTERFACE:-$WIFI_INTERFACE}"
  GATEWAY_IP="${ONIONPI_GATEWAY_IP:-$GATEWAY_IP}"
  GATEWAY_CIDR="${GATEWAY_IP}/24"
  LAN_NETWORK="${ONIONPI_LAN_NETWORK:-$LAN_NETWORK}"
  if [[ -r /etc/onionpi/onionpi.env ]]; then
    existing_country="$(sed -n 's/^ONIONPI_COUNTRY=//p' /etc/onionpi/onionpi.env | head -n 1)"
    [[ "$existing_country" =~ ^[A-Z]{2}$ ]] && COUNTRY="$existing_country"
  fi
  existing_ssid="$(nmcli -g 802-11-wireless.ssid connection show onionpi-ap 2>/dev/null || true)"
  [[ -n "$existing_ssid" ]] && SSID="$existing_ssid"
  # The firewall template is regenerated below, so the current rule is the only
  # record of whether SSH from the Wi-Fi was allowed.
  if [[ -r /etc/onionpi/firewall.nft ]] && ! grep -qE '^\s*tcp dport 22 accept' /etc/onionpi/firewall.nft; then
    LAN_SSH=0
  fi
  if [[ -r /etc/onionpi/update.conf ]]; then
    # shellcheck disable=SC1091
    source /etc/onionpi/update.conf
    UPDATE_REPOSITORY="${ONIONPI_UPDATE_REPOSITORY:-$UPDATE_REPOSITORY}"
    UPDATE_CHANNEL="${ONIONPI_UPDATE_CHANNEL:-$UPDATE_CHANNEL}"
    UPDATE_SCHEDULE="${ONIONPI_UPDATE_SCHEDULE:-$UPDATE_SCHEDULE}"
    UPDATE_ENABLED="${ONIONPI_UPDATE_ENABLED:-$UPDATE_ENABLED}"
    UPDATE_APPLY="${ONIONPI_UPDATE_APPLY:-$UPDATE_APPLY}"
  fi
fi

if [[ ! "$UPDATE_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  printf 'Dépôt de mise à jour invalide: %s\n' "$UPDATE_REPOSITORY" >&2
  exit 1
fi
if [[ ! "$UPDATE_CHANNEL" =~ ^(stable|edge)$ ]]; then
  printf 'Canal de mise à jour invalide: %s (attendu stable ou edge).\n' "$UPDATE_CHANNEL" >&2
  exit 1
fi
if [[ ! "$UPDATE_SCHEDULE" =~ ^([01][0-9]|2[0-3]):[0-5][0-9](,([01][0-9]|2[0-3]):[0-5][0-9]){0,5}$ ]]; then
  printf 'Horaire de mise à jour invalide: %s (attendu HH:MM[,HH:MM…]).\n' "$UPDATE_SCHEDULE" >&2
  exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
  printf 'Exécutez cet installateur avec sudo.\n' >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  printf 'Système non reconnu. Raspberry Pi OS est requis.\n' >&2
  exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "debian" && "${ID:-}" != "raspbian" ]]; then
  printf 'Système non pris en charge: %s. Utilisez Raspberry Pi OS.\n' "${PRETTY_NAME:-inconnu}" >&2
  exit 1
fi

if [[ ! "$WAN_INTERFACE" =~ ^[a-zA-Z0-9_.:-]{1,32}$ || ! "$WIFI_INTERFACE" =~ ^[a-zA-Z0-9_.:-]{1,32}$ ]]; then
  printf 'Nom d’interface invalide.\n' >&2
  exit 1
fi
if [[ "$WAN_INTERFACE" == "$WIFI_INTERFACE" ]]; then
  printf 'Les interfaces WAN et Wi-Fi doivent être différentes.\n' >&2
  exit 1
fi
for interface in "$WAN_INTERFACE" "$WIFI_INTERFACE"; do
  if [[ ! -d "/sys/class/net/$interface" ]]; then
    printf 'Interface absente: %s\n' "$interface" >&2
    exit 1
  fi
done
if [[ ! "$COUNTRY" =~ ^[A-Z]{2}$ ]]; then
  printf 'Le code pays doit contenir deux lettres majuscules, par exemple BE ou FR.\n' >&2
  exit 1
fi
if (( ${#SSID} < 1 || ${#SSID} > 32 )); then
  printf 'Le SSID doit contenir entre 1 et 32 caractères.\n' >&2
  exit 1
fi
case "$WIFI_BAND" in
  bg) WIFI_CHANNEL="${WIFI_CHANNEL:-7}" ;;
  a) WIFI_CHANNEL="${WIFI_CHANNEL:-36}" ;;
  *) printf 'Bande invalide: %s (attendu bg ou a).\n' "$WIFI_BAND" >&2; exit 1 ;;
esac
if [[ ! "$WIFI_CHANNEL" =~ ^[0-9]{1,3}$ ]]; then
  printf 'Canal Wi-Fi invalide: %s\n' "$WIFI_CHANNEL" >&2
  exit 1
fi

# A prebuilt image ships precomputed secrets instead of plaintext: WIFI_PSK is
# the PBKDF2 result of the passphrase, ADMIN_PASSWORD_HASH an scrypt digest.
WIFI_PASSWORD="${ONIONPI_WIFI_PASSWORD:-}"
WIFI_PSK="${ONIONPI_WIFI_PSK:-}"
ADMIN_PASSWORD="${ONIONPI_ADMIN_PASSWORD:-}"
ADMIN_PASSWORD_HASH="${ONIONPI_ADMIN_PASSWORD_HASH:-}"
if (( UPGRADE )); then
  # Nothing to ask and nothing to rewrite: the Wi-Fi PSK stays in
  # NetworkManager and the administrator digest stays in the database.
  WIFI_PASSWORD=""
  WIFI_PSK=""
  ADMIN_PASSWORD=""
  ADMIN_PASSWORD_HASH=""
elif [[ -n "$WIFI_PSK" ]]; then
  if [[ ! "$WIFI_PSK" =~ ^[0-9a-fA-F]{64}$ ]]; then
    printf 'ONIONPI_WIFI_PSK doit contenir 64 caractères hexadécimaux.\n' >&2
    exit 1
  fi
elif [[ -z "$WIFI_PASSWORD" ]]; then
  if [[ ! -t 0 ]]; then
    printf 'Définissez ONIONPI_WIFI_PASSWORD ou ONIONPI_WIFI_PSK en mode non interactif.\n' >&2
    exit 1
  fi
  read -r -s -p 'Mot de passe Wi-Fi (12 caractères minimum): ' WIFI_PASSWORD
  printf '\n'
fi
if [[ -n "$ADMIN_PASSWORD_HASH" ]]; then
  if [[ ! "$ADMIN_PASSWORD_HASH" =~ ^scrypt\$ ]]; then
    printf 'ONIONPI_ADMIN_PASSWORD_HASH n’est pas un condensat scrypt OnionPi.\n' >&2
    exit 1
  fi
elif [[ -z "$ADMIN_PASSWORD" ]]; then
  if [[ ! -t 0 ]]; then
    printf 'Définissez ONIONPI_ADMIN_PASSWORD ou ONIONPI_ADMIN_PASSWORD_HASH en mode non interactif.\n' >&2
    exit 1
  fi
  read -r -s -p 'Mot de passe administrateur (12 caractères minimum): ' ADMIN_PASSWORD
  printf '\n'
fi
if (( ! UPGRADE )); then
  if [[ -z "$WIFI_PSK" ]] && (( ${#WIFI_PASSWORD} < 12 || ${#WIFI_PASSWORD} > 63 )); then
    printf 'Le mot de passe Wi-Fi doit contenir entre 12 et 63 caractères.\n' >&2
    exit 1
  fi
  if [[ -z "$ADMIN_PASSWORD_HASH" ]] && (( ${#ADMIN_PASSWORD} < 12 )); then
    printf 'Le mot de passe administrateur doit contenir au moins 12 caractères.\n' >&2
    exit 1
  fi
fi

if (( UPGRADE )); then
  printf '\nMise à niveau immuable d’OnionPi (%s, %s).\n' "$WIFI_INTERFACE" "$SSID"
  printf '  Le point d’accès, les mots de passe et les données sont conservés.\n\n'
else
  printf '\nOnionPi va configurer:\n'
  printf '  Internet : %s\n  Point d’accès : %s (%s)\n  Réseau local : %s\n' \
    "$WAN_INTERFACE" "$WIFI_INTERFACE" "$SSID" "$LAN_NETWORK"
  printf '  Le trafic TCP et le DNS des clients passeront par Tor. UDP/QUIC et IPv6 seront bloqués.\n'
  printf '  La connexion Wi-Fi actuelle sur %s sera interrompue.\n\n' "$WIFI_INTERFACE"
fi
if (( ! ASSUME_YES )); then
  read -r -p 'Continuer ? [o/N] ' answer
  [[ "$answer" =~ ^[oOyY]$ ]] || exit 0
fi

export DEBIAN_FRONTEND=noninteractive
if (( ! UPGRADE )); then
  apt-get update
  apt-get install -y --no-install-recommends \
    tor nftables dnsmasq network-manager nginx openssl avahi-daemon curl iw \
    python3 python3-venv python3-pip rsync ca-certificates systemd-timesyncd dnsutils \
    gnupg gpgv
else
  for command in python3 rsync nft tor dnsmasq nginx gpgv; do
    command -v "$command" >/dev/null 2>&1 || {
      printf 'Mise à niveau hors ligne impossible: %s est absent.\n' "$command" >&2
      exit 1
    }
  done
fi

# Circumvention. obfs4proxy also provides meek_lite. snowflake-client lets the
# Pi use volunteer proxies; snowflake-proxy lets it become one. Missing
# packages only remove the matching option from the interface.
if (( ! UPGRADE )); then
  for package in obfs4proxy snowflake-client snowflake-proxy; do
    if ! apt-get install -y --no-install-recommends "$package"; then
      printf 'Paquet %s indisponible: le transport correspondant sera masqué.\n' "$package" >&2
    fi
  done
fi
# Hosting a proxy is opt-in from the web interface, never on by default. An
# upgrade must not undo the choice someone already made there.
if (( ! UPGRADE )); then
  systemctl disable --now snowflake-proxy.service 2>/dev/null || true
fi

# The interface is a static bundle. Node is only needed when the repository
# ships without a prebuilt frontend/dist, which is the developer case.
BUILD_FRONTEND=1
if [[ -s "$PROJECT_ROOT/frontend/dist/index.html" ]]; then
  BUILD_FRONTEND=0
elif (( UPGRADE )); then
  printf 'Archive de mise à niveau incomplète: frontend/dist absent.\n' >&2
  exit 1
elif ! command -v npm >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends nodejs npm
fi

if ! iw list | sed -n '/Supported interface modes:/,/Band /p' | grep -qE '^[[:space:]]*\*[[:space:]]+AP$'; then
  printf 'La carte Wi-Fi ne déclare pas le mode point d’accès (AP).\n' >&2
  exit 1
fi

BACKUP_DIR="/var/backups/onionpi-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$BACKUP_DIR"
backup_file() {
  local source="$1"
  if [[ -e "$source" ]]; then
    install -d -m 0700 "$BACKUP_DIR$(dirname -- "$source")"
    cp -a -- "$source" "$BACKUP_DIR$source"
  fi
}
for path in \
  /etc/tor/torrc /etc/dnsmasq.d/onionpi.conf /etc/nftables.conf \
  /etc/onionpi/firewall.nft /etc/nftables.d/onionpi.nft /etc/sysctl.d/70-onionpi.conf \
  /etc/nginx/sites-available/onionpi /etc/onionpi/onionpi.env \
  /etc/nginx/sites-enabled/default /etc/avahi/hosts \
  /etc/modprobe.d/onionpi-regdom.conf /etc/onionpi/tor/bridges.conf \
  /etc/onionpi/tor/policy.conf /etc/issue /etc/issue.net /etc/motd; do
  backup_file "$path"
done

if ! getent group onionpi >/dev/null; then
  groupadd --system onionpi
fi
if ! id onionpi >/dev/null 2>&1; then
  useradd --system --gid onionpi --home-dir /var/lib/onionpi --create-home --shell /usr/sbin/nologin onionpi
fi
for group in debian-tor systemd-journal; do
  if getent group "$group" >/dev/null; then
    usermod -a -G "$group" onionpi
  fi
done

install -d -m 0750 -o onionpi -g onionpi /var/lib/onionpi /var/lib/onionpi/shared
# Root-written state must not share a namespace whose directory entries the web
# account can rename. The application receives read-only group access instead.
install -d -m 0750 -o root -g onionpi /var/lib/onionpi-privileged
install -d -m 0700 -o root -g root /var/cache/onionpi-update
# 0751: /etc/onionpi stays unlistable, but debian-tor can traverse it to reach
# the one directory below that it must read.
install -d -m 0751 -o root -g onionpi /etc/onionpi
install -d -m 0750 -o root -g onionpi /etc/onionpi/tls
# setgid so every file the web interface writes here stays readable by Tor.
BRIDGE_GROUP=onionpi
if getent group debian-tor >/dev/null; then
  BRIDGE_GROUP=debian-tor
fi
install -d -m 2750 -o onionpi -g "$BRIDGE_GROUP" /etc/onionpi/tor
# dnsmasq drops privileges before re-reading its addn-hosts file, so the
# blocklist cannot live under the 0750 /var/lib/onionpi directory.
install -d -m 0755 -o onionpi -g onionpi /etc/onionpi/dns
install -d -m 0755 /etc/tor/torrc.d
install -d -m 0755 "$INSTALL_BASE" "$RELEASES_ROOT"
install -d -m 0755 /usr/local/lib/onionpi

require_regular_state_file() {
  local path="$1"
  if [[ -L "$path" || ! -f "$path" ]]; then
    printf 'Mise à niveau refusée: %s doit être un fichier régulier existant.\n' "$path" >&2
    exit 1
  fi
}

# During an upgrade the web process may still be running. Validate its mutable
# entries, then never chown, chmod or truncate them as root: a directory owner
# could swap a checked entry for a symlink between those operations.
if (( UPGRADE )); then
  for path in \
    /etc/onionpi/tor/bridges.conf \
    /etc/onionpi/tor/policy.conf \
    /etc/onionpi/dns/block.hosts \
    /var/lib/onionpi/relay.state \
    /var/lib/onionpi/blocked-macs.txt \
    /var/lib/onionpi/agent.request \
    /var/lib/onionpi/update.settings.json; do
    require_regular_state_file "$path"
  done
fi

if (( BUILD_FRONTEND )); then
  npm ci --no-audit --no-fund --prefix "$PROJECT_ROOT/frontend"
  npm run build --prefix "$PROJECT_ROOT/frontend"
fi
if [[ ! -s "$PROJECT_ROOT/frontend/dist/index.html" ]]; then
  printf 'Interface web absente: frontend/dist/index.html introuvable.\n' >&2
  exit 1
fi

# A release is prepared under a hidden name and becomes visible only after the
# backend, virtualenv and frontend are complete. Existing releases are never
# edited in place; retrying the same signed version reuses its complete tree.
if [[ ! -f "$RELEASE_DIR/.complete" ]]; then
  if [[ -e "$RELEASE_DIR" || -L "$RELEASE_DIR" ]]; then
    rm -rf -- "$RELEASE_DIR"
  fi
  RELEASE_STAGE="$(mktemp -d "$RELEASES_ROOT/.incoming-$RELEASE_VERSION.XXXXXX")"
  cleanup_release_stage() {
    [[ -z "${RELEASE_STAGE:-}" ]] || rm -rf -- "$RELEASE_STAGE"
  }
  trap cleanup_release_stage EXIT
  install -d -m 0755 "$RELEASE_STAGE/backend" "$RELEASE_STAGE/frontend"
  rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' \
    "$PROJECT_ROOT/backend/" "$RELEASE_STAGE/backend/"
  python3 -m venv "$RELEASE_STAGE/venv"

  PYTHON_TAG="$(python3 -c 'import sys; print(f"cp{sys.version_info.major}{sys.version_info.minor}")')"
  DEB_ARCH="$(dpkg --print-architecture 2>/dev/null || printf unknown)"
  WHEELHOUSE="$PROJECT_ROOT/wheelhouse/$PYTHON_TAG-$DEB_ARCH"
  if [[ -d "$WHEELHOUSE" && -s "$PROJECT_ROOT/wheelhouse/SHA256SUMS" ]]; then
    (cd "$PROJECT_ROOT" && sha256sum -c wheelhouse/SHA256SUMS)
    "$RELEASE_STAGE/venv/bin/pip" install --disable-pip-version-check \
      --no-index --find-links "$WHEELHOUSE" \
      -r "$RELEASE_STAGE/backend/requirements.txt"
  elif (( UPGRADE )); then
    printf 'Wheelhouse %s absent: mise à niveau refusée sans téléchargement.\n' "$WHEELHOUSE" >&2
    exit 1
  else
    "$RELEASE_STAGE/venv/bin/pip" install --disable-pip-version-check --no-cache-dir \
      -r "$RELEASE_STAGE/backend/requirements.txt"
  fi

  rsync -a --delete "$PROJECT_ROOT/frontend/dist/" "$RELEASE_STAGE/frontend/dist/"
  install -m 0644 "$PROJECT_ROOT/README.md" "$RELEASE_STAGE/README.md"
  install -m 0644 "$PROJECT_ROOT/VERSION" "$RELEASE_STAGE/VERSION"
  : >"$RELEASE_STAGE/.complete"
  chown -R root:root "$RELEASE_STAGE"
  # mktemp keeps the staging root private (0700). The final release root must
  # be traversable by the unprivileged service account once it is promoted.
  chmod 0755 "$RELEASE_STAGE"
  sync -f "$RELEASE_STAGE"
  mv -T "$RELEASE_STAGE" "$RELEASE_DIR"
  sync -f "$RELEASES_ROOT"
  RELEASE_STAGE=""
  trap - EXIT
fi

SESSION_SECRET=""
if [[ -r /etc/onionpi/onionpi.env ]]; then
  SESSION_SECRET="$(sed -n 's/^ONIONPI_SESSION_SECRET=//p' /etc/onionpi/onionpi.env | head -n 1)"
fi
if [[ ! "$SESSION_SECRET" =~ ^[0-9a-f]{64}$ ]]; then
  SESSION_SECRET="$(openssl rand -hex 32)"
fi
cat >/etc/onionpi/onionpi.env <<EOF
ONIONPI_DATA_DIR=/var/lib/onionpi
ONIONPI_SHARED_DIR=/var/lib/onionpi/shared
ONIONPI_DB_PATH=/var/lib/onionpi/onionpi.db
ONIONPI_FRONTEND_DIR=/opt/onionpi/current/frontend/dist
ONIONPI_GATEWAY_IP=$GATEWAY_IP
ONIONPI_WIFI_INTERFACE=$WIFI_INTERFACE
ONIONPI_UPSTREAM_INTERFACE=$WAN_INTERFACE
ONIONPI_COOKIE_SECURE=1
ONIONPI_SESSION_SECRET=$SESSION_SECRET
ONIONPI_DEVICE_NAME=OnionPi
ONIONPI_PORT=8080
# The onion service points at the loopback nginx listener, never at uvicorn:
# see the second server block in /etc/nginx/sites-available/onionpi.
ONIONPI_ONION_TARGET_PORT=8081
ONIONPI_TOR_CONFIG_DIR=/etc/onionpi/tor
ONIONPI_RELAY_STATE=/var/lib/onionpi/relay.state
ONIONPI_DNS_FILTER_DIR=/etc/onionpi/dns
ONIONPI_AGENT_RESULT=/var/lib/onionpi-privileged/agent.result
ONIONPI_UPDATE_STATE=/var/lib/onionpi-privileged/update.state
ONIONPI_MAINTENANCE_STATE=/var/lib/onionpi-privileged/maintenance.state
ONIONPI_COUNTRY=$COUNTRY
EOF
chown root:onionpi /etc/onionpi/onionpi.env
chmod 0640 /etc/onionpi/onionpi.env

cat >/etc/onionpi/install.conf <<EOF
# Written by install.sh. Read by onionpi-verify and onionpi-uninstall.
ONIONPI_WAN_INTERFACE=$WAN_INTERFACE
ONIONPI_WIFI_INTERFACE=$WIFI_INTERFACE
ONIONPI_GATEWAY_IP=$GATEWAY_IP
ONIONPI_LAN_NETWORK=$LAN_NETWORK
ONIONPI_BACKUP_DIR=$BACKUP_DIR
EOF
chmod 0644 /etc/onionpi/install.conf

if (( ! UPGRADE )); then
  if [[ -n "$ADMIN_PASSWORD_HASH" ]]; then
    ADMIN_SECRET="$ADMIN_PASSWORD_HASH"
    ADMIN_MODE=--password-hash-stdin
  else
    ADMIN_SECRET="$ADMIN_PASSWORD"
    ADMIN_MODE=--password-stdin
  fi
  printf '%s\n' "$ADMIN_SECRET" | runuser -u onionpi -- env \
    PYTHONPATH="$RELEASE_DIR/backend" \
    ONIONPI_DATA_DIR=/var/lib/onionpi \
    ONIONPI_SHARED_DIR=/var/lib/onionpi/shared \
    ONIONPI_DB_PATH=/var/lib/onionpi/onionpi.db \
    ONIONPI_SESSION_SECRET="$SESSION_SECRET" \
    "$RELEASE_DIR/venv/bin/python" -m onionpi.cli create-admin --username admin \
    --display-name Administrateur "$ADMIN_MODE"
fi
unset ADMIN_SECRET ADMIN_PASSWORD ADMIN_PASSWORD_HASH ONIONPI_ADMIN_PASSWORD ONIONPI_ADMIN_PASSWORD_HASH

sed "s/__GATEWAY_IP__/$GATEWAY_IP/g" \
  "$PROJECT_ROOT/packaging/templates/torrc.onionpi" >/etc/tor/torrc.d/onionpi.conf
chmod 0644 /etc/tor/torrc.d/onionpi.conf

# torrc includes this file unconditionally, so it must exist before Tor starts.
# An upgrade keeps the bridges already configured from the web interface.
if (( ! UPGRADE )) && [[ ! -s /etc/onionpi/tor/bridges.conf ]]; then
  cat >/etc/onionpi/tor/bridges.conf <<'EOF'
# Généré par OnionPi. Ne pas modifier à la main:
# le fichier est réécrit à chaque changement depuis l’interface web.
UseBridges 0
EOF
  chown onionpi:"$BRIDGE_GROUP" /etc/onionpi/tor/bridges.conf
  chmod 0640 /etc/onionpi/tor/bridges.conf
fi

# Exit-node policy fragment. Like the bridge file it is included
# unconditionally, so Tor refuses to start when it is missing.
if (( ! UPGRADE )) && [[ ! -s /etc/onionpi/tor/policy.conf ]]; then
  cat >/etc/onionpi/tor/policy.conf <<'EOF'
# Généré par OnionPi. Ne pas modifier à la main:
# le fichier est réécrit à chaque changement depuis l’interface web.
# Aucun pays de sortie imposé.
EOF
  chown onionpi:"$BRIDGE_GROUP" /etc/onionpi/tor/policy.conf
  chmod 0640 /etc/onionpi/tor/policy.conf
fi

# dnsmasq refuses to start when an addn-hosts file is missing: create an empty
# blocklist so a fresh install and a disabled filter behave the same way.
if (( ! UPGRADE )) && [[ ! -e /etc/onionpi/dns/block.hosts ]]; then
  printf '# Généré par OnionPi. Filtrage DNS désactivé.\n' >/etc/onionpi/dns/block.hosts
  chown onionpi:onionpi /etc/onionpi/dns/block.hosts
  chmod 0644 /etc/onionpi/dns/block.hosts
fi

if (( ! UPGRADE )) && [[ ! -s /var/lib/onionpi/relay.state ]]; then
  printf 'disabled\n' >/var/lib/onionpi/relay.state
  chown onionpi:onionpi /var/lib/onionpi/relay.state
  chmod 0640 /var/lib/onionpi/relay.state
fi

# Queue for the privileged agent: the application writes the request, root
# writes the answer next to it.
if (( ! UPGRADE )) && [[ ! -e /var/lib/onionpi/blocked-macs.txt ]]; then
  printf '# Généré par OnionPi. Une adresse MAC par ligne.\n' >/var/lib/onionpi/blocked-macs.txt
  chown onionpi:onionpi /var/lib/onionpi/blocked-macs.txt
  chmod 0640 /var/lib/onionpi/blocked-macs.txt
fi
if (( ! UPGRADE )); then
  : >>/var/lib/onionpi/agent.request
  chown onionpi:onionpi /var/lib/onionpi/agent.request
  chmod 0640 /var/lib/onionpi/agent.request
fi
if [[ ! -e /var/lib/onionpi-privileged/agent.result ]]; then
  install -m 0640 -o root -g onionpi /dev/null /var/lib/onionpi-privileged/agent.result
elif [[ -L /var/lib/onionpi-privileged/agent.result || ! -f /var/lib/onionpi-privileged/agent.result ]]; then
  printf 'État privilégié invalide: /var/lib/onionpi-privileged/agent.result\n' >&2
  exit 1
else
  chown root:onionpi /var/lib/onionpi-privileged/agent.result
  chmod 0640 /var/lib/onionpi-privileged/agent.result
fi
if ! grep -Fqx '%include /etc/tor/torrc.d/onionpi.conf' /etc/tor/torrc; then
  printf '\n%%include /etc/tor/torrc.d/onionpi.conf\n' >>/etc/tor/torrc
fi

sed \
  -e "s/__WIFI_INTERFACE__/$WIFI_INTERFACE/g" \
  -e "s/__GATEWAY_IP__/$GATEWAY_IP/g" \
  -e "s/__DHCP_START__/$DHCP_START/g" \
  -e "s/__DHCP_END__/$DHCP_END/g" \
  "$PROJECT_ROOT/packaging/templates/dnsmasq.onionpi" >/etc/dnsmasq.d/onionpi.conf
chmod 0644 /etc/dnsmasq.d/onionpi.conf

if (( LAN_SSH )); then
  LAN_SSH_RULE='    tcp dport 22 accept'
else
  LAN_SSH_RULE='    # SSH depuis le Wi-Fi refusé (--no-lan-ssh)'
fi
sed \
  -e "s/__WIFI_INTERFACE__/$WIFI_INTERFACE/g" \
  -e "s|__LAN_NETWORK__|$LAN_NETWORK|g" \
  -e "s|__LAN_SSH_RULE__|$LAN_SSH_RULE|g" \
  "$PROJECT_ROOT/packaging/templates/onionpi.nft" >/etc/onionpi/firewall.nft
chmod 0644 /etc/onionpi/firewall.nft
# Migration from the early development layout. The file belongs exclusively
# to OnionPi; removing it prevents a user wildcard include from loading twice.
rm -f /etc/nftables.d/onionpi.nft

sed "s/__WIFI_INTERFACE__/$WIFI_INTERFACE/g" \
  "$PROJECT_ROOT/packaging/templates/sysctl.onionpi" >/etc/sysctl.d/70-onionpi.conf
chmod 0644 /etc/sysctl.d/70-onionpi.conf

if [[ ! -s /etc/onionpi/tls/onionpi.key || ! -s /etc/onionpi/tls/onionpi.crt ]]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 825 \
    -subj '/CN=onionpi.local/O=OnionPi local appliance' \
    -addext "subjectAltName=DNS:onionpi.local,IP:$GATEWAY_IP" \
    -keyout /etc/onionpi/tls/onionpi.key -out /etc/onionpi/tls/onionpi.crt
fi
chown root:onionpi /etc/onionpi/tls/onionpi.key
chmod 0640 /etc/onionpi/tls/onionpi.key
chmod 0644 /etc/onionpi/tls/onionpi.crt
sed "s/__GATEWAY_IP__/$GATEWAY_IP/g" \
  "$PROJECT_ROOT/packaging/templates/nginx.onionpi" >/etc/nginx/sites-available/onionpi
ln -sfn /etc/nginx/sites-available/onionpi /etc/nginx/sites-enabled/onionpi
rm -f /etc/nginx/sites-enabled/default

grep -vE '[[:space:]]onionpi\.local([[:space:]]|$)' /etc/avahi/hosts 2>/dev/null >/run/onionpi-avahi-hosts || true
printf '%s onionpi.local\n' "$GATEWAY_IP" >>/run/onionpi-avahi-hosts
install -m 0644 /run/onionpi-avahi-hosts /etc/avahi/hosts

install -m 0644 "$PROJECT_ROOT/packaging/systemd/onionpi.service" /etc/systemd/system/onionpi.service
install -m 0644 "$PROJECT_ROOT/packaging/systemd/onionpi-ap.service" /etc/systemd/system/onionpi-ap.service
install -m 0644 "$PROJECT_ROOT/packaging/systemd/onionpi-firewall.service" /etc/systemd/system/onionpi-firewall.service
install -m 0644 "$PROJECT_ROOT/packaging/systemd/onionpi-relay.service" /etc/systemd/system/onionpi-relay.service
install -m 0644 "$PROJECT_ROOT/packaging/systemd/onionpi-relay.path" /etc/systemd/system/onionpi-relay.path
install -m 0644 "$PROJECT_ROOT/packaging/systemd/onionpi-agent.service" /etc/systemd/system/onionpi-agent.service
install -m 0644 "$PROJECT_ROOT/packaging/systemd/onionpi-agent.path" /etc/systemd/system/onionpi-agent.path
install -m 0644 "$PROJECT_ROOT/packaging/systemd/onionpi-boot-banner.service" /etc/systemd/system/onionpi-boot-banner.service
install -m 0644 "$PROJECT_ROOT/packaging/systemd/onionpi-update.service" /etc/systemd/system/onionpi-update.service
install -m 0644 "$PROJECT_ROOT/packaging/systemd/onionpi-update.timer" /etc/systemd/system/onionpi-update.timer
install -m 0644 "$PROJECT_ROOT/packaging/systemd/onionpi-update-recover.service" /etc/systemd/system/onionpi-update-recover.service
install -m 0755 "$PROJECT_ROOT/packaging/onionpi-relay-apply.sh" /usr/local/sbin/onionpi-relay-apply
install -m 0755 "$PROJECT_ROOT/packaging/onionpi-firewall-apply.sh" /usr/local/sbin/onionpi-firewall-apply
install -m 0755 "$PROJECT_ROOT/packaging/onionpi-devices-apply.sh" /usr/local/sbin/onionpi-devices-apply
install -m 0755 "$PROJECT_ROOT/packaging/onionpi-agent-apply.sh" /usr/local/sbin/onionpi-agent-apply
install -m 0755 "$PROJECT_ROOT/packaging/onionpi-update.sh" /usr/local/sbin/onionpi-update
install -m 0755 "$PROJECT_ROOT/packaging/onionpi-maintenance.sh" /usr/local/sbin/onionpi-maintenance
install -m 0755 "$PROJECT_ROOT/packaging/verify.sh" /usr/local/sbin/onionpi-verify
install -m 0755 "$PROJECT_ROOT/packaging/uninstall.sh" /usr/local/sbin/onionpi-uninstall

# --------------------------------------------------------- update client ----
# Public half of the release signing key, in the binary form gpgv reads. It
# travels with the code: an update can only be installed if it is signed by the
# key that was already on the appliance before that update existed.
if [[ -s "$PROJECT_ROOT/packaging/keys/onionpi-release.asc" ]]; then
  gpg --dearmor --yes --output /run/onionpi-release-key.gpg \
    <"$PROJECT_ROOT/packaging/keys/onionpi-release.asc"
  install -m 0644 /run/onionpi-release-key.gpg /etc/onionpi/update-signing-key.gpg
  rm -f /run/onionpi-release-key.gpg
else
  printf 'Clé de signature absente: les mises à jour ne seront vérifiées que par empreinte.\n' >&2
fi
# A first installation writes the policy; an upgrade keeps the file already
# there, since an operator may have edited it by hand.
if [[ ! -s /etc/onionpi/update.conf ]]; then
  sed \
    -e "s|__UPDATE_REPOSITORY__|$UPDATE_REPOSITORY|g" \
    -e "s|__UPDATE_CHANNEL__|$UPDATE_CHANNEL|g" \
    -e "s|__UPDATE_SCHEDULE__|$UPDATE_SCHEDULE|g" \
    -e "s|__UPDATE_ENABLED__|$UPDATE_ENABLED|g" \
    -e "s|__UPDATE_APPLY__|$UPDATE_APPLY|g" \
    "$PROJECT_ROOT/packaging/templates/update.conf" >/etc/onionpi/update.conf
  chmod 0644 /etc/onionpi/update.conf
fi
# Preferences chosen from the web interface. Owned by the application, and
# revalidated field by field by onionpi-update before anything is read from it.
if (( ! UPGRADE )) && [[ ! -e /var/lib/onionpi/update.settings.json ]]; then
  printf '{"channel": "%s", "schedule": "%s", "enabled": %s, "apply": %s}\n' \
    "$UPDATE_CHANNEL" "$UPDATE_SCHEDULE" \
    "$( ((UPDATE_ENABLED)) && printf true || printf false )" \
    "$( ((UPDATE_APPLY)) && printf true || printf false )" \
    >/var/lib/onionpi/update.settings.json
  chown onionpi:onionpi /var/lib/onionpi/update.settings.json
  chmod 0640 /var/lib/onionpi/update.settings.json
fi
if [[ ! -e /var/lib/onionpi-privileged/update.state ]]; then
install -m 0640 -o root -g onionpi /dev/null /var/lib/onionpi-privileged/update.state
elif [[ -L /var/lib/onionpi-privileged/update.state || ! -f /var/lib/onionpi-privileged/update.state ]]; then
  printf 'État privilégié invalide: /var/lib/onionpi-privileged/update.state\n' >&2
  exit 1
else
  chown root:onionpi /var/lib/onionpi-privileged/update.state
  chmod 0640 /var/lib/onionpi-privileged/update.state
fi

# ------------------------------------------------------ console identity ----
# Boot console, login prompt, MOTD and shell all draw the same ASCII banner.
install -m 0755 "$PROJECT_ROOT/packaging/assets/onionpi-banner.sh" /usr/local/lib/onionpi/onionpi-banner.sh
install -m 0755 "$PROJECT_ROOT/packaging/assets/onionpi-status.sh" /usr/local/bin/onionpi-status
install -d -m 0755 /etc/update-motd.d
install -m 0755 "$PROJECT_ROOT/packaging/assets/onionpi-motd" /etc/update-motd.d/10-onionpi
install -m 0644 "$PROJECT_ROOT/packaging/assets/onionpi-shell.sh" /etc/profile.d/onionpi.sh
# Raspberry Pi OS prints its own paragraph in /etc/motd; ours replaces it
# rather than being appended to it.
: >/etc/motd
# agetty consumes a backslash and the character after it, so every backslash
# of the drawing has to be doubled before it reaches /etc/issue.
{
  ONIONPI_FORCE_COLOR=1 /usr/local/lib/onionpi/onionpi-banner.sh | sed 's/\\/\\\\/g'
  printf '  Interface web  https://onionpi.local\n'
  printf '  Wi-Fi          %s\n' "$SSID"
  printf '  Console        \\n sur \\l — \\4{%s}\n\n' "$WIFI_INTERFACE"
} >/etc/issue
chmod 0644 /etc/issue
# /etc/issue.net is shown before an SSH login: no IP address, no SSID.
/usr/local/lib/onionpi/onionpi-banner.sh --plain >/etc/issue.net
chmod 0644 /etc/issue.net

printf 'options cfg80211 ieee80211_regdom=%s\n' "$COUNTRY" >/etc/modprobe.d/onionpi-regdom.conf
chmod 0644 /etc/modprobe.d/onionpi-regdom.conf
iw reg set "$COUNTRY"

# An upgrade leaves the access point profile alone: it holds the PSK, and
# rewriting it would drop every connected client for no gain.
if (( ! UPGRADE )); then
  if nmcli -t -f NAME connection show | grep -Fqx 'onionpi-ap'; then
    nmcli connection modify onionpi-ap \
      connection.interface-name "$WIFI_INTERFACE" \
      802-11-wireless.ssid "$SSID"
  else
    nmcli connection add type wifi ifname "$WIFI_INTERFACE" con-name onionpi-ap ssid "$SSID"
  fi
  nmcli connection modify onionpi-ap \
    connection.autoconnect no connection.autoconnect-priority 100 \
    802-11-wireless.mode ap 802-11-wireless.band "$WIFI_BAND" 802-11-wireless.channel "$WIFI_CHANNEL" \
    802-11-wireless.powersave 2 802-11-wireless.hidden no 802-11-wireless.ap-isolation yes \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.psk "${WIFI_PSK:-$WIFI_PASSWORD}" \
    802-11-wireless-security.pairwise ccmp \
    802-11-wireless-security.group ccmp \
    ipv4.method manual ipv4.addresses "$GATEWAY_CIDR" ipv4.never-default yes \
    ipv4.gateway '' ipv4.dns '' ipv6.method disabled
fi
# Older releases let NetworkManager autoconnect the AP independently. The
# dedicated systemd unit below is now the only activation path, so firewall
# failure cannot leave an unprotected AP online.
nmcli connection modify onionpi-ap connection.autoconnect no
unset WIFI_PASSWORD WIFI_PSK ONIONPI_WIFI_PASSWORD ONIONPI_WIFI_PSK

tor --verify-config -f /etc/tor/torrc
dnsmasq --test
sysctl --system >/dev/null

# Commit the application version in one rename. Relative links keep the tree
# relocatable and `mv -T` never exposes a half-written link to systemd.
current_temporary="$INSTALL_BASE/.current-$RELEASE_VERSION-$$"
ln -s "releases/$RELEASE_VERSION" "$current_temporary"
mv -Tf "$current_temporary" "$CURRENT_LINK"
ln -sfn current/VERSION "$INSTALL_BASE/VERSION"
sync -f "$INSTALL_BASE"

systemctl daemon-reload
# Without an RTC the Pi boots in 1970 and Tor rejects every relay certificate.
systemctl enable --now systemd-timesyncd || true
systemctl enable tor dnsmasq nftables onionpi-firewall onionpi-ap nginx avahi-daemon NetworkManager onionpi
systemctl enable onionpi-boot-banner.service
systemctl enable onionpi-update-recover.service
systemctl enable --now onionpi-relay.path
systemctl enable --now onionpi-agent.path
# Writes /etc/systemd/system/onionpi-update.timer.d/schedule.conf from the
# policy above, then enables or disables the timer accordingly.
/usr/local/sbin/onionpi-update --write-timer --quiet || \
  printf 'Minuterie de mise à jour non appliquée: lancez « sudo onionpi-update --write-timer ».\n' >&2
systemctl restart tor
# Do not restart the generic nftables service here: it can flush the live table
# while clients are still associated. The dedicated helper replaces our table
# atomically and onionpi-ap follows its success state.
systemctl restart onionpi-firewall
# The AP is a dependent unit of the kill switch. It may briefly disconnect on
# upgrade, but can never remain active after a failed firewall replacement.
systemctl restart onionpi-ap
# nginx -t opens the configured listen sockets. The AP address must therefore
# exist first; otherwise first boot races NetworkManager and fails with EADDRNOTAVAIL.
nginx -t
systemctl restart dnsmasq nginx avahi-daemon onionpi

if (( UPGRADE )); then
  printf '\nMise à jour terminée (version %s). Sauvegarde: %s\n' \
    "$(head -n 1 "$CURRENT_LINK/VERSION")" "$BACKUP_DIR"
  # onionpi-update runs onionpi-verify itself and rolls back on failure, so a
  # second full check here would only double the downtime.
  exit 0
fi

printf '\nInstallation terminée. Sauvegarde des fichiers modifiés: %s\n' "$BACKUP_DIR"
printf '1. Connectez-vous au Wi-Fi « %s ».\n' "$SSID"
printf '2. Ouvrez https://onionpi.local (ou https://%s).\n' "$GATEWAY_IP"
printf '3. Acceptez une fois le certificat local, puis connectez-vous avec « admin ».\n\n'

if ONIONPI_VERIFY_HTTP_WAIT_SECONDS=30 /usr/local/sbin/onionpi-verify; then
  printf '\nOnionPi est opérationnel.\n'
else
  printf '\nUn contrôle a échoué. Consultez: journalctl -u onionpi -u tor -u dnsmasq -n 100\n' >&2
  exit 1
fi
