from __future__ import annotations

import json

import pytest
from onionpi.updates import (
    DEFAULT_PREFERENCES,
    UpdateError,
    UpdateManager,
    normalise_schedule,
)


@pytest.fixture()
def manager(tmp_path) -> UpdateManager:
    return UpdateManager(
        state_path=tmp_path / "update.state",
        settings_path=tmp_path / "update.settings.json",
        version="1.2.3",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("04:30", "04:30"),
        (" 04:30 ", "04:30"),
        ("15:00,03:00", "03:00,15:00"),
        ("03:00, 03:00", "03:00"),
        ("00:00,23:59", "00:00,23:59"),
    ],
)
def test_schedule_is_normalised(raw: str, expected: str) -> None:
    assert normalise_schedule(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "  ", "4:30", "24:00", "04:60", "04h30", "04:30;05:30", "1,2,3,4,5,6,7"],
)
def test_invalid_schedule_is_refused(raw: str) -> None:
    with pytest.raises(UpdateError):
        normalise_schedule(raw)


def test_preferences_round_trip(manager: UpdateManager) -> None:
    manager.save_preferences("edge", "02:15,14:15", enabled=True, apply=False)
    stored = json.loads(manager.settings_path.read_text())
    assert stored == {
        "channel": "edge",
        "schedule": "02:15,14:15",
        "enabled": True,
        "apply": False,
    }
    assert manager.preferences().channel == "edge"
    assert manager.preferences().apply is False


def test_unknown_channel_is_refused(manager: UpdateManager) -> None:
    with pytest.raises(UpdateError):
        manager.save_preferences("nightly", "04:30", enabled=True, apply=True)
    assert not manager.settings_path.exists()


def test_settings_file_is_not_world_readable(manager: UpdateManager) -> None:
    manager.save_preferences("stable", "04:30", enabled=True, apply=True)
    assert manager.settings_path.stat().st_mode & 0o777 == 0o640


def test_corrupt_preferences_fall_back_to_defaults(manager: UpdateManager) -> None:
    manager.settings_path.write_text("{ not json")
    assert manager.preferences() == DEFAULT_PREFERENCES


def test_hostile_preferences_are_ignored_field_by_field(manager: UpdateManager) -> None:
    manager.settings_path.write_text(
        json.dumps({"channel": "../../etc", "schedule": "*-*-* 00:00:00; rm -rf /"})
    )
    preferences = manager.preferences()
    assert preferences.channel == DEFAULT_PREFERENCES.channel
    assert preferences.schedule == DEFAULT_PREFERENCES.schedule


def test_state_reports_installed_version_without_state_file(manager: UpdateManager) -> None:
    state = manager.state()
    assert state["installed"] == "1.2.3"
    assert state["available"] == ""
    assert state["update_pending"] is False


def test_state_merges_the_root_document(manager: UpdateManager) -> None:
    manager.state_path.write_text(
        json.dumps(
            {
                "installed": "1.2.3",
                "available": "1.3.0",
                "update_pending": True,
                "running": False,
                "last_check": 1_700_000_000,
                "last_check_status": "ok",
                "history": ["20260811T040000Z installé 1.3.0"] * 20,
            }
        )
    )
    state = manager.state()
    assert state["available"] == "1.3.0"
    assert state["update_pending"] is True
    assert state["last_check_status"] == "ok"
    # The interface only ever shows a short tail of the history.
    assert len(state["history"]) == 10


def test_corrupt_state_does_not_break_the_page(manager: UpdateManager) -> None:
    manager.state_path.write_text("<html>not json</html>")
    assert manager.state()["installed"] == "1.2.3"
