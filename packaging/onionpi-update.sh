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
#   * /opt/onionpi is copied aside before anything is written. A failed health
#     check restores the copy, so a bad release costs a few minutes of downtime
#     instead of a trip to the Raspberry Pi with a screen and a keyboard.
#   * The web interface never reaches this file. It writes preferences into
#     /var/lib/onionpi/update.settings.json, which is untrusted input revalidated
#     here field by field, exactly like the agent request queue.

CONFIG_FILE="/etc/onionpi/update.conf"
OVERRIDES_FILE="/var/lib/onionpi/update.settings.json"
STATE_FILE="/var/lib/onionpi/update.state"
LOCK_FILE="/run/onionpi-update.lock"
INSTALL_ROOT="/opt/onionpi"
STAGING_ROOT="/var/lib/onionpi/updates"
BACKUP_ROOT="/var/backups"
TIMER_DROPIN="/etc/systemd/system/onionpi-update.timer.d/schedule.conf"

# Defaults. /etc/onionpi/update.conf is root-owned and may override all of them.
ONIONPI_UPDATE_REPOSITORY="bastienjavx/onionpi-router"
ONIONPI_UPDATE_CHANNEL="stable"
ONIONPI_UPDATE_SCHEDULE="04:30"
ONIONPI_UPDATE_CALENDAR=""
ONIONPI_UPDATE_RANDOM_DELAY="45m"
ONIONPI_UPDATE_ENABLED=1
ONIONPI_UPDATE_APPLY=1
ONIONPI_UPDATE_OVER_TOR=1
ONIONPI_UPDATE_REQUIRE_SIGNATURE=0
ONIONPI_UPDATE_GPG_KEYRING="/etc/onionpi/update-signing-key.gpg"
ONIONPI_UPDATE_KEEP_BACKUPS=3
ONIONPI_UPDATE_ALLOW_OVERRIDES=1
ONIONPI_UPDATE_API="https://api.github.com"
ONIONPI_UPDATE_SOCKS="127.0.0.1:9050"
ONIONPI_UPDATE_TIMEOUT=180

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

installed_version() {
  if [[ -r "$INSTALL_ROOT/VERSION" ]]; then
    head -n 1 "$INSTALL_ROOT/VERSION" | tr -d '[:space:]'
  else
    printf '0.0.0'
  fi
}

# --------------------------------------------------------------- state io ----
# One small JSON document, written atomically, world-readable so the web
# interface can display it without ever being able to change it.
state_set() {
  # Never create or chmod the parent: /var/lib/onionpi is 0750 onionpi:onionpi
  # and widening it would expose the shared files and the onion key.
  [[ -d "$(dirname -- "$STATE_FILE")" ]] || return 0
  python3 - "$STATE_FILE" "$@" <<'PY'
import json
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
finally:
    handle.close()
os.chmod(handle.name, 0o644)
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
  local url="$1" destination="$2"
  local -a command=(
    curl --silent --show-error --location --fail
    --proto '=https' --tlsv1.2
    --max-time "$ONIONPI_UPDATE_TIMEOUT"
    --user-agent "onionpi-update/$(installed_version)"
    --header "Accept: application/vnd.github+json"
    --output "$destination" "$url"
  )
  if (( ONIONPI_UPDATE_OVER_TOR )); then
    command=("${command[@]:0:1}" --socks5-hostname "$ONIONPI_UPDATE_SOCKS" "${command[@]:1}")
  fi
  "${command[@]}"
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
if not re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", version):
    raise SystemExit("numéro de version inattendu")

print("\t".join([
    version,
    assets[tarball],
    assets.get("SHA256SUMS", ""),
    assets.get("SHA256SUMS.asc", ""),
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
download_release() {
  local version="$1" tarball_url="$2" sums_url="$3" signature_url="$4"
  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/onionpi-update.XXXXXX")"
  local archive="$work/onionpi-$version.tar.gz"

  log "Téléchargement de la version $version…"
  fetch "$tarball_url" "$archive" || die "Téléchargement de l’archive impossible"

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
    local expected actual
    expected="$(awk -v name="onionpi-$version.tar.gz" '$2 ~ name {print $1; exit}' "$work/SHA256SUMS")"
    [[ -n "$expected" ]] || die "onionpi-$version.tar.gz absent de SHA256SUMS"
    actual="$(sha256sum "$archive" | awk '{print $1}')"
    [[ "$expected" == "$actual" ]] || die "Empreinte SHA-256 incorrecte: $actual"
    log "Empreinte SHA-256 vérifiée."
  elif (( ONIONPI_UPDATE_REQUIRE_SIGNATURE )); then
    die "Aucun fichier SHA256SUMS publié: mise à jour refusée"
  else
    warn "Aucun SHA256SUMS publié: l’archive n’a pas pu être vérifiée."
  fi

  install -d -m 0700 "$STAGING_ROOT"
  rm -rf "${STAGING_ROOT:?}/$version"
  install -d -m 0700 "$STAGING_ROOT/$version"
  tar -xzf "$archive" -C "$STAGING_ROOT/$version" --strip-components=1
  rm -rf "$work"

  [[ -x "$STAGING_ROOT/$version/packaging/install.sh" ]] \
    || die "Archive incomplète: packaging/install.sh manquant"
  [[ -s "$STAGING_ROOT/$version/frontend/dist/index.html" ]] \
    || die "Archive incomplète: interface web absente"
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

restore_backup() {
  local backup="$1"
  [[ -d "$backup/opt-onionpi" ]] || return 1
  log "Restauration de $backup…"
  rsync -a --delete "$backup/opt-onionpi/" "$INSTALL_ROOT/"
  # Unit files travel with the release, so a rollback has to put them back too.
  if [[ -d "$backup/systemd" ]]; then
    rsync -a "$backup/systemd/" /etc/systemd/system/
    systemctl daemon-reload
  fi
  systemctl restart onionpi
}

prune_backups() {
  local keep="$ONIONPI_UPDATE_KEEP_BACKUPS" directory
  (( keep > 0 )) || return 0
  while IFS= read -r directory; do
    [[ -n "$directory" ]] || continue
    rm -rf -- "$directory"
  done < <(find "$BACKUP_ROOT" -maxdepth 1 -type d -name 'onionpi-update-*' 2>/dev/null \
    | sort -r | tail -n +$((keep + 1)))
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
  install -d -m 0700 "$backup/opt-onionpi" "$backup/systemd"
  rsync -a "$INSTALL_ROOT/" "$backup/opt-onionpi/"
  cp -a /etc/systemd/system/onionpi*.service /etc/systemd/system/onionpi*.path \
    /etc/systemd/system/onionpi*.timer "$backup/systemd/" 2>/dev/null || true
  printf '%s\n' "$current" >"$backup/VERSION"
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

  prune_backups
  rm -rf "${STAGING_ROOT:?}/$version"
  state_set "running=false" "installed=$(installed_version)" "update_pending=false" \
    "last_apply=$(now)" "last_apply_status=ok" \
    "last_apply_message=Version $version installée" \
    "history+=$(stamp) installé $version (depuis $current)"
  log "OnionPi est passé de $current à $version."
}

do_rollback() {
  local backup
  backup="$(latest_backup)"
  [[ -n "$backup" ]] || die "Aucune sauvegarde de mise à jour disponible."
  restore_backup "$backup" || die "Restauration impossible depuis $backup."
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
esac

[[ "$EUID" -eq 0 ]] || die "Exécutez onionpi-update avec les droits root."

# One update at a time: the timer and the web interface can fire together.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  die "Une mise à jour est déjà en cours."
fi

trap 'state_set "running=false"' EXIT

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
