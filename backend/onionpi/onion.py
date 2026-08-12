"""Optional onion service for the administration interface.

Reaching the Pi from outside normally means opening a port on the household
router, which advertises the machine to the whole Internet. An onion service
does the opposite: Tor carries the connection, nothing listens on the WAN, and
the address is only known to whoever holds it.

The service is created through the control port with `ADD_ONION`, so the
private key stays in a file the `onionpi` user owns and no root-owned
HiddenServiceDir is needed. Tor forgets detached services when it restarts, so
`ensure_published` re-adds the same key at every application start.

Client authorisation (v3) narrows this further. Without it the address is the
only secret, and an address travels through browser history, bookmarks and
screenshots. With it, Tor encrypts the service descriptor for a list of x25519
public keys: someone who learns the address but holds no key cannot even
resolve it.
"""

from __future__ import annotations

import base64
import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from .atomic_io import atomic_write_text
from .database import Database
from .tor_control import TorControlError, TorController

logger = logging.getLogger("onionpi.onion")

ONION_KEY = "onion_service"
KEY_PATTERN = re.compile(r"^ED25519-V3:[A-Za-z0-9+/=]{1,200}$")
CLIENT_NAME_PATTERN = re.compile(r"^[\w .-]{1,32}$")
CLIENT_KEY_PATTERN = re.compile(r"^[A-Z2-7]{52}$")

#: One entry per device allowed to reach the address. Kept small on purpose:
#: every key is a copy of the access secret living on someone else's machine.
MAX_CLIENTS = 8


class OnionError(RuntimeError):
    pass


def _base32(raw: bytes) -> str:
    """Tor spells x25519 keys in unpadded upper-case base32."""
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def generate_client_keypair() -> tuple[str, str]:
    """Returns (private, public) base32 x25519 keys in Tor's own spelling."""
    private = X25519PrivateKey.generate()
    raw_private = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _base32(raw_private), _base32(raw_public)


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
        clients: list[dict[str, Any]] = []
        for item in stored.get("clients", []) or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("public_key", ""))
            name = str(item.get("name", ""))[:32]
            if CLIENT_KEY_PATTERN.fullmatch(key) and CLIENT_NAME_PATTERN.fullmatch(name):
                clients.append(
                    {
                        "name": name,
                        "public_key": key,
                        "added_at": int(item.get("added_at", 0) or 0),
                    }
                )
        return {
            "enabled": bool(stored.get("enabled", False)),
            "service_id": str(stored.get("service_id", "")),
            "clients": clients[:MAX_CLIENTS],
        }

    def _save(self, enabled: bool, service_id: str, clients: list[dict[str, Any]]) -> None:
        self.database.set_setting(
            ONION_KEY,
            {"enabled": enabled, "service_id": service_id, "clients": clients},
        )

    def _read_key(self) -> str | None:
        try:
            value = self.key_path.read_text().strip()
        except OSError:
            return None
        return value if KEY_PATTERN.fullmatch(value) else None

    def _write_key(self, value: str) -> None:
        try:
            # 0600 from creation, not written and then narrowed: with the
            # service umask the old order left the private key group-readable
            # for as long as the write took.
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
            "client_auth": bool(stored["clients"]),
            "clients": [
                {"name": client["name"], "added_at": client["added_at"]}
                for client in stored["clients"]
            ],
            "max_clients": MAX_CLIENTS,
        }

    # ------------------------------------------------------------ actions --

    def _publish(self) -> str:
        """(Re)publishes the service with the authorised clients of the moment.

        Tor refuses to add a service ID it already serves, so an existing
        publication is withdrawn first: this is the only way to change the
        client list of a running service through the control port.
        """
        stored = self._stored()
        key = self._read_key()
        if stored["service_id"] and self.controller.onion_published(stored["service_id"]):
            self.controller.remove_onion(stored["service_id"])
        result = self.controller.add_onion(
            key, self.target, [client["public_key"] for client in stored["clients"]]
        )
        if result["private_key"] and result["private_key"] != key:
            self._write_key(result["private_key"])
        self._address = result["service_id"]
        self._save(True, result["service_id"], stored["clients"])
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
                self._save(False, stored["service_id"], stored["clients"])
                if self.on_event:
                    self.on_event("secure", "Service onion retiré")
            return self.snapshot()

    # --------------------------------------------------- client authorisation --

    def _republish_clients(self, clients: list[dict[str, Any]]) -> None:
        """Applies a new client list, restoring the previous one on refusal."""
        stored = self._stored()
        self._save(stored["enabled"], stored["service_id"], clients)
        if not stored["enabled"]:
            return
        try:
            self._publish()
        except (TorControlError, OnionError) as error:
            self._save(stored["enabled"], stored["service_id"], stored["clients"])
            try:
                self._publish()
            except (TorControlError, OnionError):
                logger.exception("Service onion non republié après un échec")
            raise OnionError(
                f"Tor a refusé la nouvelle liste d’accès: {error}"
            ) from error

    def add_client(self, name: str) -> dict[str, Any]:
        """Authorises one more device and returns its key, shown only once."""
        label = name.strip()[:32]
        if not CLIENT_NAME_PATTERN.fullmatch(label):
            raise OnionError("Nom d’accès invalide: lettres, chiffres, espace, - et _.")
        with self._lock:
            stored = self._stored()
            if not stored["enabled"] or not self._address:
                # The key file the operator has to install names the address it
                # unlocks, so there is nothing useful to hand out before then.
                raise OnionError("Publiez d’abord le service onion.")
            if any(client["name"].lower() == label.lower() for client in stored["clients"]):
                raise OnionError("Un accès porte déjà ce nom.")
            if len(stored["clients"]) >= MAX_CLIENTS:
                raise OnionError(f"{MAX_CLIENTS} accès autorisés au maximum.")
            private_key, public_key = generate_client_keypair()
            self._republish_clients(
                [
                    *stored["clients"],
                    {
                        "name": label,
                        "public_key": public_key,
                        "added_at": int(time.time()),
                    },
                ]
            )
            address = self._address
            snapshot = self.snapshot()
        if self.on_event:
            self.on_event("secure", f"Accès onion « {label} » autorisé")
        return {
            "onion": snapshot,
            "name": label,
            # The exact line Tor Browser expects in
            # <profil>/tor/onion-auth/<nom>.auth_private.
            "private_key": f"{address}:descriptor:x25519:{private_key}",
        }

    def remove_client(self, name: str) -> dict[str, Any]:
        with self._lock:
            stored = self._stored()
            remaining = [
                client for client in stored["clients"] if client["name"] != name
            ]
            if len(remaining) == len(stored["clients"]):
                raise OnionError("Cet accès n’existe pas.")
            self._republish_clients(remaining)
            snapshot = self.snapshot()
        if self.on_event:
            self.on_event("secure", f"Accès onion « {name} » révoqué")
        return snapshot

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
