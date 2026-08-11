"""Exit-node policy and scheduled identity rotation.

Pinning an exit country is a comfort feature — it makes streaming and banking
sites stop asking for a new verification every hour — and a privacy cost: the
smaller the country, the smaller the anonymity set. The interface says so, and
the setting is off by default.

Like the bridge configuration, the policy is a torrc fragment the `onionpi`
user owns; Tor re-reads it on SIGNAL RELOAD.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_text
from .database import Database
from .tor_control import TorControlError, TorController

logger = logging.getLogger("onionpi.policy")

POLICY_KEY = "tor_policy"
HEADER = (
    "# Généré par OnionPi. Ne pas modifier à la main:\n"
    "# le fichier est réécrit à chaque changement depuis l’interface web.\n"
)

#: Countries with enough exit capacity that pinning one still leaves a usable
#: anonymity set. Sorted by French name in the interface.
EXIT_COUNTRIES: dict[str, str] = {
    "AT": "Autriche",
    "BE": "Belgique",
    "BG": "Bulgarie",
    "CA": "Canada",
    "CH": "Suisse",
    "CZ": "Tchéquie",
    "DE": "Allemagne",
    "DK": "Danemark",
    "ES": "Espagne",
    "FI": "Finlande",
    "FR": "France",
    "GB": "Royaume-Uni",
    "IS": "Islande",
    "IT": "Italie",
    "LU": "Luxembourg",
    "LV": "Lettonie",
    "MD": "Moldavie",
    "NL": "Pays-Bas",
    "NO": "Norvège",
    "PL": "Pologne",
    "RO": "Roumanie",
    "SE": "Suède",
    "SK": "Slovaquie",
    "UA": "Ukraine",
    "US": "États-Unis",
}

#: Rotation intervals offered by the interface, in seconds. 0 disables it.
ROTATION_CHOICES = (0, 900, 3600, 21600, 86400)


class PolicyError(RuntimeError):
    pass


class TorPolicy:
    def __init__(
        self,
        database: Database,
        policy_path: Path,
        controller: TorController,
        demo_mode: bool = False,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        self.database = database
        self.policy_path = policy_path
        self.controller = controller
        self.demo_mode = demo_mode
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_rotation = 0

    # ----------------------------------------------------------- settings --

    def state(self) -> dict[str, Any]:
        stored = self.database.setting(POLICY_KEY, {}) or {}
        country = str(stored.get("exit_country", "")).upper()
        interval = int(stored.get("rotation_seconds", 0))
        return {
            "exit_country": country if country in EXIT_COUNTRIES else "",
            "rotation_seconds": interval if interval in ROTATION_CHOICES else 0,
        }

    def snapshot(self) -> dict[str, Any]:
        state = self.state()
        return {
            **state,
            "last_rotation": self._last_rotation,
            "next_rotation": (
                self._last_rotation + state["rotation_seconds"]
                if state["rotation_seconds"] and self._last_rotation
                else 0
            ),
            "countries": [{"code": code, "name": name} for code, name in EXIT_COUNTRIES.items()],
            "rotation_choices": list(ROTATION_CHOICES),
        }

    # ------------------------------------------------------------- torrc ---

    def _render(self, country: str) -> str:
        if not country:
            return HEADER + "# Aucun pays de sortie imposé.\n"
        # StrictNodes keeps Tor from silently falling back to another country
        # when the chosen one has no usable exit at that moment.
        return HEADER + f"ExitNodes {{{country.lower()}}}\nStrictNodes 1\n"

    def _write(self, country: str) -> None:
        if self.demo_mode:
            return
        try:
            try:
                previous = self.policy_path.read_text()
            except FileNotFoundError:
                # torrc includes this fragment unconditionally. Even a failed
                # first change must leave behind a parseable previous state.
                previous = self._render(self.state()["exit_country"])
            atomic_write_text(self.policy_path, self._render(country), mode=0o640)
        except OSError as error:
            raise PolicyError(f"Écriture impossible dans {self.policy_path}: {error}") from error
        try:
            self.controller.reload_config()
        except TorControlError as error:
            rollback_error = ""
            try:
                atomic_write_text(self.policy_path, previous, mode=0o640)
                self.controller.reload_config()
            except (OSError, TorControlError) as restore_error:
                rollback_error = f"; restauration non confirmée: {restore_error}"
                logger.exception("Restauration de la politique Tor non confirmée")
            raise PolicyError(
                f"Tor a refusé de recharger sa configuration: {error}{rollback_error}"
            ) from error

    def ensure_file(self) -> None:
        """torrc includes the fragment unconditionally: it must always exist."""
        if self.demo_mode or self.policy_path.exists():
            return
        try:
            atomic_write_text(
                self.policy_path,
                self._render(self.state()["exit_country"]),
                mode=0o640,
            )
        except OSError as error:
            logger.warning("Fragment de politique Tor non créé: %s", error)

    def update(self, exit_country: str, rotation_seconds: int) -> dict[str, Any]:
        country = exit_country.strip().upper()
        if country and country not in EXIT_COUNTRIES:
            raise PolicyError(f"Pays de sortie non pris en charge: {exit_country}")
        if rotation_seconds not in ROTATION_CHOICES:
            raise PolicyError("Intervalle de rotation non pris en charge")
        previous = self.state()
        if country != previous["exit_country"]:
            self._write(country)
        self.database.set_setting(
            POLICY_KEY, {"exit_country": country, "rotation_seconds": rotation_seconds}
        )
        if self.on_event and country != previous["exit_country"]:
            self.on_event(
                "identity",
                f"Sortie Tor forcée en {EXIT_COUNTRIES[country]}" if country
                else "Pays de sortie Tor libre",
            )
        if self.on_event and rotation_seconds != previous["rotation_seconds"]:
            self.on_event(
                "identity",
                f"Rotation d’identité toutes les {rotation_seconds // 60} min"
                if rotation_seconds
                else "Rotation d’identité désactivée",
            )
        return self.snapshot()

    # ---------------------------------------------------------- rotation ---

    def start(self) -> None:
        self.ensure_file()
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="onionpi-rotation", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            interval = self.state()["rotation_seconds"]
            if interval:
                now = int(time.time())
                if self._last_rotation == 0:
                    self._last_rotation = now
                elif now - self._last_rotation >= interval:
                    self.rotate_now(automatic=True)
            self._stop.wait(30)

    def rotate_now(self, automatic: bool = False) -> None:
        try:
            self.controller.new_identity()
        except TorControlError as error:
            logger.warning("Rotation automatique impossible: %s", error)
            return
        self._last_rotation = int(time.time())
        if automatic and self.on_event:
            self.on_event("identity", "Rotation automatique d’identité Tor")
