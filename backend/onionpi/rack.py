"""Virtual network rack: one topology for the Wi-Fi and for remote machines.

A household appliance and a data centre differ less in what they do than in how
they are read. A rack is the reading: frames, slots, a machine in each slot,
and a rule sheet attached to every machine. This module gives OnionPi that
reading, over two kinds of node.

*Local* nodes are the Wi-Fi clients already known to `DeviceGuard` and
`DeviceAccessManager`. Placing one in a rack adds no enforcement path: the
firewall keeps applying exactly the same intent, the rack only names and
arranges it. Everything a local node's rules can express is delegated back to
those two managers, so there is never a second answer to "is this device
blocked".

*Remote* nodes are machines this appliance does not route: a VPS, a home
server. They run `onionpi-node-agent`, listen on loopback behind their own v3
onion service, and are reached through Tor (`nodeclient.py`). Their rules are
pushed to the agent, which applies a kill switch of its own — outbound traffic
is dropped unless it belongs to Tor. A node in this rack is Tor-only in the
same sense the Wi-Fi is.

No credential is stored. A node's agent token and its onion client key are
derived from one master secret, the node identifier and a rotation counter:

    token = HMAC-SHA256(master, "<node>:<epoch>:token")
    x25519 seed = HMAC-SHA256(master, "<node>:<epoch>:auth")

A database copy, a configuration export or a backup therefore carries nothing
that opens a node, and rotating one node's credentials is an increment.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from .access import AccessError, DeviceAccessManager, _clean_schedule, _schedule_allows
from .atomic_io import atomic_write_text
from .database import Database
from .netcontrol import DeviceGuard, NetControlError, normalize_mac
from .nodeclient import AGENT_VERBS, MANUAL_VERBS, NodeClient, NodeError
from .tor_control import TorControlError, TorController

logger = logging.getLogger("onionpi.rack")

#: A Raspberry Pi is not a hyperscaler. These ceilings keep the topology
#: readable on a phone and the monitoring loop inside the appliance's budget.
MAX_RACKS = 8
MAX_NODES = 64
MAX_UNITS = 42
DEFAULT_UNITS = 12
MAX_KEEP_OPEN_PORTS = 8

#: How the monitor spends its time: a tick every minute, a handful of nodes per
#: tick, oldest reading first. A full rack is swept in about ten minutes, and
#: no tick can hold more than three sockets open at once.
MONITOR_TICK_SECONDS = 60
MONITOR_BATCH = 6
MONITOR_WORKERS = 3

NAME_PATTERN = re.compile(r"^[\w .'’()\-]{1,48}$", re.UNICODE)
ROLE_PATTERN = re.compile(r"^[\w .'’()\-]{0,32}$", re.UNICODE)
ONION_PATTERN = re.compile(r"^[a-z2-7]{56}$")
ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
RACK_ID_PATTERN = re.compile(r"^[0-9a-f]{8}$")
COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")

NODE_KINDS = ("local", "remote")
EGRESS_MODES = ("tor-only", "direct")
ACCESS_MODES = ("allowed", "blocked")

POLICY_VERSION = 1

#: Left open on the node's own INPUT chain whatever else the policy says.
#: Locking an operator out of a machine on the other side of the planet, with
#: no console, is a worse outcome than the port being reachable.
DEFAULT_KEEP_OPEN_PORTS = (22,)


class RackError(RuntimeError):
    pass


def _node_id() -> str:
    return secrets.token_hex(8)


def _rack_id() -> str:
    return secrets.token_hex(4)


def clean_name(value: str, field: str = "Nom") -> str:
    cleaned = " ".join(value.split())[:48]
    if not NAME_PATTERN.fullmatch(cleaned):
        raise RackError(f"{field} invalide: lettres, chiffres, espace, - _ ( ) et '.")
    return cleaned


def clean_onion(value: str) -> str:
    """Accepts `abc….onion`, `http://abc….onion` or the bare service id."""
    cleaned = value.strip().lower()
    cleaned = cleaned.removeprefix("http://").removeprefix("https://")
    cleaned = cleaned.split("/", 1)[0].split(":", 1)[0].removesuffix(".onion")
    if cleaned and not ONION_PATTERN.fullmatch(cleaned):
        raise RackError("Adresse onion invalide: 56 caractères, sans le suffixe .onion.")
    return cleaned


def clean_rules(raw: Any) -> dict[str, Any]:
    """Validates a rule sheet. Unknown keys are dropped, never carried along."""
    document = raw if isinstance(raw, dict) else {}
    access = str(document.get("access", "allowed"))
    if access not in ACCESS_MODES:
        raise RackError("Accès invalide: « allowed » ou « blocked ».")
    egress = str(document.get("egress", "tor-only"))
    if egress not in EGRESS_MODES:
        raise RackError("Sortie invalide: « tor-only » ou « direct ».")
    # Not truncated to two characters: « Suède » would silently become « SU »,
    # which is a country nobody chose and a circuit nobody expects.
    country = str(document.get("exit_country", "")).strip().upper()
    if country and not COUNTRY_PATTERN.fullmatch(country):
        raise RackError("Pays de sortie invalide: deux lettres, par exemple SE.")
    ports: list[int] = []
    for item in list(document.get("keep_open_ports", []) or [])[:MAX_KEEP_OPEN_PORTS]:
        try:
            port = int(item)
        except (TypeError, ValueError) as error:
            raise RackError("Port invalide: entier entre 1 et 65535.") from error
        if not 1 <= port <= 65535:
            raise RackError("Port invalide: entier entre 1 et 65535.")
        if port not in ports:
            ports.append(port)
    try:
        schedule = _clean_schedule(document.get("schedule"))
    except AccessError as error:
        raise RackError(str(error)) from error
    return {
        "access": access,
        "egress": egress,
        "exit_country": country,
        "keep_open_ports": ports or list(DEFAULT_KEEP_OPEN_PORTS),
        "schedule": schedule,
    }


def policy_document(rules: dict[str, Any], blocked: bool) -> dict[str, Any]:
    """The exact document handed to the agent, digest included.

    The digest is computed over the canonical form so both sides can tell
    whether the node already runs these rules without comparing field by field.
    """
    body = {
        "version": POLICY_VERSION,
        "egress": rules["egress"],
        "exit_country": rules["exit_country"],
        "keep_open_ports": sorted(rules["keep_open_ports"]),
        "isolated": bool(blocked),
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return {**body, "digest": hashlib.sha256(canonical).hexdigest()}


class RackManager:
    """Owns the topology, derives node credentials and drives remote agents."""

    def __init__(
        self,
        database: Database,
        key_path: Path,
        guard: DeviceGuard,
        access: DeviceAccessManager,
        client: NodeClient,
        controller: TorController,
        demo_mode: bool = False,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        self.database = database
        self.key_path = key_path
        self.guard = guard
        self.access = access
        self.client = client
        self.controller = controller
        self.demo_mode = demo_mode
        self.on_event = on_event
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ----------------------------------------------------------- secrets ---

    def _master(self) -> bytes:
        """Reads the master secret, creating it on first use.

        Losing this file does not lose the rack: it invalidates every node
        credential at once, and each node has to be re-enrolled. That is the
        same trade the onion service key makes, and it is why the file lives
        next to it under the application's own directory.
        """
        try:
            raw = self.key_path.read_text().strip()
        except OSError:
            raw = ""
        if len(raw) == 64 and re.fullmatch(r"[0-9a-f]{64}", raw):
            return bytes.fromhex(raw)
        secret = secrets.token_hex(32)
        try:
            atomic_write_text(self.key_path, secret + "\n", mode=0o600)
        except OSError as error:
            raise RackError(
                f"Écriture impossible du secret de baie {self.key_path}: {error}"
            ) from error
        return bytes.fromhex(secret)

    def _derive(self, node_id: str, epoch: int, purpose: str) -> bytes:
        return hmac.new(
            self._master(), f"{node_id}:{epoch}:{purpose}".encode(), hashlib.sha256
        ).digest()

    def token_for(self, node_id: str, epoch: int) -> str:
        return self._derive(node_id, epoch, "token").hex()

    def client_keypair(self, node_id: str, epoch: int) -> tuple[str, str]:
        """(private, public) x25519 keys in the spellings Tor asks for.

        The private half goes to the control port as base64 — that is what
        `ONION_CLIENT_AUTH_ADD` reads. The public half goes into the node's
        `authorized_clients` directory as base32, which is what a hidden
        service reads. Same key, two dialects, and Tor is strict about both.
        """
        private = X25519PrivateKey.from_private_bytes(
            self._derive(node_id, epoch, "auth")
        )
        raw_private = private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        raw_public = private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return (
            base64.b64encode(raw_private).decode("ascii"),
            base64.b32encode(raw_public).decode("ascii").rstrip("="),
        )

    # ------------------------------------------------------------- state ---

    @staticmethod
    def _json(raw: Any, fallback: Any) -> Any:
        try:
            value = json.loads(str(raw or ""))
        except json.JSONDecodeError:
            return fallback
        return value if isinstance(value, type(fallback)) else fallback

    def _node_view(
        self,
        row: dict[str, Any],
        now: int,
        moment: time.struct_time,
        blocked_macs: set[str] | None = None,
    ) -> dict[str, Any]:
        try:
            rules = clean_rules(self._json(row.get("rules"), {}))
        except RackError:
            # A row hand-edited into nonsense must not take the page down; the
            # safe reading is the default sheet, which blocks nothing.
            rules = clean_rules({})
        state = self._json(row.get("state"), {})
        blocked = self._desired_block(rules, now, moment)
        view = {
            "id": str(row["id"]),
            "rack_id": str(row["rack_id"] or ""),
            "position": int(row["position"] or 0),
            "kind": str(row["kind"]),
            "name": str(row["name"]),
            "role": str(row["role"] or ""),
            "mac": str(row["mac"] or ""),
            "onion": str(row["onion"] or ""),
            "address": f"{row['onion']}.onion" if row["onion"] else "",
            "agent_port": int(row["agent_port"] or 0),
            "token_epoch": int(row["token_epoch"] or 1),
            "client_auth": bool(row["client_auth"]),
            "notes": str(row["notes"] or ""),
            "rules": rules,
            "state": state,
            "last_seen": int(row["last_seen"] or 0),
            "last_error": str(row["last_error"] or ""),
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
            "policy_digest": policy_document(rules, blocked)["digest"],
        }
        if blocked_macs is None:
            blocked_macs = self.guard.blocked_macs()
        view["status"] = self._status_of(view, blocked, now, blocked_macs)
        return view

    def _desired_block(
        self, rules: dict[str, Any], now: int, moment: time.struct_time
    ) -> bool:
        if rules["access"] == "blocked":
            return True
        schedule = rules["schedule"]
        return bool(schedule) and not _schedule_allows(schedule, moment)

    @staticmethod
    def _status_of(
        view: dict[str, Any], blocked: bool, now: int, blocked_macs: set[str]
    ) -> str:
        """What the slot shows: isolation first, then reachability."""
        if blocked:
            return "isolated"
        if view["kind"] == "local":
            return "isolated" if view["mac"] in blocked_macs else "online"
        if not view["onion"]:
            return "pending"
        if not view["last_seen"]:
            return "unknown"
        # Three missed sweeps. One slow circuit is not an outage.
        return "online" if now - view["last_seen"] < MONITOR_TICK_SECONDS * 10 else "offline"

    def snapshot(self) -> dict[str, Any]:
        now = int(time.time())
        moment = time.localtime(now)
        blocked_macs = self.guard.blocked_macs()
        nodes = [
            self._node_view(row, now, moment, blocked_macs)
            for row in self.database.rack_nodes()
        ]
        racks = []
        for rack in self.database.racks():
            occupied = sum(
                1 for node in nodes if node["rack_id"] == rack["id"] and node["position"]
            )
            racks.append({**rack, "occupied": occupied})
        return {
            "racks": racks,
            "nodes": nodes,
            "limits": {
                "max_racks": MAX_RACKS,
                "max_nodes": MAX_NODES,
                "max_units": MAX_UNITS,
                "default_units": DEFAULT_UNITS,
            },
            "verbs": [
                {"id": verb, "label": AGENT_VERBS[verb][0]} for verb in MANUAL_VERBS
            ],
            "egress_modes": list(EGRESS_MODES),
            "now": now,
        }

    def _require_node(self, node_id: str) -> dict[str, Any]:
        if not ID_PATTERN.fullmatch(node_id):
            raise RackError("Identifiant de nœud invalide.")
        row = self.database.rack_node(node_id)
        if row is None:
            raise RackError("Ce nœud n’existe pas.")
        return row

    def node(self, node_id: str) -> dict[str, Any]:
        now = int(time.time())
        return self._node_view(self._require_node(node_id), now, time.localtime(now))

    # -------------------------------------------------------------- racks --

    def create_rack(self, name: str, location: str, units: int) -> dict[str, Any]:
        label = clean_name(name, "Nom de la baie")
        place = " ".join(location.split())[:48]
        if place and not ROLE_PATTERN.fullmatch(place):
            raise RackError("Emplacement invalide.")
        if not 1 <= units <= MAX_UNITS:
            raise RackError(f"Une baie compte de 1 à {MAX_UNITS} U.")
        with self._lock:
            if len(self.database.racks()) >= MAX_RACKS:
                raise RackError(f"{MAX_RACKS} baies au maximum.")
            self.database.create_rack(_rack_id(), label, place, units)
        self._notify("secure", f"Baie « {label} » créée")
        return self.snapshot()

    def update_rack(self, rack_id: str, name: str, location: str, units: int) -> dict[str, Any]:
        if not RACK_ID_PATTERN.fullmatch(rack_id):
            raise RackError("Identifiant de baie invalide.")
        label = clean_name(name, "Nom de la baie")
        place = " ".join(location.split())[:48]
        if place and not ROLE_PATTERN.fullmatch(place):
            raise RackError("Emplacement invalide.")
        if not 1 <= units <= MAX_UNITS:
            raise RackError(f"Une baie compte de 1 à {MAX_UNITS} U.")
        with self._lock:
            if self.database.rack(rack_id) is None:
                raise RackError("Cette baie n’existe pas.")
            highest = max(
                (
                    int(row["position"] or 0)
                    for row in self.database.rack_nodes()
                    if str(row["rack_id"] or "") == rack_id
                ),
                default=0,
            )
            if units < highest:
                raise RackError(
                    f"La baie contient un nœud en U{highest}: videz-le avant de la réduire."
                )
            self.database.update_rack(rack_id, label, place, units)
        return self.snapshot()

    def delete_rack(self, rack_id: str) -> dict[str, Any]:
        if not RACK_ID_PATTERN.fullmatch(rack_id):
            raise RackError("Identifiant de baie invalide.")
        with self._lock:
            rack = self.database.rack(rack_id)
            if rack is None:
                raise RackError("Cette baie n’existe pas.")
            self.database.delete_rack(rack_id)
        self._notify("secure", f"Baie « {rack['name']} » supprimée")
        return self.snapshot()

    # -------------------------------------------------------------- nodes --

    def create_node(
        self,
        kind: str,
        name: str,
        role: str = "",
        mac: str = "",
        onion: str = "",
        agent_port: int = 9080,
        notes: str = "",
        rules: Any = None,
    ) -> dict[str, Any]:
        """Adds a machine to the inventory. It starts unracked, in no slot."""
        if kind not in NODE_KINDS:
            raise RackError("Type de nœud invalide.")
        label = clean_name(name)
        title = " ".join(role.split())[:32]
        if title and not ROLE_PATTERN.fullmatch(title):
            raise RackError("Rôle invalide.")
        if not 1 <= agent_port <= 65535:
            raise RackError("Port de l’agent invalide.")
        address = clean_onion(onion) if kind == "remote" else ""
        hardware = ""
        if kind == "local":
            try:
                hardware = normalize_mac(mac)
            except ValueError as error:
                raise RackError(str(error)) from error
        sheet = clean_rules(rules)
        node_id = _node_id()
        with self._lock:
            rows = self.database.rack_nodes()
            if len(rows) >= MAX_NODES:
                raise RackError(f"{MAX_NODES} nœuds au maximum dans la baie virtuelle.")
            if hardware and any(str(row["mac"] or "") == hardware for row in rows):
                raise RackError("Cet appareil occupe déjà un emplacement.")
            if address and any(str(row["onion"] or "") == address for row in rows):
                raise RackError("Cette adresse onion est déjà enregistrée.")
            self.database.create_rack_node(
                node_id,
                kind,
                {
                    "name": label,
                    "role": title,
                    "mac": hardware,
                    "onion": address,
                    "agent_port": agent_port,
                    "notes": " ".join(notes.split())[:200],
                    "rules": json.dumps(sheet),
                },
            )
        self._apply_local(node_id)
        if address:
            self._register_client_auth(node_id)
        self._notify("device", f"Nœud « {label} » ajouté à la baie virtuelle")
        return self.node(node_id)

    def update_node(
        self,
        node_id: str,
        name: str,
        role: str = "",
        onion: str = "",
        agent_port: int = 0,
        notes: str = "",
    ) -> dict[str, Any]:
        row = self._require_node(node_id)
        label = clean_name(name)
        title = " ".join(role.split())[:32]
        if title and not ROLE_PATTERN.fullmatch(title):
            raise RackError("Rôle invalide.")
        values: dict[str, Any] = {
            "name": label,
            "role": title,
            "notes": " ".join(notes.split())[:200],
        }
        if str(row["kind"]) == "remote":
            address = clean_onion(onion)
            if agent_port and not 1 <= agent_port <= 65535:
                raise RackError("Port de l’agent invalide.")
            values["onion"] = address
            if agent_port:
                values["agent_port"] = agent_port
            if address != str(row["onion"] or ""):
                # A new address needs its own authorisation, and the reading
                # kept from the previous one says nothing about this machine.
                values["client_auth"] = 0
                values["state"] = "{}"
                values["last_seen"] = 0
                values["last_error"] = ""
        previous = str(row["onion"] or "")
        with self._lock:
            self.database.update_rack_node(node_id, values)
        if "client_auth" in values:
            # Tor keeps one credential per address: leaving the old one behind
            # would keep an address this appliance no longer administers
            # resolvable from it.
            self._forget_client_auth(previous)
            if values.get("onion"):
                self._register_client_auth(node_id)
        self._apply_local(node_id)
        return self.node(node_id)

    def delete_node(self, node_id: str) -> dict[str, Any]:
        row = self._require_node(node_id)
        with self._lock:
            self.database.delete_rack_node(node_id)
        self._forget_client_auth(str(row["onion"] or ""))
        # The rack stops describing this machine; the firewall must stop acting
        # on a sheet nobody can see any more.
        if str(row["kind"]) == "local" and row["mac"]:
            try:
                self.access.remove(str(row["mac"]))
            except AccessError:
                pass
        self._notify("device", f"Nœud « {row['name']} » retiré de la baie virtuelle")
        return self.snapshot()

    def move_node(self, node_id: str, rack_id: str, position: int) -> dict[str, Any]:
        """Assigns a slot, or frees one when `rack_id` is empty."""
        self._require_node(node_id)
        if rack_id:
            if not RACK_ID_PATTERN.fullmatch(rack_id):
                raise RackError("Identifiant de baie invalide.")
            rack = self.database.rack(rack_id)
            if rack is None:
                raise RackError("Cette baie n’existe pas.")
            if not 1 <= position <= int(rack["units"]):
                raise RackError(f"Cette baie compte {rack['units']} U.")
        else:
            position = 0
        with self._lock:
            self.database.move_rack_node(node_id, rack_id or None, position)
        return self.snapshot()

    def set_rules(self, node_id: str, rules: Any) -> dict[str, Any]:
        row = self._require_node(node_id)
        sheet = clean_rules(rules)
        with self._lock:
            self.database.update_rack_node(node_id, {"rules": json.dumps(sheet)})
        self._apply_local(node_id)
        if str(row["kind"]) == "remote" and row["onion"]:
            # Failure here is reported, not raised: the intent is stored, and
            # the monitor keeps trying until the node accepts it.
            try:
                self.push_policy(node_id)
            except (RackError, NodeError) as error:
                logger.info("Règles non poussées vers %s: %s", row["name"], error)
        self._notify("secure", f"Règles de « {row['name']} » enregistrées")
        return self.node(node_id)

    # ------------------------------------------------------ enforcement ----

    def _apply_local(self, node_id: str) -> None:
        """Mirrors a local node's sheet into the managers that enforce it.

        `DeviceGuard` and `DeviceAccessManager` stay the only writers of the
        block list: the rack expresses intent, it never reaches nftables.
        """
        row = self.database.rack_node(node_id)
        if row is None or str(row["kind"]) != "local" or not row["mac"]:
            return
        mac = str(row["mac"])
        try:
            sheet = clean_rules(self._json(row.get("rules"), {}))
        except RackError:
            return
        try:
            self.access.update(mac, str(row["name"]), None, sheet["schedule"])
        except AccessError as error:
            logger.warning("Règle d’accès refusée pour %s: %s", mac, error)
        try:
            if sheet["access"] == "blocked":
                self.guard.block(mac, str(row["name"]))
            elif mac in self.guard.blocked_macs():
                self.guard.unblock(mac)
        except NetControlError as error:
            logger.warning("Blocage non appliqué pour %s: %s", mac, error)

    def push_policy(self, node_id: str) -> dict[str, Any]:
        """Sends the current sheet to a remote agent and records the answer."""
        row = self._require_node(node_id)
        if str(row["kind"]) != "remote":
            raise RackError("Seuls les nœuds distants reçoivent une politique.")
        now = int(time.time())
        sheet = clean_rules(self._json(row.get("rules"), {}))
        document = policy_document(
            sheet, self._desired_block(sheet, now, time.localtime(now))
        )
        result = self._call(row, "apply-policy", document)
        state = self._json(row.get("state"), {})
        state["policy"] = {
            "digest": document["digest"],
            "egress": document["egress"],
            "applied_at": now,
        }
        with self._lock:
            self.database.update_rack_node(
                node_id, {"state": json.dumps(state), "last_seen": now, "last_error": ""}
            )
        return result

    def run_action(self, node_id: str, verb: str, unit: str = "") -> dict[str, Any]:
        if verb not in MANUAL_VERBS:
            raise RackError("Action inconnue pour un nœud.")
        row = self._require_node(node_id)
        if str(row["kind"]) != "remote":
            raise RackError("Cette action ne concerne que les nœuds distants.")
        payload: dict[str, Any] = {}
        if verb == "journal":
            if unit and not re.fullmatch(r"[\w.@-]{1,64}", unit):
                raise RackError("Nom de service invalide.")
            payload = {"unit": unit or "tor", "lines": 80}
        result = self._call(row, verb, payload)
        if verb == "status":
            self._record_status(str(row["id"]), result)
        else:
            self._notify(
                "secure", f"{AGENT_VERBS[verb][0]} demandé sur « {row['name']} »"
            )
        return result

    def _call(
        self, row: dict[str, Any], verb: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        node_id = str(row["id"])
        try:
            return self.client.call(
                node_id=node_id,
                name=str(row["name"]),
                onion=str(row["onion"] or ""),
                port=int(row["agent_port"] or 9080),
                token=self.token_for(node_id, int(row["token_epoch"] or 1)),
                verb=verb,
                payload=payload,
            )
        except NodeError as error:
            with self._lock:
                self.database.update_rack_node(node_id, {"last_error": str(error)[:200]})
            raise

    def _record_status(self, node_id: str, status: dict[str, Any]) -> None:
        row = self.database.rack_node(node_id)
        if row is None:
            return
        previous = self._json(row.get("state"), {})
        state = {
            key: status[key]
            for key in (
                "agent_version",
                "hostname",
                "uptime_seconds",
                "load",
                "memory_percent",
                "storage_percent",
                "tor",
                "policy",
                "services",
            )
            if key in status
        }
        # The reading from the node is the truth about what it runs, but the
        # last push we made is the truth about what we asked for.
        state.setdefault("policy", previous.get("policy", {}))
        with self._lock:
            self.database.update_rack_node(
                node_id,
                {
                    "state": json.dumps(state)[:8000],
                    "last_seen": int(time.time()),
                    "last_error": "",
                },
            )

    # ----------------------------------------------------- client auth -----

    def _register_client_auth(self, node_id: str) -> None:
        """Teaches Tor the key that decrypts this node's descriptor.

        Best effort by design: `ONION_CLIENT_AUTH_ADD` needs Tor 0.4.3 and a
        control port, and a node published without client authorisation works
        without it. The signature check is what actually authenticates the
        call; this narrows who can even resolve the address.
        """
        row = self.database.rack_node(node_id)
        if row is None or not row["onion"]:
            return
        private, _ = self.client_keypair(node_id, int(row["token_epoch"] or 1))
        try:
            self.controller.add_client_auth(str(row["onion"]), private)
        except TorControlError as error:
            logger.info("Autorisation client onion non enregistrée: %s", error)
            with self._lock:
                self.database.update_rack_node(node_id, {"client_auth": 0})
            return
        with self._lock:
            self.database.update_rack_node(node_id, {"client_auth": 1})

    def _forget_client_auth(self, onion: str) -> None:
        if not onion:
            return
        try:
            self.controller.remove_client_auth(onion)
        except TorControlError as error:
            logger.info("Autorisation client onion non retirée: %s", error)

    # ------------------------------------------------------- enrolment -----

    def enrollment(self, node_id: str) -> dict[str, Any]:
        """Everything the installer on the node needs, and nothing more.

        The token is a shared secret: it authenticates this appliance to the
        node and the node's answers to this appliance. It is derived, so it can
        be shown again — an operator who loses the bundle does not have to
        rebuild the machine — and rotating it is one call away.
        """
        row = self._require_node(node_id)
        if str(row["kind"]) != "remote":
            raise RackError("Seuls les nœuds distants ont un agent à installer.")
        epoch = int(row["token_epoch"] or 1)
        _, public = self.client_keypair(node_id, epoch)
        return {
            "node_id": node_id,
            "name": str(row["name"]),
            "token": self.token_for(node_id, epoch),
            "token_epoch": epoch,
            "agent_port": int(row["agent_port"] or 9080),
            "client_public_key": public,
            "onion": str(row["onion"] or ""),
            "command": (
                "sudo ./install-node-agent.sh"
                f" --node {node_id}"
                f" --port {int(row['agent_port'] or 9080)}"
                f" --token {self.token_for(node_id, epoch)}"
                f" --client-key {public}"
            ),
        }

    def rotate_token(self, node_id: str) -> dict[str, Any]:
        row = self._require_node(node_id)
        if str(row["kind"]) != "remote":
            raise RackError("Seuls les nœuds distants ont un jeton.")
        with self._lock:
            self.database.update_rack_node(
                node_id,
                {
                    "token_epoch": int(row["token_epoch"] or 1) + 1,
                    "client_auth": 0,
                    "last_error": "",
                },
            )
        self._forget_client_auth(str(row["onion"] or ""))
        self._register_client_auth(node_id)
        self._notify("secure", f"Jeton du nœud « {row['name']} » renouvelé")
        return self.enrollment(node_id)

    # --------------------------------------------------------- monitoring --

    def refresh(self, node_id: str) -> dict[str, Any]:
        """Polls one node now, and pushes its rules if they drifted."""
        row = self._require_node(node_id)
        if str(row["kind"]) != "remote" or not row["onion"]:
            return self.node(node_id)
        status = self._call(row, "status", {})
        self._record_status(node_id, status)
        view = self.node(node_id)
        reported = str((view["state"].get("policy") or {}).get("digest", ""))
        if reported != view["policy_digest"]:
            try:
                self.push_policy(node_id)
            except (RackError, NodeError) as error:
                logger.info("Règles non poussées vers %s: %s", view["name"], error)
            view = self.node(node_id)
        return view

    def _due(self) -> list[str]:
        """Remote nodes to poll this tick, least recently heard from first."""
        rows = [
            row
            for row in self.database.rack_nodes()
            if str(row["kind"]) == "remote" and row["onion"]
        ]
        rows.sort(key=lambda row: int(row["last_seen"] or 0))
        return [str(row["id"]) for row in rows[:MONITOR_BATCH]]

    def _sweep(self) -> None:
        due = [(node_id, self.node(node_id)["status"] == "online") for node_id in self._due()]
        if not due:
            return
        with ThreadPoolExecutor(
            max_workers=MONITOR_WORKERS, thread_name_prefix="onionpi-rack"
        ) as pool:
            for node_id, was_online in due:
                pool.submit(self._sweep_one, node_id, was_online)

    def _sweep_one(self, node_id: str, was_online: bool) -> None:
        try:
            self.refresh(node_id)
        except (RackError, NodeError):
            row = self.database.rack_node(node_id)
            if row is not None and row["client_auth"]:
                # Tor keeps client credentials in memory only. A restart of the
                # daemon makes every authorised node unresolvable at once, and
                # that looks exactly like an outage until they are re-taught.
                self._register_client_auth(node_id)
            if was_online and row is not None:
                self._notify("device", f"Nœud « {row['name']} » injoignable")
            return
        if not was_online:
            row = self.database.rack_node(node_id)
            if row is not None:
                self._notify("device", f"Nœud « {row['name']} » de nouveau en ligne")

    def start(self) -> None:
        if self.demo_mode or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="onionpi-rack-monitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._sweep()
            except Exception:
                logger.exception("Balayage de la baie interrompu")
            self._stop.wait(MONITOR_TICK_SECONDS)

    # ------------------------------------------------------------ helpers --

    def _notify(self, kind: str, message: str) -> None:
        if self.on_event:
            self.on_event(kind, message)

    def republish_client_auth(self) -> None:
        """Re-teaches Tor every node key at startup: Tor forgets on restart."""
        for row in self.database.rack_nodes():
            if str(row["kind"]) == "remote" and row["onion"] and row["client_auth"]:
                self._register_client_auth(str(row["id"]))

    def seed_demo(self) -> None:
        """A plausible rack, so the interface can be judged before any node."""
        if not self.demo_mode or self.database.racks():
            return
        rack_id = _rack_id()
        self.database.create_rack(rack_id, "Baie principale", "Salon", DEFAULT_UNITS)
        demo = [
            ("remote", "vps-relais", "Relais Snowflake", 1, "b" * 56),
            ("remote", "vps-stockage", "Sauvegardes chiffrées", 3, "c" * 56),
            ("local", "Portable Camille", "Poste de travail", 5, ""),
        ]
        for kind, name, role, position, onion in demo:
            node_id = _node_id()
            self.database.create_rack_node(
                node_id,
                kind,
                {
                    "rack_id": rack_id,
                    "position": position,
                    "name": name,
                    "role": role,
                    "onion": onion,
                    "mac": "aa:bb:cc:dd:ee:01" if kind == "local" else "",
                    "rules": json.dumps(clean_rules({})),
                    "client_auth": 1 if onion else 0,
                },
            )
            if kind == "remote":
                self._record_status(
                    node_id,
                    self.client.call(
                        node_id=node_id,
                        name=name,
                        onion=onion,
                        port=9080,
                        token="",
                        verb="status",
                    ),
                )
                # A demonstration rack shows a settled installation: without
                # this, every node would claim its rules were still pending.
                self.push_policy(node_id)
