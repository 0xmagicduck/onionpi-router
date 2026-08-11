"""Optional Snowflake proxy hosting.

Running a Snowflake proxy is the mirror image of using one: the Pi lends its
uplink so that people in censored countries can reach Tor. It is not a Tor
relay, carries no exit traffic, and nothing of what passes through it is
attributable to this machine's Tor client.

The web application never touches systemd directly. It writes a one-word state
file; a systemd path unit picks the change up and a root oneshot service does
the start/stop. That keeps `NoNewPrivileges=true` on the app service intact.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_text
from .system import _run

logger = logging.getLogger("onionpi.relay")

SERVICE = "snowflake-proxy.service"
BINARY = Path("/usr/bin/snowflake-proxy")
STATE_VALUES = {"enabled", "disabled"}

# snowflake-proxy logs one line per period, e.g.
# "In the last 1h0m0s, there were 12 connections. Traffic Relayed ↓ 48 MB, ↑ 9 MB."
SUMMARY_LINE = re.compile(
    r"there were (?P<connections>\d+) connections?\."
    r"(?:.*?↓\s*(?P<down>\d+)\s*(?P<down_unit>[KMGT]?B))?"
    r"(?:.*?↑\s*(?P<up>\d+)\s*(?P<up_unit>[KMGT]?B))?"
)
UNITS = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}


class RelayError(RuntimeError):
    pass


class SnowflakeRelay:
    def __init__(self, state_path: Path, demo_mode: bool = False) -> None:
        self.state_path = state_path
        self.demo_mode = demo_mode
        self._demo_enabled = True
        self._lock = threading.Lock()

    @property
    def installed(self) -> bool:
        return self.demo_mode or BINARY.exists()

    def desired_state(self) -> bool:
        try:
            return self.state_path.read_text().strip() == "enabled"
        except OSError:
            return False

    def _active(self) -> bool:
        return _run(["systemctl", "is-active", SERVICE]) == "active"

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if not self.installed:
            raise RelayError(
                "snowflake-proxy n’est pas installé. Ajoutez le paquet Debian "
                "snowflake-proxy puis relancez l’installateur OnionPi."
            )
        if self.demo_mode:
            self._demo_enabled = enabled
            return self.status()
        with self._lock:
            try:
                atomic_write_text(
                    self.state_path,
                    "enabled\n" if enabled else "disabled\n",
                    mode=0o640,
                )
            except OSError as error:
                raise RelayError(f"Écriture impossible dans {self.state_path}: {error}") from error
            # The path unit reacts within a moment; poll so the interface reports
            # the real systemd state instead of an optimistic guess.
            for _ in range(12):
                if self._active() == enabled:
                    break
                time.sleep(0.5)
            else:
                logger.warning("snowflake-proxy n’a pas atteint l’état demandé (%s)", enabled)
        return self.status()

    def statistics(self, hours: int = 24) -> dict[str, Any]:
        if self.demo_mode:
            return {"connections": 46, "downloaded": 402_000_000, "uploaded": 88_000_000, "periods": 24}
        output = _run(
            [
                "journalctl",
                "-u",
                SERVICE,
                "--no-pager",
                "--output=cat",
                "--since",
                f"-{hours}h",
            ],
            timeout=5,
        )
        connections = 0
        downloaded = 0
        uploaded = 0
        periods = 0
        for line in output.splitlines():
            match = SUMMARY_LINE.search(line)
            if not match:
                continue
            periods += 1
            connections += int(match.group("connections"))
            if match.group("down"):
                downloaded += int(match.group("down")) * UNITS.get(match.group("down_unit"), 1)
            if match.group("up"):
                uploaded += int(match.group("up")) * UNITS.get(match.group("up_unit"), 1)
        return {
            "connections": connections,
            "downloaded": downloaded,
            "uploaded": uploaded,
            "periods": periods,
        }

    def status(self) -> dict[str, Any]:
        if self.demo_mode:
            return {
                "installed": True,
                "enabled": self._demo_enabled,
                "active": self._demo_enabled,
                "controllable": True,
                "statistics": self.statistics(),
            }
        installed = self.installed
        return {
            "installed": installed,
            "enabled": self.desired_state(),
            "active": installed and self._active(),
            # Without the path unit the state file is only a preference.
            "controllable": installed and self.state_path.parent.is_dir(),
            "statistics": self.statistics() if installed else None,
        }
