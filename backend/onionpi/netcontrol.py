"""Client control: per-device blocking and DNS filtering.

Both features write a plain data file and ask the privileged agent to apply it.
Neither of them needs the web application to hold any capability: nftables and
dnsmasq are driven by root-owned scripts with their own allow-lists.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .agent import AgentError, PrivilegedAgent
from .atomic_io import atomic_write_text
from .database import Database

logger = logging.getLogger("onionpi.netcontrol")

MAC_PATTERN = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
DOMAIN_PATTERN = re.compile(r"^(?=.{1,253}$)([a-z0-9_](-*[a-z0-9_])*\.)+[a-z]{2,63}$")
HOSTS_LINE = re.compile(r"^\s*(?:0\.0\.0\.0|127\.0\.0\.1|::)\s+(\S+)")

BLOCKED_DEVICES_KEY = "blocked_devices"
DNS_FILTER_KEY = "dns_filter"

#: Every profile below is a superset of the previous "standard" list, so
#: selecting several simply merges into one deduplicated file.
DNS_PROFILES: dict[str, dict[str, str]] = {
    "standard": {
        "label": "Publicités et traqueurs",
        "description": "Liste unifiée StevenBlack: régies publicitaires, traqueurs et sites malveillants connus.",
        "url": "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
    },
    "social": {
        "label": "Traqueurs des réseaux sociaux",
        "description": "Ajoute les domaines de pistage de Facebook, TikTok, X et consorts.",
        "url": "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/social/hosts",
    },
    "gambling": {
        "label": "Jeux d’argent",
        "description": "Ajoute les sites de paris et de casino en ligne.",
        "url": "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/gambling/hosts",
    },
    "adult": {
        "label": "Contenus pour adultes",
        "description": "Ajoute les sites pornographiques recensés par la liste.",
        "url": "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn/hosts",
    },
}

MAX_LIST_BYTES = 16 * 1024**2
MAX_DOMAINS = 300_000


def normalize_mac(value: str) -> str:
    cleaned = value.strip().lower().replace("-", ":")
    if not MAC_PATTERN.fullmatch(cleaned):
        raise ValueError(f"Adresse MAC invalide: {value}")
    return cleaned


def normalize_domain(value: str) -> str:
    cleaned = value.strip().lower().rstrip(".")
    cleaned = cleaned.removeprefix("http://").removeprefix("https://").split("/", 1)[0]
    if not DOMAIN_PATTERN.fullmatch(cleaned):
        raise ValueError(f"Nom de domaine invalide: {value}")
    return cleaned


class NetControlError(RuntimeError):
    pass


class DeviceGuard:
    """Keeps the list of Wi-Fi clients denied any access through the Pi."""

    def __init__(
        self,
        database: Database,
        state_path: Path,
        agent: PrivilegedAgent,
        demo_mode: bool = False,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        self.database = database
        self.state_path = state_path
        self.agent = agent
        self.demo_mode = demo_mode
        self.on_event = on_event
        self._lock = threading.Lock()
        # Time-based blocks computed by the access scheduler. They are merged
        # into the same file so nftables keeps a single source of truth.
        self._policy_blocks: set[str] = set()

    def entries(self) -> list[dict[str, Any]]:
        stored = self.database.setting(BLOCKED_DEVICES_KEY, []) or []
        entries: list[dict[str, Any]] = []
        for item in stored:
            if not isinstance(item, dict):
                continue
            try:
                mac = normalize_mac(str(item.get("mac", "")))
            except ValueError:
                continue
            entries.append(
                {
                    "mac": mac,
                    "label": str(item.get("label", ""))[:64],
                    "blocked_at": int(item.get("blocked_at", 0)),
                }
            )
        return entries

    def blocked_macs(self) -> set[str]:
        """Devices blocked on purpose, without the time-based ones."""
        return {entry["mac"] for entry in self.entries()}

    @property
    def policy_blocks(self) -> set[str]:
        """Devices currently outside their allowed window, or paused."""
        return set(self._policy_blocks)

    def set_policy_blocks(self, macs: set[str]) -> None:
        """Publishes the access scheduler's decision for the current minute.

        Nothing else may write `blocked-macs.txt`: two writers would race and
        the loser's entries would silently stop being enforced.
        """
        cleaned: set[str] = set()
        for mac in macs:
            try:
                cleaned.add(normalize_mac(mac))
            except ValueError:
                continue
        with self._lock:
            if cleaned == self._policy_blocks:
                return
            previous = self._policy_blocks
            self._policy_blocks = cleaned
            try:
                self._apply(self.entries())
            except NetControlError:
                # Keep the old set so the next tick tries the change again
                # instead of believing it is already applied.
                self._policy_blocks = previous
                raise

    def _write_state(self, entries: list[dict[str, Any]]) -> None:
        addresses = sorted({entry["mac"] for entry in entries} | self._policy_blocks)
        body = "".join(f"{mac}\n" for mac in addresses)
        atomic_write_text(
            self.state_path,
            "# Généré par OnionPi. Une adresse MAC par ligne.\n" + body,
            mode=0o640,
        )

    def _apply(self, entries: list[dict[str, Any]]) -> None:
        self.database.set_setting(BLOCKED_DEVICES_KEY, entries)
        if self.demo_mode:
            return
        try:
            self._write_state(entries)
        except OSError as error:
            raise NetControlError(f"Écriture impossible dans {self.state_path}: {error}") from error
        try:
            self.agent.submit("devices")
        except AgentError as error:
            raise NetControlError(str(error)) from error

    def block(self, mac: str, label: str = "") -> list[dict[str, Any]]:
        try:
            address = normalize_mac(mac)
        except ValueError as error:
            raise NetControlError(str(error)) from error
        with self._lock:
            entries = [entry for entry in self.entries() if entry["mac"] != address]
            entries.append({"mac": address, "label": label.strip()[:64], "blocked_at": int(time.time())})
            entries.sort(key=lambda entry: entry["mac"])
            self._apply(entries)
        if self.on_event:
            self.on_event("device", f"Appareil {label or address} bloqué")
        return entries

    def block_many(
        self, requested: list[tuple[str, str]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Blocks a whole batch in one pass, for a configuration import.

        Calling block() in a loop rewrites the file and waits for the privileged
        agent once per device: a list of a few hundred entries would hold a
        worker thread for as long as that agent stays silent, which is minutes
        rather than seconds. Returns the resulting list and the entries that
        were not valid MAC addresses.
        """
        rejected: list[str] = []
        additions: dict[str, dict[str, Any]] = {}
        now = int(time.time())
        for mac, label in requested:
            try:
                address = normalize_mac(mac)
            except ValueError as error:
                rejected.append(str(error))
                continue
            additions[address] = {"mac": address, "label": label.strip()[:64], "blocked_at": now}
        if not additions:
            return self.entries(), rejected
        with self._lock:
            entries = [entry for entry in self.entries() if entry["mac"] not in additions]
            entries.extend(additions.values())
            entries.sort(key=lambda entry: entry["mac"])
            self._apply(entries)
        if self.on_event:
            self.on_event("device", f"{len(additions)} appareil(s) bloqué(s)")
        return entries, rejected

    def unblock(self, mac: str) -> list[dict[str, Any]]:
        try:
            address = normalize_mac(mac)
        except ValueError as error:
            raise NetControlError(str(error)) from error
        with self._lock:
            entries = self.entries()
            remaining = [entry for entry in entries if entry["mac"] != address]
            if len(remaining) == len(entries):
                raise NetControlError("Cet appareil n’est pas bloqué")
            self._apply(remaining)
        if self.on_event:
            self.on_event("device", f"Appareil {address} débloqué")
        return remaining

    def resync(self) -> None:
        """Re-pushes the list after a restart, so nftables cannot drift."""
        entries = self.entries()
        if self.demo_mode or not (entries or self._policy_blocks):
            return
        try:
            self._apply(entries)
        except NetControlError as error:
            logger.warning("Blocages d’appareils non appliqués: %s", error)


class DnsFilter:
    """Domain blocklist served to Wi-Fi clients by dnsmasq.

    The lists are fetched through Tor's SOCKS port: asking GitHub for a
    blocklist over the clear uplink would describe the household to a third
    party, which is exactly what this router exists to avoid.
    """

    def __init__(
        self,
        database: Database,
        hosts_path: Path,
        agent: PrivilegedAgent,
        socks_port: int = 9050,
        demo_mode: bool = False,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        self.database = database
        self.hosts_path = hosts_path
        self.agent = agent
        self.socks_port = socks_port
        self.demo_mode = demo_mode
        self.on_event = on_event
        self._lock = threading.Lock()
        self._refreshing = False

    def state(self) -> dict[str, Any]:
        stored = self.database.setting(DNS_FILTER_KEY, {}) or {}
        profiles = [item for item in stored.get("profiles", []) if item in DNS_PROFILES]
        return {
            "profiles": profiles,
            "custom_blocked": list(stored.get("custom_blocked", []))[:2000],
            "allowed": list(stored.get("allowed", []))[:2000],
            "domain_count": int(stored.get("domain_count", 0)),
            "updated_at": int(stored.get("updated_at", 0)),
            "last_error": str(stored.get("last_error", "")),
        }

    def snapshot(self) -> dict[str, Any]:
        state = self.state()
        return {
            **state,
            "enabled": bool(state["profiles"] or state["custom_blocked"]),
            "refreshing": self._refreshing,
            "available_profiles": [
                {"id": key, **{k: v for k, v in value.items() if k != "url"}}
                for key, value in DNS_PROFILES.items()
            ],
        }

    def _store(self, **changes: Any) -> dict[str, Any]:
        state = self.state()
        state.update(changes)
        self.database.set_setting(DNS_FILTER_KEY, state)
        return state

    def _download(self, url: str) -> list[str]:
        proxy = None if self.demo_mode else f"socks5h://127.0.0.1:{self.socks_port}"
        domains: list[str] = []
        with httpx.Client(proxy=proxy, timeout=60, follow_redirects=True) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                if int(response.headers.get("content-length", 0)) > MAX_LIST_BYTES:
                    raise NetControlError("Liste trop volumineuse")
                size = 0
                for line in response.iter_lines():
                    size += len(line) + 1
                    if size > MAX_LIST_BYTES:
                        raise NetControlError("Liste trop volumineuse")
                    match = HOSTS_LINE.match(line)
                    if not match:
                        continue
                    candidate = match.group(1).lower().rstrip(".")
                    if candidate in {"localhost", "localhost.localdomain", "local"}:
                        continue
                    if DOMAIN_PATTERN.fullmatch(candidate):
                        domains.append(candidate)
        return domains

    def _write_hosts(self, domains: list[str]) -> None:
        # dnsmasq re-reads this file as an unprivileged user on SIGHUP.
        body = "# Généré par OnionPi. Ne pas modifier à la main.\n"
        body += "".join(f"0.0.0.0 {domain}\n" for domain in domains)
        atomic_write_text(self.hosts_path, body, mode=0o644)

    def rebuild(self) -> dict[str, Any]:
        """Downloads every selected profile and regenerates the hosts file."""
        with self._lock:
            self._refreshing = True
        try:
            state = self.state()
            domains: set[str] = set()
            for profile in state["profiles"]:
                domains.update(self._download(DNS_PROFILES[profile]["url"]))
                if len(domains) > MAX_DOMAINS:
                    raise NetControlError(
                        f"Plus de {MAX_DOMAINS} domaines: réduisez le nombre de listes."
                    )
            domains.update(state["custom_blocked"])
            domains.difference_update(state["allowed"])
            ordered = sorted(domains)
            if not self.demo_mode:
                try:
                    self._write_hosts(ordered)
                except OSError as error:
                    raise NetControlError(
                        f"Écriture impossible dans {self.hosts_path}: {error}"
                    ) from error
                try:
                    self.agent.submit("dns")
                except AgentError as error:
                    raise NetControlError(str(error)) from error
            self._store(domain_count=len(ordered), updated_at=int(time.time()), last_error="")
            if self.on_event:
                self.on_event("secure", f"Filtrage DNS actualisé ({len(ordered)} domaines)")
            return self.snapshot()
        except NetControlError:
            raise
        except Exception as error:  # httpx raises a whole family of transport errors
            message = "Listes injoignables. Tor doit être connecté pour les télécharger."
            self._store(last_error=message)
            logger.warning("Téléchargement des listes DNS impossible: %s", error)
            raise NetControlError(message) from error
        finally:
            with self._lock:
                self._refreshing = False

    def update(
        self,
        profiles: list[str],
        custom_blocked: list[str],
        allowed: list[str],
    ) -> dict[str, Any]:
        unknown = [item for item in profiles if item not in DNS_PROFILES]
        if unknown:
            raise NetControlError(f"Liste inconnue: {unknown[0]}")
        try:
            blocked_domains = sorted({normalize_domain(item) for item in custom_blocked if item.strip()})
            allowed_domains = sorted({normalize_domain(item) for item in allowed if item.strip()})
        except ValueError as error:
            raise NetControlError(str(error)) from error
        self._store(
            profiles=sorted(set(profiles)),
            custom_blocked=blocked_domains,
            allowed=allowed_domains,
        )
        return self.rebuild()
