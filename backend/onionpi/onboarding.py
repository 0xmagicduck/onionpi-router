"""First-open checks and privacy-preserving product telemetry."""

from __future__ import annotations

import secrets
import time
from typing import Any

from .auth import hash_password
from .backends import FirewallBackend
from .config import Settings
from .database import Database
from .maintenance import MaintenanceWindow
from .system import _run

ONBOARDING_KEY = "onboarding.v1"
RECOVERY_HASH_KEY = "recovery-code.v1"
PROTECTION_METRICS_KEY = "protection-metrics.v1"
REQUIRED_STEPS = ("password", "interfaces", "firewall", "clock", "recovery")


class OnboardingManager:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        firewall: FirewallBackend,
        maintenance: MaintenanceWindow,
    ) -> None:
        self.database = database
        self.settings = settings
        self.firewall = firewall
        self.maintenance = maintenance

    def _document(self) -> dict[str, Any]:
        raw = self.database.setting(ONBOARDING_KEY, {})
        return raw if isinstance(raw, dict) else {}

    def mark(self, step: str) -> None:
        if step not in REQUIRED_STEPS:
            raise ValueError(f"Étape inconnue: {step}")
        document = self._document()
        steps = document.get("steps") if isinstance(document.get("steps"), dict) else {}
        steps[step] = True
        document["steps"] = steps
        document["updated_at"] = int(time.time())
        self.database.set_setting(ONBOARDING_KEY, document)

    def status(self) -> dict[str, Any]:
        document = self._document()
        stored = document.get("steps") if isinstance(document.get("steps"), dict) else {}
        steps = {step: bool(stored.get(step)) for step in REQUIRED_STEPS}
        return {
            "complete": all(steps.values()),
            "steps": steps,
            "interfaces": {
                "wan": self.settings.upstream_interface,
                "access_point": self.settings.wifi_interface,
            },
            "clock_synchronised": self.clock_synchronised(),
            "maintenance": self.maintenance.state(),
        }

    def confirm_interfaces(self, wan: str, access_point: str) -> None:
        if wan != self.settings.upstream_interface or access_point != self.settings.wifi_interface:
            raise ValueError("Les interfaces ne correspondent pas à la configuration installée")
        if wan == access_point:
            raise ValueError("Les interfaces WAN et point d’accès doivent être différentes")
        self.mark("interfaces")

    def test_firewall(self) -> dict[str, Any]:
        result = self.firewall.self_test()
        if result.get("status") != "ok":
            raise RuntimeError(str(result.get("message") or "Test du coupe-circuit refusé"))
        self.mark("firewall")
        return result

    def clock_synchronised(self) -> bool:
        if self.settings.demo_mode:
            return True
        return _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]) == "yes"

    def confirm_clock(self) -> None:
        if not self.clock_synchronised():
            raise ValueError("L’horloge n’est pas encore synchronisée")
        self.mark("clock")

    def issue_recovery_code(self) -> str:
        # 120 random bits, grouped only to make a handwritten copy less error-prone.
        compact = secrets.token_hex(15).upper()
        code = "-".join(compact[index : index + 5] for index in range(0, len(compact), 5))
        self.database.set_setting(RECOVERY_HASH_KEY, hash_password(code))
        return code

    def recovery_hash(self) -> str:
        value = self.database.setting(RECOVERY_HASH_KEY, "")
        return value if isinstance(value, str) else ""

    def record_recovery(self, successful: bool) -> None:
        metrics = self.protection_metrics()
        key = "recoveries" if successful else "recovery_failures"
        metrics[key] = int(metrics.get(key, 0)) + 1
        self.database.set_setting(PROTECTION_METRICS_KEY, metrics)

    def observe_protection(self, status: str, now: int | None = None) -> dict[str, Any]:
        stamp = int(now if now is not None else time.time())
        metrics = self.protection_metrics()
        previous = str(metrics.get("last_status", ""))
        previous_at = int(metrics.get("last_at", stamp))
        elapsed = max(0, min(stamp - previous_at, 3600))
        durations = metrics.get("seconds") if isinstance(metrics.get("seconds"), dict) else {}
        if previous in {"protected", "contained", "degraded", "demo"}:
            durations[previous] = int(durations.get(previous, 0)) + elapsed
        if status == "contained" and previous != "contained":
            metrics["contained_entries"] = int(metrics.get("contained_entries", 0)) + 1
        metrics.update({"last_status": status, "last_at": stamp, "seconds": durations})
        self.database.set_setting(PROTECTION_METRICS_KEY, metrics)
        return self.public_metrics(metrics)

    def protection_metrics(self) -> dict[str, Any]:
        value = self.database.setting(PROTECTION_METRICS_KEY, {})
        return value if isinstance(value, dict) else {}

    @staticmethod
    def public_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
        seconds = metrics.get("seconds") if isinstance(metrics.get("seconds"), dict) else {}
        protected = int(seconds.get("protected", 0))
        known = sum(int(seconds.get(item, 0)) for item in ("protected", "contained", "degraded"))
        recoveries = int(metrics.get("recoveries", 0))
        failures = int(metrics.get("recovery_failures", 0))
        return {
            "protected_seconds": protected,
            "protected_ratio": round(protected / known, 4) if known else 0.0,
            "contained_entries": int(metrics.get("contained_entries", 0)),
            "recoveries": recoveries,
            "recovery_success_rate": round(recoveries / (recoveries + failures), 4)
            if recoveries + failures
            else None,
        }
