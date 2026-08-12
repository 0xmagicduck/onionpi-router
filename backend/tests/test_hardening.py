from __future__ import annotations

import time
from pathlib import Path

import pytest
from onionpi.auth import hash_password
from onionpi.config import get_settings
from onionpi.hardening import SEVERITY_WEIGHTS, security_audit
from onionpi.services import build_app_services

PROTECTED = {
    "status": "protected",
    "summary": "Tor, le DNS et le coupe-circuit protègent le trafic des clients.",
}
DEGRADED = {"status": "degraded", "summary": "Coupe-circuit arrêté."}


@pytest.fixture()
def services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ONIONPI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ONIONPI_SHARED_DIR", str(tmp_path / "shared"))
    monkeypatch.setenv("ONIONPI_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("ONIONPI_SESSION_SECRET", "audit-test-secret")
    monkeypatch.setenv("ONIONPI_DEMO_MODE", "1")
    monkeypatch.setenv("ONIONPI_FIREWALL_RULES", str(tmp_path / "firewall.nft"))
    get_settings.cache_clear()
    composed = build_app_services(get_settings())
    composed.database.initialize()
    yield composed
    get_settings.cache_clear()


def _finding(report: dict, identifier: str) -> dict:
    return next(item for item in report["findings"] if item["id"] == identifier)


def test_report_is_ordered_by_urgency_and_scored(services) -> None:
    report = security_audit(services, PROTECTED)

    assert 0 <= report["score"] <= 100
    assert report["counts"]["total"] == len(report["findings"])
    # Unresolved findings come first, worst severity leading.
    order = list(SEVERITY_WEIGHTS)
    ranks = [
        (item["ok"], order.index(item["severity"])) for item in report["findings"]
    ]
    assert ranks == sorted(ranks)
    # Demonstration mode is never presented as a safe configuration.
    assert _finding(report, "demo-mode")["ok"] is False
    assert report["counts"]["pending"] >= 1


def test_a_degraded_kill_switch_offers_the_repair_action(services) -> None:
    report = security_audit(services, DEGRADED)
    finding = _finding(report, "protection-state")
    assert finding["ok"] is False
    assert finding["action"] == "restart-firewall"
    assert finding["severity"] == "critical"


def test_ssh_reachable_from_the_wifi_is_reported(services) -> None:
    services.settings.firewall_rules_path.write_text(
        "chain lan_input {\n    tcp dport 22 accept\n    counter drop\n}\n"
    )
    # The check only reads the real ruleset outside demonstration mode.
    object.__setattr__(services.settings, "demo_mode", False)
    try:
        report = security_audit(services, PROTECTED)
        assert _finding(report, "ssh-exposure")["ok"] is False

        services.settings.firewall_rules_path.write_text(
            "chain lan_input {\n    # SSH refusé\n    counter drop\n}\n"
        )
        assert _finding(security_audit(services, PROTECTED), "ssh-exposure")["ok"]
    finally:
        object.__setattr__(services.settings, "demo_mode", True)


def test_an_old_password_is_flagged(services) -> None:
    services.database.create_user("admin", "Camille", hash_password("phrase-tres-solide"))
    assert _finding(security_audit(services, PROTECTED), "password-age")["ok"]

    with services.database.connect() as connection:
        connection.execute(
            "UPDATE users SET password_changed_at = ?",
            (int(time.time()) - 400 * 24 * 3600,),
        )
    report = security_audit(services, PROTECTED)
    assert _finding(report, "password-age")["ok"] is False
    assert "400 jour(s)" in _finding(report, "password-age")["detail"]


def test_an_onion_service_without_client_authorisation_is_flagged(services) -> None:
    assert _finding(security_audit(services, PROTECTED), "onion-exposure")["ok"]

    services.onion.set_enabled(True)
    report = security_audit(services, PROTECTED)
    assert _finding(report, "onion-exposure")["ok"] is False

    services.onion.add_client("Portable")
    assert _finding(security_audit(services, PROTECTED), "onion-exposure")["ok"]


def test_the_report_carries_no_secret(services) -> None:
    services.database.create_user("admin", "Camille", hash_password("phrase-tres-solide"))
    services.onion.set_enabled(True)
    services.onion.add_client("Portable")
    serialized = repr(security_audit(services, PROTECTED))

    assert "scrypt" not in serialized
    assert "descriptor:x25519" not in serialized
    assert "ED25519-V3" not in serialized
