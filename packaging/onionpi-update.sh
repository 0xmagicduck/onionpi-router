#!/usr/bin/env bash
set -Eeuo pipefail

# OnionPi update client.
#
# Runs as root from onionpi-update.service, itself started by
# onionpi-update.timer at the hours configured in /etc/onionpi/update.conf, or
# on demand through the privileged agent when someone presses the button in the
# web interface.
#
# Design constraints that shaped this script:
#
#   * Everything it downloads goes through the Tor SOCKS port by default. A
#     plain HTTPS request to api.github.com would tell the local network, and
#     the exit side of the connection, that this address runs an OnionPi.
#   * The release tarball is only unpacked once its SHA-256 matches the
#     SHA256SUMS file published next to it, and, when a key is configured, only
#     once that file carries a valid OpenPGP signature.
#   * Application releases are immutable below /opt/onionpi/releases. The
#     current symlink is switched atomically, while a root-owned journal tracks
#     system files so an interrupted update can restore one coherent version.
#   * The web interface never reaches this file. It writes preferences into
#     /var/lib/onionpi/update.settings.json, which is untrusted input revalidated
#     here field by field, exactly like the agent request queue.

CONFIG_FILE="/etc/onionpi/update.conf"
OVERRIDES_FILE="/var/lib/onionpi/update.settings.json"
STATE_FILE="/var/lib/onionpi-privileged/update.state"
LOCK_FILE="/run/onionpi-update.lock"
INSTALL_BASE="/opt/onionpi"
INSTALL_ROOT="$INSTALL_BASE/current"
RELEASES_ROOT="$INSTALL_BASE/releases"
STAGING_ROOT="/var/cache/onionpi-update"
BACKUP_ROOT="/var/backups"
TRANSACTION_FILE="/var/lib/onionpi-privileged/update-transaction.json"
TIMER_DROPIN="/etc/systemd/system/onionpi-update.timer.d/schedule.conf"

# Defaults. /etc/onionpi/update.conf is root-owned and may override all of them.
ONIONPI_UPDATE_REPOSITORY="0xmagicduck/onionpi-router"
ONIONPI_UPDATE_CHANNEL="stable"
ONIONPI_UPDATE_SCHEDULE="04:30"
ONIONPI_UPDATE_CALENDAR=""
ONIONPI_UPDATE_RANDOM_DELAY="45m"
ONIONPI_UPDATE_ENABLED=1
ONIONPI_UPDATE_APPLY=1
ONIONPI_UPDATE_OVER_TOR=1
ONIONPI_UPDATE_REQUIRE_SIGNATURE=1
ONIONPI_UPDATE_GPG_KEYRING="/etc/onionpi/update-signing-key.gpg"
ONIONPI_UPDATE_KEEP_BACKUPS=3
ONIONPI_UPDATE_ALLOW_OVERRIDES=1
ONIONPI_UPDATE_API="https://api.github.com"
ONIONPI_UPDATE_SOCKS="127.0.0.1:9050"
ONIONPI_UPDATE_TIMEOUT=180
ONIONPI_UPDATE_MAX_ARCHIVE_BYTES=268435456

MODE="check"
FORCE=0
QUIET=0

usage() {
  cat <<'EOF'
Client de mise à jour OnionPi.

Usage: onionpi-update [action]
  --check          cherche une version disponible et l'enregistre (défaut)
  --apply          installe la version disponible si la politique l'autorise
  --force          installe même si la version est identique ou plus ancienne
  --rollback       restaure la dernière sauvegarde connue
  --recover        termine le rollback d'une mise à jour interrompue
  --boot-recover   reprise au démarrage, sans redémarrer les services trop tôt
  --status         affiche l'état courant sans rien contacter
  --write-timer    régénère l'horaire systemd depuis la configuration
  --quiet          n'écrit que les erreurs sur la sortie standard
  -h, --help       affiche cette aide
EOF
}

while (($#)); do
  case "$1" in
    --check) MODE="check"; shift ;;
    --apply) MODE="apply"; shift ;;
    --rollback) MODE="rollback"; shift ;;
    --recover) MODE="recover"; shift ;;
    --boot-recover) MODE="boot-recover"; shift ;;
    --status) MODE="status"; shift ;;
    --write-timer) MODE="write-timer"; shift ;;
    --force) FORCE=1; shift ;;
    --quiet) QUIET=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Option inconnue: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

log()  { (( QUIET )) || printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
die()  { printf '%s\n' "$*" >&2; exit 1; }

if [[ -r "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

# ------------------------------------------------------------ preferences ----
# The interface may only change three things, and each one is re-derived here
# from a strict pattern. Anything else in the file is ignored on purpose.
read_overrides() {
  (( ONIONPI_UPDATE_ALLOW_OVERRIDES )) || return 0
  [[ -r "$OVERRIDES_FILE" ]] || return 0
  local parsed
  parsed="$(python3 - "$OVERRIDES_FILE" <<'PY' 2>/dev/null || true
import json
import re
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        document = json.load(handle)
except (OSError, ValueError):
    raise SystemExit(0)
if not isinstance(document, dict):
    raise SystemExit(0)

out = []
channel = str(document.get("channel", ""))
if channel in {"stable", "edge"}:
    out.append(f"channel={channel}")
schedule = str(document.get("schedule", ""))
slots = [slot.strip() for slot in schedule.split(",") if slot.strip()]
if slots and len(slots) <= 6 and all(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s) for s in slots):
    out.append("schedule=" + ",".join(slots))
for key in ("enabled", "apply"):
    value = document.get(key)
    if isinstance(value, bool):
        out.append(f"{key}={1 if value else 0}")
print("\n".join(out))
PY
)"
  local line key value
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      channel) ONIONPI_UPDATE_CHANNEL="$value" ;;
      schedule) ONIONPI_UPDATE_SCHEDULE="$value" ;;
      enabled) ONIONPI_UPDATE_ENABLED="$value" ;;
      apply) ONIONPI_UPDATE_APPLY="$value" ;;
    esac
  done <<<"$parsed"
}

read_overrides

[[ "$ONIONPI_UPDATE_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || die "Dépôt de mise à jour invalide: $ONIONPI_UPDATE_REPOSITORY"
[[ "$ONIONPI_UPDATE_CHANNEL" =~ ^(stable|edge)$ ]] \
  || die "Canal de mise à jour invalide: $ONIONPI_UPDATE_CHANNEL"
[[ "$ONIONPI_UPDATE_MAX_ARCHIVE_BYTES" =~ ^[0-9]+$ ]] \
  && (( ONIONPI_UPDATE_MAX_ARCHIVE_BYTES >= 1048576 )) \
  || die "ONIONPI_UPDATE_MAX_ARCHIVE_BYTES doit être un entier d’au moins 1048576"

installed_version() {
  if [[ -r "$INSTALL_ROOT/VERSION" ]]; then
    head -n 1 "$INSTALL_ROOT/VERSION" | tr -d '[:space:]'
  else
    printf '0.0.0'
  fi
}

# Closed list of every root-owned path install.sh may mutate. Rollback walks
# the same list, so a file introduced by an interrupted version is removed
# instead of surviving beside the restored release.
ROOT_MUTATION_PATHS=(
  /etc/onionpi
  /etc/tor/torrc.d/onionpi.conf
  /etc/dnsmasq.d/onionpi.conf
  /etc/sysctl.d/70-onionpi.conf
  /etc/nginx/sites-available/onionpi
  /etc/nginx/sites-enabled/onionpi
  /etc/avahi/hosts
  /etc/modprobe.d/onionpi-regdom.conf
  /etc/update-motd.d/10-onionpi
  /etc/profile.d/onionpi.sh
  /etc/issue /etc/issue.net /etc/motd
  /etc/systemd/system/onionpi.service
  /etc/systemd/system/onionpi-ap.service
  /etc/systemd/system/onionpi-firewall.service
  /etc/systemd/system/onionpi-relay.service
  /etc/systemd/system/onionpi-relay.path
  /etc/systemd/system/onionpi-agent.service
  /etc/systemd/system/onionpi-agent.path
  /etc/systemd/system/onionpi-boot-banner.service
  /etc/systemd/system/onionpi-update.service
  /etc/systemd/system/onionpi-update.timer
  /etc/systemd/system/onionpi-update-recover.service
  /etc/systemd/system/multi-user.target.wants/onionpi-update-recover.service
  /etc/systemd/system/onionpi-update.timer.d
  /usr/local/sbin/onionpi-firewall-apply
  /usr/local/sbin/onionpi-devices-apply
  /usr/local/sbin/onionpi-relay-apply
  /usr/local/sbin/onionpi-agent-apply
  /usr/local/sbin/onionpi-update
  /usr/local/sbin/onionpi-maintenance
  /usr/local/sbin/onionpi-verify
  /usr/local/sbin/onionpi-uninstall
  /usr/local/lib/onionpi
  /usr/local/bin/onionpi-status
)

transaction_write() {
  local backup="$1" old_target="$2" new_version="$3"
  python3 - "$TRANSACTION_FILE" "$backup" "$old_target" "$new_version" <<'PY'
import json
import os
import sys
import tempfile

path, backup, old_target, new_version = sys.argv[1:]
handle = tempfile.NamedTemporaryFile(
    "w", encoding="utf-8", dir=os.path.dirname(path), delete=False,
    prefix=".update-transaction."
)
try:
    json.dump({"phase": "applying", "backup": backup,
               "old_target": old_target, "new_version": new_version},
              handle, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
    os.fchmod(handle.fileno(), 0o600)
finally:
    handle.close()
os.replace(handle.name, path)
directory_fd = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
}

transaction_clear() {
  python3 - "$TRANSACTION_FILE" <<'PY'
import os
import sys

path = sys.argv[1]
try:
    os.unlink(path)
except FileNotFoundError:
    pass
directory_fd = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
}

# --------------------------------------------------------------- state io ----
# One small JSON document, written atomically in a root-owned namespace and
# group-readable so the web interface can display it but never replace it.
state_set() {
  # Never create or chmod the parent: /var/lib/onionpi is 0750 onionpi:onionpi
  # and widening it would expose the shared files and the onion key.
  [[ -d "$(dirname -- "$STATE_FILE")" ]] || return 0
  python3 - "$STATE_FILE" "$@" <<'PY'
import json
import grp
import os
import sys
import tempfile

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict):
        state = {}
except (OSError, ValueError):
    state = {}

for pair in sys.argv[2:]:
    key, _, raw = pair.partition("=")
    if raw in {"true", "false"}:
        value: object = raw == "true"
    elif raw == "null":
        value = None
    elif raw.lstrip("-").isdigit():
        value = int(raw)
    else:
        value = raw
    if key == "history+":
        history = state.get("history")
        if not isinstance(history, list):
            history = []
        history.insert(0, value)
        state["history"] = history[:10]
    else:
        state[key] = value

directory = os.path.dirname(path) or "."
handle = tempfile.NamedTemporaryFile(
    "w", encoding="utf-8", dir=directory, delete=False, prefix=".update.state."
)
try:
    json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
    # Set ownership and mode on the already-open object. The parent is
    # root-owned as well, so neither the descriptor nor its directory entry can
    # be exchanged by the application.
    os.fchown(handle.fileno(), 0, grp.getgrnam("onionpi").gr_gid)
    os.fchmod(handle.fileno(), 0o640)
finally:
    handle.close()
os.replace(handle.name, path)
PY
}

state_get() {
  python3 - "$STATE_FILE" "$1" <<'PY' 2>/dev/null || true
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        state = json.load(handle)
except (OSError, ValueError):
    raise SystemExit(0)
value = state.get(sys.argv[2], "")
if isinstance(value, bool):
    value = "1" if value else "0"
print("" if value is None else value)
PY
}

now() { date -u +%s; }
stamp() { date -u +%Y%m%dT%H%M%SZ; }

# ------------------------------------------------------------- networking ----
# curl through the Tor SOCKS port. --socks5-hostname keeps the DNS lookup inside
# Tor as well, so the resolver never sees api.github.com either.
fetch() {
  local url="$1" destination="$2" max_bytes="${3:-4194304}"
  [[ "$max_bytes" =~ ^[0-9]+$ ]] && (( max_bytes > 0 )) \
    || die "Limite de téléchargement invalide"
  local -a command=(
    curl --silent --show-error --location --fail
    --proto '=https' --tlsv1.2
    --max-time "$ONIONPI_UPDATE_TIMEOUT"
    --max-filesize "$max_bytes"
    --user-agent "onionpi-update/$(installed_version)"
    --header "Accept: application/vnd.github+json"
    --output "$destination" "$url"
  )
  if (( ONIONPI_UPDATE_OVER_TOR )); then
    command=("${command[@]:0:1}" --socks5-hostname "$ONIONPI_UPDATE_SOCKS" "${command[@]:1}")
  fi
  "${command[@]}" || return 1
  local downloaded
  downloaded="$(wc -c <"$destination")"
  (( downloaded <= max_bytes )) || {
    rm -f -- "$destination"
    return 1
  }
}

# Returns "version<TAB>tarball_url<TAB>sums_url<TAB>signature_url".
resolve_release() {
  local endpoint response
  if [[ "$ONIONPI_UPDATE_CHANNEL" == "edge" ]]; then
    endpoint="$ONIONPI_UPDATE_API/repos/$ONIONPI_UPDATE_REPOSITORY/releases/tags/edge"
  else
    endpoint="$ONIONPI_UPDATE_API/repos/$ONIONPI_UPDATE_REPOSITORY/releases/latest"
  fi
  response="$(mktemp)"
  if ! fetch "$endpoint" "$response"; then
    rm -f "$response"
    return 1
  fi
  local parsed status=0
  parsed="$(python3 - "$response" <<'PY'
import json
import re
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    release = json.load(handle)

assets = {asset.get("name", ""): asset.get("browser_download_url", "")
          for asset in release.get("assets", [])}
tarball = next((name for name in assets if re.fullmatch(r"onionpi-.+\.tar\.gz", name)), "")
if not tarball:
    raise SystemExit("aucune archive onionpi-*.tar.gz dans cette publication")

# The archive name carries the version: a release tag can be a moving pointer
# ("edge"), the file name never is.
version = re.fullmatch(r"onionpi-(.+)\.tar\.gz", tarball).group(1)
# Must start with an alphanumeric: the version becomes a path component under
# /var/cache/onionpi-update, and ".." matched the old pattern, which turned an
# asset called onionpi-...tar.gz into an "rm -rf" of the parent directory.
if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,39}", version) or ".." in version:
    raise SystemExit("numéro de version inattendu")


def checked(url: str, required: bool) -> str:
    """Refuses a download location the publisher did not have to control."""
    if not url:
        if required:
            raise SystemExit("adresse de téléchargement absente")
        return ""
    host = re.fullmatch(r"https://([A-Za-z0-9.-]+)(/.*)?", url)
    if not host or host.group(1).rsplit(".", 2)[-2:] not in (
        ["github", "com"], ["githubusercontent", "com"]
    ):
        raise SystemExit(f"adresse de téléchargement inattendue: {url[:80]}")
    return url


print("\t".join([
    version,
    checked(assets[tarball], True),
    checked(assets.get("SHA256SUMS", ""), False),
    checked(assets.get("SHA256SUMS.asc", ""), False),
]))
PY
)" || status=$?
  rm -f "$response"
  (( status == 0 )) || return 1
  printf '%s\n' "$parsed"
}

# Semantic-version comparison. "0.2.0-edge.44" is older than "0.2.0" and newer
# than "0.2.0-edge.43", and any version is newer than the 0.0.0 reported by an
# installation without a VERSION file. Edge builds therefore carry the version
# they lead to, never the one already published.
newer_than() {
  python3 - "$1" "$2" <<'PY'
import re
import sys


def key(value: str) -> tuple:
    core, _, pre = value.partition("-")
    numbers = [int(part) if part.isdigit() else 0 for part in core.split(".")[:4]]
    numbers += [0] * (4 - len(numbers))
    # A release sorts after every pre-release sharing its numbers.
    parts = [(0, int(p), "") if p.isdigit() else (1, 0, p)
             for p in re.split(r"[.+]", pre) if p]
    return (numbers, 1 if not pre else 0, parts)


raise SystemExit(0 if key(sys.argv[1]) > key(sys.argv[2]) else 1)
PY
}

# ------------------------------------------------------------------ timer ----
write_timer() {
  local -a calendars=()
  if [[ -n "$ONIONPI_UPDATE_CALENDAR" ]]; then
    calendars+=("$ONIONPI_UPDATE_CALENDAR")
  else
    local slot
    local -a slots=()
    IFS=',' read -r -a slots <<<"$ONIONPI_UPDATE_SCHEDULE"
    for slot in "${slots[@]}"; do
      slot="${slot//[[:space:]]/}"
      [[ -n "$slot" ]] || continue
      [[ "$slot" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || die "Horaire invalide: $slot"
      calendars+=("*-*-* $slot:00")
    done
  fi
  (( ${#calendars[@]} )) || calendars=("*-*-* 04:30:00")

  install -d -m 0755 "$(dirname -- "$TIMER_DROPIN")"
  {
    printf '# Généré par onionpi-update --write-timer. Ne pas modifier à la main.\n'
    printf '[Timer]\n'
    # An empty assignment clears the values inherited from the unit file, so a
    # new schedule replaces the old one instead of being added to it.
    printf 'OnCalendar=\n'
    printf 'OnCalendar=%s\n' "${calendars[@]}"
    printf 'RandomizedDelaySec=%s\n' "$ONIONPI_UPDATE_RANDOM_DELAY"
  } >"$TIMER_DROPIN.tmp"
  chmod 0644 "$TIMER_DROPIN.tmp"
  mv "$TIMER_DROPIN.tmp" "$TIMER_DROPIN"

  systemctl daemon-reload
  if (( ONIONPI_UPDATE_ENABLED )); then
    systemctl enable --now onionpi-update.timer >/dev/null 2>&1 || true
    systemctl restart onionpi-update.timer
  else
    systemctl disable --now onionpi-update.timer >/dev/null 2>&1 || true
  fi
  refresh_schedule_state
  log "Horaire appliqué: ${calendars[*]}"
}

refresh_schedule_state() {
  local next=""
  next="$(systemctl show onionpi-update.timer -p NextElapseUSecRealtime --value 2>/dev/null || true)"
  local epoch=""
  if [[ -n "$next" && "$next" != "n/a" ]]; then
    epoch="$(date -u -d "$next" +%s 2>/dev/null || true)"
  fi
  state_set \
    "schedule=$ONIONPI_UPDATE_SCHEDULE" \
    "channel=$ONIONPI_UPDATE_CHANNEL" \
    "enabled=$( ((ONIONPI_UPDATE_ENABLED)) && echo true || echo false )" \
    "auto_apply=$( ((ONIONPI_UPDATE_APPLY)) && echo true || echo false )" \
    "over_tor=$( ((ONIONPI_UPDATE_OVER_TOR)) && echo true || echo false )" \
    "repository=$ONIONPI_UPDATE_REPOSITORY" \
    "next_run=${epoch:-null}"
}

# ------------------------------------------------------------- download ------
# Leaves the verified, unpacked tree in $STAGING_DIR.
STAGING_DIR=""
DOWNLOAD_WORK=""
download_release() {
  local version="$1" tarball_url="$2" sums_url="$3" signature_url="$4"
  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/onionpi-update.XXXXXX")"
  DOWNLOAD_WORK="$work"
  local archive="$work/onionpi-$version.tar.gz"

  log "Téléchargement de la version $version…"
  local expected=""
  if [[ -n "$sums_url" ]]; then
    fetch "$sums_url" "$work/SHA256SUMS" || die "Téléchargement de SHA256SUMS impossible"
    if [[ -n "$signature_url" && -s "$ONIONPI_UPDATE_GPG_KEYRING" ]]; then
      fetch "$signature_url" "$work/SHA256SUMS.asc" || die "Signature indisponible"
      gpgv --keyring "$ONIONPI_UPDATE_GPG_KEYRING" "$work/SHA256SUMS.asc" "$work/SHA256SUMS" \
        || die "Signature de SHA256SUMS refusée"
      log "Signature OpenPGP vérifiée."
    elif (( ONIONPI_UPDATE_REQUIRE_SIGNATURE )); then
      die "Signature exigée mais absente ou sans trousseau (${ONIONPI_UPDATE_GPG_KEYRING})"
    fi
    # Exact name comparison: a regex match would let onionpi-1X0.tar.gz answer
    # for onionpi-1.0.tar.gz.
    expected="$(awk -v name="onionpi-$version.tar.gz" \
      '{ sub(/^\*/, "", $2); if ($2 == name) { print $1; exit } }' "$work/SHA256SUMS")"
    [[ -n "$expected" ]] || die "onionpi-$version.tar.gz absent de SHA256SUMS"
  elif (( ONIONPI_UPDATE_REQUIRE_SIGNATURE )); then
    die "Aucun fichier SHA256SUMS publié: mise à jour refusée"
  else
    warn "Aucun SHA256SUMS publié: l’archive n’a pas pu être vérifiée."
  fi

  # Authenticate the small checksum manifest before accepting the much larger
  # archive. curl enforces the bound while streaming and wc checks it again for
  # servers that omit or lie about Content-Length.
  fetch "$tarball_url" "$archive" "$ONIONPI_UPDATE_MAX_ARCHIVE_BYTES" \
    || die "Téléchargement de l’archive impossible ou archive trop volumineuse"
  if [[ -n "$expected" ]]; then
    local actual
    actual="$(sha256sum "$archive" | awk '{print $1}')"
    [[ "$expected" == "$actual" ]] || die "Empreinte SHA-256 incorrecte: $actual"
    log "Empreinte SHA-256 vérifiée."
  fi

  # Refuse any unexpected object before a root extraction. install.sh creates
  # this cache root-owned, and the updater reasserts that invariant here.
  if [[ -L "$STAGING_ROOT" ]]; then
    die "Lien symbolique inattendu en $STAGING_ROOT: extraction refusée"
  fi
  install -d -m 0700 -o root -g root "$STAGING_ROOT"
  [[ "$(stat -c '%u:%a' "$STAGING_ROOT")" == "0:700" ]] \
    || die "$STAGING_ROOT n’appartient pas exclusivement à root: extraction refusée"
  rm -rf "${STAGING_ROOT:?}/$version"
  install -d -m 0700 "$STAGING_ROOT/$version"
  # --no-same-owner: the archive is verified, but nothing it contains should be
  # able to choose a uid when it is unpacked by root.
  tar -xzf "$archive" -C "$STAGING_ROOT/$version" --strip-components=1 --no-same-owner
  rm -rf "$work"
  DOWNLOAD_WORK=""

  [[ -x "$STAGING_ROOT/$version/packaging/install.sh" ]] \
    || die "Archive incomplète: packaging/install.sh manquant"
  [[ -s "$STAGING_ROOT/$version/frontend/dist/index.html" ]] \
    || die "Archive incomplète: interface web absente"
  [[ -s "$STAGING_ROOT/$version/wheelhouse/SHA256SUMS" ]] \
    || die "Archive incomplète: wheelhouse signé absent"
  [[ -s "$STAGING_ROOT/$version/SBOM.spdx.json" ]] \
    || die "Archive incomplète: nomenclature SPDX absente"
  local shipped
  shipped="$(head -n 1 "$STAGING_ROOT/$version/VERSION" 2>/dev/null | tr -d '[:space:]' || true)"
  [[ "$shipped" == "$version" ]] \
    || die "L’archive annonce la version $shipped, attendue $version"
  STAGING_DIR="$STAGING_ROOT/$version"
}

# -------------------------------------------------------------- rollback -----
latest_backup() {
  find "$BACKUP_ROOT" -maxdepth 1 -type d -name 'onionpi-update-*' 2>/dev/null | sort | tail -n 1
}

backup_root_files() {
  local destination="$1" path
  install -d -m 0700 "$destination"
  for path in "${ROOT_MUTATION_PATHS[@]}"; do
    [[ -e "$path" || -L "$path" ]] || continue
    rsync -aR "$path" "$destination/"
  done
}

restore_backup() {
  local backup="$1"
  local restart_services="${2:-1}"
  [[ -s "$backup/current-target" ]] || return 1
  log "Restauration de $backup…"
  local target path saved temporary
  target="$(head -n 1 "$backup/current-target")"
  [[ "$target" =~ ^/opt/onionpi/releases/[0-9A-Za-z][0-9A-Za-z.+-]{0,39}$ ]] || return 1
  [[ -f "$target/.complete" ]] || return 1
  for path in "${ROOT_MUTATION_PATHS[@]}"; do
    saved="$backup/rootfs$path"
    rm -rf -- "$path"
    if [[ -e "$saved" || -L "$saved" ]]; then
      install -d "$(dirname -- "$path")"
      cp -a -- "$saved" "$path"
    fi
  done
  temporary="$INSTALL_BASE/.current-rollback-$$"
  ln -s "${target#"$INSTALL_BASE/"}" "$temporary"
  mv -Tf "$temporary" "$INSTALL_ROOT"
  ln -sfn current/VERSION "$INSTALL_BASE/VERSION"
  # syncfs through the install root also persists restored /etc and /usr files
  # because the appliance keeps them on the same root filesystem.
  sync -f "$INSTALL_BASE"
  systemctl daemon-reload
  if (( restart_services )); then
    systemctl restart tor
    systemctl restart onionpi-firewall
    systemctl restart dnsmasq nginx onionpi
  fi
}

recover_interrupted() {
  local restart_services="${1:-1}"
  [[ -s "$TRANSACTION_FILE" ]] || return 0
  local backup
  backup="$(python3 - "$TRANSACTION_FILE" <<'PY'
import json
import sys

try:
    value = json.load(open(sys.argv[1], encoding="utf-8")).get("backup", "")
except (OSError, ValueError):
    value = ""
print(value)
PY
)"
  [[ "$backup" =~ ^/var/backups/onionpi-update-[0-9]{8}T[0-9]{6}Z$ ]] \
    || die "Journal de mise à jour invalide: $TRANSACTION_FILE"
  warn "Mise à jour interrompue détectée; restauration de $backup."
  restore_backup "$backup" "$restart_services" \
    || die "Restauration automatique impossible depuis $backup"
  prune_releases
  transaction_clear
  state_set "running=false" "last_apply_status=rolled-back" \
    "last_apply_message=Restauration automatique après interruption" \
    "installed=$(installed_version)" \
    "history+=$(stamp) restauration automatique après interruption"
}

prune_backups() {
  local keep="$ONIONPI_UPDATE_KEEP_BACKUPS" directory
  [[ "$keep" =~ ^[0-9]+$ ]] || keep=3
  while IFS= read -r directory; do
    [[ -n "$directory" ]] || continue
    rm -rf -- "$directory"
  done < <(find "$BACKUP_ROOT" -maxdepth 1 -type d -name 'onionpi-update-*' 2>/dev/null \
    | sort -r | tail -n +$((keep + 1)))
}

prune_releases() {
  local directory target referenced
  target="$(readlink -f "$INSTALL_ROOT" 2>/dev/null || true)"
  while IFS= read -r directory; do
    [[ -n "$directory" && "$directory" != "$target" ]] || continue
    referenced=0
    while IFS= read -r saved; do
      [[ "$saved" == "$directory" ]] && referenced=1
    done < <(find "$BACKUP_ROOT" -maxdepth 2 -type f -name current-target \
      -exec head -n 1 {} \; 2>/dev/null)
    (( referenced )) || rm -rf -- "$directory"
  done < <(find "$RELEASES_ROOT" -mindepth 1 -maxdepth 1 -type d \
    -name '[0-9A-Za-z]*' 2>/dev/null | sort)
  # A hard cut can bypass install.sh's EXIT trap. Hidden build directories and
  # temporary current links are never valid release targets, so remove them
  # once the journal has restored a known-complete version.
  find "$RELEASES_ROOT" -mindepth 1 -maxdepth 1 -type d \
    -name '.incoming-[0-9A-Za-z]*' -exec rm -rf -- {} + 2>/dev/null || true
  find "$INSTALL_BASE" -mindepth 1 -maxdepth 1 -type l \
    -name '.current-*' -delete 2>/dev/null || true
}

health_check() {
  ONIONPI_VERIFY_HTTP_WAIT_SECONDS=60 /usr/local/sbin/onionpi-verify
}

# ------------------------------------------------------------------ modes ----
do_check() {
  local current release version
  current="$(installed_version)"
  if ! release="$(resolve_release)"; then
    state_set "last_check=$(now)" "last_check_status=error" \
      "last_check_message=Publication introuvable ou réseau indisponible" \
      "installed=$current"
    die "Impossible de joindre $ONIONPI_UPDATE_REPOSITORY (canal $ONIONPI_UPDATE_CHANNEL)."
  fi
  IFS=$'\t' read -r version TARBALL_URL SUMS_URL SIGNATURE_URL <<<"$release"
  local pending="false"
  if newer_than "$version" "$current"; then pending="true"; fi
  state_set \
    "installed=$current" \
    "available=$version" \
    "update_pending=$pending" \
    "last_check=$(now)" \
    "last_check_status=ok" \
    "last_check_message=" \
    "channel=$ONIONPI_UPDATE_CHANNEL"
  if [[ "$pending" == "true" ]]; then
    log "Version $version disponible (installée: $current)."
    return 0
  fi
  log "OnionPi est à jour ($current)."
  return 0
}

do_apply() {
  local current version backup
  current="$(installed_version)"
  # do_check leaves TARBALL_URL, SUMS_URL and SIGNATURE_URL behind, and aborts
  # the whole run when the release cannot be resolved.
  do_check
  version="$(state_get available)"
  [[ -n "$version" ]] || die "Aucune version disponible: lancez d’abord --check."
  if ! newer_than "$version" "$current" && (( ! FORCE )); then
    log "Rien à installer: $current est déjà la version publiée."
    return 0
  fi

  state_set "running=true" "last_apply_status=running" "last_apply_message=Installation de $version"
  download_release "$version" "$TARBALL_URL" "$SUMS_URL" "$SIGNATURE_URL"

  backup="$BACKUP_ROOT/onionpi-update-$(stamp)"
  install -d -m 0700 "$backup"
  backup_root_files "$backup/rootfs"
  printf '%s\n' "$current" >"$backup/VERSION"
  readlink -f "$INSTALL_ROOT" >"$backup/current-target"
  sync -f "$backup"
  transaction_write "$backup" "$(cat "$backup/current-target")" "$version"
  log "Sauvegarde: $backup"

  local failure=""
  if ! "$STAGING_DIR/packaging/install.sh" --upgrade --yes; then
    failure="l’installateur a échoué"
  elif ! health_check; then
    failure="le contrôle post-installation a échoué"
  fi

  if [[ -n "$failure" ]]; then
    warn "Mise à jour vers $version refusée: $failure."
    if restore_backup "$backup"; then
      prune_releases
      transaction_clear
      state_set "running=false" "last_apply=$(now)" "last_apply_status=rolled-back" \
        "last_apply_message=Retour à $current: $failure" "installed=$(installed_version)" \
        "history+=$(stamp) rollback $version ($failure)"
      die "Retour à la version $current effectué."
    fi
    state_set "running=false" "last_apply=$(now)" "last_apply_status=error" \
      "last_apply_message=Échec et restauration impossible: $failure" \
      "history+=$(stamp) échec $version ($failure)"
    die "Échec de la mise à jour et de la restauration. Intervention manuelle nécessaire."
  fi

  transaction_clear
  prune_backups
  prune_releases
  rm -rf "${STAGING_ROOT:?}/$version"
  state_set "running=false" "installed=$(installed_version)" "update_pending=false" \
    "last_apply=$(now)" "last_apply_status=ok" \
    "last_apply_message=Version $version installée" \
    "history+=$(stamp) installé $version (depuis $current)"
  log "OnionPi est passé de $current à $version."
}

do_rollback() {
  local backup current_target target_path target_version
  backup="$(latest_backup)"
  [[ -n "$backup" ]] || die "Aucune sauvegarde de mise à jour disponible."
  current_target="$(readlink -f "$INSTALL_ROOT")"
  target_path="$(head -n 1 "$backup/current-target" 2>/dev/null || true)"
  [[ "$target_path" =~ ^/opt/onionpi/releases/[0-9A-Za-z][0-9A-Za-z.+-]{0,39}$ ]] \
    && [[ -f "$target_path/.complete" ]] \
    || die "Sauvegarde de mise à jour invalide: $backup"
  target_version="$(basename -- "$target_path")"
  # Manual rollback mutates the same root surface as automatic recovery. Its
  # journal must be durable first, otherwise a power cut could leave a mixed
  # set of helpers with no boot-time repair instruction.
  transaction_write "$backup" "$current_target" "$target_version"
  restore_backup "$backup" || die "Restauration impossible depuis $backup."
  prune_releases
  transaction_clear
  health_check || warn "Le contrôle après restauration signale un problème."
  state_set "running=false" "installed=$(installed_version)" \
    "last_apply=$(now)" "last_apply_status=rolled-back" \
    "last_apply_message=Restauration manuelle depuis $backup" \
    "history+=$(stamp) restauration manuelle ($backup)"
  log "Restauration terminée depuis $backup."
}

do_status() {
  # Read-only on purpose: --status must work for an unprivileged operator.
  if [[ -r "$STATE_FILE" ]]; then
    cat "$STATE_FILE"
  else
    printf '{"installed": "%s"}\n' "$(installed_version)"
  fi
}

case "$MODE" in
  status) do_status; exit 0 ;;
  write-timer)
    [[ "$EUID" -eq 0 ]] || die "Exécutez onionpi-update avec les droits root."
    write_timer
    exit 0
    ;;
  recover|boot-recover)
    [[ "$EUID" -eq 0 ]] || die "Exécutez onionpi-update avec les droits root."
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
      die "Une mise à jour est déjà en cours."
    fi
    if [[ "$MODE" == "boot-recover" ]]; then
      recover_interrupted 0
    else
      recover_interrupted
    fi
    exit 0
    ;;
esac

[[ "$EUID" -eq 0 ]] || die "Exécutez onionpi-update avec les droits root."

# One update at a time: the timer and the web interface can fire together.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  die "Une mise à jour est déjà en cours."
fi

# A timer run or manual action also heals a journal left by a hard power cut.
recover_interrupted

cleanup_update() {
  local status=$?
  if [[ -n "$DOWNLOAD_WORK" && -d "$DOWNLOAD_WORK" ]]; then
    rm -rf -- "$DOWNLOAD_WORK"
  fi
  state_set "running=false" || true
  trap - EXIT
  exit "$status"
}
trap cleanup_update EXIT

case "$MODE" in
  check) do_check ;;
  apply)
    if (( ! ONIONPI_UPDATE_ENABLED )) && (( ! FORCE )); then
      log "Mises à jour désactivées dans la configuration."
      exit 0
    fi
    if (( ! ONIONPI_UPDATE_APPLY )) && (( ! FORCE )); then
      do_check
      log "Installation automatique désactivée: seule la vérification a été faite."
      exit 0
    fi
    do_apply
    ;;
  rollback) do_rollback ;;
esac
