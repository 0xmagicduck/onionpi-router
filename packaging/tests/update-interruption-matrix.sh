#!/usr/bin/env bash
set -Eeuo pipefail

# Model every durable checkpoint used by onionpi-update. The test intentionally
# uses real symlinks, snapshots and removal of introduced files, but a temporary
# root so it runs without touching the host.
for checkpoint in 0 1 2 3 4 5; do
  work="$(mktemp -d)"
  releases="$work/opt/onionpi/releases"
  current="$work/opt/onionpi/current"
  root="$work/root"
  backup="$work/backup"
  journal="$work/update-transaction"
  mkdir -p "$releases/0.2.1" "$releases/0.3.0" "$root/etc" "$backup/rootfs/etc"
  : >"$releases/0.2.1/.complete"
  : >"$releases/0.3.0/.complete"
  ln -s releases/0.2.1 "$current"
  printf 'ancienne\n' >"$root/etc/onionpi.conf"
  cp "$root/etc/onionpi.conf" "$backup/rootfs/etc/onionpi.conf"
  printf '%s\n' "$releases/0.2.1" >"$backup/current-target"

  # 0 downloaded; 1 release prepared; 2 journal durable; 3 root mutations;
  # 4 current switched; 5 health passed and journal cleared.
  if (( checkpoint >= 2 )); then printf '%s\n' "$backup" >"$journal"; fi
  if (( checkpoint >= 3 )); then
    printf 'nouvelle\n' >"$root/etc/onionpi.conf"
    printf 'introduit\n' >"$root/etc/nouveau-helper"
  fi
  if (( checkpoint >= 4 )); then
    ln -s releases/0.3.0 "$work/opt/onionpi/.next"
    python3 - "$work/opt/onionpi/.next" "$current" <<'PY'
import os
import sys
os.replace(sys.argv[1], sys.argv[2])
PY
  fi
  if (( checkpoint >= 5 )); then rm -f "$journal"; fi

  # Boot recovery: a durable journal always chooses the complete old release
  # and restores/removes the entire closed mutation surface.
  if [[ -e "$journal" ]]; then
    cp "$backup/rootfs/etc/onionpi.conf" "$root/etc/onionpi.conf"
    rm -f "$root/etc/nouveau-helper"
    ln -s releases/0.2.1 "$work/opt/onionpi/.rollback"
    python3 - "$work/opt/onionpi/.rollback" "$current" <<'PY'
import os
import sys
os.replace(sys.argv[1], sys.argv[2])
PY
    rm -f "$journal"
  fi

  target="$(readlink "$current")"
  if [[ "$target" == releases/0.2.1 ]]; then
    [[ "$(cat "$root/etc/onionpi.conf")" == ancienne && ! -e "$root/etc/nouveau-helper" ]]
  elif [[ "$target" == releases/0.3.0 ]]; then
    [[ "$(cat "$root/etc/onionpi.conf")" == nouvelle && -e "$root/etc/nouveau-helper" ]]
  else
    printf 'Lien current incohérent au point %s: %s\n' "$checkpoint" "$target" >&2
    exit 1
  fi
  rm -rf -- "$work"
done

# Manual rollback has its own durable journal before it starts restoring root
# files. Exercise every checkpoint too: a cut before the journal keeps the new
# version, every later cut deterministically completes the rollback.
for checkpoint in 0 1 2 3 4 5; do
  work="$(mktemp -d)"
  releases="$work/opt/onionpi/releases"
  current="$work/opt/onionpi/current"
  root="$work/root"
  backup="$work/backup"
  journal="$work/update-transaction"
  mkdir -p "$releases/0.2.1" "$releases/0.3.0" "$root/etc" "$backup/rootfs/etc"
  : >"$releases/0.2.1/.complete"
  : >"$releases/0.3.0/.complete"
  ln -s releases/0.3.0 "$current"
  printf 'nouvelle\n' >"$root/etc/onionpi.conf"
  printf 'introduit\n' >"$root/etc/nouveau-helper"
  printf 'ancienne\n' >"$backup/rootfs/etc/onionpi.conf"
  printf '%s\n' "$releases/0.2.1" >"$backup/current-target"

  # 0 before journal; 1 journal durable; 2 old files restored; 3 introduced
  # files removed; 4 current switched; 5 journal durably cleared.
  if (( checkpoint >= 1 )); then printf '%s\n' "$backup" >"$journal"; fi
  if (( checkpoint >= 2 )); then
    cp "$backup/rootfs/etc/onionpi.conf" "$root/etc/onionpi.conf"
  fi
  if (( checkpoint >= 3 )); then rm -f "$root/etc/nouveau-helper"; fi
  if (( checkpoint >= 4 )); then
    ln -s releases/0.2.1 "$work/opt/onionpi/.rollback"
    python3 - "$work/opt/onionpi/.rollback" "$current" <<'PY'
import os
import sys
os.replace(sys.argv[1], sys.argv[2])
PY
  fi
  if (( checkpoint >= 5 )); then rm -f "$journal"; fi

  if [[ -e "$journal" ]]; then
    cp "$backup/rootfs/etc/onionpi.conf" "$root/etc/onionpi.conf"
    rm -f "$root/etc/nouveau-helper"
    ln -s releases/0.2.1 "$work/opt/onionpi/.recovery"
    python3 - "$work/opt/onionpi/.recovery" "$current" <<'PY'
import os
import sys
os.replace(sys.argv[1], sys.argv[2])
PY
    rm -f "$journal"
  fi

  target="$(readlink "$current")"
  if (( checkpoint == 0 )); then
    [[ "$target" == releases/0.3.0 ]]
    [[ "$(cat "$root/etc/onionpi.conf")" == nouvelle ]]
    [[ -e "$root/etc/nouveau-helper" ]]
  else
    [[ "$target" == releases/0.2.1 ]]
    [[ "$(cat "$root/etc/onionpi.conf")" == ancienne ]]
    [[ ! -e "$root/etc/nouveau-helper" ]]
  fi
  rm -rf -- "$work"
done

printf 'Matrice de 12 interruptions installation/rollback validée.\n'
