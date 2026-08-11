from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import __version__

logger = logging.getLogger("onionpi.config")


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    cleaned = value.strip().lower()
    if cleaned in _TRUE:
        return True
    if cleaned not in _FALSE:
        # "ONIONPI_COOKIE_SECURE=ture" used to mean "off" without a word. A
        # switch that silently turns itself off on a typo is worse than one
        # that complains.
        logger.warning(
            "%s=%r n’est ni vrai ni faux: la valeur est ignorée (désactivé).", name, value
        )
    return False


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Reads an integer setting, refusing values outside a usable range.

    An unparseable or out-of-range value falls back to the default and says so.
    Silently accepting ONIONPI_SESSION_TTL=0 or a negative storage reserve turns
    a typo into a security setting nobody chose.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("%s=%r n’est pas un entier: %d retenu.", name, raw, default)
        return default
    if not minimum <= value <= maximum:
        logger.warning(
            "%s=%d hors des bornes [%d, %d]: %d retenu.", name, value, minimum, maximum, default
        )
        return default
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    shared_dir: Path
    database_path: Path
    frontend_dir: Path
    tor_control_host: str
    tor_control_port: int
    tor_cookie_path: Path
    tor_config_dir: Path
    relay_state_path: Path
    dns_filter_dir: Path
    country: str
    gateway_ip: str
    wifi_interface: str
    upstream_interface: str
    session_ttl_seconds: int
    max_request_bytes: int
    max_upload_bytes: int
    cookie_secure: bool
    demo_mode: bool
    app_port: int
    device_name: str
    session_secret: str
    storage_reserve_bytes: int
    version: str

    @property
    def allowed_origins(self) -> set[str]:
        configured = {
            item.strip().rstrip("/")
            for item in os.getenv("ONIONPI_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        }
        configured.update(
            {
                f"http://{self.gateway_ip}:{self.app_port}",
                f"https://{self.gateway_ip}",
                f"http://onionpi.local:{self.app_port}",
                "https://onionpi.local",
                f"http://localhost:{self.app_port}",
                f"http://127.0.0.1:{self.app_port}",
            }
        )
        if self.demo_mode:
            # The Vite development server. It has no business being trusted by
            # an appliance on someone's network.
            configured.update({"http://localhost:5173", "http://127.0.0.1:5173"})
        return configured

    @property
    def bridge_config_path(self) -> Path:
        return self.tor_config_dir / "bridges.conf"

    @property
    def circumvention_state_path(self) -> Path:
        return self.data_dir / "circumvention.json"

    @property
    def circumvention_cache_path(self) -> Path:
        return self.data_dir / "circumvention-cache.json"

    @property
    def tor_policy_path(self) -> Path:
        """Exit-node policy fragment, included by torrc next to bridges.conf."""
        return self.tor_config_dir / "policy.conf"

    @property
    def onion_key_path(self) -> Path:
        return self.data_dir / "onion.key"

    @property
    def blocked_macs_path(self) -> Path:
        return self.data_dir / "blocked-macs.txt"

    @property
    def agent_request_path(self) -> Path:
        return self.data_dir / "agent.request"

    @property
    def agent_result_path(self) -> Path:
        return Path(os.getenv("ONIONPI_AGENT_RESULT", self.data_dir / "agent.result")).resolve()

    @property
    def update_state_path(self) -> Path:
        """Written by onionpi-update as root, only ever read from here."""
        return Path(os.getenv("ONIONPI_UPDATE_STATE", self.data_dir / "update.state")).resolve()

    @property
    def update_settings_path(self) -> Path:
        """Update preferences chosen from the interface, revalidated as root."""
        return self.data_dir / "update.settings.json"

    @property
    def dns_block_path(self) -> Path:
        """Hosts file dnsmasq reads through addn-hosts.

        It lives outside /var/lib/onionpi because dnsmasq drops privileges and
        would not be able to traverse that 0750 directory.
        """
        return self.dns_filter_dir / "block.hosts"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.dns_filter_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("Répertoire de filtrage DNS inaccessible: %s", self.dns_filter_dir)
        # In production install.sh owns this directory: it must stay readable by
        # the debian-tor user, so never widen its permissions from here.
        try:
            self.tor_config_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning(
                "Répertoire de configuration Tor inaccessible: %s", self.tor_config_dir
            )


def _installed_version(project_root: Path) -> str:
    """The VERSION file of the deployed tree, falling back to the package one.

    install.sh copies VERSION next to the code in /opt/onionpi, and
    onionpi-update compares that file with the version published on GitHub.
    Reading the same file here keeps the interface and the updater in
    agreement even when someone reinstalls by hand.
    """
    candidates = [
        Path(os.getenv("ONIONPI_VERSION_FILE", "")) if os.getenv("ONIONPI_VERSION_FILE") else None,
        project_root / "VERSION",
        project_root.parent / "VERSION",
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            value = candidate.read_text().strip()
        except OSError:
            continue
        if value:
            return value.splitlines()[0].strip()
    return __version__


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.getenv("ONIONPI_DATA_DIR", project_root / ".data")).resolve()
    shared_dir = Path(os.getenv("ONIONPI_SHARED_DIR", data_dir / "shared")).resolve()
    session_secret = os.getenv("ONIONPI_SESSION_SECRET", "")
    if not session_secret:
        # Without a stable secret every restart silently invalidates all
        # sessions, which looks like a random logout bug to the user.
        session_secret = secrets.token_hex(32)
        if not _flag("ONIONPI_DEMO_MODE"):
            logger.warning(
                "ONIONPI_SESSION_SECRET absent: secret temporaire généré, "
                "les sessions seront perdues au prochain redémarrage."
            )
    settings = Settings(
        data_dir=data_dir,
        shared_dir=shared_dir,
        database_path=Path(os.getenv("ONIONPI_DB_PATH", data_dir / "onionpi.db")).resolve(),
        frontend_dir=Path(
            os.getenv("ONIONPI_FRONTEND_DIR", project_root / "frontend" / "dist")
        ).resolve(),
        tor_control_host=os.getenv("ONIONPI_TOR_CONTROL_HOST", "127.0.0.1"),
        tor_control_port=_bounded_int("ONIONPI_TOR_CONTROL_PORT", 9051, 1, 65535),
        tor_cookie_path=Path(
            os.getenv("ONIONPI_TOR_COOKIE", "/run/tor/control.authcookie")
        ),
        tor_config_dir=Path(
            os.getenv("ONIONPI_TOR_CONFIG_DIR", data_dir / "tor")
        ).resolve(),
        relay_state_path=Path(
            os.getenv("ONIONPI_RELAY_STATE", data_dir / "relay.state")
        ).resolve(),
        dns_filter_dir=Path(
            os.getenv("ONIONPI_DNS_FILTER_DIR", data_dir / "dns")
        ).resolve(),
        country=os.getenv("ONIONPI_COUNTRY", "").strip().upper()[:2],
        gateway_ip=os.getenv("ONIONPI_GATEWAY_IP", "10.42.0.1"),
        wifi_interface=os.getenv("ONIONPI_WIFI_INTERFACE", "wlan0"),
        upstream_interface=os.getenv("ONIONPI_UPSTREAM_INTERFACE", "eth0"),
        # 5 minutes to 30 days. Zero would log everyone out at the moment they
        # signed in, and an unbounded value never expires a stolen cookie.
        session_ttl_seconds=_bounded_int("ONIONPI_SESSION_TTL", 43_200, 300, 2_592_000),
        max_request_bytes=_bounded_int(
            "ONIONPI_MAX_REQUEST_BYTES", 1024**2, 64 * 1024, 16 * 1024**2
        ),
        max_upload_bytes=_bounded_int(
            "ONIONPI_MAX_UPLOAD_BYTES", 1024**3, 1024**2, 64 * 1024**3
        ),
        cookie_secure=_flag("ONIONPI_COOKIE_SECURE"),
        demo_mode=_flag("ONIONPI_DEMO_MODE"),
        app_port=_bounded_int("ONIONPI_PORT", 8080, 1, 65535),
        device_name=os.getenv("ONIONPI_DEVICE_NAME", "OnionPi"),
        session_secret=session_secret,
        # Never negative: the upload budget is free space minus this reserve,
        # and a negative reserve would hand uploads the last blocks of the card.
        storage_reserve_bytes=_bounded_int(
            "ONIONPI_STORAGE_RESERVE_BYTES", 512 * 1024**2, 0, 64 * 1024**3
        ),
        version=_installed_version(project_root),
    )
    settings.ensure_directories()
    if settings.demo_mode and Path("/etc/onionpi/onionpi.env").exists():
        # Demonstration mode makes every control a no-op and every reading a
        # plausible invention: Tor reports "connecté", blocked devices are never
        # written to nftables, and the DNS blocklists are fetched without going
        # through Tor. On a machine that is actually routing a household, an
        # interface that says everything is fine while nothing is enforced is
        # the worst possible failure. It cannot be corrected from here — the
        # value is in a root-owned file — so say it as loudly as a log allows.
        logger.error(
            "ONIONPI_DEMO_MODE est actif sur une installation réelle: aucune "
            "protection n’est appliquée et l’interface affiche des données "
            "fictives. Retirez ce réglage de /etc/onionpi/onionpi.env."
        )
    return settings
