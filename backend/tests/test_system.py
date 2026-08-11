from __future__ import annotations

from pathlib import Path

from onionpi.system import MetricsSampler, system_snapshot


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
        "dnsmasq",
        "onionpi-firewall",
    }


def test_demo_sampler_is_independent_from_host_interfaces() -> None:
    sampler = MetricsSampler("interface-that-does-not-exist", demo_mode=True)

    sampler.sample()
    sample = sampler.history()[-1]

    assert sample["download_mbps"] > 0
    assert sample["upload_mbps"] > 0
