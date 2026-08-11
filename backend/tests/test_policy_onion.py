from __future__ import annotations

from pathlib import Path

import pytest
from onionpi.database import Database
from onionpi.onion import OnionError, OnionService
from onionpi.policy import PolicyError, TorPolicy
from onionpi.tor_control import TorControlError


class FakeController:
    """Records what would have been sent to Tor's control port."""

    def __init__(self, fail: bool = False) -> None:
        self.reloads = 0
        self.identities = 0
        self.added: list[str | None] = []
        self.removed: list[str] = []
        self.published: set[str] = set()
        self.fail = fail
        self.demo_mode = False

    def reload_config(self) -> None:
        if self.fail:
            raise TorControlError("rechargement refusé")
        self.reloads += 1

    def new_identity(self) -> None:
        self.identities += 1

    def add_onion(self, private_key: str | None, target: str) -> dict[str, str]:
        if self.fail:
            raise TorControlError("ADD_ONION refusé")
        self.added.append(private_key)
        service_id = "a" * 56
        self.published.add(service_id)
        return {"service_id": service_id, "private_key": private_key or "ED25519-V3:AAAA"}

    def remove_onion(self, service_id: str) -> None:
        self.removed.append(service_id)
        self.published.discard(service_id)

    def onion_published(self, service_id: str) -> bool:
        return service_id in self.published


@pytest.fixture()
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    database.initialize()
    return database


def test_policy_writes_a_valid_torrc_fragment(database: Database, tmp_path: Path) -> None:
    path = tmp_path / "policy.conf"
    controller = FakeController()
    policy = TorPolicy(database, path, controller)  # type: ignore[arg-type]

    policy.update("BE", 3600)
    body = path.read_text()
    assert "ExitNodes {be}" in body
    assert "StrictNodes 1" in body
    assert controller.reloads == 1
    assert policy.state() == {"exit_country": "BE", "rotation_seconds": 3600}

    # Clearing the country must remove the directive, not comment it out badly.
    policy.update("", 0)
    assert "ExitNodes" not in path.read_text()
    assert controller.reloads == 2

    with pytest.raises(PolicyError):
        policy.update("ZZ", 0)
    with pytest.raises(PolicyError):
        policy.update("BE", 77)


def test_policy_reports_a_refused_reload(database: Database, tmp_path: Path) -> None:
    path = tmp_path / "policy.conf"
    policy = TorPolicy(database, path, FakeController(fail=True))  # type: ignore[arg-type]
    with pytest.raises(PolicyError):
        policy.update("FR", 0)
    assert path.exists()
    assert "ExitNodes" not in path.read_text()


def test_policy_ensure_file_creates_the_include(database: Database, tmp_path: Path) -> None:
    path = tmp_path / "policy.conf"
    TorPolicy(database, path, FakeController()).ensure_file()  # type: ignore[arg-type]
    assert path.exists()


def test_policy_rotation_uses_the_control_port(database: Database, tmp_path: Path) -> None:
    controller = FakeController()
    policy = TorPolicy(database, tmp_path / "policy.conf", controller)  # type: ignore[arg-type]
    policy.rotate_now(automatic=True)
    assert controller.identities == 1
    assert policy.snapshot()["last_rotation"] > 0


def test_onion_publishes_and_reuses_its_key(database: Database, tmp_path: Path) -> None:
    key_path = tmp_path / "onion.key"
    controller = FakeController()
    service = OnionService(database, key_path, controller, "127.0.0.1:8080")  # type: ignore[arg-type]

    state = service.set_enabled(True)
    assert state["address"] == "a" * 56 + ".onion"
    assert state["published"] is True
    assert key_path.read_text().strip() == "ED25519-V3:AAAA"
    assert key_path.stat().st_mode & 0o777 == 0o600
    assert controller.added == [None]

    # Tor forgets detached services on restart: the same key must come back.
    controller.published.clear()
    service._address = ""
    service.ensure_published()
    assert controller.added == [None, "ED25519-V3:AAAA"]
    assert service.address == "a" * 56

    service.set_enabled(False)
    assert controller.removed == ["a" * 56]
    assert service.snapshot()["address"] == ""


def test_onion_rotation_drops_the_previous_key(database: Database, tmp_path: Path) -> None:
    key_path = tmp_path / "onion.key"
    controller = FakeController()
    service = OnionService(database, key_path, controller, "127.0.0.1:8080")  # type: ignore[arg-type]
    service.set_enabled(True)

    service.rotate_address()
    # The old key was thrown away, so Tor was asked for a brand new one.
    assert controller.added == [None, None]


def test_onion_reports_a_refused_publication(database: Database, tmp_path: Path) -> None:
    service = OnionService(database, tmp_path / "onion.key", FakeController(fail=True), "127.0.0.1:8080")  # type: ignore[arg-type]
    with pytest.raises(OnionError):
        service.set_enabled(True)
