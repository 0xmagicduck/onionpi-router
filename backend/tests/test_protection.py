from onionpi.protection import protection_snapshot


def services(**overrides: bool) -> list[dict[str, object]]:
    values = {
        "tor": True,
        "NetworkManager": True,
        "onionpi-ap": True,
        "dnsmasq": True,
        "onionpi-firewall": True,
    }
    values.update(overrides)
    return [{"id": name, "label": name, "active": active} for name, active in values.items()]


def test_protection_requires_every_runtime_control() -> None:
    snapshot = protection_snapshot({"connected": True}, services())
    assert snapshot["status"] == "protected"
    assert snapshot["safe"] is True


def test_firewall_failure_is_never_presented_as_secure() -> None:
    snapshot = protection_snapshot(
        {"connected": True}, services(**{"onionpi-firewall": False})
    )
    assert snapshot["status"] == "degraded"
    assert snapshot["safe"] is False
    assert "Coupe-circuit" in snapshot["summary"]


def test_tor_failure_is_contained_when_kill_switch_is_active() -> None:
    snapshot = protection_snapshot({"connected": False}, services())
    assert snapshot["status"] == "contained"
    assert snapshot["safe"] is True
    assert snapshot["label"] == "Sorties bloquées"


def test_demo_mode_never_claims_real_protection() -> None:
    snapshot = protection_snapshot({"connected": True}, services(), demo_mode=True)
    assert snapshot["status"] == "demo"
    assert snapshot["safe"] is False
