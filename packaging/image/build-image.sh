#!/usr/bin/env bash
# Builds a flashable Raspberry Pi image that installs OnionPi by itself on the
# first boot. Runs on macOS (hdiutil) and Linux (mtools), no root required.
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$PROJECT_ROOT/build"
BASE_URL="https://downloads.raspberrypi.com/raspios_lite_arm64_latest"

SOURCE=""
SOURCE_SHA256="${ONIONPI_BASE_SHA256:-}"
OUTPUT=""
HOSTNAME_VALUE="onionpi"
LOGIN="onionpi"
SSID="OnionPi Wi-Fi"
COUNTRY="BE"
BAND="bg"
CHANNEL=""
WAN_INTERFACE="eth0"
WIFI_INTERFACE="wlan0"
MESH_INTERFACE=""
MESH_ID="OnionPi-Mesh"
MESH_ADDRESS=""
MESH_BAND="a"
MESH_CHANNEL=""
SSH_KEY=""
LAN_SSH=1
COMPRESS=0
KEEP_WORK=0

usage() {
  cat <<'EOF'
Construit une image OnionPi prête à flasher.

Usage: ./packaging/image/build-image.sh [options]
  --source CHEMIN|URL  image Raspberry Pi OS de base (.img, .img.xz ou URL)
  --source-sha256 X    empreinte SHA-256 (ou fichier qui la contient)
  --out CHEMIN         fichier image produit (défaut: build/onionpi-<date>.img)
  --hostname NOM       nom réseau de la Pi (défaut: onionpi)
  --login NOM          compte système créé sur la Pi (défaut: onionpi)
  --ssid NOM           nom du Wi-Fi OnionPi (défaut: OnionPi Wi-Fi)
  --country CODE       code pays ISO à deux lettres (défaut: BE)
  --band bg|a          bande du point d'accès (défaut: bg)
  --channel N          canal (défaut: 7 en bg, 36 en a)
  --wan INTERFACE      interface Internet de la Pi (défaut: eth0)
  --wifi INTERFACE     interface point d'accès (défaut: wlan0)
  --mesh INTERFACE     radio Wi-Fi dédiée au maillage 802.11s
  --mesh-id NOM        identifiant partagé du mesh (défaut: OnionPi-Mesh)
  --mesh-address CIDR  adresse unique du nœud dans 10.43.0.0/16
  --mesh-band bg|a     bande du backhaul mesh (défaut: a)
  --mesh-channel N     canal commun à tous les nœuds (défaut: 36 en a)
  --ssh-key FICHIER    clé publique SSH à installer sur le compte
  --no-lan-ssh         interdire SSH depuis le Wi-Fi OnionPi
  --compress           produire aussi une archive .img.xz
  --keep-work          conserver les fichiers intermédiaires
  -h, --help           afficher cette aide

Mots de passe: définissez ONIONPI_WIFI_PASSWORD, ONIONPI_ADMIN_PASSWORD,
ONIONPI_LOGIN_PASSWORD et ONIONPI_MESH_PASSWORD si le mesh est activé. Sinon,
des mots de passe aléatoires sont générés et écrits à côté de l'image dans un
fichier lisible par vous seul.

L'image ne contient jamais ces mots de passe en clair: seulement le PSK Wi-Fi
dérivé, le condensat scrypt du compte web et le condensat SHA-512 du compte
système.
EOF
}

while (($#)); do
  case "$1" in
    --source) SOURCE="${2:?source manquante}"; shift 2 ;;
    --source-sha256) SOURCE_SHA256="${2:?empreinte manquante}"; shift 2 ;;
    --out) OUTPUT="${2:?chemin manquant}"; shift 2 ;;
    --hostname) HOSTNAME_VALUE="${2:?nom manquant}"; shift 2 ;;
    --login) LOGIN="${2:?nom manquant}"; shift 2 ;;
    --ssid) SSID="${2:?SSID manquant}"; shift 2 ;;
    --country) COUNTRY="${2:?code pays manquant}"; shift 2 ;;
    --band) BAND="${2:?bande manquante}"; shift 2 ;;
    --channel) CHANNEL="${2:?canal manquant}"; shift 2 ;;
    --wan) WAN_INTERFACE="${2:?interface manquante}"; shift 2 ;;
    --wifi) WIFI_INTERFACE="${2:?interface manquante}"; shift 2 ;;
    --mesh) MESH_INTERFACE="${2:?interface mesh manquante}"; shift 2 ;;
    --mesh-id) MESH_ID="${2:?identifiant mesh manquant}"; shift 2 ;;
    --mesh-address) MESH_ADDRESS="${2:?adresse mesh manquante}"; shift 2 ;;
    --mesh-band) MESH_BAND="${2:?bande mesh manquante}"; shift 2 ;;
    --mesh-channel) MESH_CHANNEL="${2:?canal mesh manquant}"; shift 2 ;;
    --ssh-key) SSH_KEY="${2:?fichier manquant}"; shift 2 ;;
    --no-lan-ssh) LAN_SSH=0; shift ;;
    --compress) COMPRESS=1; shift ;;
    --keep-work) KEEP_WORK=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Option inconnue: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$BAND" in
  bg) CHANNEL="${CHANNEL:-7}" ;;
  a) CHANNEL="${CHANNEL:-36}" ;;
  *) printf 'Bande invalide: %s (bg ou a).\n' "$BAND" >&2; exit 1 ;;
esac
case "$MESH_BAND" in
  bg) MESH_CHANNEL="${MESH_CHANNEL:-7}" ;;
  a) MESH_CHANNEL="${MESH_CHANNEL:-36}" ;;
  *) printf 'Bande mesh invalide: %s (bg ou a).\n' "$MESH_BAND" >&2; exit 1 ;;
esac
[[ "$COUNTRY" =~ ^[A-Z]{2}$ ]] || { printf 'Code pays invalide: %s\n' "$COUNTRY" >&2; exit 1; }
[[ "$HOSTNAME_VALUE" =~ ^[a-z0-9-]{1,32}$ ]] || { printf 'Nom d’hôte invalide.\n' >&2; exit 1; }
[[ "$LOGIN" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { printf 'Nom de compte invalide.\n' >&2; exit 1; }
(( ${#SSID} >= 1 && ${#SSID} <= 32 )) || { printf 'SSID invalide.\n' >&2; exit 1; }
[[ -z "$MESH_INTERFACE" || "$MESH_INTERFACE" =~ ^[A-Za-z0-9_.:-]{1,32}$ ]] \
  || { printf 'Interface mesh invalide.\n' >&2; exit 1; }
[[ -z "$MESH_INTERFACE" || ( "$MESH_INTERFACE" != "$WAN_INTERFACE" && "$MESH_INTERFACE" != "$WIFI_INTERFACE" ) ]] \
  || { printf 'La radio mesh doit être distincte du WAN et du point d’accès.\n' >&2; exit 1; }
[[ "$MESH_ID" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$ ]] \
  || { printf 'Identifiant mesh invalide.\n' >&2; exit 1; }
[[ -z "$MESH_ADDRESS" || "$MESH_ADDRESS" =~ ^10\.43\.[0-9]{1,3}\.[0-9]{1,3}/16$ ]] \
  || { printf 'Adresse mesh invalide.\n' >&2; exit 1; }
if [[ -n "$MESH_ADDRESS" ]]; then
  IFS=. read -r _ _ mesh_third mesh_fourth <<<"${MESH_ADDRESS%/*}"
  ((10#$mesh_third <= 255 && 10#$mesh_fourth <= 255)) \
    || { printf 'Adresse mesh hors plage.\n' >&2; exit 1; }
  [[ "$MESH_ADDRESS" != 10.43.0.0/16 && "$MESH_ADDRESS" != 10.43.255.255/16 ]] \
    || { printf 'Adresse mesh réservée.\n' >&2; exit 1; }
fi

for tool in curl python3 tar openssl shasum; do
  command -v "$tool" >/dev/null 2>&1 || command -v "${tool/shasum/sha256sum}" >/dev/null 2>&1 || {
    printf 'Outil requis absent: %s\n' "$tool" >&2; exit 1; }
done
sha256() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1"; else sha256sum "$1"; fi
}

# The admin digest uses hashlib.scrypt, which the system Python on macOS does
# not always provide. Prefer the project virtualenv when it exists.
PYTHON=""
for candidate in "$PROJECT_ROOT/.venv/bin/python" python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
    && "$candidate" -c 'import hashlib; hashlib.scrypt(b"x", salt=b"y", n=2, r=1, p=1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  printf 'Aucun Python avec hashlib.scrypt. Créez le venv du projet:\n' >&2
  printf '  python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt\n' >&2
  exit 1
fi

mkdir -p "$WORK_DIR"
STAMP="$(date -u +%Y%m%d)"
OUTPUT="${OUTPUT:-$WORK_DIR/onionpi-$STAMP.img}"
mkdir -p "$(dirname -- "$OUTPUT")"

# ---------------------------------------------------------------- frontend --
if [[ ! -s "$PROJECT_ROOT/frontend/dist/index.html" ]]; then
  command -v npm >/dev/null 2>&1 || {
    printf 'frontend/dist absent et npm introuvable: impossible de construire l’interface.\n' >&2
    exit 1; }
  printf '==> Construction de l’interface web\n'
  (cd "$PROJECT_ROOT/frontend" && npm ci --no-audit --no-fund && npm run build)
fi

# ----------------------------------------------------------------- secrets --
printf '==> Préparation des secrets\n'
generated_notes=()
random_phrase() {
  python3 - "$1" <<'PY'
import secrets, string, sys
alphabet = string.ascii_letters + string.digits
print("".join(secrets.choice(alphabet) for _ in range(int(sys.argv[1]))))
PY
}
WIFI_PASSWORD="${ONIONPI_WIFI_PASSWORD:-}"
ADMIN_PASSWORD="${ONIONPI_ADMIN_PASSWORD:-}"
LOGIN_PASSWORD="${ONIONPI_LOGIN_PASSWORD:-}"
MESH_PASSWORD="${ONIONPI_MESH_PASSWORD:-}"
if [[ -z "$WIFI_PASSWORD" ]]; then WIFI_PASSWORD="$(random_phrase 20)"; generated_notes+=(wifi); fi
if [[ -z "$ADMIN_PASSWORD" ]]; then ADMIN_PASSWORD="$(random_phrase 20)"; generated_notes+=(admin); fi
if [[ -z "$LOGIN_PASSWORD" ]]; then LOGIN_PASSWORD="$(random_phrase 20)"; generated_notes+=(login); fi
if [[ -n "$MESH_INTERFACE" && -z "$MESH_PASSWORD" ]]; then
  MESH_PASSWORD="$(random_phrase 20)"
  generated_notes+=(mesh)
fi
(( ${#WIFI_PASSWORD} >= 12 && ${#WIFI_PASSWORD} <= 63 )) || {
  printf 'ONIONPI_WIFI_PASSWORD doit contenir entre 12 et 63 caractères.\n' >&2; exit 1; }
(( ${#ADMIN_PASSWORD} >= 12 )) || {
  printf 'ONIONPI_ADMIN_PASSWORD doit contenir au moins 12 caractères.\n' >&2; exit 1; }
(( ${#LOGIN_PASSWORD} >= 12 )) || {
  printf 'ONIONPI_LOGIN_PASSWORD doit contenir au moins 12 caractères.\n' >&2; exit 1; }
[[ -z "$MESH_INTERFACE" ]] || (( ${#MESH_PASSWORD} >= 12 && ${#MESH_PASSWORD} <= 63 )) || {
  printf 'ONIONPI_MESH_PASSWORD doit contenir entre 12 et 63 caractères.\n' >&2; exit 1; }

WIFI_PSK="$(SSID="$SSID" WIFI_PASSWORD="$WIFI_PASSWORD" python3 - <<'PY'
import hashlib, os
psk = hashlib.pbkdf2_hmac(
    "sha1", os.environ["WIFI_PASSWORD"].encode(), os.environ["SSID"].encode(), 4096, 32
)
print(psk.hex())
PY
)"
ADMIN_HASH="$(ADMIN_PASSWORD="$ADMIN_PASSWORD" PYTHONPATH="$PROJECT_ROOT/backend" "$PYTHON" - <<'PY'
import os
from onionpi.auth import hash_password
print(hash_password(os.environ["ADMIN_PASSWORD"]))
PY
)"
# LibreSSL, which macOS ships as /usr/bin/openssl, cannot produce SHA-512
# crypt digests. Look for a build that can before giving up.
sha512_crypt() {
  local candidate
  for candidate in openssl /opt/homebrew/bin/openssl /opt/homebrew/opt/openssl@3/bin/openssl \
    /usr/local/opt/openssl@3/bin/openssl; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && printf 'probe' | "$candidate" passwd -6 -stdin >/dev/null 2>&1; then
      printf '%s' "$1" | "$candidate" passwd -6 -stdin
      return 0
    fi
  done
  if command -v mkpasswd >/dev/null 2>&1; then
    mkpasswd -m sha512crypt -s <<<"$1"
    return 0
  fi
  return 1
}
if ! LOGIN_HASH="$(sha512_crypt "$LOGIN_PASSWORD")"; then
  printf 'Aucun outil ne sait produire un condensat SHA-512 crypt.\n' >&2
  printf 'Sur macOS: brew install openssl@3. Sur Debian: apt-get install whois.\n' >&2
  exit 1
fi

# ----------------------------------------------------------- base image ----
resolve_source() {
  if [[ -z "$SOURCE" ]]; then
    local cached="$WORK_DIR/raspios-lite-arm64.img.xz"
    local checksum_file="$WORK_DIR/raspios-lite-arm64.img.xz.sha256"
    curl --fail --location --silent --show-error \
      --output "$checksum_file.part" "$BASE_URL.sha256"
    mv "$checksum_file.part" "$checksum_file"
    SOURCE_SHA256="$checksum_file"
    local expected current=""
    expected="$(grep -Eo '[0-9A-Fa-f]{64}' "$checksum_file" | head -n 1 | tr '[:upper:]' '[:lower:]')"
    [[ -n "$expected" ]] || {
      printf 'Empreinte officielle Raspberry Pi OS illisible.\n' >&2; exit 1; }
    [[ ! -s "$cached" ]] || current="$(sha256 "$cached" | awk '{print tolower($1)}')"
    if [[ "$current" != "$expected" ]]; then
      printf '==> Téléchargement de Raspberry Pi OS Lite arm64\n'
      curl --fail --location --progress-bar --output "$cached.part" "$BASE_URL"
      mv "$cached.part" "$cached"
    else
      printf '==> Image de base déjà téléchargée: %s\n' "$cached"
    fi
    SOURCE="$cached"
  elif [[ "$SOURCE" =~ ^https?:// ]]; then
    local cached
    cached="$WORK_DIR/$(basename -- "${SOURCE%%\?*}")"
    [[ -s "$cached" ]] || curl --fail --location --progress-bar --output "$cached" "$SOURCE"
    SOURCE="$cached"
  fi
  [[ -s "$SOURCE" ]] || { printf 'Image de base introuvable: %s\n' "$SOURCE" >&2; exit 1; }

  local expected=""
  if [[ -f "$SOURCE_SHA256" ]]; then
    expected="$(grep -Eo '[0-9A-Fa-f]{64}' "$SOURCE_SHA256" | head -n 1 | tr '[:upper:]' '[:lower:]')"
  elif [[ "$SOURCE_SHA256" =~ ^[0-9A-Fa-f]{64}$ ]]; then
    expected="$(tr '[:upper:]' '[:lower:]' <<<"$SOURCE_SHA256")"
  fi
  [[ -n "$expected" ]] || {
    printf 'Une empreinte SHA-256 est obligatoire (--source-sha256 ou ONIONPI_BASE_SHA256).\n' >&2
    exit 1
  }
  local actual
  actual="$(sha256 "$SOURCE" | awk '{print tolower($1)}')"
  [[ "$actual" == "$expected" ]] || {
    printf 'Empreinte SHA-256 de l’image de base incorrecte: %s\n' "$actual" >&2
    exit 1
  }
  printf '==> Image de base vérifiée: %s\n' "$actual"
}
resolve_source

printf '==> Préparation de %s\n' "$OUTPUT"
rm -f "$OUTPUT"
case "$SOURCE" in
  *.xz)
    command -v xz >/dev/null 2>&1 || { printf 'xz requis pour décompresser l’image.\n' >&2; exit 1; }
    xz --decompress --stdout "$SOURCE" >"$OUTPUT" ;;
  *.zip)
    command -v unzip >/dev/null 2>&1 || { printf 'unzip requis.\n' >&2; exit 1; }
    unzip -p "$SOURCE" '*.img' >"$OUTPUT" ;;
  *) cp "$SOURCE" "$OUTPUT" ;;
esac

# ------------------------------------------------------------- payload -----
STAGE="$WORK_DIR/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/onionpi"
printf '==> Construction de la charge utile\n'
tar_options=()
[[ "$(uname -s)" == "Darwin" ]] && tar_options+=(--no-xattrs)
COPYFILE_DISABLE=1 tar "${tar_options[@]}" -czf "$STAGE/onionpi/payload.tar.gz" -C "$(dirname -- "$PROJECT_ROOT")" \
  --exclude='.git' --exclude='node_modules' --exclude='.venv' --exclude='.data' \
  --exclude='build' --exclude='__pycache__' --exclude='.pytest_cache' \
  --exclude='.DS_Store' --exclude='*.tsbuildinfo' \
  "$(basename -- "$PROJECT_ROOT")"

{
  # This file is sourced by Bash on first boot. Quote every value: the default
  # SSID contains a space, and password hashes can contain shell metacharacters.
  printf 'ONIONPI_WAN_INTERFACE=%q\n' "$WAN_INTERFACE"
  printf 'ONIONPI_WIFI_INTERFACE=%q\n' "$WIFI_INTERFACE"
  printf 'ONIONPI_MESH_INTERFACE=%q\n' "$MESH_INTERFACE"
  printf 'ONIONPI_MESH_ID=%q\n' "$MESH_ID"
  printf 'ONIONPI_MESH_ADDRESS=%q\n' "$MESH_ADDRESS"
  printf 'ONIONPI_MESH_BAND=%q\n' "$MESH_BAND"
  printf 'ONIONPI_MESH_CHANNEL=%q\n' "$MESH_CHANNEL"
  printf 'ONIONPI_SSID=%q\n' "$SSID"
  printf 'ONIONPI_COUNTRY=%q\n' "$COUNTRY"
  printf 'ONIONPI_BAND=%q\n' "$BAND"
  printf 'ONIONPI_CHANNEL=%q\n' "$CHANNEL"
  printf 'ONIONPI_LAN_SSH=%q\n' "$LAN_SSH"
  printf 'ONIONPI_WIFI_PSK=%q\n' "$WIFI_PSK"
  printf 'ONIONPI_ADMIN_PASSWORD_HASH=%q\n' "$ADMIN_HASH"
  printf 'ONIONPI_MESH_PASSWORD=%q\n' "$MESH_PASSWORD"
} >"$STAGE/onionpi/install.env"
bash -n "$STAGE/onionpi/install.env"
chmod 0600 "$STAGE/onionpi/install.env"
printf '%s\n' "$HOSTNAME_VALUE" >"$STAGE/onionpi/hostname"
printf '%s\n' "$COUNTRY" >"$STAGE/onionpi/country"
printf '%s:%s\n' "$LOGIN" "$LOGIN_HASH" >"$STAGE/onionpi/userconf.txt"
cp "$IMAGE_DIR/onionpi-firstboot.sh" "$STAGE/onionpi/onionpi-firstboot.sh"
cp "$IMAGE_DIR/onionpi-firstboot.service" "$STAGE/onionpi/onionpi-firstboot.service"
cp "$IMAGE_DIR/firstrun.sh" "$STAGE/firstrun.sh"
: >"$STAGE/ssh"
if [[ -n "$SSH_KEY" ]]; then
  [[ -s "$SSH_KEY" ]] || { printf 'Clé SSH introuvable: %s\n' "$SSH_KEY" >&2; exit 1; }
  cp "$SSH_KEY" "$STAGE/onionpi/authorized_keys"
fi

# --------------------------------------------------- boot partition access --
BOOT_OFFSET="$(python3 - "$OUTPUT" <<'PY'
import struct, sys
with open(sys.argv[1], "rb") as image:
    mbr = image.read(512)
entry = mbr[446:462]
if entry[4] not in (0x0b, 0x0c, 0x0e, 0x06, 0x04, 0x01):
    sys.exit(f"première partition inattendue (type {entry[4]:#x})")
print(struct.unpack_from("<I", entry, 8)[0] * 512)
PY
)"

ATTACHED_DEVICE=""
MOUNT_POINT=""
cleanup() {
  if [[ -n "$MOUNT_POINT" && -d "$MOUNT_POINT" ]]; then
    rm -rf "$MOUNT_POINT/.Spotlight-V100" "$MOUNT_POINT/.fseventsd" \
      "$MOUNT_POINT/.TemporaryItems" "$MOUNT_POINT/.Trashes" 2>/dev/null || true
    find "$MOUNT_POINT" -name '._*' -delete 2>/dev/null || true
  fi
  if [[ -n "$ATTACHED_DEVICE" ]]; then
    sync
    hdiutil detach "$ATTACHED_DEVICE" >/dev/null 2>&1 \
      || hdiutil detach "$ATTACHED_DEVICE" -force >/dev/null 2>&1 || true
    ATTACHED_DEVICE=""
  fi
}
trap cleanup EXIT

printf '==> Écriture dans la partition de démarrage\n'
if [[ "$(uname -s)" == "Darwin" ]]; then
  attach_output="$(hdiutil attach -imagekey diskimage-class=CRawDiskImage "$OUTPUT")"
  ATTACHED_DEVICE="$(awk 'NR==1{print $1}' <<<"$attach_output")"
  MOUNT_POINT="$(awk '/\/Volumes\//{ $1=""; $2=""; sub(/^ +/, ""); print; exit }' <<<"$attach_output")"
  [[ -n "$MOUNT_POINT" && -d "$MOUNT_POINT" ]] || {
    printf 'Partition de démarrage non montée.\n%s\n' "$attach_output" >&2; exit 1; }
  [[ -s "$MOUNT_POINT/cmdline.txt" ]] || {
    printf 'cmdline.txt absent: ce n’est pas une image Raspberry Pi OS.\n' >&2; exit 1; }
  CMDLINE="$(tr -d '\n' <"$MOUNT_POINT/cmdline.txt")"
else
  command -v mcopy >/dev/null 2>&1 || {
    printf 'mtools requis sur Linux: apt-get install mtools\n' >&2; exit 1; }
  CMDLINE="$(mtype -i "$OUTPUT@@$BOOT_OFFSET" ::/cmdline.txt | tr -d '\r\n')" || {
    printf 'cmdline.txt illisible dans l’image.\n' >&2; exit 1; }
fi

CMDLINE="$(sed -e 's| systemd\.run=[^ ]*||g' -e 's| systemd\.run_success_action=[^ ]*||g' \
  -e 's| systemd\.run_failure_action=[^ ]*||g' <<<"$CMDLINE")"
CMDLINE="$CMDLINE systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.run_failure_action=reboot"
printf '%s\n' "$CMDLINE" >"$STAGE/cmdline.txt"

if [[ "$(uname -s)" == "Darwin" ]]; then
  COPYFILE_DISABLE=1 cp -R "$STAGE"/. "$MOUNT_POINT"/
  cleanup
else
  mmd -i "$OUTPUT@@$BOOT_OFFSET" ::/onionpi 2>/dev/null || true
  mcopy -i "$OUTPUT@@$BOOT_OFFSET" -o -s "$STAGE/onionpi" ::/
  for entry in "$STAGE"/*; do
    if [[ -f "$entry" ]]; then
      mcopy -i "$OUTPUT@@$BOOT_OFFSET" -o "$entry" ::/
    fi
  done
fi
trap - EXIT

# --------------------------------------------------------------- results ---
CREDENTIALS="${OUTPUT%.img}-identifiants.txt"
umask 077
cat >"$CREDENTIALS" <<EOF
OnionPi — identifiants de l'image $(basename -- "$OUTPUT")
Généré le $(date -u +%FT%TZ)

Wi-Fi OnionPi
  SSID              : $SSID
  Mot de passe      : $WIFI_PASSWORD

Mesh OnionPi
  État              : $([[ -n "$MESH_INTERFACE" ]] && printf activé || printf désactivé)
  Identifiant       : $MESH_ID
  Mot de passe      : $MESH_PASSWORD
  Adresse demandée  : ${MESH_ADDRESS:-dérivée de la MAC au premier démarrage}

Interface web (https://$HOSTNAME_VALUE.local)
  Utilisateur       : admin
  Mot de passe      : $ADMIN_PASSWORD

Compte système (SSH)
  Utilisateur       : $LOGIN
  Mot de passe      : $LOGIN_PASSWORD

Ce fichier est le seul endroit où ces mots de passe existent en clair.
Conservez-le dans un gestionnaire de mots de passe puis supprimez-le.
EOF
chmod 0600 "$CREDENTIALS"
umask 022

sha256 "$OUTPUT" >"$OUTPUT.sha256"
if (( COMPRESS )); then
  printf '==> Compression (peut prendre plusieurs minutes)\n'
  xz --threads=0 --keep --force -6 "$OUTPUT"
  sha256 "$OUTPUT.xz" >"$OUTPUT.xz.sha256"
fi
(( KEEP_WORK )) || rm -rf "$STAGE"

printf '\nImage prête: %s\n' "$OUTPUT"
if (( COMPRESS )); then printf 'Archive     : %s.xz\n' "$OUTPUT"; fi
printf 'Empreinte   : %s\n' "$(cut -d' ' -f1 <"$OUTPUT.sha256")"
printf 'Identifiants: %s\n' "$CREDENTIALS"
if ((${#generated_notes[@]})); then
  printf 'Mots de passe générés automatiquement: %s\n' "${generated_notes[*]}"
fi
printf '\nFlashez avec Raspberry Pi Imager (« Utiliser une image personnalisée ») ou:\n'
printf '  sudo dd if=%s of=/dev/rdiskN bs=4m status=progress\n' "$OUTPUT"
printf 'Branchez la Pi en Ethernet au premier démarrage: elle installe OnionPi seule\n'
printf 'puis publie le Wi-Fi « %s » (comptez 10 à 20 minutes).\n' "$SSID"
