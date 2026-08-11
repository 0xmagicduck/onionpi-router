"""Read the short, root-owned physical maintenance window."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class MaintenanceWindow:
    def __init__(self, state_path: Path, demo_mode: bool = False) -> None:
        self.state_path = state_path
        self.demo_mode = demo_mode

    def state(self) -> dict[str, Any]:
        if self.demo_mode:
            return {"active": True, "expires_at": int(time.time()) + 900, "source": "demo"}
        try:
            if self.state_path.is_symlink() or not self.state_path.is_file():
                raise OSError("état absent")
            document = json.loads(self.state_path.read_text(encoding="utf-8"))
            expires_at = int(document.get("expires_at", 0))
            source = str(document.get("source", "console"))[:32]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {"active": False, "expires_at": None, "source": ""}
        active = expires_at > int(time.time())
        return {
            "active": active,
            "expires_at": expires_at if active else None,
            "source": source if active else "",
        }

    @property
    def active(self) -> bool:
        return bool(self.state()["active"])
