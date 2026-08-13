from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Any

import pytest
from onionpi.agent import ACTIONS, AgentError, PrivilegedAgent
from onionpi.database import Database
from onionpi.netcontrol import (
    DNS_FILTER_KEY,
    DeviceGuard,
    DnsFilter,
    DnsFilterBusy,
    NetControlError,
    normalize_domain,
    normalize_mac,
)


class RecordingAgent(PrivilegedAgent):
    """Stands in for systemd: records verbs instead of performing them."""

    def __init__(self) -> None:
        super().__init__(Path("/nonexistent"), Path("/nonexistent"))
        self.submitted: list[str] = []

    def submit(self, action: str, timeout: float = 12.0) -> dict[str, Any]:
        self.submitted.append(action)
        return {"action": action, "status": "ok", "message": ""}


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[Database, RecordingAgent]:
    database = Database(tmp_path / "test.db")
    database.initialize()
    return database, RecordingAgent()


def test_normalizers_accept_usual_spellings() -> None:
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_domain("HTTPS://Ads.Example.com/track?a=1") == "ads.example.com"
    for value in ("aa:bb:cc:dd:ee", "zz:bb:cc:dd:ee:ff", ""):
        with pytest.raises(ValueError):
            normalize_mac(value)
    for value in ("not a domain", "localhost", "-bad.com"):
        with pytest.raises(ValueError):
            normalize_domain(value)


def test_device_guard_writes_state_and_asks_for_apply(
    workspace: tuple[Database, RecordingAgent], tmp_path: Path
) -> None:
    database, agent = workspace
    state = tmp_path / "blocked-macs.txt"
    guard = DeviceGuard(database, state, agent)

    guard.block("AA:BB:CC:DD:EE:FF", "Tablette")
    assert agent.submitted == ["devices"]
    assert "aa:bb:cc:dd:ee:ff" in state.read_text()
    assert guard.blocked_macs() == {"aa:bb:cc:dd:ee:ff"}

    guard.unblock("aa:bb:cc:dd:ee:ff")
    assert guard.entries() == []
    # The file must be rewritten, not merely forgotten in the database.
    assert "aa:bb:cc:dd:ee:ff" not in state.read_text()

    with pytest.raises(NetControlError):
        guard.unblock("aa:bb:cc:dd:ee:ff")


def test_device_guard_survives_a_restart(
    workspace: tuple[Database, RecordingAgent], tmp_path: Path
) -> None:
    database, agent = workspace
    state = tmp_path / "blocked-macs.txt"
    DeviceGuard(database, state, agent).block("aa:bb:cc:dd:ee:ff")
    state.unlink()

    # A fresh process re-reads the intent from the database and pushes it back.
    DeviceGuard(database, state, agent).resync()
    assert state.read_text().count("aa:bb:cc:dd:ee:ff") == 1
    assert agent.submitted == ["devices", "devices"]


def test_dns_filter_writes_hosts_file_and_reloads(
    workspace: tuple[Database, RecordingAgent], tmp_path: Path
) -> None:
    database, agent = workspace
    hosts = tmp_path / "block.hosts"
    dns = DnsFilter(database, hosts, agent)

    snapshot = dns.update([], ["Ads.Example.com", "tracker.test"], ["ads.example.com"])
    assert snapshot["enabled"] is True
    body = hosts.read_text()
    # The allow list wins over the block list, and only over that one entry.
    assert "0.0.0.0 tracker.test" in body
    assert "ads.example.com" not in body
    assert snapshot["domain_count"] == 1
    assert agent.submitted == ["dns"]
    assert hosts.stat().st_mode & 0o777 == 0o644

    with pytest.raises(NetControlError):
        dns.update(["inconnue"], [], [])
    with pytest.raises(NetControlError):
        dns.update([], ["pas un domaine"], [])


def test_dns_filter_refuses_a_second_simultaneous_rebuild(
    workspace: tuple[Database, RecordingAgent], tmp_path: Path
) -> None:
    """One rebuild at a time: each holds a full blocklist in a Pi's memory."""
    database, agent = workspace
    dns = DnsFilter(database, tmp_path / "block.hosts", agent)
    started = threading.Event()
    release = threading.Event()
    refused: list[Exception] = []

    def slow_download(_url: str) -> list[str]:
        started.set()
        release.wait(5)
        return ["lent.test"]

    database.set_setting(
        DNS_FILTER_KEY, {"profiles": ["standard"], "custom_blocked": [], "allowed": []}
    )
    dns._download = slow_download  # type: ignore[method-assign]

    worker = threading.Thread(target=dns.rebuild)
    worker.start()
    try:
        assert started.wait(5)
        try:
            dns.rebuild()
        except DnsFilterBusy as error:
            refused.append(error)
    finally:
        release.set()
        worker.join(5)

    assert refused, "un second rebuild simultané doit être refusé"
    # The refused call wrote no hosts file and asked for no reload, so the two
    # writers never race over what dnsmasq ends up serving.
    assert agent.submitted == ["dns"]
    # The refusal left the flag alone: the appliance still accepts a rebuild
    # once the first one has finished.
    assert dns.snapshot()["refreshing"] is False
    assert dns.rebuild()["domain_count"] == 1
    assert agent.submitted == ["dns", "dns"]


def test_agent_rejects_unknown_verbs() -> None:
    with tempfile.TemporaryDirectory() as directory:
        agent = PrivilegedAgent(Path(directory) / "request", Path(directory) / "result")
        with pytest.raises(AgentError):
            agent.submit("rm -rf /")
        # Nothing was written: an unknown verb never reaches the queue.
        assert not (Path(directory) / "request").exists()


def test_agent_reports_missing_privileged_service() -> None:
    with tempfile.TemporaryDirectory() as directory:
        agent = PrivilegedAgent(Path(directory) / "request", Path(directory) / "result")
        with pytest.raises(AgentError, match="aucune réponse"):
            agent.submit("dns", timeout=0.2)
        assert "poweroff" in ACTIONS
        # Shutting down never waits for an answer that cannot arrive.
        assert agent.submit("poweroff")["status"] == "pending"
