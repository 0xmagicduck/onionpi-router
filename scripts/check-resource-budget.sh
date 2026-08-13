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
grep -Fqx 'MAX_RULES = 128' "$PROJECT_ROOT/backend/onionpi/access.py"
grep -Fqx 'MAX_CLIENTS = 8' "$PROJECT_ROOT/backend/onionpi/onion.py"
grep -Fqx 'MAX_TRACKED_DEVICES = 256' "$PROJECT_ROOT/backend/onionpi/accounting.py"
# Virtual rack: the ceilings, and the shape of the monitoring sweep. Polling a
# node holds a Tor circuit open, so the batch and the worker count are as much
# part of the budget as the node limit itself.
grep -Fqx 'MAX_RACKS = 8' "$PROJECT_ROOT/backend/onionpi/rack.py"
grep -Fqx 'MAX_NODES = 64' "$PROJECT_ROOT/backend/onionpi/rack.py"
grep -Fqx 'MONITOR_BATCH = 6' "$PROJECT_ROOT/backend/onionpi/rack.py"
grep -Fqx 'MONITOR_WORKERS = 3' "$PROJECT_ROOT/backend/onionpi/rack.py"
grep -Fqx 'MAX_PROFILES = 12' "$PROJECT_ROOT/backend/onionpi/rack.py"
# History is the only table the rack grows on its own, and it grows once per
# probe per node: the per-node ceiling is what keeps a full rack bounded.
grep -Fqx 'MAX_SAMPLES_PER_NODE = 288' "$PROJECT_ROOT/backend/onionpi/database.py"
grep -Fqx 'MemoryMax=128M' "$PROJECT_ROOT/packaging/agent/systemd/onionpi-node-agent.service"
# Two counter sets, each bounded on the kernel side as well: a full set stops
# accepting elements instead of growing.
[[ "$(grep -c '^    size 512$' "$PROJECT_ROOT/packaging/templates/onionpi.nft")" == 2 ]]

printf 'Budget de ressources v0.4 respecté.\n'
