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
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from onionpi.nodeclient import PROTOCOL_VERSION, sign_request, sign_response
from onionpi.rack import bundle_digest, clean_rules, policy_document

AGENT_DIR = Path(__file__).resolve().parents[2] / "packaging" / "agent"


def load(name: str, filename: str) -> ModuleType:
    # The agent imports its two mesh modules by plain name. On a node they sit
    # beside it in /usr/local/lib/onionpi-node, which is `sys.path[0]` for a
    # program run from there; loading by path here has to reproduce that.
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    spec = importlib.util.spec_from_file_location(name, AGENT_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # No __pycache__ beside the installer. It is content the release would
    # carry into the appliance's agent copy, and the digest that pins a node's
    # download is taken over every file in that directory: a bytecode cache
    # there is an install that fails closed for a reason nobody can see.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


@pytest.fixture(scope="module")
def agent() -> ModuleType:
    return load("onionpi_node_agent", "onionpi-node-agent.py")


@pytest.fixture(scope="module")
def renderer() -> ModuleType:
    return load("onionpi_render_policy", "render-policy.py")


@pytest.fixture(scope="module")
def macos_renderer() -> ModuleType:
    return load("onionpi_render_policy_macos", "render-policy-macos.py")


# --------------------------------------------------------------- protocol ---


def test_both_halves_compute_the_same_signature(agent: ModuleType) -> None:
    token = "a" * 64
    node = "0" * 16
    body = json.dumps({"digest": "0" * 64}, separators=(",", ":")).encode()
    timestamp = int(time.time())
    nonce = "b" * 16
    canonical = (
        f"{agent.PROTOCOL_VERSION}\n{node}\napply-policy\n{timestamp}\n{nonce}\n"
        f"{hashlib.sha256(body).hexdigest()}"
    )
    key = hmac.new(token.encode(), b"onionpi-node/2/request", hashlib.sha256).digest()
    expected = hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()
    assert sign_request(token, node, "apply-policy", timestamp, nonce, body) == expected
    assert agent.sign_request(token, node, "apply-policy", timestamp, nonce, body) == expected
    assert agent.PROTOCOL_VERSION == PROTOCOL_VERSION


def test_both_halves_compute_the_same_answer_signature(agent: ModuleType) -> None:
    token = "a" * 64
    node = "0" * 16
    body = b'{"ok":true}'
    assert agent.sign_response(token, node, "status", 1_700_000_000, "b" * 16, 200, body) == (
        sign_response(token, node, "status", 1_700_000_000, "b" * 16, 200, body)
    )


def test_the_policy_document_version_is_not_the_protocol_version(
    agent: ModuleType, renderer: ModuleType, macos_renderer: ModuleType
) -> None:
    """The two used to be one constant, and bumping the protocol silently made
    every privileged renderer refuse the policy it was handed.

    They now happen to hold the same number, so equality proves nothing either
    way. What must hold is that both halves and all three privileged renderers
    read the *policy* version — a renderer left behind refuses every policy it
    is handed, and a node stops being filtered at all.
    """
    from onionpi.rack import POLICY_VERSION as APPLIANCE_POLICY_VERSION

    assert agent.POLICY_VERSION == APPLIANCE_POLICY_VERSION
    assert renderer.POLICY_VERSION == APPLIANCE_POLICY_VERSION
    assert macos_renderer.POLICY_VERSION == APPLIANCE_POLICY_VERSION
    windows = (AGENT_DIR / "onionpi-node-apply-windows.ps1").read_text(encoding="utf-8")
    assert f"$policy.version -ne {APPLIANCE_POLICY_VERSION}" in windows
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


def call_headers(
    agent: ModuleType,
    *,
    token: str,
    node: str,
    verb: str,
    body: bytes,
    nonce: str,
    signature: str | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    stamp = int(time.time()) if timestamp is None else timestamp
    return {
        "X-OnionPi-Version": str(agent.PROTOCOL_VERSION),
        "X-OnionPi-Node": node,
        "X-OnionPi-Timestamp": str(stamp),
        "X-OnionPi-Nonce": nonce,
        "X-OnionPi-Signature": (
            signature
            if signature is not None
            else agent.sign_request(token, node, verb, stamp, nonce, body)
        ),
    }


def authenticator(agent: ModuleType, token: str, node: str) -> Any:
    """A handler with no socket under it: only `authenticate` is exercised."""
    agent.Handler.config = {"NODE_ID": node, "TOKEN": token}
    agent.Handler.replay = agent.ReplayGuard(size=8)

    def authenticate(headers: dict[str, str], verb: str, body: bytes) -> Any:
        handler = agent.Handler.__new__(agent.Handler)
        handler.headers = headers
        return handler.authenticate(verb, body)

    return authenticate


def test_an_unsigned_call_does_not_spend_the_nonce(agent: ModuleType) -> None:
    """The replay memory is a resource, and it used to be spendable for free.

    `accept()` ran before the signature was checked, so anyone able to reach
    the onion could push 512 invented nonces through, evict the record of a
    captured call, and replay it inside the clock window. Nothing an
    unauthenticated caller sends may touch that memory.
    """
    token, node, body = "a" * 64, "0" * 16, b"{}"
    authenticate = authenticator(agent, token, node)
    nonce = "c" * 16

    forged = call_headers(
        agent, token=token, node=node, verb="status", body=body, nonce=nonce,
        signature="0" * 64,
    )
    assert authenticate(forged, "status", body) is None

    # The genuine call reusing that nonce still goes through: the failed one
    # never got to record it.
    genuine = call_headers(
        agent, token=token, node=node, verb="status", body=body, nonce=nonce
    )
    assert authenticate(genuine, "status", body) is not None
    # And now it is spent, once.
    assert authenticate(genuine, "status", body) is None


def test_a_call_signed_for_another_node_is_refused(agent: ModuleType) -> None:
    token, node, body = "a" * 64, "0" * 16, b"{}"
    authenticate = authenticator(agent, token, node)
    stamp = int(time.time())
    headers = call_headers(
        agent, token=token, node=node, verb="status", body=body, nonce="d" * 16,
        signature=agent.sign_request(token, "1" * 16, "status", stamp, "d" * 16, body),
        timestamp=stamp,
    )
    assert authenticate(headers, "status", body) is None


def test_a_call_signed_for_another_verb_is_refused(agent: ModuleType) -> None:
    token, node, body = "a" * 64, "0" * 16, b"{}"
    authenticate = authenticator(agent, token, node)
    stamp = int(time.time())
    headers = call_headers(
        agent, token=token, node=node, verb="status", body=body, nonce="e" * 16,
        signature=agent.sign_request(token, node, "reboot", stamp, "e" * 16, body),
        timestamp=stamp,
    )
    assert authenticate(headers, "status", body) is None


def test_a_stale_call_is_refused(agent: ModuleType) -> None:
    token, node, body = "a" * 64, "0" * 16, b"{}"
    authenticate = authenticator(agent, token, node)
    stale = int(time.time()) - agent.MAX_CLOCK_SKEW - 1
    headers = call_headers(
        agent, token=token, node=node, verb="status", body=body, nonce="f" * 16,
        timestamp=stale,
    )
    assert authenticate(headers, "status", body) is None


def test_a_version_1_call_is_refused(agent: ModuleType) -> None:
    """Version 1 left the answer unsigned. Accepting it here would let a node
    be talked down to a protocol whose replies nobody can attribute."""
    token, node, body = "a" * 64, "0" * 16, b"{}"
    authenticate = authenticator(agent, token, node)
    headers = call_headers(
        agent, token=token, node=node, verb="status", body=body, nonce="a" * 16
    )
    headers["X-OnionPi-Version"] = "1"
    assert authenticate(headers, "status", body) is None


# ------------------------------------------------------- bundle pinning ---


def shell_bundle_digest(directory: Path) -> str:
    result = subprocess.run(
        [
            "bash",
            str(AGENT_DIR / "bootstrap-node.sh"),
            "--print-bundle-digest",
            str(directory),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_the_shell_and_the_appliance_hash_a_bundle_the_same_way() -> None:
    """Two implementations of one manifest that drift apart verify nothing.

    The appliance prints the digest of its own reviewed copy of the installer;
    the bootstrap recomputes it on the node from the archive it just fetched.
    If the two ever disagree on ordering, on the separator or on which files
    count, every install fails closed — and the next fix is to weaken the
    check.
    """
    assert shell_bundle_digest(AGENT_DIR) == bundle_digest(AGENT_DIR)


def test_a_bundle_digest_notices_a_changed_file(tmp_path: Path) -> None:
    tree = tmp_path / "agent"
    (tree / "systemd").mkdir(parents=True)
    (tree / "onionpi-node-agent.py").write_text("print('a')\n", encoding="utf-8")
    (tree / "systemd" / "unit.service").write_text("[Unit]\n", encoding="utf-8")
    before = bundle_digest(tree)
    assert before == shell_bundle_digest(tree)

    (tree / "onionpi-node-agent.py").write_text("print('b')\n", encoding="utf-8")
    after = bundle_digest(tree)
    assert after != before
    assert after == shell_bundle_digest(tree)

    # A file added anywhere in the tree is a different bundle: a payload
    # dropped beside the installer is exactly what this catches.
    (tree / "systemd" / "extra.service").write_text("[Unit]\n", encoding="utf-8")
    assert bundle_digest(tree) not in (before, after)
    assert bundle_digest(tree) == shell_bundle_digest(tree)


def test_a_bundle_digest_is_empty_without_a_reviewed_copy(tmp_path: Path) -> None:
    assert bundle_digest(tmp_path / "absent") == ""
    (tmp_path / "vide").mkdir()
    assert bundle_digest(tmp_path / "vide") == ""


def test_the_bootstrap_refuses_to_run_unpinned() -> None:
    """No digest and no explicit opt-out means nothing is executed."""
    result = subprocess.run(
        ["bash", str(AGENT_DIR / "bootstrap-node.sh"), "--platform", "linux"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "empreinte" in result.stderr.lower()


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
        {"version": 3, "egress": "tor-only", "digest": "0" * 64},
        {"version": 1, "egress": "tor-only", "digest": "0" * 64},
        {"version": 2, "egress": "rien", "digest": "0" * 64},
        {"version": 2, "egress": "tor-only", "digest": "0" * 63},
        {"version": 2, "egress": "tor-only", "digest": "0" * 64, "keep_open_ports": [1] * 9},
        {"version": 2, "egress": "tor-only", "digest": "0" * 64, "keep_open_ports": [True]},
        {"version": 2, "egress": "tor-only", "digest": "0" * 64, "mesh_port": 70000},
        {"version": 2, "egress": "tor-only", "digest": "0" * 64, "mesh_port": True},
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


def test_macos_policy_redirects_tcp_and_dns_then_blocks_direct_output(
    macos_renderer: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps(policy_document(clean_rules({}), False)), encoding="utf-8")
    ruleset = macos_renderer.render(macos_renderer.load(path), 19050, 19052, 19053)
    assert "rdr on lo0 inet proto tcp" in ruleset
    assert "port 19052" in ruleset
    assert "rdr on lo0 inet proto udp" in ruleset
    assert "port 19053" in ruleset
    assert "route-to (lo0 127.0.0.1) inet proto tcp" in ruleset
    assert "to any port 53 user != _onionpi-node" in ruleset
    assert "to <onionpi_tor_virtual4> user != _onionpi-node" in ruleset
    assert ruleset.index("to any port 53 user != _onionpi-node") < ruleset.index(
        "pass out quick inet to <onionpi_local4>"
    )
    assert "block return out all" in ruleset
    assert "pass out quick inet proto { tcp, udp } user _onionpi-node" in ruleset
    assert "pass in quick proto tcp to port { 22 }" in ruleset


def test_macos_isolation_precedes_the_loopback_allow(
    macos_renderer: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "p.json"
    path.write_text(
        json.dumps(policy_document(clean_rules({"access": "blocked"}), True)),
        encoding="utf-8",
    )
    ruleset = macos_renderer.render(macos_renderer.load(path), 19050, 19052, 19053)
    isolation = "block return quick on lo0 proto tcp to port 19050 user != _onionpi-node"
    assert ruleset.index(isolation) < ruleset.index("pass quick on lo0 all")


def test_windows_refuses_tor_only_instead_of_cutting_the_machine_off() -> None:
    script = (AGENT_DIR / "onionpi-node-apply-windows.ps1").read_text(encoding="ascii")
    refusal = 'Tor-only indisponible sur Windows: aucune interface TUN configuree'
    assert refusal in script
    assert "DefaultOutboundAction Block" not in script
    installer = (AGENT_DIR / "install-node-agent-windows.ps1").read_text(encoding="ascii")
    assert "DefaultOutboundAction $profile.DefaultOutboundAction" in installer
    assert 'Get-NetFirewallRule -Group "OnionPi Node"' in installer
    assert "Windows reste en sortie directe" in installer


def test_every_windows_script_is_ascii_and_bootstrap_parses_from_memory() -> None:
    scripts = list(AGENT_DIR.glob("*.ps1"))
    assert scripts
    for path in scripts:
        path.read_bytes().decode("ascii")
    bootstrap = (AGENT_DIR / "bootstrap-node.ps1").read_text(encoding="ascii")
    assert "[IO.File]::ReadAllBytes($installer.FullName)" in bootstrap
    assert "[ScriptBlock]::Create($installerText)" in bootstrap
    assert bootstrap.index("ScriptBlock]::Create") < bootstrap.index("& $installerBlock")
