"""The node half of the rack, exercised from here.

`packaging/agent/` runs as root on someone else's machine and is the only part
of OnionPi installed outside the appliance. It has no test suite of its own, so
these load the two programs by path and check what actually matters: that both
halves of the protocol compute the same signature, and that the policy renderer
refuses everything it should before a single nftables rule is written.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from onionpi.nodeclient import PROTOCOL_VERSION, sign
from onionpi.rack import clean_rules, policy_document

AGENT_DIR = Path(__file__).resolve().parents[2] / "packaging" / "agent"


def load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, AGENT_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def agent() -> ModuleType:
    return load("onionpi_node_agent", "onionpi-node-agent.py")


@pytest.fixture(scope="module")
def renderer() -> ModuleType:
    return load("onionpi_render_policy", "render-policy.py")


# --------------------------------------------------------------- protocol ---


def test_both_halves_compute_the_same_signature(agent: ModuleType) -> None:
    token = "a" * 64
    body = json.dumps({"digest": "0" * 64}, separators=(",", ":")).encode()
    timestamp = int(time.time())
    nonce = "b" * 16
    canonical = (
        f"{agent.PROTOCOL_VERSION}\napply-policy\n{timestamp}\n{nonce}\n"
        f"{hashlib.sha256(body).hexdigest()}"
    )
    expected = hmac.new(token.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    assert sign(token, "apply-policy", timestamp, nonce, body) == expected
    assert agent.PROTOCOL_VERSION == PROTOCOL_VERSION


def test_a_nonce_is_accepted_once(agent: ModuleType) -> None:
    guard = agent.ReplayGuard(size=4)
    assert guard.accept("premier") is True
    assert guard.accept("premier") is False
    # The memory is bounded, so an attacker cannot make it grow without end by
    # replaying: the oldest entries fall out, the clock window covers the rest.
    for index in range(8):
        guard.accept(f"nonce-{index}")
    assert len(guard._seen) <= 4


def test_the_agent_and_the_appliance_share_one_vocabulary(agent: ModuleType) -> None:
    from onionpi.nodeclient import AGENT_VERBS

    assert set(agent.VERBS) == set(AGENT_VERBS)


def test_the_policy_the_appliance_sends_is_one_the_agent_accepts(
    agent: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "POLICY_PATH", tmp_path / "policy.json")
    monkeypatch.setattr(
        agent.PrivilegedRequest, "submit", lambda self, action, timeout=25.0: {"status": "ok"}
    )
    document = policy_document(clean_rules({"keep_open_ports": [22, 443]}), False)
    result = agent.store_policy(document)
    assert result["applied"] is True
    stored = json.loads((tmp_path / "policy.json").read_text())
    assert stored["digest"] == document["digest"]
    assert stored["keep_open_ports"] == [22, 443]


@pytest.mark.parametrize(
    "broken",
    [
        {"egress": "clair", "digest": "0" * 64},
        {"egress": "tor-only", "digest": "trop court"},
        {"egress": "tor-only", "digest": "0" * 64, "keep_open_ports": [0]},
        {"egress": "tor-only", "digest": "0" * 64, "keep_open_ports": ["22"]},
        {"egress": "tor-only", "digest": "0" * 64, "exit_country": "suede"},
    ],
)
def test_the_agent_refuses_a_malformed_policy(
    agent: ModuleType, broken: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "POLICY_PATH", tmp_path / "policy.json")
    with pytest.raises(ValueError):
        agent.store_policy(broken)
    assert not (tmp_path / "policy.json").exists()


def test_only_listed_units_can_be_read(agent: ModuleType) -> None:
    with pytest.raises(ValueError):
        agent.read_journal("../../etc/shadow", 10)
    with pytest.raises(ValueError):
        agent.read_journal("nginx", 10)


# ----------------------------------------------------------- policy render --


def rendered(renderer: ModuleType, path: Path, rules: dict[str, Any], blocked: bool) -> str:
    path.write_text(json.dumps(policy_document(clean_rules(rules), blocked)), encoding="utf-8")
    return renderer.render(renderer.load(path), 9050)


def test_the_default_policy_drops_everything_that_is_not_tor(
    renderer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renderer, "account_id", lambda names: 42 if names != "onionpi-node" else 43)
    ruleset = rendered(renderer, tmp_path / "p.json", {}, False)
    assert "type filter hook output priority 0; policy drop;" in ruleset
    assert "meta skuid 42 accept" in ruleset
    assert "type filter hook input priority 0; policy drop;" in ruleset
    # The door left open on purpose: a VPS with no reachable port is a VPS lost.
    assert "tcp dport { 22 } accept" in ruleset
    assert "type filter hook forward priority 0; policy drop;" in ruleset


def test_isolation_cuts_applications_off_the_proxy_but_not_the_agent(
    renderer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renderer, "account_id", lambda names: 42 if names != "onionpi-node" else 43)
    ruleset = rendered(renderer, tmp_path / "p.json", {"access": "blocked"}, True)
    socks = 'oif "lo" tcp dport 9050 meta skuid != { 0, 42, 43 } drop'
    assert socks in ruleset
    # Before the rule that accepts loopback wholesale, or it never matches.
    assert ruleset.index(socks) < ruleset.index('    oif "lo" accept')


def test_direct_egress_is_an_exception_that_still_filters_what_comes_in(
    renderer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renderer, "account_id", lambda names: 42 if names != "onionpi-node" else 43)
    ruleset = rendered(renderer, tmp_path / "p.json", {"egress": "direct"}, False)
    assert "hook output priority 0; policy accept;" in ruleset
    assert "type filter hook input priority 0; policy drop;" in ruleset


@pytest.mark.parametrize(
    "broken",
    [
        {"version": 2, "egress": "tor-only", "digest": "0" * 64},
        {"version": 1, "egress": "rien", "digest": "0" * 64},
        {"version": 1, "egress": "tor-only", "digest": "0" * 63},
        {"version": 1, "egress": "tor-only", "digest": "0" * 64, "keep_open_ports": [1] * 9},
        {"version": 1, "egress": "tor-only", "digest": "0" * 64, "keep_open_ports": [True]},
    ],
)
def test_the_renderer_refuses_before_writing_a_rule(
    renderer: ModuleType, broken: dict[str, Any], tmp_path: Path
) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(SystemExit):
        renderer.load(path)


def test_a_node_without_tor_is_never_left_half_filtered(
    renderer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renderer, "account_id", lambda names: None)
    path = tmp_path / "p.json"
    path.write_text(json.dumps(policy_document(clean_rules({}), False)), encoding="utf-8")
    with pytest.raises(SystemExit):
        renderer.render(renderer.load(path), 9050)
