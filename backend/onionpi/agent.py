"""Broker for the few actions that need root.

The web application runs with `NoNewPrivileges=true` and cannot call systemctl,
nft or reboot. It writes one short request line into a file it owns; a systemd
path unit notices the change and a root oneshot service performs the action
after re-validating it against its own allow-list.

The request file is therefore untrusted input on the privileged side: nothing
here is a security boundary, `onionpi-agent-apply` is.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_text

logger = logging.getLogger("onionpi.agent")

#: Verb -> (label, waits for a result?). Powering the machine down never
#: reports back: the reply would have to survive the shutdown it triggered.
ACTIONS: dict[str, tuple[str, bool]] = {
    "devices": ("Application des blocages d’appareils", True),
    "dns": ("Rechargement du filtrage DNS", True),
    "restart-tor": ("Redémarrage de Tor", True),
    "restart-dnsmasq": ("Redémarrage du service DNS", True),
    "restart-network": ("Redémarrage du Wi-Fi", True),
    "restart-firewall": ("Rechargement du pare-feu", True),
    "self-test-network": ("Test du coupe-circuit", True),
    "update-check": ("Recherche d’une mise à jour", True),
    "update": ("Installation de la mise à jour", True),
    "update-schedule": ("Application de l’horaire de mise à jour", True),
    "reboot": ("Redémarrage de la Raspberry Pi", False),
    "poweroff": ("Extinction de la Raspberry Pi", False),
}


class AgentError(RuntimeError):
    pass


class PrivilegedAgent:
    def __init__(self, request_path: Path, result_path: Path, demo_mode: bool = False) -> None:
        self.request_path = request_path
        self.result_path = result_path
        self.demo_mode = demo_mode
        # The transport is one request file, not a queue. Serialising callers
        # prevents two simultaneous buttons from overwriting each other.
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """True when both halves of the request channel are usable."""
        return self.demo_mode or (
            self.request_path.is_file()
            and self.result_path.is_file()
            and os.access(self.request_path, os.W_OK)
            and os.access(self.result_path, os.R_OK)
        )

    def _result_for(self, nonce: str) -> tuple[str, str] | None:
        try:
            parts = self.result_path.read_text().strip().split(" ", 2)
        except OSError:
            return None
        if len(parts) < 2 or parts[0] != nonce:
            return None
        return parts[1], parts[2] if len(parts) > 2 else ""

    def submit(self, action: str, timeout: float = 12.0) -> dict[str, Any]:
        if action not in ACTIONS:
            raise AgentError(f"Action inconnue: {action}")
        label, expects_result = ACTIONS[action]
        if self.demo_mode:
            return {"action": action, "status": "ok", "message": f"{label} (démonstration)"}
        with self._lock:
            nonce = secrets.token_hex(8)
            try:
                atomic_write_text(
                    self.request_path,
                    f"{nonce} {action}\n",
                    mode=0o640,
                )
            except OSError as error:
                raise AgentError(
                    f"Écriture impossible dans {self.request_path}: {error}"
                ) from error
            if not expects_result:
                return {"action": action, "status": "pending", "message": label}
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                result = self._result_for(nonce)
                if result:
                    status, message = result
                    if status != "ok":
                        raise AgentError(message or f"{label}: échec")
                    return {"action": action, "status": "ok", "message": message or label}
                time.sleep(0.25)
            logger.warning("Aucune réponse de onionpi-agent pour %s", action)
            raise AgentError(
                f"{label}: aucune réponse du service privilégié. "
                "Vérifiez « systemctl status onionpi-agent.path »."
            )
