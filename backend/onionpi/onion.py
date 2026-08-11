"""Optional onion service for the administration interface.

Reaching the Pi from outside normally means opening a port on the household
router, which advertises the machine to the whole Internet. An onion service
does the opposite: Tor carries the connection, nothing listens on the WAN, and
the address is only known to whoever holds it.

The service is created through the control port with `ADD_ONION`, so the
private key stays in a file the `onionpi` user owns and no root-owned
HiddenServiceDir is needed. Tor forgets detached services when it restarts, so
`ensure_published` re-adds the same key at every application start.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_text
from .database import Database
from .tor_control import TorControlError, TorController

logger = logging.getLogger("onionpi.onion")

ONION_KEY = "onion_service"
KEY_PATTERN = re.compile(r"^ED25519-V3:[A-Za-z0-9+/=]{1,200}$")


class OnionError(RuntimeError):
    pass


class OnionService:
    def __init__(
        self,
        database: Database,
        key_path: Path,
        controller: TorController,
        target: str,
        demo_mode: bool = False,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        self.database = database
        self.key_path = key_path
        self.controller = controller
        self.target = target
        self.demo_mode = demo_mode
        self.on_event = on_event
        self._lock = threading.Lock()
        self._address = ""

    # ------------------------------------------------------------- state ---

    @property
    def address(self) -> str:
        """Current hostname, or an empty string when the service is off."""
        return self._address

    def _stored(self) -> dict[str, Any]:
        stored = self.database.setting(ONION_KEY, {}) or {}
        return {
            "enabled": bool(stored.get("enabled", False)),
            "service_id": str(stored.get("service_id", "")),
        }

    def _read_key(self) -> str | None:
        try:
            value = self.key_path.read_text().strip()
        except OSError:
            return None
        return value if KEY_PATTERN.fullmatch(value) else None

    def _write_key(self, value: str) -> None:
        try:
            atomic_write_text(self.key_path, value + "\n", mode=0o600)
        except OSError as error:
            raise OnionError(f"Écriture impossible dans {self.key_path}: {error}") from error

    def snapshot(self) -> dict[str, Any]:
        stored = self._stored()
        published = bool(self._address) and (
            self.demo_mode or self.controller.onion_published(stored["service_id"])
        )
        return {
            "enabled": stored["enabled"],
            "published": published,
            "address": f"{self._address}.onion" if self._address else "",
            "has_key": self.demo_mode or self._read_key() is not None,
            "target": self.target,
        }

    # ------------------------------------------------------------ actions --

    def _publish(self) -> str:
        key = self._read_key()
        result = self.controller.add_onion(key, self.target)
        if result["private_key"] and result["private_key"] != key:
            self._write_key(result["private_key"])
        self._address = result["service_id"]
        self.database.set_setting(
            ONION_KEY, {"enabled": True, "service_id": result["service_id"]}
        )
        return self._address

    def ensure_published(self) -> None:
        """Re-publishes at startup, and after Tor has been restarted."""
        stored = self._stored()
        if not stored["enabled"]:
            return
        with self._lock:
            if stored["service_id"] and self.controller.onion_published(stored["service_id"]):
                self._address = stored["service_id"]
                return
            try:
                self._publish()
            except (TorControlError, OnionError) as error:
                logger.warning("Service onion non republié: %s", error)

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            if enabled:
                try:
                    address = self._publish()
                except TorControlError as error:
                    raise OnionError(str(error)) from error
                if self.on_event:
                    self.on_event("secure", "Service onion publié")
                logger.info("Service onion publié: %s.onion", address)
            else:
                stored = self._stored()
                if stored["service_id"]:
                    try:
                        self.controller.remove_onion(stored["service_id"])
                    except TorControlError as error:
                        logger.warning("DEL_ONION refusé: %s", error)
                self._address = ""
                self.database.set_setting(
                    ONION_KEY, {"enabled": False, "service_id": stored["service_id"]}
                )
                if self.on_event:
                    self.on_event("secure", "Service onion retiré")
            return self.snapshot()

    def rotate_address(self) -> dict[str, Any]:
        """Throws the key away and publishes a brand new address."""
        with self._lock:
            stored = self._stored()
            if stored["service_id"]:
                try:
                    self.controller.remove_onion(stored["service_id"])
                except TorControlError:
                    pass
            try:
                self.key_path.unlink(missing_ok=True)
            except OSError as error:
                raise OnionError(f"Suppression de la clé impossible: {error}") from error
            self._address = ""
            try:
                self._publish()
            except TorControlError as error:
                raise OnionError(str(error)) from error
            if self.on_event:
                self.on_event("secure", "Nouvelle adresse onion générée")
            return self.snapshot()
