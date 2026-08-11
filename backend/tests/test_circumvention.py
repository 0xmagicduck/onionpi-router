from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from onionpi.circumvention import (
    CircumventionError,
    CircumventionManager,
    load_catalog,
    validate_bridge_line,
)


class FakeController:
    """Stands in for the Tor control port: records reloads, can refuse them."""

    def __init__(self, refuse: bool = False, progress: int = 100) -> None:
        self.refuse = refuse
        self.progress = progress
        self.reloads = 0

    def reload_config(self) -> None:
        self.reloads += 1
        if self.refuse:
            from onionpi.tor_control import TorControlError

            raise TorControlError("configuration refusée")

    def bootstrap_progress(self) -> int:
        return self.progress


def build(tmp_path: Path, country: str = "BE", **kwargs) -> CircumventionManager:
    return CircumventionManager(
        config_path=tmp_path / "bridges.conf",
        state_path=tmp_path / "circumvention.json",
        cache_path=tmp_path / "cache.json",
        controller=kwargs.pop("controller", FakeController()),
        country=country,
        require_installed=False,
        **kwargs,
    )


def test_bundled_catalog_has_bridges_for_every_transport() -> None:
    catalog = load_catalog()
    for transport in ("obfs4", "snowflake", "meek"):
        assert catalog["builtin"][transport], transport
        for line in catalog["builtin"][transport]:
            validate_bridge_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "obfs4 192.0.2.1:443 ABCDEF cert=abc iat-mode=0",
        "Bridge snowflake 192.0.2.3:80 FINGERPRINT url=https://example.org/",
        "192.0.2.9:9001",
        "obfs4 [2001:db8::1]:443 ABCDEF cert=abc",
    ],
)
def test_valid_bridge_lines(line: str) -> None:
    assert validate_bridge_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "SocksPort 9050",
        "obfs4 192.0.2.1:443\nExitPolicy accept *:*",
        "wireguard 192.0.2.1:443 KEY",
        "obfs4 192.0.2.1:99999 ABCDEF",
        "obfs4 999.0.2.1:443 ABCDEF",
        "obfs4 192.0.2.1:443 cert=" + "a" * 900,
    ],
)
def test_rejected_bridge_lines(line: str) -> None:
    with pytest.raises(CircumventionError):
        validate_bridge_line(line)


def test_direct_mode_writes_a_disabled_fragment(tmp_path: Path) -> None:
    manager = build(tmp_path)
    manager.update(mode="direct", transport="snowflake", country="BE", custom_bridges=[])
    content = (tmp_path / "bridges.conf").read_text()
    assert "UseBridges 0" in content
    assert "Bridge " not in content


def test_manual_mode_renders_plugin_and_bridges(tmp_path: Path) -> None:
    manager = build(tmp_path)
    manager.update(
        mode="manual",
        transport="obfs4",
        country="FR",
        custom_bridges=["obfs4 192.0.2.1:443 ABCDEF cert=abc iat-mode=0"],
    )
    content = (tmp_path / "bridges.conf").read_text()
    assert "UseBridges 1" in content
    assert "ClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy" in content
    assert content.count("Bridge ") == 1
    stored = json.loads((tmp_path / "circumvention.json").read_text())
    assert stored["mode"] == "manual"
    assert stored["custom_bridges"] == ["obfs4 192.0.2.1:443 ABCDEF cert=abc iat-mode=0"]


def test_meek_uses_the_transport_name_tor_expects(tmp_path: Path) -> None:
    manager = build(tmp_path)
    assert "ClientTransportPlugin meek_lite exec" in manager.render("meek")


def test_auto_mode_starts_on_bridges_in_a_censored_country(tmp_path: Path) -> None:
    manager = build(tmp_path, country="IR")
    snapshot = manager.update(mode="auto", transport="snowflake", country="IR", custom_bridges=[])
    assert snapshot["censored_country"] is True
    assert snapshot["auto_transport"] == manager.recommended()[0]
    assert "UseBridges 1" in (tmp_path / "bridges.conf").read_text()


def test_auto_mode_stays_direct_where_tor_is_not_filtered(tmp_path: Path) -> None:
    manager = build(tmp_path, country="BE")
    snapshot = manager.update(mode="auto", transport="snowflake", country="BE", custom_bridges=[])
    assert snapshot["censored_country"] is False
    assert snapshot["auto_transport"] == ""
    assert "UseBridges 0" in (tmp_path / "bridges.conf").read_text()


def test_rejected_reload_restores_the_previous_fragment(tmp_path: Path) -> None:
    controller = FakeController()
    manager = build(tmp_path, controller=controller)
    manager.update(mode="direct", transport="snowflake", country="BE", custom_bridges=[])
    before = (tmp_path / "bridges.conf").read_text()

    controller.refuse = True
    with pytest.raises(CircumventionError):
        manager.update(mode="manual", transport="snowflake", country="BE", custom_bridges=[])

    assert (tmp_path / "bridges.conf").read_text() == before
    assert manager.config.mode == "direct"


def test_rejected_first_reload_leaves_a_safe_mandatory_fragment(tmp_path: Path) -> None:
    manager = build(tmp_path, controller=FakeController(refuse=True))

    with pytest.raises(CircumventionError):
        manager.update(
            mode="manual",
            transport="snowflake",
            country="BE",
            custom_bridges=[],
        )

    assert (tmp_path / "bridges.conf").read_text().endswith("UseBridges 0\n")


def long_ago(manager: CircumventionManager) -> None:
    """Push the watchdog clocks far enough back to cross every threshold.

    time.monotonic() counts from boot on Linux: a literal 0.0 only reads as
    "long ago" on a machine that has been up for a while, and a freshly booted
    CI runner made these tests silently observe a stall of a few seconds.
    """
    past = time.monotonic() - 10_000
    manager._stalled_since = past
    manager._last_switch = past


def test_watchdog_enables_bridges_after_a_stalled_bootstrap(tmp_path: Path) -> None:
    controller = FakeController(progress=10)
    manager = build(tmp_path, country="BE", controller=controller)
    manager.update(mode="auto", transport="snowflake", country="BE", custom_bridges=[])

    manager.tick()  # first stalled observation only starts the timer
    assert manager.config.auto_transport == ""

    long_ago(manager)  # simulate a long stall
    manager.tick()
    first = manager.config.auto_transport
    assert first in {"snowflake", "obfs4"}
    assert "UseBridges 1" in (tmp_path / "bridges.conf").read_text()

    long_ago(manager)
    manager.tick()
    assert manager.config.auto_transport != first


def test_watchdog_does_nothing_once_tor_is_bootstrapped(tmp_path: Path) -> None:
    manager = build(tmp_path, country="IR", controller=FakeController(progress=100))
    manager.update(mode="auto", transport="snowflake", country="IR", custom_bridges=[])
    chosen = manager.config.auto_transport
    long_ago(manager)
    manager.tick()
    assert manager.config.auto_transport == chosen


def test_manual_mode_refuses_a_transport_without_bridges(tmp_path: Path) -> None:
    manager = build(tmp_path)
    manager.catalog = {"builtin": {"obfs4": []}, "countries": {}}
    with pytest.raises(CircumventionError):
        manager.update(mode="manual", transport="obfs4", country="BE", custom_bridges=[])


def test_stored_configuration_is_reloaded(tmp_path: Path) -> None:
    build(tmp_path).update(
        mode="manual",
        transport="obfs4",
        country="RU",
        custom_bridges=["obfs4 192.0.2.1:443 ABCDEF cert=abc"],
    )
    restored = build(tmp_path)
    assert restored.config.mode == "manual"
    assert restored.config.country == "RU"
    assert restored.active_transport() == "obfs4"
