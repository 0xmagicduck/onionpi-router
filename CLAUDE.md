# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OnionPi turns a Raspberry Pi into a Wi-Fi access point whose TCP and DNS
traffic is forced through Tor, administered from a local web interface. The
repository is both the application and the appliance: `packaging/` installs it
onto a Pi, and a deployed Pi updates itself from GitHub releases built from
this same tree. A mistake here reaches machines that reinstall themselves
unattended at 4 a.m.

## Commands

```bash
./scripts/check.sh                 # everything CI runs, fail-fast order
./scripts/check.sh backend         # or: meta | backend | frontend | shell
```

Setup:

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
(cd frontend && npm ci)
```

Backend tests (must run from `backend/`, `pytest.ini` sets `pythonpath = .`):

```bash
cd backend && ../.venv/bin/pytest -q
cd backend && ../.venv/bin/pytest tests/test_api.py::test_mutations_require_csrf_and_paths_stay_in_share -q
```

Lint / typecheck / build:

```bash
.venv/bin/ruff check backend
npm run --prefix frontend check     # tsc -b, no emit
npm run --prefix frontend build     # tsc -b && vite build -> frontend/dist
```

Run the API locally (no Pi needed — see Demo mode):

```bash
export ONIONPI_DEMO_MODE=1
export ONIONPI_SESSION_SECRET="$(openssl rand -hex 32)"
cd backend
printf '%s\n' 'mot-de-passe-de-demo-solide' | ../.venv/bin/python -m onionpi.cli create-admin --password-stdin
../.venv/bin/uvicorn onionpi.main:app --host 127.0.0.1 --port 8080
```

`npm run --prefix frontend dev` serves the UI on 5173 and proxies `/api`
(including the WebSocket) to `127.0.0.1:8080`.

Release: `./scripts/set-version.sh X.Y.Z` (updates `VERSION`,
`frontend/package.json`, `backend/onionpi/__init__.py` together), merge, tag
`vX.Y.Z`, then bump to the next dev version. Details in `CONTRIBUTING.md` and
`docs/updates.md`.

## Architecture

### The privilege model is the point

The web service (`backend/onionpi`) runs unprivileged with
`NoNewPrivileges=true`. It **never** calls `systemctl`, `nft`, `reboot`, or
`sudo`. Instead:

1. it writes a data file it owns (`blocked-macs.txt`, `bridges.conf`,
   `block.hosts`, `relay.state`, …);
2. it writes `"<nonce> <verb>"` into `/var/lib/onionpi/agent.request`
   (`agent.py`, verbs enumerated in `ACTIONS`);
3. `packaging/systemd/onionpi-agent.path` wakes a root oneshot that runs
   `packaging/onionpi-agent-apply.sh`, which **re-validates the verb against
   its own allow-list**, takes no argument from the file, and writes the answer
   to root-owned `/var/lib/onionpi-privileged/agent.result`.

The request file is untrusted input on the privileged side. `agent.py` is
convenience, `onionpi-agent-apply.sh` is the security boundary. When a change
needs root, the correct answer is a new verb validated in the root script,
never a `sudo` in the web service. Snowflake relay and device blocking use the
same shape via their own `.path` units.

Two exceptions where the app writes outside `/var/lib/onionpi`, both granted
narrowly by `install.sh`: `/etc/onionpi/tor/{bridges,policy}.conf` (reloaded
through the Tor control port, not systemd) and `/etc/onionpi/dns/block.hosts`
(dnsmasq drops privileges and cannot traverse the 0750 data dir).

### Backend modules

`main.py` (FastAPI, ~1000 lines) is the whole HTTP surface: it instantiates
every manager at import time as module-level singletons and wires them
together. Everything under `/api/v1`. Auth is a cookie session plus a CSRF
header — read endpoints depend on `current_session`, every mutation depends on
`csrf_session` (Origin check + `X-CSRF-Token` compared with
`secrets.compare_digest`). Static frontend is mounted from
`settings.frontend_dir`.

- `config.py` — frozen `Settings` dataclass from `ONIONPI_*` env vars,
  `get_settings()` is `lru_cache`d. Every path in the app is a property here;
  add new ones there rather than composing paths at call sites.
- `tor_control.py` — raw control-port client (SAFECOOKIE auth over a socket),
  used for `NEWNYM`, `SETCONF`, `ADD_ONION`, circuit and bootstrap state.
- `circumvention.py` — bridge/pluggable-transport state machine plus a
  background watchdog thread that escalates from direct to bridges when
  bootstrap stalls; bundled catalog in `onionpi/data/circumvention.json`.
- `netcontrol.py` — `DeviceGuard` (MAC blocking) and `DnsFilter` (hosts-file
  blocklists fetched through Tor's SOCKS port with `httpx[socks]`).
- `accounting.py` — per-device byte totals. The counters live in two dynamic
  nftables sets and only root may read them, so `onionpi-accounting.timer`
  publishes a JSON snapshot and this module folds it into totals that survive a
  rule reload. Read-only: no new privileged verb.
- `policy.py` / `onion.py` / `relay.py` / `updates.py` / `system.py` — exit
  country and scheduled `NEWNYM`, onion service, Snowflake proxy switch,
  read-only view of the root updater plus the three preferences it accepts,
  metrics/journal sampling.
- `database.py` — SQLite (WAL), schema as one `SCHEMA` string executed
  idempotently on `initialize()`; users, sessions, chat messages, activity
  feed, and a JSON `settings` key/value table that holds UI-owned intent.
- `agent.py`, `auth.py` (scrypt hashing, login limiter), `cli.py`
  (`onionpi-admin create-admin`).

Stored intent is the source of truth: nftables and Tor forget everything on
restart, so `lifespan` re-pushes device blocks and republishes the onion
address at startup.

### Demo mode

`ONIONPI_DEMO_MODE=1` makes every manager return plausible data instead of
touching Tor, nftables or systemd. It is how the tests run and how the UI is
developed on a laptop. New subsystems that touch the host must carry a
`demo_mode` branch, or `pytest` and local dev break.

### Frontend

Vite + React 18 + TypeScript, no router and no state library: `App.tsx` keeps
`page` in state synced to the URL hash, `hooks/usePolling.ts` re-fetches each
endpoint on its own interval, `hooks/useChat.ts` holds the WebSocket. All HTTP
goes through `api.ts`, which owns the CSRF token and throws `ApiError`. Types
in `types.ts` are hand-written and must be kept in step with the payloads
`main.py` returns.

### Packaging

`packaging/install.sh` is the single source of truth for the deployed layout:
NetworkManager AP, dnsmasq, nftables kill switch, nginx TLS front end, systemd
units, and the `--upgrade` path the updater re-runs in place. Templates in
`packaging/templates/`, root helper scripts as `onionpi-*-apply.sh`,
`packaging/image/build-image.sh` produces a flashable Raspberry Pi OS image
that installs on first boot.

The AP profile has `connection.autoconnect=no`; `onionpi-ap.service` is its
only activation path and depends on `onionpi-firewall.service`. Root-written
agent/update state belongs under `/var/lib/onionpi-privileged`, and update
staging belongs under `/var/cache/onionpi-update`, never in an application-owned
directory.

## Conventions CI enforces

- **Versions agree** across `VERSION`, `frontend/package.json` and
  `backend/onionpi/__init__.py` (`scripts/check-version.sh`) — a disagreement
  is how an appliance believes it is up to date when it is not.
- **Every script and unit added under `packaging/` must appear in both
  `install.sh` and `uninstall.sh`.** An uninstall that leaves files behind is a
  bug.
- **No credentials tracked by git** (`scripts/check-secrets.sh`): private keys,
  scrypt digests, `*-identifiants.txt`, `.onion` addresses.
- Shell: `set -Eeuo pipefail`, quoted variables, shellcheck clean at
  `--severity=warning`, no unvalidated input reaching `systemctl`, `nft` or
  `rm`.
- Python: no `subprocess(shell=True)`, no paths built by string concatenation.
  Ruff config in `ruff.toml` — deliberately narrow, and its `ignore` list
  encodes real decisions (`RUF001-003` off because French typography is
  intentional).

## Language

User-facing text — UI strings, error messages, README, docs, commit-visible
prose — is in **French**. Code comments are in **English**, and explain *why*;
comments that paraphrase the next line get deleted in review.
