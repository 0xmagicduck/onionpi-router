from __future__ import annotations

from pathlib import Path

from onionpi.system import MetricsSampler, _mesh_peers, mesh_details, system_snapshot


def test_real_system_snapshot_survives_restricted_host_metrics(tmp_path: Path) -> None:
    # This intentionally uses the real psutil build and host permissions. The
    # managed macOS test environment denies some sysctl calls, which reproduces
    # the failure that originally made /api/v1/status return 500.
    snapshot = system_snapshot(tmp_path, demo_mode=False)

    assert snapshot["hostname"]
    assert snapshot["storage_total"] >= snapshot["storage_used"] >= 0
    assert snapshot["uptime_seconds"] >= 0
    assert {service["id"] for service in snapshot["services"]} == {
        "tor",
        "NetworkManager",
        "onionpi-ap",
        "dnsmasq",
        "onionpi-firewall",
    }


def test_demo_sampler_is_independent_from_host_interfaces() -> None:
    sampler = MetricsSampler("interface-that-does-not-exist", demo_mode=True)

    sampler.sample()
    sample = sampler.history()[-1]

    assert sample["download_mbps"] > 0
    assert sample["upload_mbps"] > 0


def test_demo_mesh_exposes_a_real_multihop_next_hop() -> None:
    mesh = mesh_details("wlan1", "bat0", "Maison-Onion", "10.43.0.1/16", True)

    assert mesh["active"] is True
    assert mesh["peer_count"] == 2
    assert mesh["peers"][1]["next_hop"] == mesh["peers"][0]["mac"]


def test_batman_originators_are_normalized(monkeypatch) -> None:
    output = """\
Originator        last-seen (#/255) Nexthop          [outgoingIF]
02:42:ac:11:00:02    0.400s (144.4 MBit) 02:42:ac:11:00:02 [wlan1]
02:42:ac:11:00:03    0.800s (72.2 MBit) 02:42:ac:11:00:02 [wlan1]
"""
    monkeypatch.setattr("onionpi.system._run", lambda command, timeout=2: output)

    peers = _mesh_peers("bat0")

    assert peers == [
        {
            "mac": "02:42:ac:11:00:02",
            "last_seen": "0.400s",
            "throughput_mbps": 144.4,
            "next_hop": "02:42:ac:11:00:02",
        },
        {
            "mac": "02:42:ac:11:00:03",
            "last_seen": "0.800s",
            "throughput_mbps": 72.2,
            "next_hop": "02:42:ac:11:00:02",
        },
    ]
