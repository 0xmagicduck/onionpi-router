#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

grep -Fqx 'MemoryHigh=320M' "$PROJECT_ROOT/packaging/systemd/onionpi.service"
grep -Fqx 'MemoryMax=384M' "$PROJECT_ROOT/packaging/systemd/onionpi.service"
grep -Fqx 'TasksMax=64' "$PROJECT_ROOT/packaging/systemd/onionpi.service"
grep -Fqx 'LimitNOFILE=1024' "$PROJECT_ROOT/packaging/systemd/onionpi.service"
grep -Fqx 'HASHING_SLOTS = 4' "$PROJECT_ROOT/backend/onionpi/auth.py"
grep -Fqx 'MAX_MESSAGES = 2_000' "$PROJECT_ROOT/backend/onionpi/database.py"
grep -Fqx 'MAX_ACTIVITIES = 4_000' "$PROJECT_ROOT/backend/onionpi/database.py"
grep -Fq 'deque(maxlen=180)' "$PROJECT_ROOT/backend/onionpi/system.py"
grep -Fqx 'ONIONPI_UPDATE_MAX_ARCHIVE_BYTES=268435456' \
  "$PROJECT_ROOT/packaging/templates/update.conf"

printf 'Budget de ressources v0.3 respecté.\n'
