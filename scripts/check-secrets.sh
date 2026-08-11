#!/usr/bin/env bash
set -Eeuo pipefail

# Cheap guard against the mistakes that matter for this project: a Wi-Fi
# passphrase, an onion private key, a generated credentials file or a scrypt
# digest committed by accident. It is not a substitute for reviewing a diff.
# Untracked source is included so the local check protects a change before it
# is staged, exactly when a developer still has time to remove the secret.

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

status=0
report() { printf '%s\n' "$1" >&2; status=1; }

while IFS= read -r file; do
  case "$file" in
    *-identifiants.txt|*openrc*|clouds.yaml|*.pem|*.key)
      report "Fichier sensible suivi par git: $file"
      ;;
  esac
done < <(git ls-files --cached --others --exclude-standard)

# Patterns, not entropy: a false positive here is a five-second review, a
# missed passphrase is a compromised access point.
patterns=(
  'BEGIN [A-Z ]*PRIVATE KEY'
  # A real digest, not the f-string in auth.py that builds one.
  'scrypt\$[0-9]+\$[0-9]+\$[0-9]+\$[A-Za-z0-9_=-]{16,}'
  'ONIONPI_WIFI_PASSWORD=[^$"'"'"'{[:space:]]'
  'ONIONPI_ADMIN_PASSWORD=[^$"'"'"'{[:space:]]'
  '[a-z2-7]{56}\.onion'
)
for pattern in "${patterns[@]}"; do
  matches=()
  while IFS= read -r file; do
    case "$file" in
      scripts/check-secrets.sh|*.lock|package-lock.json) continue ;;
    esac
    [[ -f "$file" ]] || continue
    while IFS= read -r match; do
      matches+=("$match")
    done < <(grep -IHnE -- "$pattern" "$file" 2>/dev/null || true)
  done < <(git ls-files --cached --others --exclude-standard)
  if (( ${#matches[@]} )); then
    report "Motif sensible « $pattern »:"
    printf '%s\n' "${matches[@]}" >&2
  fi
done

(( status )) || printf 'Aucun secret évident dans les fichiers source.\n'
exit "$status"
