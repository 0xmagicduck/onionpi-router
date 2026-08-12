"""Per-device traffic totals, accumulated from the nftables counters.

The firewall counts bytes per client in two dynamic sets, and only root may
read them: `onionpi-accounting` samples them from a timer and writes
`traffic.json` in the privileged state directory. This module never runs nft,
it folds those readings into totals stored in the database.

The folding is the point. A set is recreated empty by every rule reload, so a
raw reading is only the traffic since the last load; keeping the previous
reading and adding the difference is what turns it into a figure a household
can read. Downloads are counted per IP address — the destination MAC is not
resolved yet at postrouting — so they are charged through the lease the client
currently holds, and left pending until that lease is known rather than
charged to the wrong device.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .database import Database

ACCOUNTING_KEY = "traffic_accounting"

#: A household router sees a few dozen clients. The cap keeps the stored
#: document bounded even where hundreds of them come and go.
MAX_TRACKED_DEVICES = 256


def _integer(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _integers(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _integer(item) for key, item in value.items()}


def _counters(section: Any) -> tuple[int, dict[str, int]]:
    """`(set handle, {element: bytes})` out of one `nft -j list set` document.

    The handle changes when the table is recreated, which is how a reload is
    told apart from a set that merely happens to hold smaller counters.
    """
    if not isinstance(section, dict):
        return 0, {}
    handle = 0
    counters: dict[str, int] = {}
    for item in section.get("nftables", []):
        if not isinstance(item, dict):
            continue
        block = item.get("set")
        if not isinstance(block, dict):
            continue
        handle = _integer(block.get("handle"))
        elements = block.get("elem")
        if not isinstance(elements, list):
            continue
        for element in elements:
            entry = element.get("elem") if isinstance(element, dict) else None
            if not isinstance(entry, dict):
                continue
            key = entry.get("val")
            counter = entry.get("counter")
            if not isinstance(key, str) or not isinstance(counter, dict):
                continue
            counters[key.strip().lower()] = _integer(counter.get("bytes"))
    return handle, counters


class TrafficAccountant:
    """Turns the firewall's byte counters into per-device totals."""

    def __init__(
        self,
        database: Database,
        state_path: Path,
        demo_mode: bool = False,
    ) -> None:
        self.database = database
        self.state_path = state_path
        self.demo_mode = demo_mode
        self._lock = threading.Lock()

    # ------------------------------------------------------------- reading --
    def _sample(self) -> dict[str, Any]:
        """The document the privileged sampler last wrote, or nothing."""
        try:
            document = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}
        return document if isinstance(document, dict) else {}

    def _document(self) -> dict[str, Any]:
        stored = self.database.setting(ACCOUNTING_KEY, {}) or {}
        if not isinstance(stored, dict):
            stored = {}
        totals: dict[str, dict[str, int]] = {}
        for mac, entry in (stored.get("totals") or {}).items():
            if not isinstance(entry, dict):
                continue
            values = _integers(entry)
            totals[str(mac)] = {
                "download": values.get("download", 0),
                "upload": values.get("upload", 0),
                "seen_at": values.get("seen_at", 0),
            }
        addresses = stored.get("addresses")
        return {
            "totals": totals,
            "upload_raw": _integers(stored.get("upload_raw")),
            "download_raw": _integers(stored.get("download_raw")),
            "addresses": {
                str(ip): str(mac)
                for ip, mac in (addresses if isinstance(addresses, dict) else {}).items()
                if isinstance(ip, str) and isinstance(mac, str)
            },
            "upload_handle": _integer(stored.get("upload_handle")),
            "download_handle": _integer(stored.get("download_handle")),
            "since": _integer(stored.get("since")),
        }

    def totals(self) -> dict[str, dict[str, int]]:
        """Stored totals, without reading the counters again."""
        return self._document()["totals"]

    def snapshot(self) -> dict[str, Any]:
        """What the interface says about the measurement itself."""
        document = self._document()
        return {
            "supported": self.demo_mode or self.state_path.exists(),
            "since": document["since"],
            "updated_at": _integer(self._sample().get("updated_at")),
            "devices": len(document["totals"]),
        }

    # ------------------------------------------------------------- folding --
    def update(self, devices: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
        """Adds whatever the counters gained since the last call.

        `devices` supplies the current leases: a download counter keyed on an
        IP address can be charged to a device no other way.
        """
        if self.demo_mode:
            return {}
        with self._lock:
            document = self._document()
            leases = {
                str(device.get("ip", "")).strip(): str(device.get("mac", "")).strip().lower()
                for device in devices
                if str(device.get("ip", "")).strip() and str(device.get("mac", "")).strip()
            }
            addresses = {**document["addresses"], **leases}

            sample = self._sample()
            upload_handle, uploads = _counters(sample.get("upload"))
            download_handle, downloads = _counters(sample.get("download"))
            if not (uploads or downloads or upload_handle or download_handle):
                # No sample yet, or an installation without the timer: show what
                # is already stored rather than blanking every figure.
                return document["totals"]

            totals = document["totals"]
            now = int(time.time())
            upload_raw = self._fold(
                totals,
                "upload",
                uploads,
                document["upload_raw"],
                reset=upload_handle != document["upload_handle"],
                resolve=lambda key: key,
                now=now,
            )
            download_raw = self._fold(
                totals,
                "download",
                downloads,
                document["download_raw"],
                reset=download_handle != document["download_handle"],
                resolve=addresses.get,
                now=now,
            )
            totals = self._prune(totals)
            self.database.set_setting(
                ACCOUNTING_KEY,
                {
                    "totals": totals,
                    "upload_raw": upload_raw,
                    "download_raw": download_raw,
                    "addresses": self._prune_addresses(addresses, leases, totals),
                    "upload_handle": upload_handle,
                    "download_handle": download_handle,
                    "since": document["since"] or now,
                },
            )
            return totals

    def _fold(
        self,
        totals: dict[str, dict[str, int]],
        direction: str,
        current: dict[str, int],
        previous: dict[str, int],
        reset: bool,
        resolve: Callable[[str], str | None],
        now: int,
    ) -> dict[str, int]:
        """Charges the growth of every counter to the device behind it.

        Returns the new baseline. A counter whose device is unknown keeps its
        old baseline rather than gaining a new one: its bytes are not dropped,
        they are charged as soon as the lease that explains them shows up.
        """
        baseline = dict(previous)
        for key, value in current.items():
            mac = resolve(key)
            if not mac:
                continue
            before = 0 if reset else previous.get(key, 0)
            # A counter that went backwards belongs to a fresh element, so all
            # of it is new traffic.
            gained = value if value < before else value - before
            entry = totals.setdefault(mac, {"download": 0, "upload": 0, "seen_at": 0})
            entry[direction] += gained
            entry["seen_at"] = now
            baseline[key] = value
        # An element the kernel forgot cannot come back with a larger counter,
        # so keeping its baseline would only make the next reading disappear.
        return {key: value for key, value in baseline.items() if key in current}

    def _prune(self, totals: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        if len(totals) <= MAX_TRACKED_DEVICES:
            return totals
        ordered = sorted(totals.items(), key=lambda item: item[1]["seen_at"], reverse=True)
        return dict(ordered[:MAX_TRACKED_DEVICES])

    def _prune_addresses(
        self,
        addresses: dict[str, str],
        leases: dict[str, str],
        totals: dict[str, dict[str, int]],
    ) -> dict[str, str]:
        """Keeps the leases in use and those still explaining a total."""
        kept = {ip: mac for ip, mac in addresses.items() if ip in leases or mac in totals}
        return dict(list(kept.items())[:MAX_TRACKED_DEVICES])

    def reset(self) -> dict[str, Any]:
        """Forgets every total and restarts from the counters as they stand."""
        with self._lock:
            sample = self._sample()
            upload_handle, uploads = _counters(sample.get("upload"))
            download_handle, downloads = _counters(sample.get("download"))
            self.database.set_setting(
                ACCOUNTING_KEY,
                {
                    "totals": {},
                    "upload_raw": uploads,
                    "download_raw": downloads,
                    "addresses": {},
                    "upload_handle": upload_handle,
                    "download_handle": download_handle,
                    "since": int(time.time()),
                },
            )
        return self.snapshot()
