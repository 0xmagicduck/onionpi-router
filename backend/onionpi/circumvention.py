"""Bridges and pluggable transports.

OnionPi writes a single Tor configuration fragment and asks Tor to reload it
through the control port. Nothing here needs root: install.sh gives the
`onionpi` user write access to that one file and to nothing else.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .tor_control import TorControlError, TorController

logger = logging.getLogger("onionpi.circumvention")

MOAT_URL = "https://bridges.torproject.org/moat/circumvention"
BUNDLED_CATALOG = Path(__file__).with_name("data") / "circumvention.json"

MODES = ("direct", "auto", "manual")

# Seconds Tor is allowed to stay below 100 % before the watchdog reacts. The
# first threshold is generous because a cold Pi also needs time to fetch a
# consensus over a working connection.
STALL_BEFORE_BRIDGES = 150
STALL_BEFORE_ROTATION = 210
WATCHDOG_INTERVAL = 15


@dataclass(frozen=True)
class Transport:
    id: str
    label: str
    #: Name Tor expects in `Bridge` lines, which differs from our own id.
    tor_name: str
    binary: Path
    description: str

    @property
    def available(self) -> bool:
        return self.binary.exists()

    def plugin_line(self) -> str:
        return f"ClientTransportPlugin {self.tor_name} exec {self.binary}"


TRANSPORTS: tuple[Transport, ...] = (
    Transport(
        id="snowflake",
        label="Snowflake",
        tor_name="snowflake",
        binary=Path("/usr/bin/snowflake-client"),
        description=(
            "Rebondit par des volontaires en WebRTC. Aucun pont à demander, "
            "c’est le choix le plus simple quand Tor est bloqué."
        ),
    ),
    Transport(
        id="obfs4",
        label="obfs4",
        tor_name="obfs4",
        binary=Path("/usr/bin/obfs4proxy"),
        description=(
            "Brouille le trafic pour qu’il ne ressemble à rien de connu. "
            "Rapide, mais les ponts publics sont parfois déjà bloqués."
        ),
    ),
    Transport(
        id="meek",
        label="meek (domain fronting)",
        tor_name="meek_lite",
        binary=Path("/usr/bin/obfs4proxy"),
        description=(
            "Fait passer Tor pour du trafic vers un grand CDN. Lent et coûteux, "
            "à garder en dernier recours."
        ),
    ),
)

TRANSPORTS_BY_ID = {transport.id: transport for transport in TRANSPORTS}
TOR_NAMES = {transport.tor_name for transport in TRANSPORTS}

BRIDGE_LINE = re.compile(
    r"^(?:(?P<transport>[A-Za-z][A-Za-z0-9_]{0,19})\s+)?"
    r"(?P<address>\[[0-9A-Fa-f:]+\]|[0-9]{1,3}(?:\.[0-9]{1,3}){3}):(?P<port>[0-9]{1,5})"
    r"(?P<rest>(?:\s+[!-~]+)*)$"
)


class CircumventionError(RuntimeError):
    pass


def validate_bridge_line(line: str) -> str:
    """Rejects anything that is not a bridge line before it reaches torrc.

    A single bad line makes Tor refuse the whole configuration on reload, so
    the check happens here rather than in Tor's parser.
    """
    if "\n" in line or "\r" in line:
        # One line per bridge, always: a fragment carrying its own newline
        # would smuggle unrelated directives into torrc.
        raise CircumventionError("Une ligne de pont ne peut pas contenir de retour à la ligne")
    cleaned = " ".join(line.strip().split())
    if not cleaned or cleaned.startswith("#"):
        raise CircumventionError("Ligne de pont vide")
    if len(cleaned) > 800:
        raise CircumventionError("Ligne de pont trop longue")
    if not cleaned.isprintable() or not cleaned.isascii():
        raise CircumventionError("La ligne de pont contient des caractères interdits")
    if cleaned.lower().startswith("bridge "):
        cleaned = cleaned[7:].strip()
    match = BRIDGE_LINE.match(cleaned)
    if not match:
        raise CircumventionError(f"Ligne de pont illisible: {cleaned[:60]}")
    if not 1 <= int(match.group("port")) <= 65535:
        raise CircumventionError("Port de pont invalide")
    transport = match.group("transport")
    if transport and transport not in TOR_NAMES:
        supported = ", ".join(sorted(TOR_NAMES))
        raise CircumventionError(
            f"Transport « {transport} » non pris en charge. Disponibles: {supported}"
        )
    return cleaned


def load_catalog(cache_path: Path | None = None) -> dict[str, Any]:
    """Bundled bridge list, overridden by a successful moat refresh."""
    catalog = json.loads(BUNDLED_CATALOG.read_text())
    catalog.setdefault("source", "bundled")
    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("Cache de contournement illisible: %s", cache_path)
            return catalog
        if cached.get("builtin"):
            cached.setdefault("source", "moat")
            cached.setdefault("countries", catalog["countries"])
            return cached
    return catalog


def fetch_catalog(cache_path: Path) -> dict[str, Any]:
    """Refreshes the bridge list from the Tor Project.

    Deliberately a direct HTTPS request: it has to work while Tor itself is
    still blocked. It simply fails where bridges.torproject.org is censored,
    and the bundled snapshot keeps the feature usable.
    """
    aliases = {"meek-azure": "meek", "meek": "meek"}
    builtin: dict[str, list[str]] = {"obfs4": [], "snowflake": [], "meek": []}
    countries: dict[str, list[str]] = {}
    headers = {"Content-Type": "application/vnd.api+json"}
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        payload = client.post(f"{MOAT_URL}/builtin", headers=headers).json()
        for name, lines in payload.items():
            key = aliases.get(name, name)
            if key in builtin and isinstance(lines, list) and not builtin[key]:
                builtin[key] = [str(line) for line in lines]
        mapping = client.post(f"{MOAT_URL}/map", headers=headers).json()
    for country, value in mapping.items():
        order: list[str] = []
        for setting in value.get("settings", []):
            kind = aliases.get(setting.get("bridges", {}).get("type", ""), "")
            kind = kind or setting.get("bridges", {}).get("type", "")
            if kind in TRANSPORTS_BY_ID and kind not in order:
                order.append(kind)
        if order and re.fullmatch(r"[a-z]{2}", country):
            countries[country] = order
    if not any(builtin.values()):
        raise CircumventionError("Réponse inattendue du service de ponts")
    bundled = load_catalog()
    for key, lines in builtin.items():
        if not lines:
            builtin[key] = list(bundled.get("builtin", {}).get(key, []))
    catalog = {
        "snapshot_at": int(time.time()),
        "builtin": builtin,
        "countries": countries or dict(bundled.get("countries", {})),
        "source": "moat",
    }
    _atomic_write(cache_path, json.dumps(catalog, indent=2) + "\n")
    return catalog


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        temporary.write_text(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class BridgeConfig:
    mode: str = "auto"
    transport: str = "snowflake"
    country: str = ""
    custom_bridges: list[str] = field(default_factory=list)
    #: Transport the watchdog is currently trying, empty while direct.
    auto_transport: str = ""
    updated_at: int = 0

    @classmethod
    def load(cls, path: Path, default_country: str = "") -> BridgeConfig:
        config = cls(country=default_country)
        try:
            stored = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return config
        if not isinstance(stored, dict):
            return config
        mode = str(stored.get("mode", config.mode))
        config.mode = mode if mode in MODES else "auto"
        transport = str(stored.get("transport", config.transport))
        config.transport = transport if transport in TRANSPORTS_BY_ID else "snowflake"
        config.country = str(stored.get("country", default_country))[:2].upper()
        bridges = stored.get("custom_bridges", [])
        config.custom_bridges = [str(item) for item in bridges if isinstance(item, str)][:40]
        auto = str(stored.get("auto_transport", ""))
        config.auto_transport = auto if auto in TRANSPORTS_BY_ID else ""
        config.updated_at = int(stored.get("updated_at", 0) or 0)
        return config

    def save(self, path: Path) -> None:
        _atomic_write(
            path,
            json.dumps(
                {
                    "mode": self.mode,
                    "transport": self.transport,
                    "country": self.country,
                    "custom_bridges": self.custom_bridges,
                    "auto_transport": self.auto_transport,
                    "updated_at": self.updated_at,
                },
                indent=2,
            )
            + "\n",
        )


class CircumventionManager:
    """Owns the generated Tor fragment and the anti-censorship watchdog."""

    HEADER = (
        "# Généré par OnionPi. Ne pas modifier à la main:\n"
        "# le fichier est réécrit à chaque changement depuis l’interface web.\n"
    )

    def __init__(
        self,
        *,
        config_path: Path,
        state_path: Path,
        cache_path: Path,
        controller: TorController,
        country: str = "",
        demo_mode: bool = False,
        require_installed: bool = True,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config_path = config_path
        self.state_path = state_path
        self.cache_path = cache_path
        self.controller = controller
        self.demo_mode = demo_mode
        #: Refuse a transport whose binary is missing. Disabled off-device,
        #: where the pluggable transports are obviously not installed.
        self.require_installed = require_installed and not demo_mode
        self.on_event = on_event or (lambda kind, message: None)
        self.catalog = load_catalog(cache_path)
        self.config = BridgeConfig.load(state_path, country)
        if not self.config.country:
            self.config.country = country
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stalled_since: float | None = None
        self._last_switch = 0.0

    # ------------------------------------------------------------------ state

    @property
    def country_code(self) -> str:
        return (self.config.country or "").lower()[:2]

    def recommended(self, country: str | None = None) -> list[str]:
        code = (country if country is not None else self.country_code).lower()[:2]
        order = [
            item
            for item in self.catalog.get("countries", {}).get(code, [])
            if item in TRANSPORTS_BY_ID
        ]
        if not order:
            order = ["snowflake", "obfs4"]
        return [item for item in order if TRANSPORTS_BY_ID[item].available] or order

    def is_censored_country(self, country: str | None = None) -> bool:
        code = (country if country is not None else self.country_code).lower()[:2]
        return bool(code) and code in self.catalog.get("countries", {})

    def bridges_for(self, transport: str) -> list[str]:
        if self.config.custom_bridges:
            return list(self.config.custom_bridges)
        return [str(line) for line in self.catalog.get("builtin", {}).get(transport, [])]

    def render(self, transport: str | None) -> str:
        """Builds the Tor fragment for a transport, or for a direct connection."""
        if not transport:
            return self.HEADER + "UseBridges 0\n"
        chosen = TRANSPORTS_BY_ID.get(transport)
        if chosen is None:
            raise CircumventionError(f"Transport inconnu: {transport}")
        lines = [validate_bridge_line(line) for line in self.bridges_for(transport)]
        if not lines:
            raise CircumventionError(
                f"Aucun pont {chosen.label} disponible. Ajoutez des ponts manuellement."
            )
        body = [self.HEADER, "UseBridges 1", chosen.plugin_line()]
        body.extend(f"Bridge {line}" for line in lines)
        return "\n".join(body) + "\n"

    # ----------------------------------------------------------------- apply

    def _apply(self, transport: str | None) -> None:
        content = self.render(transport)
        if self.demo_mode:
            return
        try:
            previous = self.config_path.read_text()
        except OSError:
            previous = ""
        if previous == content:
            return
        try:
            _atomic_write(self.config_path, content)
        except OSError as error:
            raise CircumventionError(
                f"Écriture impossible dans {self.config_path}: {error}"
            ) from error
        try:
            self.controller.reload_config()
        except TorControlError as error:
            # A refused reload leaves Tor running on the old configuration;
            # putting the file back keeps the two in sync.
            if previous:
                try:
                    _atomic_write(self.config_path, previous)
                    self.controller.reload_config()
                except (OSError, TorControlError):
                    logger.exception("Restauration de la configuration de ponts impossible")
            raise CircumventionError(f"Tor a refusé la configuration: {error}") from error

    def active_transport(self) -> str | None:
        """Transport that should currently be configured, given the mode."""
        if self.config.mode == "direct":
            return None
        if self.config.mode == "manual":
            return self.config.transport
        return self.config.auto_transport or None

    def reconcile(self) -> None:
        """Re-applies the stored configuration, e.g. after a restart."""
        with self._lock:
            try:
                self._apply(self.active_transport())
            except CircumventionError as error:
                logger.warning("Configuration de ponts non appliquée: %s", error)

    def update(
        self,
        *,
        mode: str,
        transport: str,
        country: str,
        custom_bridges: list[str],
    ) -> dict[str, Any]:
        if mode not in MODES:
            raise CircumventionError("Mode de connexion inconnu")
        if transport not in TRANSPORTS_BY_ID:
            raise CircumventionError("Transport inconnu")
        chosen = TRANSPORTS_BY_ID[transport]
        if mode == "manual" and self.require_installed and not chosen.available:
            raise CircumventionError(
                f"{chosen.label} n’est pas installé sur cette Raspberry Pi ({chosen.binary})."
            )
        cleaned = [validate_bridge_line(line) for line in custom_bridges if line.strip()]
        if len(cleaned) > 40:
            raise CircumventionError("40 ponts au maximum")
        country_code = re.sub(r"[^A-Za-z]", "", country)[:2].upper()

        with self._lock:
            previous = self.config
            self.config = BridgeConfig(
                mode=mode,
                transport=transport,
                country=country_code,
                custom_bridges=cleaned,
                auto_transport=previous.auto_transport,
                updated_at=int(time.time()),
            )
            if mode == "direct":
                self.config.auto_transport = ""
            elif mode == "manual":
                self.config.auto_transport = ""
            elif mode == "auto":
                # Starting censored means starting on bridges: waiting for a
                # failed bootstrap would only delay a connection we know needs
                # help.
                if self.is_censored_country() and not self.config.auto_transport:
                    self.config.auto_transport = self.recommended()[0]
            try:
                self._apply(self.active_transport())
            except CircumventionError:
                self.config = previous
                raise
            self.config.save(self.state_path)
            self._stalled_since = None
            self._last_switch = time.monotonic()
        label = {"direct": "connexion directe", "auto": "mode automatique", "manual": chosen.label}
        self.on_event("secure", f"Connexion Tor: {label[mode]}")
        return self.snapshot()

    def refresh_catalog(self) -> dict[str, Any]:
        catalog = fetch_catalog(self.cache_path)
        with self._lock:
            self.catalog = catalog
            active = self.active_transport()
            if active and not self.config.custom_bridges:
                self._apply(active)
        return self.snapshot()

    # -------------------------------------------------------------- watchdog

    def start(self) -> None:
        if self.demo_mode or (self._thread and self._thread.is_alive()):
            return
        self.reconcile()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="onionpi-circumvention", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # keep the watchdog alive whatever Tor answers
                logger.exception("Cycle de contournement en échec")
            self._stop.wait(WATCHDOG_INTERVAL)

    def tick(self) -> None:
        if self.config.mode != "auto":
            return
        progress = self.controller.bootstrap_progress()
        now = time.monotonic()
        if progress >= 100:
            self._stalled_since = None
            return
        if self._stalled_since is None:
            self._stalled_since = now
            return
        stalled = now - self._stalled_since
        current = self.config.auto_transport
        threshold = STALL_BEFORE_ROTATION if current else STALL_BEFORE_BRIDGES
        if stalled < threshold or now - self._last_switch < threshold:
            return
        order = self.recommended()
        if not order:
            return
        index = order.index(current) + 1 if current in order else 0
        target = order[index % len(order)]
        with self._lock:
            try:
                self._apply(target)
            except CircumventionError as error:
                logger.warning("Bascule automatique vers %s impossible: %s", target, error)
                self._last_switch = now
                return
            self.config.auto_transport = target
            self.config.updated_at = int(time.time())
            self.config.save(self.state_path)
            self._stalled_since = None
            self._last_switch = now
        reason = "Tor bloqué" if not current else f"{current} sans effet"
        self.on_event(
            "secure",
            f"{reason}: bascule automatique sur {TRANSPORTS_BY_ID[target].label}",
        )

    # -------------------------------------------------------------- snapshot

    def snapshot(self) -> dict[str, Any]:
        stalled = 0
        if self._stalled_since is not None:
            stalled = int(time.monotonic() - self._stalled_since)
        return {
            "mode": self.config.mode,
            "transport": self.config.transport,
            "country": self.config.country,
            "custom_bridges": list(self.config.custom_bridges),
            "auto_transport": self.config.auto_transport,
            "updated_at": self.config.updated_at,
            "censored_country": self.is_censored_country(),
            "recommended": self.recommended(),
            "known_countries": sorted(self.catalog.get("countries", {})),
            "catalog": {
                "source": self.catalog.get("source", "bundled"),
                "snapshot_at": int(self.catalog.get("snapshot_at", 0) or 0),
                "counts": {
                    key: len(value)
                    for key, value in self.catalog.get("builtin", {}).items()
                },
            },
            "stalled_seconds": stalled,
            "transports": [
                {
                    "id": transport.id,
                    "label": transport.label,
                    "description": transport.description,
                    "available": transport.available or self.demo_mode,
                    "binary": str(transport.binary),
                }
                for transport in TRANSPORTS
            ],
        }
