"""Client half of the rack node protocol.

A rack node is a machine that is not on the Wi-Fi: a VPS, a home server, a
second Pi. It runs `onionpi-node-agent`, which listens on loopback only and is
published as its own v3 onion service. Nothing about it is reachable from the
Internet — the agent has no port on any public interface — and this appliance
reaches it by dialling that address through Tor's SOCKS port.

Two independent locks guard the channel:

1. **Onion client authorisation.** The node encrypts its descriptor for the
   x25519 key of this appliance alone. Someone who learns the address, and even
   someone watching the directory system, cannot resolve it.
2. **A signed request.** Every call carries an HMAC-SHA256 signature over the
   verb, a timestamp, a nonce and the digest of the body. The agent refuses a
   stale timestamp and a repeated nonce, so a captured request cannot be
   replayed.

The verbs below are the whole vocabulary. The agent keeps the same list and
re-validates against its own copy: this one exists to fail early and to keep
the interface honest about what a node can be asked to do.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any

import httpx

logger = logging.getLogger("onionpi.nodeclient")

#: Verb -> (label, seconds allowed). Everything is short: a call travels
#: through six Tor hops and a slow node must not hold an HTTP worker.
AGENT_VERBS: dict[str, tuple[str, float]] = {
    "status": ("État du nœud", 25.0),
    "apply-policy": ("Application des règles", 40.0),
    "new-identity": ("Nouvelle identité Tor", 25.0),
    "restart-tor": ("Redémarrage de Tor", 40.0),
    "journal": ("Lecture du journal", 25.0),
    "reboot": ("Redémarrage du nœud", 15.0),
}

#: Verbs an operator may fire by hand from the interface. `apply-policy` is
#: absent on purpose: rules are pushed by the manager when they change, never
#: as a free-form command carrying a body chosen at the other end of a form.
MANUAL_VERBS = ("status", "new-identity", "restart-tor", "journal", "reboot")

PROTOCOL_VERSION = 1
MAX_RESPONSE_BYTES = 256 * 1024


class NodeError(RuntimeError):
    """The node could not be reached, or refused the call."""


def sign(token: str, verb: str, timestamp: int, nonce: str, body: bytes) -> str:
    """The signature both halves compute. Keep in step with the agent."""
    digest = hashlib.sha256(body).hexdigest()
    canonical = f"{PROTOCOL_VERSION}\n{verb}\n{timestamp}\n{nonce}\n{digest}"
    return hmac.new(token.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def _demo_status(name: str) -> dict[str, Any]:
    return {
        "agent_version": "0.4.1",
        "hostname": name.lower().replace(" ", "-")[:32] or "node",
        "uptime_seconds": 412_233,
        "load": 0.14,
        "memory_percent": 38.2,
        "storage_percent": 51.0,
        "tor": {"connected": True, "bootstrap": 100, "exit_country": "NL"},
        "policy": {"digest": "", "egress": "tor-only", "applied_at": 0},
        "services": [
            {"id": "tor", "label": "Tor", "active": True},
            {"id": "onionpi-node-agent", "label": "Agent OnionPi", "active": True},
        ],
    }


class NodeClient:
    """Speaks the agent protocol over Tor. Holds no state of its own."""

    def __init__(self, socks_port: int = 9050, demo_mode: bool = False) -> None:
        self.socks_port = socks_port
        self.demo_mode = demo_mode

    def call(
        self,
        *,
        node_id: str,
        name: str,
        onion: str,
        port: int,
        token: str,
        verb: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if verb not in AGENT_VERBS:
            raise NodeError(f"Action inconnue: {verb}")
        label, timeout = AGENT_VERBS[verb]
        if self.demo_mode:
            return self._demo_call(name, verb, payload or {})
        if not onion:
            raise NodeError("Ce nœud n’a pas encore d’adresse onion.")
        body = json.dumps(payload or {}, separators=(",", ":")).encode()
        timestamp = int(time.time())
        nonce = secrets.token_hex(16)
        headers = {
            "Content-Type": "application/json",
            "X-OnionPi-Version": str(PROTOCOL_VERSION),
            "X-OnionPi-Node": node_id,
            "X-OnionPi-Timestamp": str(timestamp),
            "X-OnionPi-Nonce": nonce,
            "X-OnionPi-Signature": sign(token, verb, timestamp, nonce, body),
        }
        url = f"http://{onion}.onion:{port}/agent/v1/{verb}"
        try:
            with httpx.Client(
                proxy=f"socks5h://127.0.0.1:{self.socks_port}", timeout=timeout
            ) as client:
                response = client.post(url, content=body, headers=headers)
        except Exception as error:  # httpx raises a whole family of transport errors
            # The address, the circuit and the agent all fail the same way from
            # here, and the distinction is not one an operator can act on.
            raise NodeError(f"{label}: nœud injoignable via Tor.") from error
        return self._decode(response, label)

    @staticmethod
    def _decode(response: httpx.Response, label: str) -> dict[str, Any]:
        raw = response.content[: MAX_RESPONSE_BYTES + 1]
        if len(raw) > MAX_RESPONSE_BYTES:
            raise NodeError(f"{label}: réponse anormalement longue, ignorée.")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NodeError(f"{label}: réponse illisible du nœud.") from error
        if not isinstance(document, dict):
            raise NodeError(f"{label}: réponse illisible du nœud.")
        if response.status_code == 401:
            raise NodeError(
                f"{label}: le nœud a refusé la signature. Le jeton a peut-être "
                "été renouvelé sans être réinstallé."
            )
        if response.status_code != 200:
            detail = str(document.get("detail", ""))[:200]
            raise NodeError(f"{label}: {detail or f'erreur {response.status_code}'}")
        return document

    def _demo_call(self, name: str, verb: str, payload: dict[str, Any]) -> dict[str, Any]:
        if verb == "status":
            return _demo_status(name)
        if verb == "journal":
            unit = str(payload.get("unit", "tor"))[:64]
            return {
                "unit": unit,
                "lines": [
                    f"août 13 04:00:01 {unit}[1]: démarrage (démonstration)",
                    f"août 13 04:00:04 {unit}[1]: Bootstrapped 100% (done)",
                ],
            }
        if verb == "apply-policy":
            return {
                "applied": True,
                "digest": str(payload.get("digest", "")),
                "message": "Règles appliquées (démonstration)",
            }
        return {"ok": True, "message": f"{AGENT_VERBS[verb][0]} (démonstration)"}
