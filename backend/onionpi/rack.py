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
from .mesh import (
    DEFAULT_MESH_PORT,
    IDENTITY_PREFIX,
    MAX_MESH_FORWARDS,
    MAX_MESH_PORTS,
    NETMAP_LIFETIME,
    MeshCoordinator,
    MeshError,
    clean_mesh_rules,
    decode_key,
    rotation_message,
    verify_announcement,
    verify_signature,
)
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
MAX_PROFILES = 12
MAX_CABLES = 128
MAX_CABLE_PORTS = 8

#: The Wi-Fi reading behind a local node's line — its address, whether it
#: answers, what it moved. Reading it means asking the kernel for its neighbour
#: table, so the rack keeps the answer for a few seconds rather than paying
#: that price on every poll of every page.
WIFI_CACHE_SECONDS = 10
#: Slots offered by the discovery panel at once. A household has a handful of
#: machines; a list longer than this is noise, not inventory.
MAX_DISCOVERED = 16

#: Window the availability figure is read over, and the level above which a
#: reading is called out on the node's sheet.
HISTORY_WINDOW_SECONDS = 24 * 3600
SATURATION_PERCENT = 90.0

#: How the monitor spends its time: a tick every minute, a handful of nodes per
#: tick, oldest reading first. A full rack is swept in about ten minutes, and
#: no tick can hold more than three sockets open at once.
MONITOR_TICK_SECONDS = 60
MONITOR_BATCH = 6
MONITOR_WORKERS = 3

#: What a bulk call may ask for. Each one is the operation a single node
#: already offers, run over a list: nothing here reaches further than the
#: buttons on one node's sheet, it only saves the operator the repetition.
BULK_OPERATIONS = ("isolate", "allow", "refresh", "profile", "unrack")

NAME_PATTERN = re.compile(r"^[\w .'’()\-]{1,48}$", re.UNICODE)
ROLE_PATTERN = re.compile(r"^[\w .'’()\-]{0,32}$", re.UNICODE)
ONION_PATTERN = re.compile(r"^[a-z2-7]{56}$")
ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
RACK_ID_PATTERN = re.compile(r"^[0-9a-f]{8}$")
CABLE_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")
COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
SOURCE_REF_PATTERN = re.compile(r"^[0-9a-f]{40}$")

NODE_KINDS = ("local", "remote")
EGRESS_MODES = ("tor-only", "direct")
ACCESS_MODES = ("allowed", "blocked")
CABLE_COLORS = ("amber", "cyan", "violet", "green")
CABLE_SPEEDS = ("100-mbps", "1-gbps", "10-gbps")

#: Version 2 porte les deux champs du maillage. Le renderer de chaque
#: plateforme les revalide, et refuse une version qu'il ne connaît pas plutôt
#: que d'appliquer un pare-feu dont il devine les champs manquants.
POLICY_VERSION = 2

# The bootstrap is fetched from GitHub, and nothing about that download is
# trusted: the appliance pins it to the digest of its own copy of the agent
# (see `bundle_digest`). Credentials never travel here at all — they are typed
# into the installer's prompt, so they are not in the URL, in a referrer, in
# the download host's logs, nor in the node's process list.
NODE_BOOTSTRAP_REPOSITORY = "https://raw.githubusercontent.com/0xmagicduck/onionpi-router"

#: Left open on the node's own INPUT chain whatever else the policy says.
#: Locking an operator out of a machine on the other side of the planet, with
#: no console, is a worse outcome than the port being reachable.
DEFAULT_KEEP_OPEN_PORTS = (22,)


class RackError(RuntimeError):
    pass


def bundle_digest(root: Path) -> str:
    """One digest over every file the node installer is made of.

    `install.sh` copies `packaging/agent/` next to the backend of the release
    it promotes, and that release was verified against a signed `SHA256SUMS`.
    The appliance's own copy is therefore the reviewed one, and printing this
    digest in the enrolment command pins the GitHub download to exactly that
    content: the node installs what this appliance carries, or nothing.

    Empty when the directory is missing, which is how a source checkout and a
    demo run behave; the installer then refuses unless told to skip the check.
    """
    lines: list[str] = []
    try:
        files = sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    except OSError:
        return ""
    for path in files:
        try:
            content = path.read_bytes()
        except OSError:
            return ""
        name = path.relative_to(root).as_posix()
        lines.append(f"{hashlib.sha256(content).hexdigest()}  {name}\n")
    if not lines:
        return ""
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def _node_id() -> str:
    return secrets.token_hex(8)


def _rack_id() -> str:
    return secrets.token_hex(4)


def _cable_id() -> str:
    return secrets.token_hex(6)


def clean_name(value: str, field: str = "Nom") -> str:
    cleaned = " ".join(value.split())[:48]
    if not NAME_PATTERN.fullmatch(cleaned):
        raise RackError(f"{field} invalide: lettres, chiffres, espace, - _ ( ) et '.")
    return cleaned


def _number(value: Any) -> float:
    """A reading from the other side of a Tor circuit, or zero."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def relative_delay(seconds: int) -> str:
    """A delay as a sentence fragment: « 12 min », « 3 h », « 2 j »."""
    seconds = max(int(seconds), 0)
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    if seconds < 172_800:
        return f"{seconds // 3600} h"
    return f"{seconds // 86_400} j"


def clean_onion(value: str) -> str:
    """Accepts `abc….onion`, `http://abc….onion` or the bare service id."""
    cleaned = value.strip().lower()
    cleaned = cleaned.removeprefix("http://").removeprefix("https://")
    cleaned = cleaned.split("/", 1)[0].split(":", 1)[0].removesuffix(".onion")
    if cleaned and not ONION_PATTERN.fullmatch(cleaned):
        raise RackError("Adresse onion invalide: 56 caractères, sans le suffixe .onion.")
    return cleaned


def clean_rules(raw: Any, known_nodes: set[str] | None = None) -> dict[str, Any]:
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
    try:
        mesh = clean_mesh_rules(document.get("mesh"), known_nodes)
    except MeshError as error:
        raise RackError(str(error)) from error
    return {
        "access": access,
        "egress": egress,
        "exit_country": country,
        "keep_open_ports": ports or list(DEFAULT_KEEP_OPEN_PORTS),
        "schedule": schedule,
        "mesh": mesh,
    }


def policy_document(rules: dict[str, Any], blocked: bool) -> dict[str, Any]:
    """The exact document handed to the agent, digest included.

    The digest is computed over the canonical form so both sides can tell
    whether the node already runs these rules without comparing field by field.
    """
    mesh = rules.get("mesh", {})
    body = {
        "version": POLICY_VERSION,
        "egress": rules["egress"],
        "exit_country": rules["exit_country"],
        "keep_open_ports": sorted(rules["keep_open_ports"]),
        "isolated": bool(blocked),
        # The one hole the kill switch opens for the overlay, and only for the
        # direct path: the relayed path rides Tor and needs no exception.
        "mesh_enabled": bool(mesh.get("enabled", False)),
        "mesh_port": DEFAULT_MESH_PORT if mesh.get("enabled") else 0,
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
        coordinator: MeshCoordinator,
        demo_mode: bool = False,
        on_event: Callable[[str, str], None] | None = None,
        wifi_view: Callable[[], list[dict[str, Any]]] | None = None,
        agent_dir: Path | None = None,
        source_ref: str = "",
    ) -> None:
        self.database = database
        self.key_path = key_path
        # The reviewed copy of the installer this appliance was released with:
        # what the enrolment command pins the GitHub download to.
        self.agent_dir = agent_dir
        self.source_ref = source_ref if SOURCE_REF_PATTERN.fullmatch(source_ref) else ""
        self.guard = guard
        self.access = access
        self.client = client
        self.controller = controller
        # The rack signs network maps; it does not hold any node's identity.
        # That split is what lets two nodes talk without trusting the centre.
        self.coordinator = coordinator
        self.demo_mode = demo_mode
        self.on_event = on_event
        # Composed rather than imported: the rack reads the Wi-Fi, it does not
        # own it, and the same page must work when nothing supplies one.
        self.wifi_view = wifi_view or (lambda: [])
        self._wifi_cache: tuple[float, dict[str, dict[str, Any]]] = (0.0, {})
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

    def _wifi_index(self) -> dict[str, dict[str, Any]]:
        """The Wi-Fi clients, by MAC, refreshed at most every few seconds."""
        stamp, cached = self._wifi_cache
        if time.monotonic() - stamp < WIFI_CACHE_SECONDS:
            return cached
        index: dict[str, dict[str, Any]] = {}
        try:
            for device in self.wifi_view():
                mac = str(device.get("mac", "")).lower()
                if mac:
                    index[mac] = device
        except Exception:  # a missing neighbour table must not empty the page
            logger.exception("Lecture des clients Wi-Fi impossible")
            return cached
        self._wifi_cache = (time.monotonic(), index)
        return index

    def _node_view(
        self,
        row: dict[str, Any],
        now: int,
        moment: time.struct_time,
        blocked_macs: set[str] | None = None,
        wifi: dict[str, dict[str, Any]] | None = None,
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
            "mesh_identity": str(row["mesh_identity"] or ""),
            "mesh_static": str(row["mesh_static"] or ""),
            "mesh_static_signature": str(row["mesh_static_signature"] or ""),
            "mesh_address": str(row["mesh_address"] or ""),
            "mesh_endorsements": self._json(row["mesh_endorsements"], {}) or {},
            "netmap_serial": int(row["netmap_serial"] or 0),
            # The node's own reading of its radio address, refreshed by `status`
            # and never stored as a column: it belongs to the link, not to the
            # identity, and it changes when the radio does.
            "mesh_v4": str((state.get("mesh") or {}).get("direct", "")),
        }
        if blocked_macs is None:
            blocked_macs = self.guard.blocked_macs()
        view["status"] = self._status_of(view, blocked, now, blocked_macs)
        view["link"] = self._link_of(view, wifi if wifi is not None else self._wifi_index())
        view["alerts"] = self._alerts_of(view, now)
        return view

    @staticmethod
    def _link_of(
        view: dict[str, Any], wifi: dict[str, dict[str, Any]]
    ) -> dict[str, Any] | None:
        """What the Wi-Fi says about a local node: address, presence, volume.

        A remote node has no line here on purpose — this appliance does not
        route it, and inventing an address for it would be a claim the rack
        cannot back up.
        """
        if view["kind"] != "local":
            return None
        device = wifi.get(view["mac"])
        if device is None:
            return None
        return {
            "ip": str(device.get("ip", "")),
            "online": bool(device.get("online", False)),
            "download": int(device.get("download", 0) or 0),
            "upload": int(device.get("upload", 0) or 0),
        }

    def _alerts_of(self, view: dict[str, Any], now: int) -> list[dict[str, str]]:
        """Everything about this node an operator would want told, once.

        The sheet already carries the facts; this reads them the way a person
        would, so a rack of sixty machines can be scanned instead of audited.
        """
        alerts: list[dict[str, str]] = []

        def add(level: str, message: str) -> None:
            alerts.append({"level": level, "message": message})

        if view["status"] == "offline":
            since = relative_delay(now - view["last_seen"])
            add("danger", f"Injoignable depuis {since}.")
        if view["last_error"]:
            add("danger", view["last_error"])
        if view["kind"] == "remote":
            if not view["onion"]:
                add("info", "Adresse onion attendue : installez l’agent sur la machine.")
            elif not view["client_auth"]:
                add(
                    "warning",
                    "Autorisation client onion non enregistrée : l’adresse reste "
                    "résoluble par quiconque la connaît.",
                )
            if view["state"].get("policy") and (
                str(view["state"]["policy"].get("digest", "")) != view["policy_digest"]
            ):
                add("warning", "Le nœud n’applique pas encore les règles enregistrées.")
            tor = view["state"].get("tor") or {}
            if view["state"] and not tor.get("connected", True):
                add("warning", f"Tor du nœud à {int(tor.get('bootstrap', 0))} % d’amorçage.")
            for service in view["state"].get("services") or []:
                if isinstance(service, dict) and not service.get("active", True):
                    add("warning", f"Service « {service.get('label', '?')} » arrêté.")
            for field, label in (
                ("memory_percent", "Mémoire"),
                ("storage_percent", "Disque"),
            ):
                value = float(view["state"].get(field) or 0)
                if value >= SATURATION_PERCENT:
                    add("warning", f"{label} du nœud à {value:.0f} %.")
        if view["rules"]["egress"] == "direct":
            add("warning", "Sortie directe : ce nœud ne passe pas par Tor.")
        return alerts

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
        wifi = self._wifi_index()
        nodes = [
            self._node_view(row, now, moment, blocked_macs, wifi)
            for row in self.database.rack_nodes()
        ]
        racks = []
        for rack in self.database.racks():
            members = [node for node in nodes if node["rack_id"] == rack["id"]]
            racks.append(
                {
                    **rack,
                    "occupied": sum(1 for node in members if node["position"]),
                    "alerts": sum(len(node["alerts"]) for node in members),
                }
            )
        node_index = {node["id"]: node for node in nodes}
        cables = [
            self._cable_view(row, node_index) for row in self.database.rack_cables()
        ]
        return {
            "racks": racks,
            "nodes": nodes,
            "cables": cables,
            "profiles": self.profiles(),
            "discovered": self._discovered(nodes, wifi),
            "health": {
                "warnings": sum(
                    1
                    for node in nodes
                    for alert in node["alerts"]
                    if alert["level"] == "warning"
                ),
                "failures": sum(
                    1
                    for node in nodes
                    for alert in node["alerts"]
                    if alert["level"] == "danger"
                ),
            },
            "limits": {
                "max_racks": MAX_RACKS,
                "max_nodes": MAX_NODES,
                "max_units": MAX_UNITS,
                "default_units": DEFAULT_UNITS,
                "max_profiles": MAX_PROFILES,
                "max_cables": MAX_CABLES,
            },
            "verbs": [
                {"id": verb, "label": AGENT_VERBS[verb][0]} for verb in MANUAL_VERBS
            ],
            "egress_modes": list(EGRESS_MODES),
            "mesh": self.mesh(nodes),
            "now": now,
        }

    @staticmethod
    def _discovered(
        nodes: list[dict[str, Any]], wifi: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Wi-Fi clients that are not yet a node, ready to be racked."""
        known = {node["mac"] for node in nodes if node["mac"]}
        found = [
            {
                "mac": mac,
                "name": str(device.get("name", "")) or "Appareil",
                "ip": str(device.get("ip", "")),
                "online": bool(device.get("online", False)),
            }
            for mac, device in wifi.items()
            if mac not in known
        ]
        found.sort(key=lambda device: (not device["online"], device["name"].lower()))
        return found[:MAX_DISCOVERED]

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

    # ------------------------------------------------------------ cables --

    @staticmethod
    def _port_count(row: dict[str, Any]) -> int:
        """Ports drawn on a faceplate: clients have one, servers four."""
        return 1 if str(row.get("kind", "")) == "local" else 4

    @staticmethod
    def _cable_view(
        row: dict[str, Any], nodes: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        source = nodes.get(str(row["source_node_id"]))
        target = nodes.get(str(row["target_node_id"]))
        statuses = {str((source or {}).get("status", "offline")), str((target or {}).get("status", "offline"))}
        if statuses & {"offline", "pending", "unknown"}:
            status = "offline"
        elif "isolated" in statuses:
            status = "warning"
        else:
            status = "online"
        return {
            **row,
            "source_name": str((source or {}).get("name", "Nœud retiré")),
            "target_name": str((target or {}).get("name", "Nœud retiré")),
            "status": status,
        }

    def create_cable(
        self,
        rack_id: str,
        source_node_id: str,
        source_port: int,
        target_node_id: str,
        target_port: int,
        label: str = "",
        color: str = "cyan",
        speed: str = "1-gbps",
    ) -> dict[str, Any]:
        if not RACK_ID_PATTERN.fullmatch(rack_id):
            raise RackError("Identifiant de baie invalide.")
        if source_node_id == target_node_id:
            raise RackError("Un câble doit relier deux appareils différents.")
        if color not in CABLE_COLORS:
            raise RackError("Couleur de câble invalide.")
        if speed not in CABLE_SPEEDS:
            raise RackError("Vitesse de câble invalide.")
        with self._lock:
            if len(self.database.rack_cables()) >= MAX_CABLES:
                raise RackError(f"{MAX_CABLES} câbles au maximum.")
            source = self._require_node(source_node_id)
            target = self._require_node(target_node_id)
            for node in (source, target):
                if str(node["rack_id"] or "") != rack_id or not int(node["position"] or 0):
                    raise RackError("Les deux appareils doivent occuper cette baie.")
            for port, node in ((source_port, source), (target_port, target)):
                if not 1 <= int(port) <= min(self._port_count(node), MAX_CABLE_PORTS):
                    raise RackError("Port réseau invalide pour cet appareil.")
            occupied = {
                (str(cable[side + "_node_id"]), int(cable[side + "_port"]))
                for cable in self.database.rack_cables()
                for side in ("source", "target")
            }
            if (source_node_id, source_port) in occupied or (target_node_id, target_port) in occupied:
                raise RackError("Un de ces ports est déjà câblé.")
            title = " ".join(label.split())[:48] or f"{source['name']} ↔ {target['name']}"
            self.database.create_rack_cable(
                _cable_id(),
                {
                    "rack_id": rack_id,
                    "source_node_id": source_node_id,
                    "source_port": int(source_port),
                    "target_node_id": target_node_id,
                    "target_port": int(target_port),
                    "label": title,
                    "color": color,
                    "speed": speed,
                },
            )
        self._notify("device", f"Câble « {title} » ajouté")
        return self.snapshot()

    def delete_cable(self, cable_id: str) -> dict[str, Any]:
        if not CABLE_ID_PATTERN.fullmatch(cable_id):
            raise RackError("Identifiant de câble invalide.")
        with self._lock:
            cable = self.database.rack_cable(cable_id)
            if cable is None:
                raise RackError("Ce câble n’existe pas.")
            self.database.delete_rack_cable(cable_id)
        self._notify("device", f"Câble « {cable['label']} » retiré")
        return self.snapshot()

    def arrange_rack(self, rack_id: str) -> dict[str, Any]:
        """Closes the gaps: the machines keep their order, U1 upwards.

        Moves go through `move_rack_node` one at a time, so the slot index
        stays the referee. A machine already in the right slot is not touched.
        """
        if not RACK_ID_PATTERN.fullmatch(rack_id):
            raise RackError("Identifiant de baie invalide.")
        if self.database.rack(rack_id) is None:
            raise RackError("Cette baie n’existe pas.")
        with self._lock:
            members = sorted(
                (
                    row
                    for row in self.database.rack_nodes()
                    if str(row["rack_id"] or "") == rack_id and int(row["position"] or 0)
                ),
                key=lambda row: int(row["position"]),
            )
            for target, row in enumerate(members, start=1):
                if int(row["position"]) != target:
                    self.database.move_rack_node(str(row["id"]), rack_id, target)
        return self.snapshot()

    # ----------------------------------------------------------- profiles --

    def profiles(self) -> list[dict[str, Any]]:
        entries = []
        for row in self.database.rack_profiles():
            try:
                rules = clean_rules(self._json(row.get("rules"), {}))
            except RackError:
                rules = clean_rules({})
            entries.append({**row, "rules": rules})
        return entries

    def save_profile(self, profile_id: str, name: str, rules: Any) -> dict[str, Any]:
        """Stores a named rule sheet. An empty id creates one."""
        label = clean_name(name, "Nom du profil")
        sheet = clean_rules(rules)
        with self._lock:
            if profile_id:
                if not RACK_ID_PATTERN.fullmatch(profile_id):
                    raise RackError("Identifiant de profil invalide.")
                if self.database.rack_profile(profile_id) is None:
                    raise RackError("Ce profil n’existe pas.")
            else:
                if len(self.database.rack_profiles()) >= MAX_PROFILES:
                    raise RackError(f"{MAX_PROFILES} profils au maximum.")
                profile_id = _rack_id()
            self.database.save_rack_profile(profile_id, label, json.dumps(sheet))
        return self.snapshot()

    def delete_profile(self, profile_id: str) -> dict[str, Any]:
        if not RACK_ID_PATTERN.fullmatch(profile_id):
            raise RackError("Identifiant de profil invalide.")
        with self._lock:
            if self.database.rack_profile(profile_id) is None:
                raise RackError("Ce profil n’existe pas.")
            self.database.delete_rack_profile(profile_id)
        return self.snapshot()

    def profile_rules(self, profile_id: str) -> dict[str, Any]:
        if not RACK_ID_PATTERN.fullmatch(profile_id):
            raise RackError("Identifiant de profil invalide.")
        row = self.database.rack_profile(profile_id)
        if row is None:
            raise RackError("Ce profil n’existe pas.")
        return clean_rules(self._json(row.get("rules"), {}))

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
        if row["mesh_identity"]:
            # Deleting the row removes the peer from the next map; the
            # revocation list is what tells nodes still holding the old map to
            # stop, up to its `not_after`.
            self.coordinator.revoke(str(row["mesh_identity"]))
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

    def set_rules(self, node_id: str, rules: Any, announce: bool = True) -> dict[str, Any]:
        """Stores a rule sheet and hands it to whoever enforces it.

        `announce` is false when a group action runs: twenty machines changed
        in one gesture are one line in the activity feed, not twenty.
        """
        row = self._require_node(node_id)
        known = {str(other["id"]) for other in self.database.rack_nodes()}
        sheet = clean_rules(rules, known)
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
            # Only this node's own map is pushed now. Its peers learn about the
            # change on their next sweep: twelve circuits opened from one form
            # submission is a page that times out, and the sweep is the
            # documented bound on how long that takes.
            try:
                self.push_netmap(node_id)
            except (RackError, NodeError, MeshError) as error:
                logger.info("Carte non publiée vers %s: %s", row["name"], error)
        if announce:
            self._notify("secure", f"Règles de « {row['name']} » enregistrées")
        return self.node(node_id)

    def bulk(
        self, operation: str, node_ids: list[str], profile_id: str = ""
    ) -> dict[str, Any]:
        """Runs one node operation over a list, and reports each refusal.

        A partial failure is the normal case — one node of twelve is offline —
        so nothing is rolled back and nothing is hidden: what succeeded stands,
        what failed comes back named.
        """
        if operation not in BULK_OPERATIONS:
            raise RackError("Action groupée inconnue.")
        if not node_ids:
            raise RackError("Aucun nœud sélectionné.")
        if len(node_ids) > MAX_NODES:
            raise RackError(f"{MAX_NODES} nœuds au maximum par action groupée.")
        rules = self.profile_rules(profile_id) if operation == "profile" else None
        failures: list[dict[str, str]] = []
        applied = 0
        for node_id in node_ids:
            try:
                row = self._require_node(node_id)
                if operation == "refresh":
                    self.refresh(node_id)
                elif operation == "unrack":
                    self.move_node(node_id, "", 0)
                elif operation == "profile":
                    self.set_rules(node_id, rules, announce=False)
                else:
                    sheet = clean_rules(self._json(row.get("rules"), {}))
                    sheet["access"] = "blocked" if operation == "isolate" else "allowed"
                    self.set_rules(node_id, sheet, announce=False)
                applied += 1
            except (RackError, NodeError) as error:
                row = self.database.rack_node(node_id)
                failures.append(
                    {
                        "id": node_id,
                        "name": str(row["name"]) if row else node_id,
                        "message": str(error)[:200],
                    }
                )
        if applied:
            labels = {
                "isolate": "isolés",
                "allow": "autorisés",
                "refresh": "interrogés",
                "profile": "reréglés",
                "unrack": "sortis de la baie",
            }
            self._notify("secure", f"{applied} nœud(s) {labels[operation]} en une action")
        return {"snapshot": self.snapshot(), "applied": applied, "failures": failures}

    def import_devices(self, macs: list[str], rack_id: str = "") -> dict[str, Any]:
        """Turns Wi-Fi clients into local nodes, named as the lease named them.

        The rack is the only thing gained: the device was already known to the
        firewall, and a node created here starts with the default sheet, which
        blocks nothing.
        """
        if not macs:
            raise RackError("Aucun appareil sélectionné.")
        if rack_id and self.database.rack(rack_id) is None:
            raise RackError("Cette baie n’existe pas.")
        wifi = self._wifi_index()
        created: list[str] = []
        failures: list[dict[str, str]] = []
        for raw in macs[:MAX_DISCOVERED]:
            try:
                mac = normalize_mac(raw)
            except ValueError as error:
                failures.append({"id": str(raw)[:32], "name": str(raw)[:32], "message": str(error)})
                continue
            device = wifi.get(mac, {})
            # A lease name is whatever the device announced, so it goes through
            # the same cleaning as anything typed into the form — and falls
            # back to the address when the machine announced nothing usable.
            label = " ".join(str(device.get("name", "")).split())[:48]
            try:
                label = clean_name(label or f"Appareil {mac[-5:]}")
            except RackError:
                label = f"Appareil {mac[-5:]}"
            try:
                node = self.create_node("local", label, mac=mac)
                created.append(node["id"])
                if rack_id:
                    free = self._free_position(rack_id)
                    if free:
                        self.move_node(node["id"], rack_id, free)
            except RackError as error:
                failures.append({"id": mac, "name": label, "message": str(error)[:200]})
        return {"snapshot": self.snapshot(), "applied": len(created), "failures": failures}

    def _free_position(self, rack_id: str) -> int:
        """Lowest empty U of a rack, or 0 when it is full."""
        rack = self.database.rack(rack_id)
        if rack is None:
            return 0
        taken = {
            int(row["position"] or 0)
            for row in self.database.rack_nodes()
            if str(row["rack_id"] or "") == rack_id
        }
        for unit in range(1, int(rack["units"]) + 1):
            if unit not in taken:
                return unit
        return 0

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
        if not result.get("applied"):
            message = str(result.get("message", ""))[:200] or "Politique refusée par le nœud."
            with self._lock:
                self.database.update_rack_node(node_id, {"last_error": message})
            raise NodeError(message)
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

    # ------------------------------------------------------------- maillage --

    def _adopt_announcement(self, row: dict[str, Any], announcement: Any) -> None:
        """Records what a node says about its own overlay identity.

        The rack never mints one of these. It checks that the static key the
        node offers is signed by the identity it claims — otherwise the centre
        would choose the key its peers encrypt with — and that a *change* of
        identity is signed by the key being replaced. A node whose key is
        replaced without that proof keeps the key the rack already knows: an
        unproven rotation is indistinguishable from a stolen enrolment token.
        """
        node_id = str(row["id"])
        try:
            checked = verify_announcement(announcement)
        except MeshError as error:
            logger.info("Annonce de maillage refusée pour %s: %s", node_id, error)
            self._notify(
                "danger",
                f"Annonce de maillage refusée pour « {row['name']} »: {error}",
            )
            return
        previous = str(row["mesh_identity"] or "")
        endorsements = str(row["mesh_endorsements"] or "{}")
        if previous and previous != checked["identity"]:
            proof = str((announcement or {}).get("rotation_signature", ""))
            try:
                # A stored key nobody can parse cannot vouch for its successor,
                # and this runs on the monitor thread: it must refuse, not raise.
                previous_key = decode_key(IDENTITY_PREFIX, previous)
            except MeshError:
                previous_key = b""
            if not previous_key or not verify_signature(
                previous_key, rotation_message(previous, checked["identity"]), proof
            ):
                self._notify(
                    "danger",
                    f"Changement de clé de maillage refusé sur « {row['name']} »: "
                    "il n’est pas signé par la clé précédente.",
                )
                return
            self.coordinator.revoke(previous)
            # The trustees signed the key that is going away, not this one.
            endorsements = "{}"
        elif previous == checked["identity"] and str(row["mesh_static"] or "") == checked["static"]:
            return
        with self._lock:
            self.database.update_rack_node(
                node_id,
                {
                    "mesh_identity": checked["identity"],
                    "mesh_static": checked["static"],
                    "mesh_static_signature": checked["static_signature"],
                    "mesh_address": checked["address"],
                    "mesh_endorsements": endorsements,
                },
            )
        if previous and previous != checked["identity"]:
            self._notify("secure", f"Clé de maillage de « {row['name']} » renouvelée")

    def _mesh_views(self) -> list[dict[str, Any]]:
        now = int(time.time())
        moment = time.localtime(now)
        blocked = self.guard.blocked_macs()
        return [
            self._node_view(row, now, moment, blocked, {})
            for row in self.database.rack_nodes()
            if str(row["kind"]) == "remote"
        ]

    def netmap_for(self, node_id: str) -> dict[str, Any]:
        """The map a node would receive right now, unsigned and unnumbered.

        Exposed so the interface can show what a node is being told without
        burning a serial number: issuing a map is not a read.
        """
        views = self._mesh_views()
        target = next((view for view in views if view["id"] == node_id), None)
        if target is None:
            raise RackError("Ce nœud n’existe pas.")
        peers = [
            entry
            for view in views
            if view["id"] != node_id
            for entry in (self.coordinator.peer_entry(view),)
            if entry is not None
        ]
        return self.coordinator.body(target, peers)

    def push_netmap(self, node_id: str, force: bool = False) -> dict[str, Any]:
        """Issues and delivers this node's map, when its content has changed.

        Skipped when the content is identical and the map still has more than
        half its life left: a rack sweeps every minute, and re-signing the same
        peers sixty times an hour would spend circuits to say nothing. A node
        whose map is not renewed before `not_after` stops trusting it, which is
        the point — an appliance that has gone quiet must not keep a maillage
        running indefinitely.
        """
        row = self._require_node(node_id)
        if str(row["kind"]) != "remote":
            raise RackError("Seuls les nœuds distants reçoivent une carte.")
        if not row["onion"]:
            raise RackError("Ce nœud n’a pas encore d’adresse onion.")
        body = self.netmap_for(node_id)
        digest = self.coordinator.digest(body)
        state = self._json(row.get("state"), {})
        published = state.get("netmap") if isinstance(state.get("netmap"), dict) else {}
        now = int(time.time())
        stale = now - int(published.get("issued_at", 0) or 0) > NETMAP_LIFETIME // 2
        if not force and not stale and str(published.get("digest", "")) == digest:
            return {"published": False, "digest": digest, "peers": len(body["peers"])}
        document = self.coordinator.issue(body, now)
        result = self._call(row, "netmap", document)
        if not result.get("accepted"):
            message = str(result.get("message", ""))[:200] or "Carte refusée par le nœud."
            with self._lock:
                self.database.update_rack_node(node_id, {"last_error": message})
            raise NodeError(message)
        state["netmap"] = {
            "digest": digest,
            "serial": document["serial"],
            "issued_at": now,
            "peers": len(body["peers"]),
            "forwards": len(body["forwards"]),
        }
        values: dict[str, Any] = {
            "state": json.dumps(state)[:8000],
            "netmap_serial": int(document["serial"]),
            "last_seen": now,
        }
        # A published map says nothing about the rules: clearing the node's last
        # error here would erase a refusal the operator has to see, and a rack
        # that hides a refused policy behind an accepted map is worse than one
        # that reports neither.
        if str(row["last_error"] or "").startswith(AGENT_VERBS["netmap"][0]):
            values["last_error"] = ""
        with self._lock:
            self.database.update_rack_node(node_id, values)
        return {"published": True, "digest": digest, "peers": len(body["peers"])}

    def rotate_mesh_key(self, node_id: str) -> dict[str, Any]:
        """Asks a node to replace its overlay identity, and republishes.

        The node generates the new key and signs the change with the old one.
        Nothing about the new key comes from here — which is why losing the
        coordinator's key costs re-signing maps, not re-enrolling machines.
        """
        row = self._require_node(node_id)
        if str(row["kind"]) != "remote":
            raise RackError("Seuls les nœuds distants ont une clé de maillage.")
        result = self._call(row, "mesh-rotate", {})
        self._adopt_announcement(row, result.get("mesh"))
        return self.node(node_id)

    def mesh(self, views: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """What the overlay looks like from here, for the interface."""
        if views is None:
            views = self._mesh_views()
        else:
            views = [view for view in views if view["kind"] == "remote"]
        lock = self.coordinator.lock()
        members = []
        for view in views:
            entry = self.coordinator.peer_entry(view)
            published = view["state"].get("netmap") or {}
            members.append(
                {
                    "id": view["id"],
                    "name": view["name"],
                    "enabled": bool(view["rules"]["mesh"]["enabled"]),
                    "identity": view["mesh_identity"],
                    "address": view["mesh_address"],
                    "direct": view["mesh_v4"],
                    "ports": list(view["rules"]["mesh"]["ports"]),
                    "forwards": list(view["rules"]["mesh"]["forwards"]),
                    "endorsed": len(view["mesh_endorsements"]),
                    "in_map": entry is not None,
                    "netmap_serial": view["netmap_serial"],
                    "netmap_peers": int(published.get("peers", 0) or 0),
                    "netmap_issued_at": int(published.get("issued_at", 0) or 0),
                }
            )
        return {
            "coordinator": self.coordinator.public_key(),
            "lock": lock,
            "revoked": self.coordinator.revoked(),
            "members": members,
            "mesh_port": DEFAULT_MESH_PORT,
            "limits": {
                "max_ports": MAX_MESH_PORTS,
                "max_forwards": MAX_MESH_FORWARDS,
            },
        }

    def set_mesh_lock(
        self, enabled: bool, threshold: int, trustees: list[str]
    ) -> dict[str, Any]:
        try:
            self.coordinator.set_lock(enabled, threshold, trustees)
        except MeshError as error:
            raise RackError(str(error)) from error
        self._notify(
            "secure",
            "Verrou de maillage activé" if enabled else "Verrou de maillage désactivé",
        )
        return self.mesh()

    def set_endorsements(self, node_id: str, endorsements: Any) -> dict[str, Any]:
        """Stores the trustee signatures that let a node's key enter the maillage.

        Checked here so a node sheet says the truth about what it holds; the
        check that matters happens on each node, against its own pinned
        `mesh.lock`. A rack that could vouch for a key would be exactly the
        single point of compromise the lock exists to remove.
        """
        row = self._require_node(node_id)
        identity = str(row["mesh_identity"] or "")
        if not identity:
            raise RackError("Ce nœud n’a pas encore annoncé de clé de maillage.")
        try:
            kept = self.coordinator.check_endorsements(node_id, identity, endorsements)
        except MeshError as error:
            raise RackError(str(error)) from error
        with self._lock:
            self.database.update_rack_node(
                node_id, {"mesh_endorsements": json.dumps(kept)}
            )
        self._notify(
            "secure",
            f"{len(kept)} contre-signature(s) enregistrée(s) pour « {row['name']} »",
        )
        return self.node(node_id)

    def endorsement_request(self, node_id: str) -> dict[str, Any]:
        """What a trustee has to sign, and with which command."""
        row = self._require_node(node_id)
        identity = str(row["mesh_identity"] or "")
        if not identity:
            raise RackError("Ce nœud n’a pas encore annoncé de clé de maillage.")
        return {
            "node_id": node_id,
            "name": str(row["name"]),
            "identity": identity,
            "message": self.coordinator.endorsement_target(node_id, identity),
            "command": (
                f"onionpi-admin mesh-endorse --node {node_id} --identity {identity}"
            ),
            "lock": self.coordinator.lock(),
        }

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
        if verb == "mesh-rotate":
            return self.rotate_mesh_key(node_id)
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
            # Only the probe writes history. Any verb can fail, but a failed
            # reboot says nothing about availability that the next sweep will
            # not say better, and counting it twice would flatter or damn the
            # figure depending on how often an operator pressed a button.
            if verb == "status":
                self.database.add_rack_sample(node_id, {"at": int(time.time())})
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
                "platform",
                "mesh",
            )
            if key in status
        }
        # The reading from the node is the truth about what it runs, but the
        # last push we made is the truth about what we asked for.
        state.setdefault("policy", previous.get("policy", {}))
        state.setdefault("netmap", previous.get("netmap", {}))
        if isinstance(status.get("mesh"), dict):
            self._adopt_announcement(row, status["mesh"])
        now = int(time.time())
        with self._lock:
            self.database.update_rack_node(
                node_id,
                {
                    "state": json.dumps(state)[:8000],
                    "last_seen": now,
                    "last_error": "",
                },
            )
        tor = state.get("tor") if isinstance(state.get("tor"), dict) else {}
        self.database.add_rack_sample(
            node_id,
            {
                "at": now,
                "reachable": True,
                "load": _number(state.get("load")),
                "memory_percent": _number(state.get("memory_percent")),
                "storage_percent": _number(state.get("storage_percent")),
                "bootstrap": int(_number((tor or {}).get("bootstrap"))),
            },
        )

    def history(self, node_id: str, window: int = HISTORY_WINDOW_SECONDS) -> dict[str, Any]:
        """The readings kept for one node, and what they say about it.

        Availability is the share of probes that got an answer, not a share of
        time: the sweep visits a node about every ten minutes, and pretending
        to know what happened between two probes would be an invention.
        """
        row = self._require_node(node_id)
        window = max(600, min(int(window), HISTORY_WINDOW_SECONDS * 7))
        samples = self.database.rack_samples(node_id, int(time.time()) - window)
        answered = sum(1 for sample in samples if sample["reachable"])
        return {
            "node_id": node_id,
            "name": str(row["name"]),
            "window": window,
            "samples": samples,
            "readings": len(samples),
            "availability": round(100 * answered / len(samples), 1) if samples else None,
        }

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

    def _file_digest(self, name: str) -> str:
        """SHA-256 of one file of the appliance's own agent copy, or empty."""
        if not self.agent_dir:
            return ""
        try:
            return hashlib.sha256((self.agent_dir / name).read_bytes()).hexdigest()
        except OSError:
            return ""

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
        port = int(row["agent_port"] or 9080)
        token = self.token_for(node_id, epoch)
        digest = bundle_digest(self.agent_dir) if self.agent_dir else ""
        # The coordinator key travels in the command, not over the channel it
        # authenticates: a node that learned it from a map would accept the
        # first map it was handed, which is the whole thing the key prevents.
        coordinator = self.coordinator.public_key()
        lock = self.coordinator.lock()
        lock_argument = ""
        if lock["enabled"]:
            trustees = ",".join(lock["trustees"])
            lock_argument = f" --mesh-lock {lock['threshold']}:{trustees}"
        # Generated while the signed appliance archive is assembled. VERSION
        # may name an edge build whose stable tag does not exist; this full SHA
        # always names the commit containing the bundled agent.
        source_ref = (
            self.source_ref if SOURCE_REF_PATTERN.fullmatch(self.source_ref) else ""
        )
        # `--token-stdin` is what keeps the shared secret out of the node's
        # process list and shell history: the installer reads it from the
        # terminal. The operator pastes it at the prompt from the field the
        # interface shows beside this command.
        arguments = (
            f"--node {node_id}"
            f" --port {port}"
            f" --client-key {public}"
            f" --coordinator-key {coordinator}"
            f"{lock_argument}"
            " --token-stdin"
            " --yes"
        )
        pins = ""
        if digest:
            pins = f" --bundle-digest {digest}"
        else:
            # No reviewed copy to compare against — say so in the command
            # rather than let the installer quietly trust the download.
            pins = " --unverified-bundle"
        if source_ref:
            pins += f" --ref {source_ref}"
        bootstrap_ref = source_ref or "main"
        bootstrap_root = (
            f"{NODE_BOOTSTRAP_REPOSITORY}/{bootstrap_ref}/packaging/agent"
        )
        posix_bootstrap = f"{bootstrap_root}/bootstrap-node.sh"
        windows_bootstrap = f"{bootstrap_root}/bootstrap-node.ps1"
        # Downloaded to a file rather than piped into a shell, because the
        # bytes have to be weighed before they run. `mktemp` rather than a
        # fixed /tmp name: on a shared machine a predictable path in a
        # world-writable directory is somebody else's symlink.
        #
        # The bootstrap verifies the archive it downloads, so the command has
        # to verify the bootstrap — otherwise the one file nobody checks is
        # the file doing the checking. Both digests come from this appliance's
        # own copy, which arrived in a signed release.
        checkers = {"linux": "sha256sum -c -", "macos": "shasum -a 256 -c -"}
        bootstrap_digest = self._file_digest("bootstrap-node.sh")
        commands = {}
        for platform, checker in checkers.items():
            fetch = (
                's="$(mktemp)" && curl --proto \'=https\' --tlsv1.2 -fsSL '
                f'{posix_bootstrap} -o "$s"'
            )
            if bootstrap_digest:
                fetch += f" && printf '%s  %s' {bootstrap_digest} \"$s\" | {checker}"
            commands[platform] = (
                f'{fetch} && bash "$s" --platform {platform}{pins} {arguments}; rm -f "$s"'
            )
        windows_pins = ""
        if digest:
            windows_pins = f" -BundleDigest {digest}"
        else:
            windows_pins = " -UnverifiedBundle"
        windows_lock = ""
        if lock["enabled"]:
            windows_lock = f" -MeshLock {lock['threshold']}:{','.join(lock['trustees'])}"
        if source_ref:
            windows_pins += f" -Ref {source_ref}"
        commands["windows"] = (
            "$p=Join-Path $env:TEMP 'onionpi-node.ps1'; "
            "Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue; "
            "curl.exe --proto '=https' --tlsv1.2 -fsSL "
            f"{windows_bootstrap} -o $p; "
            "if ($LASTEXITCODE -ne 0) { throw 'Telechargement GitHub refuse' }; "
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p"
            f" -Node {node_id} -Port {port}"
            f" -ClientKey {public} -CoordinatorKey {coordinator}{windows_lock}"
            f" -TokenStdin{windows_pins} -Yes"
        )
        return {
            "node_id": node_id,
            "name": str(row["name"]),
            "token": token,
            "token_epoch": epoch,
            "agent_port": port,
            "client_public_key": public,
            "coordinator_key": coordinator,
            "mesh_lock": lock,
            "bundle_digest": digest,
            "source_ref": source_ref,
            "onion": str(row["onion"] or ""),
            # Kept for clients predating the multi-platform installer.
            "command": commands["linux"],
            "commands": commands,
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
        # The map follows the same rule as the policy: intent lives here, and
        # the sweep keeps handing it over until the node holds it. This is also
        # what bounds a revocation — a peer stays reachable until every node
        # has been visited, which is the sweep's own period.
        try:
            self.push_netmap(node_id)
        except (RackError, NodeError, MeshError) as error:
            logger.info("Carte non publiée vers %s: %s", view["name"], error)
        else:
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
        demo_node_ids: list[str] = []
        for kind, name, role, position, onion in demo:
            node_id = _node_id()
            demo_node_ids.append(node_id)
            self.database.create_rack_node(
                node_id,
                kind,
                {
                    "rack_id": rack_id,
                    "position": position,
                    "name": name,
                    "role": role,
                    "onion": onion,
                    # The address of a machine the demonstration Wi-Fi also
                    # reports, so the node carries a lease like a real one.
                    "mac": "6a:4f:12:8b:33:21" if kind == "local" else "",
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
                self._seed_history(node_id)
        if len(demo_node_ids) == 3:
            # Two peers of one overlay: the relay exposes ssh and its agent, the
            # storage node reaches ssh through a local forward. Enabled here so
            # the page shows a maillage that already carries something, rather
            # than an empty table nobody can judge.
            relay, storage, _ = demo_node_ids
            self.set_rules(
                relay,
                {"mesh": {"enabled": True, "ports": [22, 9080]}},
                announce=False,
            )
            self.set_rules(
                storage,
                {
                    "mesh": {
                        "enabled": True,
                        "ports": [22],
                        "forwards": [{"listen": 2222, "node": relay, "port": 22}],
                    }
                },
                announce=False,
            )
            for node_id in (relay, storage):
                self.push_netmap(node_id, force=True)
            self.create_cable(
                rack_id,
                demo_node_ids[0],
                1,
                demo_node_ids[1],
                1,
                "Relais vers stockage",
                "cyan",
            )
            self.create_cable(
                rack_id,
                demo_node_ids[0],
                2,
                demo_node_ids[2],
                1,
                "Accès poste de travail",
                "green",
            )
        for label, rules in (
            ("Poste de travail", {}),
            ("Serveur exposé", {"keep_open_ports": [22, 443]}),
            ("Mise au placard", {"access": "blocked"}),
        ):
            self.database.save_rack_profile(
                _rack_id(), label, json.dumps(clean_rules(rules))
            )

    def _seed_history(self, node_id: str) -> None:
        """A day of plausible readings, so the availability figure has a shape."""
        now = int(time.time())
        for index in range(96):
            at = now - (95 - index) * 900
            reachable = index not in {31, 32, 33}
            self.database.add_rack_sample(
                node_id,
                {
                    "at": at,
                    "reachable": reachable,
                    "load": 0.1 + 0.02 * (index % 7) if reachable else 0.0,
                    "memory_percent": 34 + (index % 11) if reachable else 0.0,
                    "storage_percent": 51 if reachable else 0.0,
                    "bootstrap": 100 if reachable else 0,
                },
            )
