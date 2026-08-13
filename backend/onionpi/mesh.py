"""OnionMesh: le coordinateur du réseau superposé.

Ce module tient la moitié « baie » de `docs/onionmesh.md`. Il ne chiffre aucun
flux et n'ouvre aucune session: le plan de données est entre nœuds, pas ici. Ce
qu'il fait tient en trois phrases.

* Il détient **une** clé Ed25519, celle du coordinateur. Elle signe des cartes
  du réseau, rien d'autre. Un nœud n'accepte une carte que d'elle, parce que sa
  moitié publique est épinglée sur le nœud à l'installation.
* Il **transporte** des identités, il n'en fabrique pas. La clé d'un nœud est
  générée sur le nœud; ce qui remonte est la moitié publique, accompagnée de la
  signature qui lie sa clé statique X25519 à son identité. Cette signature est
  revérifiée ici avant qu'une clé entre en base: une clé statique choisie par
  le centre serait une clé avec laquelle le centre déchiffre.
* Il **numérote** les cartes. Un compteur strictement croissant, un `not_after`
  borné, une liste de révoqués. Un nœud refuse une carte dont le numéro
  n'augmente pas — sans quoi un rejeu réinstalle un pair révoqué, et une
  révocation qui se rejoue n'en est pas une.

La rotation d'une clé de nœud est signée par l'ancienne clé du nœud, pas par le
coordinateur: perdre `mesh.key` oblige à re-signer les cartes, jamais à
ré-enrôler les machines.

Sous **verrou de maillage**, le coordinateur cesse d'être un point unique dont
la compromission ouvre tout: une clé de pair nouvelle n'est acceptée par les
nœuds que contresignée par K garants sur N, et ces contre-signatures ne sont
que transportées d'ici.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from .atomic_io import atomic_write_text

logger = logging.getLogger("onionpi.mesh")

#: Version du document de carte. Distincte du protocole d'appel: la carte peut
#: évoluer sans toucher aux signatures d'appel, et un nœud refuse une version
#: qu'il ne connaît pas plutôt que d'en deviner les champs.
NETMAP_VERSION = 1

#: Durée de validité d'une carte. Une journée: assez long pour qu'un nœud
#: injoignable une nuit ne se coupe pas du maillage, assez court pour borner ce
#: qu'un coordinateur muet ou saisi laisse tourner.
NETMAP_LIFETIME = 24 * 3600

#: Combien de temps une clé révoquée reste annoncée comme telle. Au-delà, elle
#: n'est plus dans aucune carte valide et la répéter n'apprend rien.
REVOCATION_LIFETIME = 7 * 24 * 3600
MAX_REVOKED = 64

#: Ports qu'un nœud peut exposer au maillage, et redirections locales qu'il
#: peut ouvrir. Des plafonds, pas des réglages: la carte voyage à chaque
#: balayage et une fiche sans borne devient un document sans borne.
MAX_MESH_PORTS = 8
MAX_MESH_FORWARDS = 8

#: Port d'écoute du plan de données sur la boucle locale d'un nœud, publié
#: comme second port virtuel de son service onion.
DEFAULT_MESH_PORT = 9081

#: Les domaines signés. Identiques à ceux de `packaging/agent/onionpi_mesh.py`,
#: et `backend/tests/test_mesh_crypto.py` vérifie que les deux moitiés
#: produisent et acceptent exactement les mêmes octets.
STATIC_BINDING = b"onionpi-mesh/1/static"
NETMAP_BINDING = b"onionpi-mesh/1/netmap"
ENDORSEMENT_BINDING = b"onionpi-mesh/1/endorse"
ROTATION_BINDING = b"onionpi-mesh/1/rotate"

IDENTITY_PREFIX = "ed25519:"
STATIC_PREFIX = "x25519:"
KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HEX64_PATTERN = re.compile(r"^[0-9a-f]{128}$")
ADDRESS_PREFIX = bytes.fromhex("fd7a0000")

#: Clés du magasin de réglages. Le numéro de série vit là plutôt qu'en mémoire:
#: un redémarrage qui repart de zéro rendrait toutes les cartes suivantes
#: refusées par les nœuds, ce qui est un maillage muet jusqu'à réenrôlement.
SERIAL_KEY = "mesh.serial"
LOCK_KEY = "mesh.lock"
REVOKED_KEY = "mesh.revoked"

MAX_TRUSTEES = 8


class MeshError(RuntimeError):
    """La demande est refusée, et rien n'a été publié."""


# ------------------------------------------------------------- primitives ---


def encode_key(prefix: str, raw: bytes) -> str:
    return prefix + raw.hex()


def decode_key(prefix: str, value: Any) -> bytes:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise MeshError("Clé de maillage mal formée.")
    raw = value[len(prefix) :]
    if not KEY_PATTERN.fullmatch(raw):
        raise MeshError("Clé de maillage mal formée.")
    return bytes.fromhex(raw)


def canonical(document: dict[str, Any]) -> bytes:
    """La forme exacte que les deux moitiés signent et vérifient."""
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _compress_v6(groups: list[str]) -> str:
    trimmed = [group.lstrip("0") or "0" for group in groups]
    best_start, best_length, start, length = -1, 0, -1, 0
    for index, group in enumerate([*trimmed, "x"]):
        if group == "0":
            start = index if start < 0 else start
            length += 1
            continue
        if length > best_length:
            best_start, best_length = start, length
        start, length = -1, 0
    if best_length < 2:
        return ":".join(trimmed)
    head = ":".join(trimmed[:best_start])
    tail = ":".join(trimmed[best_start + best_length :])
    return f"{head}::{tail}"


def mesh_address(identity_public: bytes) -> str:
    """`fd7a:0000:` puis 96 bits de SHA-256 de la clé d'identité.

    Un ULA (RFC 4193), donc jamais routé sur Internet, et auto-certifiant: il
    n'y a rien à attribuer, aucune collision à corriger à la main, et prendre
    l'adresse d'un autre demanderait une préimage de SHA-256. C'est ce qui
    manquait au plan `10.43.X.Y`, dérivé de deux octets de MAC.
    """
    digest = hashlib.sha256(identity_public).digest()
    raw = ADDRESS_PREFIX + digest[-12:]
    return _compress_v6([raw[index : index + 2].hex() for index in range(0, 16, 2)])


def verify_signature(public: bytes, message: bytes, signature: str) -> bool:
    if not isinstance(signature, str) or not HEX64_PATTERN.fullmatch(signature):
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(
            bytes.fromhex(signature), message
        )
    except (InvalidSignature, ValueError):
        return False
    return True


def static_message(static_public: bytes) -> bytes:
    return STATIC_BINDING + b"\n" + static_public


def rotation_message(previous_identity: str, identity: str) -> bytes:
    return ROTATION_BINDING + b"\n" + f"{previous_identity}\n{identity}".encode()


def endorsement_message(node_id: str, identity: str) -> bytes:
    return ENDORSEMENT_BINDING + b"\n" + f"{node_id}\n{identity}".encode()


def netmap_message(document: dict[str, Any]) -> bytes:
    body = {key: value for key, value in document.items() if key != "signature"}
    return NETMAP_BINDING + b"\n" + canonical(body)


def verify_announcement(announcement: Any) -> dict[str, str]:
    """L'annonce d'un nœud, si elle tient debout. Sinon `MeshError`.

    La clé statique doit être signée par la clé d'identité annoncée: c'est le
    nœud qui décide avec quoi on chiffre pour lui, pas la carte.
    """
    if not isinstance(announcement, dict):
        raise MeshError("Annonce de maillage illisible.")
    identity = str(announcement.get("identity", ""))
    static = str(announcement.get("static", ""))
    signature = str(announcement.get("static_signature", ""))
    identity_raw = decode_key(IDENTITY_PREFIX, identity)
    static_raw = decode_key(STATIC_PREFIX, static)
    if not verify_signature(identity_raw, static_message(static_raw), signature):
        raise MeshError("Clé statique non signée par l'identité du nœud.")
    return {
        "identity": identity,
        "static": static,
        "static_signature": signature,
        "address": mesh_address(identity_raw),
    }


def demo_identity(material: str) -> dict[str, str]:
    """Une identité de démonstration, reproductible et réellement signée.

    Le mode démonstration n'a pas de nœud à interroger, mais la baie vérifie ce
    qu'un nœud annonce: fabriquer une annonce non signée ferait passer la
    vérification pour du décor. Celle-ci est une vraie paire de clés, dérivée
    du nom pour que deux exécutions montrent la même adresse.
    """
    root = hashlib.sha256(f"onionpi-demo/mesh/{material}".encode()).digest()
    identity = Ed25519PrivateKey.from_private_bytes(root)
    static = X25519PrivateKey.from_private_bytes(
        hashlib.sha256(root + b"/static").digest()
    )
    identity_public = identity.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    static_public = static.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return {
        "identity": encode_key(IDENTITY_PREFIX, identity_public),
        "static": encode_key(STATIC_PREFIX, static_public),
        "static_signature": identity.sign(static_message(static_public)).hex(),
        "address": mesh_address(identity_public),
    }


def demo_rotation(previous_material: str, material: str) -> dict[str, str]:
    """Une rotation de démonstration, signée par l'ancienne clé comme une vraie."""
    previous = demo_identity(previous_material)
    announcement = demo_identity(material)
    seed = hashlib.sha256(f"onionpi-demo/mesh/{previous_material}".encode()).digest()
    key = Ed25519PrivateKey.from_private_bytes(seed)
    announcement["rotation_signature"] = key.sign(
        rotation_message(previous["identity"], announcement["identity"])
    ).hex()
    return announcement


def clean_mesh_rules(document: Any, known_nodes: set[str] | None = None) -> dict[str, Any]:
    """Valide la moitié maillage d'une fiche de règles.

    `ports` est ce que ce nœud accepte de présenter à ses pairs; `forwards` est
    ce qu'il ouvre chez lui vers un pair. Les deux extrémités appliquent les
    habilitations, donc une redirection déclarée ici ne suffit pas: le pair visé
    doit avoir ouvert le port de son côté.
    """
    source = document if isinstance(document, dict) else {}
    enabled = bool(source.get("enabled", False))
    ports: list[int] = []
    for item in list(source.get("ports", []) or [])[:MAX_MESH_PORTS]:
        port = _port(item)
        if port not in ports:
            ports.append(port)
    forwards: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in list(source.get("forwards", []) or [])[:MAX_MESH_FORWARDS]:
        if not isinstance(item, dict):
            raise MeshError("Redirection de maillage illisible.")
        node = str(item.get("node", ""))
        if not re.fullmatch(r"[0-9a-f]{16}", node):
            raise MeshError("Redirection de maillage: nœud inconnu.")
        if known_nodes is not None and node not in known_nodes:
            raise MeshError("Redirection de maillage: nœud inconnu.")
        listen = _port(item.get("listen"))
        if listen in seen:
            raise MeshError("Deux redirections écoutent le même port local.")
        seen.add(listen)
        forwards.append({"listen": listen, "node": node, "port": _port(item.get("port"))})
    return {"enabled": enabled, "ports": sorted(ports), "forwards": forwards}


def _port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise MeshError("Port de maillage invalide: entier entre 1 et 65535.") from error
    if isinstance(value, bool) or not 1 <= port <= 65535:
        raise MeshError("Port de maillage invalide: entier entre 1 et 65535.")
    return port


# ----------------------------------------------------------- coordinateur ---


class MeshCoordinator:
    """La clé du coordinateur, le compteur de cartes et le verrou de maillage."""

    def __init__(self, key_path: Path, database: Any, demo_mode: bool = False) -> None:
        self.key_path = key_path
        self.database = database
        self.demo_mode = demo_mode

    # -- clé --

    def _seed(self) -> bytes:
        """La graine du coordinateur, créée à la première utilisation.

        Même compromis que la clé du service onion: le fichier appartient à
        l'application, et le perdre oblige à re-signer les cartes — pas à
        ré-enrôler les nœuds, dont les identités leur appartiennent.
        """
        try:
            raw = self.key_path.read_text().strip()
        except OSError:
            raw = ""
        if KEY_PATTERN.fullmatch(raw):
            return bytes.fromhex(raw)
        seed = secrets.token_hex(32)
        try:
            atomic_write_text(self.key_path, seed + "\n", mode=0o600)
        except OSError as error:
            raise MeshError(
                f"Écriture impossible de la clé de maillage {self.key_path}: {error}"
            ) from error
        return bytes.fromhex(seed)

    def public_key(self) -> str:
        private = Ed25519PrivateKey.from_private_bytes(self._seed())
        raw = private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return encode_key(IDENTITY_PREFIX, raw)

    def sign(self, message: bytes) -> str:
        return Ed25519PrivateKey.from_private_bytes(self._seed()).sign(message).hex()

    # -- verrou de maillage --

    def lock(self) -> dict[str, Any]:
        stored = self.database.setting(LOCK_KEY, None)
        if not isinstance(stored, dict):
            return {"enabled": False, "threshold": 0, "trustees": []}
        trustees = [str(value) for value in stored.get("trustees", [])][:MAX_TRUSTEES]
        threshold = int(stored.get("threshold", 0) or 0)
        enabled = bool(stored.get("enabled", False)) and 1 <= threshold <= len(trustees)
        return {"enabled": enabled, "threshold": threshold, "trustees": trustees}

    def set_lock(self, enabled: bool, threshold: int, trustees: list[str]) -> dict[str, Any]:
        cleaned: list[str] = []
        for trustee in trustees[:MAX_TRUSTEES]:
            decode_key(IDENTITY_PREFIX, trustee)
            if trustee not in cleaned:
                cleaned.append(trustee)
        if enabled and not 1 <= threshold <= len(cleaned):
            raise MeshError(
                "Le seuil doit être compris entre 1 et le nombre de garants déclarés."
            )
        document = {
            "enabled": bool(enabled),
            "threshold": int(threshold),
            "trustees": cleaned,
        }
        self.database.set_setting(LOCK_KEY, document)
        return self.lock()

    def endorsement_target(self, node_id: str, identity: str) -> str:
        """Le message qu'un garant signe, en hexadécimal, pour un outil hors ligne."""
        return endorsement_message(node_id, identity).hex()

    def check_endorsements(self, node_id: str, identity: str, endorsements: Any) -> dict[str, str]:
        """Ne garde que les contre-signatures valides d'un garant déclaré.

        Filtrées ici pour que la fiche d'un nœud dise la vérité sur ce qu'elle
        contient. Ce n'est pas la sécurité du verrou: celle-ci est chez le nœud,
        qui revérifie tout contre son propre `mesh.lock`.
        """
        if not isinstance(endorsements, dict):
            raise MeshError("Contre-signatures illisibles.")
        trustees = set(self.lock()["trustees"])
        message = endorsement_message(node_id, identity)
        kept: dict[str, str] = {}
        for trustee, signature in list(endorsements.items())[: MAX_TRUSTEES * 2]:
            name = str(trustee)
            if name not in trustees:
                raise MeshError("Contre-signature d'un garant qui n'est pas déclaré.")
            if not verify_signature(decode_key(IDENTITY_PREFIX, name), message, str(signature)):
                raise MeshError("Contre-signature invalide.")
            kept[name] = str(signature)
        return kept

    # -- révocations --

    def revoke(self, identity: str, now: int | None = None) -> None:
        """Inscrit une clé au tableau des révoquées, le temps de son `not_after`."""
        moment = int(time.time()) if now is None else now
        stored = self.database.setting(REVOKED_KEY, [])
        entries = [
            entry
            for entry in (stored if isinstance(stored, list) else [])
            if isinstance(entry, dict)
            and int(entry.get("at", 0)) + REVOCATION_LIFETIME > moment
            and str(entry.get("identity", "")) != identity
        ]
        entries.append({"identity": identity, "at": moment})
        self.database.set_setting(REVOKED_KEY, entries[-MAX_REVOKED:])

    def revoked(self, now: int | None = None) -> list[str]:
        moment = int(time.time()) if now is None else now
        stored = self.database.setting(REVOKED_KEY, [])
        return [
            str(entry.get("identity", ""))
            for entry in (stored if isinstance(stored, list) else [])
            if isinstance(entry, dict)
            and int(entry.get("at", 0)) + REVOCATION_LIFETIME > moment
        ]

    # -- cartes --

    def next_serial(self) -> int:
        """Le numéro de la prochaine carte. Strictement croissant, persistant."""
        current = self.database.setting(SERIAL_KEY, 0)
        serial = int(current) + 1 if isinstance(current, int) and current > 0 else 1
        self.database.set_setting(SERIAL_KEY, serial)
        return serial

    def peer_entry(self, node: dict[str, Any]) -> dict[str, Any] | None:
        """La ligne d'un nœud dans une carte, ou None s'il n'y a pas sa place.

        Un nœud sans identité annoncée, sans adresse onion ou dont le maillage
        est désactivé n'y figure pas: une ligne sans clé serait une adresse que
        personne ne peut authentifier.
        """
        mesh = node.get("rules", {}).get("mesh", {})
        if not mesh.get("enabled"):
            return None
        identity = str(node.get("mesh_identity", ""))
        static = str(node.get("mesh_static", ""))
        if not identity or not static or not node.get("onion"):
            return None
        entry: dict[str, Any] = {
            "node": str(node["id"]),
            "name": str(node.get("name", ""))[:48],
            "identity": identity,
            "static": static,
            "static_signature": str(node.get("mesh_static_signature", "")),
            "address": str(node.get("mesh_address", "")),
            "onion": str(node["onion"]),
            "mesh_port": DEFAULT_MESH_PORT,
            "grants": list(mesh.get("ports", [])),
        }
        direct = str(node.get("mesh_v4", ""))
        if direct:
            # Le chemin direct: `bat0` porte le même Noise que le flux onion,
            # sans les six sauts. Absent, le pair n'essaie tout simplement pas.
            entry["mesh_v4"] = direct
        endorsements = node.get("mesh_endorsements") or {}
        if endorsements:
            entry["endorsements"] = endorsements
        return entry

    def body(
        self,
        target: dict[str, Any],
        peers: list[dict[str, Any]],
        now: int | None = None,
    ) -> dict[str, Any]:
        """Le contenu de la carte d'un nœud, avant numérotation et signature.

        Elle ne contient pas le nœud lui-même: il connaît sa propre clé, et
        l'inscrire donnerait à un coordinateur compromis un moyen de lui faire
        accepter une identité qui n'est pas la sienne.
        """
        moment = int(time.time()) if now is None else now
        mesh = target.get("rules", {}).get("mesh", {})
        known = {peer["node"] for peer in peers}
        return {
            "version": NETMAP_VERSION,
            "node": str(target["id"]),
            "coordinator": self.public_key(),
            # Ce que ce nœud accepte de présenter à ses pairs. Il l'applique
            # depuis la carte, pas depuis une configuration locale: c'est ce qui
            # fait que les deux extrémités appliquent la même habilitation.
            "grants": list(mesh.get("ports", [])),
            "peers": peers,
            # Une redirection vers un pair absent de la carte n'ouvrirait qu'un
            # port local qui refuse tout: on ne la publie pas.
            "forwards": [
                forward
                for forward in mesh.get("forwards", [])
                if forward["node"] in known
            ],
            "revoked": self.revoked(moment),
        }

    def issue(self, body: dict[str, Any], now: int | None = None) -> dict[str, Any]:
        """Numérote, date et signe une carte. Consomme un numéro de série."""
        moment = int(time.time()) if now is None else now
        document = {
            **body,
            "serial": self.next_serial(),
            "issued_at": moment,
            "not_after": moment + NETMAP_LIFETIME,
        }
        document["signature"] = self.sign(netmap_message(document))
        return document

    @staticmethod
    def digest(body: dict[str, Any]) -> str:
        """L'empreinte d'un contenu de carte, hors numéro de série et dates.

        Elle répond à « le contenu a-t-il changé », que le balayage pose à
        chaque tour: sans elle, publier une carte identique chaque minute
        userait les circuits pour ne rien dire.
        """
        stripped = {
            key: value
            for key, value in body.items()
            if key not in ("serial", "issued_at", "not_after", "signature")
        }
        return hashlib.sha256(canonical(stripped)).hexdigest()
