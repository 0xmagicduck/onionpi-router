"""Client half of the rack node protocol.

A rack node is a machine that is not on the Wi-Fi: a VPS, a home server, a
second Pi. It runs `onionpi-node-agent`, which listens on loopback only and is
published as its own v3 onion service. Nothing about it is reachable from the
Internet — the agent has no port on any public interface — and this appliance
reaches it by dialling that address through Tor's SOCKS port.

Three independent locks guard the channel:

1. **Onion client authorisation.** The node encrypts its descriptor for the
   x25519 key of this appliance alone. Someone who learns the address, and even
   someone watching the directory system, cannot resolve it.
2. **A signed request.** Every call carries an HMAC-SHA256 signature over the
   protocol version, the node identifier, the verb, a timestamp, a nonce and
   the digest of the body. The agent refuses a stale timestamp and a repeated
   nonce, so a captured request cannot be replayed.
3. **A signed answer.** The node signs its response over the same nonce and
   timestamp plus the status code and the digest of the answer. Without it the
   circuit authenticates the *service*, not the agent: anything that manages to
   answer on that address — a squatter on the node's loopback port, an onion
   address mistyped into a node sheet — could feed this appliance invented
   readings, journal lines and policy digests, and the rack would file them as
   fact.

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

#: Version 2 signs the answer as well as the call, and binds both to the node
#: identifier. A version 1 agent cannot verify it and must be reinstalled — the
#: enrolment command is re-displayable, so that is one command per node.
PROTOCOL_VERSION = 2
MAX_RESPONSE_BYTES = 256 * 1024


class NodeError(RuntimeError):
    """The node could not be reached, or refused the call."""


def subkeys(token: str) -> tuple[bytes, bytes]:
    """(request key, response key) derived from the shared token.

    Domain separation, so the two directions never accept each other's
    signatures: without it a captured answer is a ready-made call.
    """
    root = token.encode()
    return (
        hmac.new(root, b"onionpi-node/2/request", hashlib.sha256).digest(),
        hmac.new(root, b"onionpi-node/2/response", hashlib.sha256).digest(),
    )


def sign_request(
    token: str, node_id: str, verb: str, timestamp: int, nonce: str, body: bytes
) -> str:
    """The call signature both halves compute. Keep in step with the agent."""
    key, _ = subkeys(token)
    canonical = "\n".join(
        (
            str(PROTOCOL_VERSION),
            node_id,
            verb,
            str(timestamp),
            nonce,
            hashlib.sha256(body).hexdigest(),
        )
    )
    return hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()


def sign_response(
    token: str,
    node_id: str,
    verb: str,
    timestamp: int,
    nonce: str,
    status: int,
    body: bytes,
) -> str:
    """The answer signature. Bound to the call's own nonce and timestamp, so an
    answer cannot be lifted from one exchange and served in another."""
    _, key = subkeys(token)
    canonical = "\n".join(
        (
            str(PROTOCOL_VERSION),
            node_id,
            verb,
            str(timestamp),
            nonce,
            str(status),
            hashlib.sha256(body).hexdigest(),
        )
    )
    return hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()


def _demo_status(name: str) -> dict[str, Any]:
    return {
        "agent_version": "0.4.2",
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
        "platform": {
            "system": "Linux",
            "release": "6.6",
            "machine": "aarch64",
            "policy_mode": "complet",
        },
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
            "X-OnionPi-Signature": sign_request(
                token, node_id, verb, timestamp, nonce, body
            ),
        }
        url = f"http://{onion}.onion:{port}/agent/v1/{verb}"
        try:
            with httpx.Client(
                proxy=f"socks5h://127.0.0.1:{self.socks_port}", timeout=timeout
            ) as client:
                # Streamed, so a hostile answer cannot be held in memory in full
                # before its length is judged.
                with client.stream(
                    "POST", url, content=body, headers=headers
                ) as response:
                    raw = self._read_capped(response, label)
                    status = response.status_code
                    signature = response.headers.get("X-OnionPi-Signature", "")
        except NodeError:
            raise
        except Exception as error:  # httpx raises a whole family of transport errors
            # The address, the circuit and the agent all fail the same way from
            # here, and the distinction is not one an operator can act on.
            raise NodeError(f"{label}: nœud injoignable via Tor.") from error
        expected = sign_response(token, node_id, verb, timestamp, nonce, status, raw)
        if not hmac.compare_digest(signature, expected):
            # Nothing below this line may look at the body: an answer this
            # appliance cannot attribute to the agent is not a fact about the
            # node, whatever it claims about itself.
            raise NodeError(self._unattributed(label, status, bool(signature)))
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NodeError(f"{label}: réponse illisible du nœud.") from error
        if not isinstance(document, dict):
            raise NodeError(f"{label}: réponse illisible du nœud.")
        if status != 200:
            detail = str(document.get("detail", ""))[:200]
            raise NodeError(f"{label}: {detail or f'erreur {status}'}")
        return document

    @staticmethod
    def _read_capped(response: httpx.Response, label: str) -> bytes:
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise NodeError(f"{label}: réponse anormalement longue, ignorée.")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _unattributed(label: str, status: int, signed: bool) -> str:
        if status == 401 and not signed:
            # The agent leaves its refusal unsigned on purpose: it has no reason
            # to mint a signature for a caller it just failed to recognise.
            return (
                f"{label}: le nœud a refusé la signature. Le jeton a peut-être "
                "été renouvelé sans être réinstallé."
            )
        return (
            f"{label}: réponse non authentifiée (agent trop ancien pour le "
            "protocole v2, ou adresse onion qui n’est pas celle de ce nœud). "
            "Réinstallez l’agent depuis « Préparer l’installation »."
        )

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
