from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from onionpi.access import (
    AccessError,
    DeviceAccessManager,
    _schedule_allows,
)
from onionpi.agent import AgentError, PrivilegedAgent
from onionpi.database import Database
from onionpi.netcontrol import DeviceGuard


class RecordingAgent(PrivilegedAgent):
    def __init__(self) -> None:
        super().__init__(Path("/nonexistent"), Path("/nonexistent"))
        self.submitted: list[str] = []

    def submit(self, action: str, timeout: float = 12.0) -> dict[str, Any]:
        self.submitted.append(action)
        return {"action": action, "status": "ok", "message": ""}


@pytest.fixture()
def manager(tmp_path: Path) -> tuple[DeviceAccessManager, DeviceGuard, RecordingAgent]:
    database = Database(tmp_path / "test.db")
    database.initialize()
    agent = RecordingAgent()
    guard = DeviceGuard(database, tmp_path / "blocked-macs.txt", agent)
    return DeviceAccessManager(database, guard), guard, agent


def _weekday(offset_days: int = 0) -> int:
    return (time.localtime().tm_wday + offset_days) % 7


def test_a_pause_blocks_the_device_and_expires_on_its_own(
    manager: tuple[DeviceAccessManager, DeviceGuard, RecordingAgent],
) -> None:
    access, guard, agent = manager
    snapshot = access.pause("AA:BB:CC:DD:EE:FF", 30)

    rule = snapshot["rules"][0]
    assert rule["mac"] == "aa:bb:cc:dd:ee:ff"
    assert rule["state"] == "paused"
    assert guard.policy_blocks == {"aa:bb:cc:dd:ee:ff"}
    # The paused device reaches nftables through the same file as a manual block.
    assert "aa:bb:cc:dd:ee:ff" in guard.state_path.read_text()
    assert agent.submitted == ["devices"]

    # Nothing is enforced a second later once the pause is over.
    assert access.effective_blocks(now=rule["paused_until"] + 1) == set()

    access.pause("aa:bb:cc:dd:ee:ff", 0)
    assert guard.policy_blocks == set()
    assert "aa:bb:cc:dd:ee:ff" not in guard.state_path.read_text()


def test_a_manual_block_and_a_pause_survive_each_other(
    manager: tuple[DeviceAccessManager, DeviceGuard, RecordingAgent],
) -> None:
    access, guard, _ = manager
    guard.block("11:22:33:44:55:66", "Console")
    access.pause("aa:bb:cc:dd:ee:ff", 15)

    body = guard.state_path.read_text()
    assert "11:22:33:44:55:66" in body
    assert "aa:bb:cc:dd:ee:ff" in body

    # Lifting the pause must not drop the unrelated manual block.
    access.pause("aa:bb:cc:dd:ee:ff", 0)
    assert "11:22:33:44:55:66" in guard.state_path.read_text()
    assert guard.blocked_macs() == {"11:22:33:44:55:66"}


def test_a_schedule_only_allows_its_own_window(
    manager: tuple[DeviceAccessManager, DeviceGuard, RecordingAgent],
) -> None:
    access, guard, _ = manager
    access.update(
        "aa:bb:cc:dd:ee:ff",
        alias="Console du salon",
        schedule={
            "enabled": True,
            "days": list(range(7)),
            "start": "00:00",
            "end": "23:59",
        },
    )
    assert access.snapshot()["rules"][0]["state"] == "allowed"
    assert guard.policy_blocks == set()

    # A window that has not started yet blocks the device right now.
    access.update(
        "aa:bb:cc:dd:ee:ff",
        alias="Console du salon",
        schedule={
            "enabled": True,
            "days": [_weekday(1)],
            "start": "08:00",
            "end": "09:00",
        },
    )
    assert access.snapshot()["rules"][0]["state"] == "outside"
    assert guard.policy_blocks == {"aa:bb:cc:dd:ee:ff"}


def test_a_pause_does_not_erase_the_schedule(
    manager: tuple[DeviceAccessManager, DeviceGuard, RecordingAgent],
) -> None:
    access, _, _ = manager
    schedule = {"enabled": True, "days": [0, 1, 2], "start": "07:00", "end": "21:00"}
    access.update("aa:bb:cc:dd:ee:ff", alias="Tablette", schedule=schedule)
    access.pause("aa:bb:cc:dd:ee:ff", 60)

    rule = access.rule_for("aa:bb:cc:dd:ee:ff")
    assert rule["alias"] == "Tablette"
    assert rule["schedule"] == schedule
    assert rule["paused_until"] > int(time.time())


def test_a_window_crossing_midnight_belongs_to_the_day_it_started(
    manager: tuple[DeviceAccessManager, DeviceGuard, RecordingAgent],
) -> None:
    schedule = {"enabled": True, "days": [0], "start": "22:00", "end": "06:00"}
    monday_23h = time.struct_time((2026, 8, 10, 23, 0, 0, 0, 222, -1))
    tuesday_05h = time.struct_time((2026, 8, 11, 5, 0, 0, 1, 223, -1))
    tuesday_12h = time.struct_time((2026, 8, 11, 12, 0, 0, 1, 223, -1))

    assert _schedule_allows(schedule, monday_23h) is True
    # Tuesday 05:00 still belongs to the window opened on Monday evening.
    assert _schedule_allows(schedule, tuesday_05h) is True
    assert _schedule_allows(schedule, tuesday_12h) is False


def test_invalid_rules_are_refused(
    manager: tuple[DeviceAccessManager, DeviceGuard, RecordingAgent],
) -> None:
    access, _, _ = manager
    with pytest.raises(AccessError):
        access.update("pas-une-adresse")
    with pytest.raises(AccessError):
        access.update(
            "aa:bb:cc:dd:ee:ff",
            schedule={"enabled": True, "days": [0], "start": "25:00", "end": "06:00"},
        )
    with pytest.raises(AccessError):
        access.update(
            "aa:bb:cc:dd:ee:ff",
            schedule={"enabled": True, "days": [], "start": "08:00", "end": "09:00"},
        )
    with pytest.raises(AccessError):
        access.pause("aa:bb:cc:dd:ee:ff", 100_000)
    with pytest.raises(AccessError):
        access.remove("aa:bb:cc:dd:ee:ff")


def test_a_rule_without_intent_is_forgotten(
    manager: tuple[DeviceAccessManager, DeviceGuard, RecordingAgent],
) -> None:
    access, _, _ = manager
    access.update("aa:bb:cc:dd:ee:ff", alias="Tablette")
    assert access.snapshot()["rules"]
    access.update("aa:bb:cc:dd:ee:ff", alias="")
    assert access.snapshot()["rules"] == []


def test_restore_imports_rules_without_reviving_an_old_pause(
    manager: tuple[DeviceAccessManager, DeviceGuard, RecordingAgent],
) -> None:
    access, _, _ = manager
    imported = access.restore(
        [
            {
                "mac": "AA:BB:CC:DD:EE:FF",
                "alias": "Tablette",
                "paused_until": int(time.time()) + 10_000,
                "schedule": {
                    "enabled": True,
                    "days": [0],
                    "start": "07:00",
                    "end": "21:00",
                },
            },
            {"mac": "pas-une-adresse", "alias": "ignoré"},
        ]
    )
    assert imported == 1
    rule = access.rule_for("aa:bb:cc:dd:ee:ff")
    assert rule["alias"] == "Tablette"
    assert rule["paused_until"] == 0


def test_a_refused_apply_is_retried_on_the_next_tick(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.initialize()

    class FailingAgent(RecordingAgent):
        def submit(self, action: str, timeout: float = 12.0) -> dict[str, Any]:
            self.submitted.append(action)
            raise AgentError("agent indisponible")

    agent = FailingAgent()
    guard = DeviceGuard(database, tmp_path / "blocked-macs.txt", agent)
    access = DeviceAccessManager(database, guard)

    # A privileged agent that does not answer must not lose the operator's
    # intent: the pause is stored, simply not yet enforced.
    access.pause("aa:bb:cc:dd:ee:ff", 30)
    assert access.rule_for("aa:bb:cc:dd:ee:ff")["paused_until"] > 0
    assert guard.policy_blocks == set()

    access.apply()
    assert agent.submitted == ["devices", "devices"]


def test_an_expired_pause_stops_taking_a_rule_slot(
    manager: tuple[DeviceAccessManager, DeviceGuard, RecordingAgent],
) -> None:
    access, _, _ = manager
    access.pause("aa:bb:cc:dd:ee:ff", 15)
    access.update(
        "11:22:33:44:55:66",
        alias="Console",
        schedule={"enabled": True, "days": [0], "start": "08:00", "end": "09:00"},
    )
    # A guest paused once must not keep a row for ever.
    with access._lock:
        access._store(
            [
                {**rule, "paused_until": 1 if rule["paused_until"] else 0}
                for rule in access.rules()
            ]
        )
    access.apply()

    assert [rule["mac"] for rule in access.rules()] == ["11:22:33:44:55:66"]
    # The named device kept its schedule.
    assert access.rule_for("11:22:33:44:55:66")["alias"] == "Console"


def test_an_active_pause_survives_the_cleanup(
    manager: tuple[DeviceAccessManager, DeviceGuard, RecordingAgent],
) -> None:
    access, _, _ = manager
    access.pause("aa:bb:cc:dd:ee:ff", 60)
    access.apply()
    assert access.rule_for("aa:bb:cc:dd:ee:ff")["paused_until"] > int(time.time())
