"""Per-device access policy: aliases, temporary pauses and daily windows.

Blocking a device permanently already exists in `DeviceGuard`. What a household
actually asks for is narrower and time-bound: "cut the tablet for an hour",
"no Internet for the children's console after 21:00". Both are expressed here
as intent stored in the database, never as a rule the web application pushes to
nftables itself.

A background thread recomputes which devices are currently outside their window
and hands the resulting set to `DeviceGuard`, which remains the only writer of
`blocked-macs.txt` and the only caller of the privileged agent. No new root verb
is needed: the privilege boundary is untouched.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from .database import Database
from .netcontrol import DeviceGuard, NetControlError, normalize_mac

logger = logging.getLogger("onionpi.access")

ACCESS_KEY = "device_access"

TIME_PATTERN = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")

#: Monday first, like `time.struct_time.tm_wday`.
WEEKDAYS: tuple[str, ...] = (
    "Lundi",
    "Mardi",
    "Mercredi",
    "Jeudi",
    "Vendredi",
    "Samedi",
    "Dimanche",
)

#: Pause durations offered by the interface, in minutes.
PAUSE_CHOICES = (15, 30, 60, 120, 480)

MAX_RULES = 128
MAX_PAUSE_MINUTES = 1440
TICK_SECONDS = 20


class AccessError(RuntimeError):
    pass


def _minutes(value: str) -> int:
    hours, _, minutes = value.partition(":")
    return int(hours) * 60 + int(minutes)


def _clean_schedule(raw: Any) -> dict[str, Any] | None:
    """Validates one daily window, or returns None when there is none."""
    if not isinstance(raw, dict) or not raw.get("enabled"):
        return None
    start = str(raw.get("start", ""))
    end = str(raw.get("end", ""))
    if not TIME_PATTERN.fullmatch(start) or not TIME_PATTERN.fullmatch(end):
        raise AccessError("Heure invalide: utilisez le format HH:MM.")
    if start == end:
        raise AccessError("La plage autorisée doit durer plus d’une minute.")
    days = sorted({int(day) for day in raw.get("days", []) if isinstance(day, int)})
    if not days or any(day < 0 or day > 6 for day in days):
        raise AccessError("Choisissez au moins un jour de la semaine.")
    return {"enabled": True, "days": days, "start": start, "end": end}


def _schedule_allows(schedule: dict[str, Any], moment: time.struct_time) -> bool:
    """True when `moment` falls inside the window, midnight crossing included."""
    start = _minutes(str(schedule["start"]))
    end = _minutes(str(schedule["end"]))
    minute_of_day = moment.tm_hour * 60 + moment.tm_min
    if start < end:
        return moment.tm_wday in schedule["days"] and start <= minute_of_day < end
    # 21:00 → 07:00 belongs to the day it started on, so an early morning
    # minute is checked against the previous day's selection.
    if minute_of_day >= start:
        return moment.tm_wday in schedule["days"]
    if minute_of_day < end:
        return (moment.tm_wday - 1) % 7 in schedule["days"]
    return False


class DeviceAccessManager:
    """Owns the per-device rules and keeps the effective block list in sync."""

    def __init__(
        self,
        database: Database,
        guard: DeviceGuard,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        self.database = database
        self.guard = guard
        self.on_event = on_event
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------- state ---

    def rules(self) -> list[dict[str, Any]]:
        stored = self.database.setting(ACCESS_KEY, {}) or {}
        if not isinstance(stored, dict):
            return []
        rules: list[dict[str, Any]] = []
        for mac, item in stored.items():
            if not isinstance(item, dict):
                continue
            try:
                address = normalize_mac(str(mac))
            except ValueError:
                continue
            try:
                schedule = _clean_schedule(item.get("schedule"))
            except AccessError:
                schedule = None
            rules.append(
                {
                    "mac": address,
                    "alias": str(item.get("alias", ""))[:64],
                    "paused_until": max(0, int(item.get("paused_until", 0) or 0)),
                    "schedule": schedule,
                }
            )
        rules.sort(key=lambda rule: rule["mac"])
        return rules

    @staticmethod
    def _state_of(rule: dict[str, Any], now: int, moment: time.struct_time) -> str:
        if rule["paused_until"] > now:
            return "paused"
        schedule = rule["schedule"]
        if schedule and not _schedule_allows(schedule, moment):
            return "outside"
        return "allowed"

    def effective_blocks(self, now: int | None = None) -> set[str]:
        """MAC addresses the policy wants blocked at this very moment."""
        moment_epoch = int(time.time()) if now is None else now
        moment = time.localtime(moment_epoch)
        return {
            rule["mac"]
            for rule in self.rules()
            if self._state_of(rule, moment_epoch, moment) != "allowed"
        }

    def snapshot(self) -> dict[str, Any]:
        now = int(time.time())
        moment = time.localtime(now)
        blocked = self.guard.blocked_macs()
        entries = []
        for rule in self.rules():
            state = self._state_of(rule, now, moment)
            entries.append(
                {
                    **rule,
                    # A manual block outranks any schedule: say so rather than
                    # showing a device as "allowed" while nftables drops it.
                    "state": "blocked" if rule["mac"] in blocked else state,
                    "manually_blocked": rule["mac"] in blocked,
                }
            )
        return {
            "rules": entries,
            "weekdays": list(WEEKDAYS),
            "pause_choices": list(PAUSE_CHOICES),
            "now": now,
        }

    def rule_for(self, mac: str) -> dict[str, Any]:
        """The stored rule of one device, or a blank one."""
        try:
            address = normalize_mac(mac)
        except ValueError:
            address = ""
        blank: dict[str, Any] = {
            "mac": address,
            "alias": "",
            "paused_until": 0,
            "schedule": None,
        }
        return next(
            (rule for rule in self.rules() if rule["mac"] == address), blank
        )

    def alias_of(self, mac: str) -> str:
        return str(self.rule_for(mac)["alias"])

    # ----------------------------------------------------------- mutation ---

    def _store(self, rules: list[dict[str, Any]]) -> None:
        self.database.set_setting(
            ACCESS_KEY,
            {
                rule["mac"]: {
                    "alias": rule["alias"],
                    "paused_until": rule["paused_until"],
                    "schedule": rule["schedule"],
                }
                for rule in rules
            },
        )

    def _write_rule(
        self, mac: str, alias: str, paused_until: int, schedule: dict[str, Any] | None
    ) -> None:
        """Stores one rule, or drops it once it carries no intent at all."""
        rules = [rule for rule in self.rules() if rule["mac"] != mac]
        if alias or paused_until or schedule:
            if len(rules) >= MAX_RULES:
                raise AccessError(
                    f"{MAX_RULES} appareils au maximum ont une règle d’accès."
                )
            rules.append(
                {
                    "mac": mac,
                    "alias": alias,
                    "paused_until": paused_until,
                    "schedule": schedule,
                }
            )
            rules.sort(key=lambda rule: rule["mac"])
        self._store(rules)

    def update(
        self,
        mac: str,
        alias: str = "",
        paused_minutes: int | None = None,
        schedule: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Creates or replaces the whole rule of one device, then applies it."""
        try:
            address = normalize_mac(mac)
        except ValueError as error:
            raise AccessError(str(error)) from error
        if paused_minutes is not None and not 0 <= paused_minutes <= MAX_PAUSE_MINUTES:
            raise AccessError(f"La pause va de 0 à {MAX_PAUSE_MINUTES} minutes.")
        cleaned = _clean_schedule(schedule)
        with self._lock:
            paused_until = self.rule_for(address)["paused_until"]
            if paused_minutes is not None:
                paused_until = (
                    int(time.time()) + paused_minutes * 60 if paused_minutes else 0
                )
            self._write_rule(address, alias.strip()[:64], paused_until, cleaned)
        self.apply()
        return self.snapshot()

    def pause(self, mac: str, minutes: int) -> dict[str, Any]:
        """Suspends or restores one device without touching its other settings."""
        try:
            address = normalize_mac(mac)
        except ValueError as error:
            raise AccessError(str(error)) from error
        if not 0 <= minutes <= MAX_PAUSE_MINUTES:
            raise AccessError(f"La pause va de 0 à {MAX_PAUSE_MINUTES} minutes.")
        with self._lock:
            current = self.rule_for(address)
            self._write_rule(
                address,
                str(current["alias"]),
                int(time.time()) + minutes * 60 if minutes else 0,
                current["schedule"],
            )
        self.apply()
        if self.on_event:
            label = current["alias"] or address
            self.on_event(
                "device",
                f"Accès de {label} suspendu {minutes} min"
                if minutes
                else f"Accès de {label} rétabli",
            )
        return self.snapshot()

    def remove(self, mac: str) -> dict[str, Any]:
        try:
            address = normalize_mac(mac)
        except ValueError as error:
            raise AccessError(str(error)) from error
        with self._lock:
            rules = self.rules()
            remaining = [rule for rule in rules if rule["mac"] != address]
            if len(remaining) == len(rules):
                raise AccessError("Cet appareil n’a pas de règle d’accès.")
            self._store(remaining)
        self.apply()
        return self.snapshot()

    def restore(self, rules: list[dict[str, Any]]) -> int:
        """Imports a whole set of rules from a configuration or a backup."""
        cleaned: list[dict[str, Any]] = []
        for item in rules[:MAX_RULES]:
            if not isinstance(item, dict):
                continue
            try:
                address = normalize_mac(str(item.get("mac", "")))
                schedule = _clean_schedule(item.get("schedule"))
            except (ValueError, AccessError):
                continue
            cleaned.append(
                {
                    "mac": address,
                    "alias": str(item.get("alias", ""))[:64],
                    # A pause is a few minutes of intent, not a setting worth
                    # restoring hours later from a backup.
                    "paused_until": 0,
                    "schedule": schedule,
                }
            )
        with self._lock:
            self._store(sorted(cleaned, key=lambda rule: rule["mac"]))
        self.apply()
        return len(cleaned)

    # ------------------------------------------------------------- apply ---

    def _forget_expired_pauses(self) -> None:
        """Clears finished pauses, and the rules they were the only reason for.

        Without this, one "pause 15 min" on a passing guest would keep a row
        forever and slowly eat the MAX_RULES budget.
        """
        now = int(time.time())
        with self._lock:
            rules = self.rules()
            if not any(0 < rule["paused_until"] <= now for rule in rules):
                return
            remaining: list[dict[str, Any]] = []
            for rule in rules:
                # An active pause is left alone; only a finished one is cleared.
                paused = rule["paused_until"] if rule["paused_until"] > now else 0
                if rule["alias"] or rule["schedule"] or paused:
                    remaining.append({**rule, "paused_until": paused})
            self._store(remaining)

    def apply(self) -> None:
        self._forget_expired_pauses()
        try:
            self.guard.set_policy_blocks(self.effective_blocks())
        except NetControlError as error:
            logger.warning("Règles d’accès non appliquées: %s", error)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="onionpi-access", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.apply()
            self._stop.wait(TICK_SECONDS)
