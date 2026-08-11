"""Read-only view of the update client, plus the preferences it accepts.

`onionpi-update` runs as root from a systemd timer. This module never calls it
directly: it reads the state document that client writes, and writes back the
three preferences the interface is allowed to influence. Every one of them is
revalidated by the root script before use, so this file is a convenience layer,
not a security boundary.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("onionpi.updates")

CHANNELS = ("stable", "edge")
_SLOT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_SLOTS = 6


class UpdateError(RuntimeError):
    pass


def normalise_schedule(value: str) -> str:
    """"04:30, 16:00" -> "04:30,16:00", or a refusal."""
    slots = [slot.strip() for slot in str(value).split(",") if slot.strip()]
    if not slots:
        raise UpdateError("Indiquez au moins une heure au format HH:MM.")
    if len(slots) > MAX_SLOTS:
        raise UpdateError(f"Au maximum {MAX_SLOTS} horaires.")
    for slot in slots:
        if not _SLOT.match(slot):
            raise UpdateError(f"Horaire invalide: {slot} (attendu HH:MM sur 24 heures).")
    # Duplicates would create two systemd calendar entries for the same minute.
    unique = sorted(dict.fromkeys(slots))
    return ",".join(unique)


@dataclass(frozen=True)
class UpdatePreferences:
    channel: str
    schedule: str
    enabled: bool
    apply: bool

    def as_document(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "schedule": self.schedule,
            "enabled": self.enabled,
            "apply": self.apply,
        }


DEFAULT_PREFERENCES = UpdatePreferences(
    channel="stable", schedule="04:30", enabled=True, apply=True
)


class UpdateManager:
    def __init__(
        self,
        state_path: Path,
        settings_path: Path,
        version: str,
        demo_mode: bool = False,
    ) -> None:
        self.state_path = state_path
        self.settings_path = settings_path
        self.version = version
        self.demo_mode = demo_mode

    # ------------------------------------------------------------- reading --
    def _state(self) -> dict[str, Any]:
        try:
            document = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}
        return document if isinstance(document, dict) else {}

    def preferences(self) -> UpdatePreferences:
        try:
            document = json.loads(self.settings_path.read_text())
        except (OSError, ValueError):
            return DEFAULT_PREFERENCES
        if not isinstance(document, dict):
            return DEFAULT_PREFERENCES
        channel = str(document.get("channel", DEFAULT_PREFERENCES.channel))
        if channel not in CHANNELS:
            channel = DEFAULT_PREFERENCES.channel
        try:
            schedule = normalise_schedule(str(document.get("schedule", "")))
        except UpdateError:
            schedule = DEFAULT_PREFERENCES.schedule
        return UpdatePreferences(
            channel=channel,
            schedule=schedule,
            enabled=bool(document.get("enabled", True)),
            apply=bool(document.get("apply", True)),
        )

    def state(self) -> dict[str, Any]:
        """Everything the Paramètres page shows about updates."""
        state = self._state()
        preferences = self.preferences()
        installed = str(state.get("installed") or self.version)
        available = str(state.get("available") or "")
        return {
            "supported": self.demo_mode or self.settings_path.parent.is_dir(),
            "installed": installed,
            "available": available,
            "update_pending": bool(state.get("update_pending", False)),
            "running": bool(state.get("running", False)),
            "repository": str(state.get("repository") or ""),
            "over_tor": bool(state.get("over_tor", True)),
            "channel": preferences.channel,
            "schedule": preferences.schedule,
            "enabled": preferences.enabled,
            "auto_apply": preferences.apply,
            "next_run": state.get("next_run"),
            "last_check": state.get("last_check"),
            "last_check_status": str(state.get("last_check_status") or ""),
            "last_check_message": str(state.get("last_check_message") or ""),
            "last_apply": state.get("last_apply"),
            "last_apply_status": str(state.get("last_apply_status") or ""),
            "last_apply_message": str(state.get("last_apply_message") or ""),
            "history": [str(entry) for entry in state.get("history", [])][:10],
        }

    # ------------------------------------------------------------- writing --
    def save_preferences(
        self, channel: str, schedule: str, enabled: bool, apply: bool
    ) -> UpdatePreferences:
        if channel not in CHANNELS:
            raise UpdateError(f"Canal inconnu: {channel}")
        preferences = UpdatePreferences(
            channel=channel,
            schedule=normalise_schedule(schedule),
            enabled=bool(enabled),
            apply=bool(apply),
        )
        # Written even in demonstration mode: the file is the only place the
        # choice survives a page reload, and it is inert without the root agent.
        document = json.dumps(preferences.as_document(), ensure_ascii=False, indent=2)
        try:
            directory = self.settings_path.parent
            handle = tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=directory,
                delete=False,
                prefix=".update.settings.",
            )
            try:
                handle.write(document + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            os.chmod(handle.name, 0o640)
            os.replace(handle.name, self.settings_path)
        except OSError as error:
            raise UpdateError(
                f"Écriture impossible dans {self.settings_path}: {error}"
            ) from error
        return preferences

    def demo_state(self) -> dict[str, Any]:
        """A plausible document so the demonstration mode shows real controls."""
        preferences = self.preferences()
        now = int(time.time())
        return {
            **self.state(),
            "supported": True,
            "repository": "0xmagicduck/onionpi-router",
            "available": self.version,
            "update_pending": False,
            "next_run": now + 3600,
            "last_check": now - 1800,
            "last_check_status": "ok",
            "last_apply_status": "ok",
            "last_apply_message": f"Version {self.version} installée",
            "channel": preferences.channel,
        }
