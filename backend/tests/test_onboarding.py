from __future__ import annotations

from types import SimpleNamespace

from onionpi.auth import verify_password
from onionpi.database import Database
from onionpi.maintenance import MaintenanceWindow
from onionpi.onboarding import OnboardingManager


class Firewall:
    def self_test(self) -> dict[str, str]:
        return {"status": "ok", "message": "testé"}

    def restart(self) -> dict[str, str]:
        return {"status": "ok"}


def manager(tmp_path) -> OnboardingManager:
    database = Database(tmp_path / "onboarding.db")
    database.initialize()
    settings = SimpleNamespace(
        upstream_interface="eth0",
        wifi_interface="wlan0",
        demo_mode=True,
    )
    return OnboardingManager(
        database,
        settings,
        Firewall(),
        MaintenanceWindow(tmp_path / "maintenance.state", demo_mode=True),
    )


def test_first_open_requires_every_safety_step(tmp_path) -> None:
    onboarding = manager(tmp_path)
    assert onboarding.status()["complete"] is False

    onboarding.mark("password")
    onboarding.confirm_interfaces("eth0", "wlan0")
    assert onboarding.test_firewall()["status"] == "ok"
    onboarding.confirm_clock()
    code = onboarding.issue_recovery_code()

    # Issuing is not enough: the UI only confirms the step after downloading.
    assert onboarding.status()["steps"]["recovery"] is False
    assert verify_password(code, onboarding.recovery_hash()) is True
    onboarding.mark("recovery")
    assert onboarding.status()["complete"] is True


def test_product_metrics_count_time_containment_and_recovery(tmp_path) -> None:
    onboarding = manager(tmp_path)
    onboarding.observe_protection("protected", now=100)
    metrics = onboarding.observe_protection("contained", now=160)
    assert metrics["protected_seconds"] == 60
    assert metrics["contained_entries"] == 1
    onboarding.observe_protection("protected", now=220)
    onboarding.record_recovery(True)
    onboarding.record_recovery(False)
    public = onboarding.public_metrics(onboarding.protection_metrics())
    assert public["protected_ratio"] == 0.5
    assert public["recovery_success_rate"] == 0.5
