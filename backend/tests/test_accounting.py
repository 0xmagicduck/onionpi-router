from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from onionpi.accounting import TrafficAccountant
from onionpi.database import Database

PHONE = "3c:07:54:2a:91:8f"
LAPTOP = "6a:4f:12:8b:33:21"


def nft_set(name: str, handle: int, elements: dict[str, int]) -> dict[str, Any]:
    """The shape `nft -j list set` produces for a set with counters."""
    return {
        "nftables": [
            {"metainfo": {"version": "1.0.6", "json_schema_version": 1}},
            {
                "set": {
                    "family": "inet",
                    "name": name,
                    "table": "onionpi",
                    "type": "ether_addr",
                    "handle": handle,
                    "flags": ["dynamic"],
                    "elem": [
                        {"elem": {"val": key, "counter": {"packets": 3, "bytes": value}}}
                        for key, value in elements.items()
                    ],
                }
            },
        ]
    }


@pytest.fixture()
def accountant(tmp_path: Path) -> TrafficAccountant:
    database = Database(tmp_path / "test.db")
    database.initialize()
    return TrafficAccountant(database, tmp_path / "traffic.json", demo_mode=False)


def publish(
    accountant: TrafficAccountant,
    uploads: dict[str, int],
    downloads: dict[str, int],
    handle: int = 7,
) -> None:
    accountant.state_path.write_text(
        json.dumps(
            {
                "updated_at": 1_700_000_000,
                "upload": nft_set("client_upload", handle, uploads),
                "download": nft_set("client_download", handle + 1, downloads),
            }
        )
    )


def leases() -> list[dict[str, Any]]:
    return [
        {"ip": "10.42.0.10", "mac": LAPTOP},
        {"ip": "10.42.0.11", "mac": PHONE},
    ]


def test_counters_become_totals_per_device(accountant: TrafficAccountant) -> None:
    publish(accountant, {LAPTOP: 1_000, PHONE: 400}, {"10.42.0.10": 9_000})

    totals = accountant.update(leases())

    assert totals[LAPTOP] == {"download": 9_000, "upload": 1_000, "seen_at": totals[LAPTOP]["seen_at"]}
    assert totals[PHONE]["upload"] == 400
    assert totals[PHONE]["download"] == 0


def test_only_the_growth_is_added_between_two_samples(accountant: TrafficAccountant) -> None:
    publish(accountant, {LAPTOP: 1_000}, {"10.42.0.10": 9_000})
    accountant.update(leases())

    publish(accountant, {LAPTOP: 1_500}, {"10.42.0.10": 12_000})
    totals = accountant.update(leases())

    assert totals[LAPTOP]["upload"] == 1_500
    assert totals[LAPTOP]["download"] == 12_000


def test_a_firewall_reload_does_not_lose_the_earlier_bytes(
    accountant: TrafficAccountant,
) -> None:
    publish(accountant, {LAPTOP: 5_000}, {"10.42.0.10": 20_000})
    accountant.update(leases())

    # A reload recreates the table: new handles, counters restarting from zero.
    publish(accountant, {LAPTOP: 300}, {"10.42.0.10": 700}, handle=21)
    totals = accountant.update(leases())

    assert totals[LAPTOP]["upload"] == 5_300
    assert totals[LAPTOP]["download"] == 20_700


def test_a_counter_that_restarts_within_the_same_ruleset_is_added_whole(
    accountant: TrafficAccountant,
) -> None:
    publish(accountant, {LAPTOP: 5_000}, {})
    accountant.update(leases())

    publish(accountant, {LAPTOP: 120}, {})
    totals = accountant.update(leases())

    assert totals[LAPTOP]["upload"] == 5_120


def test_downloads_wait_for_the_lease_that_explains_them(
    accountant: TrafficAccountant,
) -> None:
    publish(accountant, {}, {"10.42.0.99": 4_000})

    assert accountant.update(leases()) == {}

    # The device shows up in the neighbour table one poll later: its bytes are
    # charged then, rather than being lost or charged to someone else.
    totals = accountant.update([*leases(), {"ip": "10.42.0.99", "mac": "aa:bb:cc:dd:ee:ff"}])

    assert totals["aa:bb:cc:dd:ee:ff"]["download"] == 4_000


def test_totals_survive_a_restart_without_being_counted_twice(
    accountant: TrafficAccountant, tmp_path: Path
) -> None:
    publish(accountant, {LAPTOP: 8_000}, {})
    accountant.update(leases())

    database = Database(tmp_path / "test.db")
    restarted = TrafficAccountant(database, accountant.state_path, demo_mode=False)
    totals = restarted.update(leases())

    assert totals[LAPTOP]["upload"] == 8_000


def test_an_unsampled_installation_keeps_showing_what_it_knows(
    accountant: TrafficAccountant,
) -> None:
    publish(accountant, {LAPTOP: 8_000}, {})
    accountant.update(leases())
    accountant.state_path.unlink()

    assert accountant.update(leases())[LAPTOP]["upload"] == 8_000
    assert accountant.snapshot()["supported"] is False


def test_reset_restarts_from_the_current_counters(accountant: TrafficAccountant) -> None:
    publish(accountant, {LAPTOP: 8_000}, {})
    accountant.update(leases())

    accountant.reset()

    assert accountant.totals() == {}
    # The bytes already in the kernel counter belong to the period that was
    # just discarded: only what comes after them may be counted again.
    publish(accountant, {LAPTOP: 8_500}, {})
    assert accountant.update(leases())[LAPTOP]["upload"] == 500


def test_demonstration_mode_never_touches_the_counters(tmp_path: Path) -> None:
    database = Database(tmp_path / "demo.db")
    database.initialize()
    accountant = TrafficAccountant(database, tmp_path / "absent.json", demo_mode=True)

    assert accountant.update(leases()) == {}
    assert accountant.snapshot()["supported"] is True


def test_a_corrupt_sample_is_ignored(accountant: TrafficAccountant) -> None:
    accountant.state_path.write_text("{ pas du JSON")

    assert accountant.update(leases()) == {}
