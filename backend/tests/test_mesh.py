"""OnionMesh: les deux moitiés, et ce qu'elles refusent.

Le maillage est la seule partie d'OnionPi où deux machines se parlent sans
passer par l'appliance. Ce qui compte ici n'est donc pas qu'un cas nominal
fonctionne, mais que les refus tiennent: une carte rejouée, une carte périmée,
une clé statique que le coordinateur aurait choisie, un pair absent de la
carte, un port non habilité, une rotation non signée par la clé qu'elle
remplace.

Les primitives sont écrites en Python pur dans `packaging/agent/onionpi_mesh.py`
et validées ici contre les vecteurs des RFC 8032, 7748 et 8439. Le reste
vérifie que la moitié « baie », qui utilise `cryptography`, et la moitié
« nœud », qui n'utilise que la bibliothèque standard, produisent et acceptent
exactement les mêmes octets.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from onionpi import mesh as appliance
from onionpi.database import Database
from tests.test_node_agent import load

# ------------------------------------------------------------- fixtures ---


@pytest.fixture(scope="module")
def node() -> ModuleType:
    return load("onionpi_mesh", "onionpi_mesh.py")


@pytest.fixture(scope="module")
def runtime_module() -> ModuleType:
    return load("onionpi_mesh_runtime", "onionpi_mesh_runtime.py")


@pytest.fixture
def coordinator(tmp_path: Path) -> appliance.MeshCoordinator:
    database = Database(tmp_path / "mesh.db")
    database.initialize()
    return appliance.MeshCoordinator(tmp_path / "mesh.key", database)


def announce(node: ModuleType, identity: Any, node_id: str = "a" * 16) -> dict[str, Any]:
    return {"node": node_id, **identity.announcement()}


# ---------------------------------------------------------- primitives ---


def test_ed25519_matches_the_rfc_8032_vectors(node: ModuleType) -> None:
    seed = bytes.fromhex(
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
    )
    public = bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    )
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
        "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert node.ed25519_public(seed) == public
    assert node.ed25519_sign(seed, b"") == signature
    assert node.ed25519_verify(public, b"", signature)
    # Un bit de message change, la signature ne vaut plus rien.
    assert not node.ed25519_verify(public, b"\x00", signature)
    assert not node.ed25519_verify(public, b"", bytes(64))


def test_x25519_matches_the_rfc_7748_vectors(node: ModuleType) -> None:
    alice = bytes.fromhex(
        "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"
    )
    bob = bytes.fromhex(
        "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb"
    )
    shared = bytes.fromhex(
        "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"
    )
    assert node.x25519(alice, node.x25519_public(bob)) == shared
    assert node.x25519(bob, node.x25519_public(alice)) == shared


def test_chacha20_poly1305_matches_the_rfc_8439_vector(node: ModuleType) -> None:
    key = bytes(range(0x80, 0xA0))
    nonce = bytes.fromhex("070000004041424344454647")
    associated = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
    plaintext = (
        b"Ladies and Gentlemen of the class of '99: If I could offer you only "
        b"one tip for the future, sunscreen would be it."
    )
    sealed = node._aead_encrypt_pure(key, nonce, plaintext, associated)
    assert sealed[-16:].hex() == "1ae10b594f09e26a7e902ecbd0600691"
    assert node._aead_decrypt_pure(key, nonce, sealed, associated) == plaintext
    # Le chemin rapide, quand `cryptography` est là, doit être le même chiffre.
    assert node.aead_decrypt(key, nonce, node.aead_encrypt(key, nonce, plaintext, associated), associated) == plaintext
    with pytest.raises(ValueError):
        node._aead_decrypt_pure(key, nonce, sealed, b"un autre en-tete")


def test_a_tampered_frame_is_refused_rather_than_returned(node: ModuleType) -> None:
    initiator, responder = node.Identity.generate(), node.Identity.generate()
    first = node.HandshakeState(initiator.static_private)
    second = node.HandshakeState(responder.static_private)
    second.read_message_one(first.write_message_one(responder.static_public))
    message, _, receive = second.write_message_two()
    _, send, _ = first.read_message_two(message)
    sealed = bytearray(send.encrypt(b"charge utile"))
    sealed[0] ^= 0x01
    with pytest.raises(ValueError):
        receive.decrypt(bytes(sealed))


# --------------------------------------------------------- Noise IK ------


def test_the_handshake_authenticates_both_ends(node: ModuleType) -> None:
    initiator, responder = node.Identity.generate(), node.Identity.generate()
    first = node.HandshakeState(initiator.static_private)
    second = node.HandshakeState(responder.static_private)
    assert second.read_message_one(
        first.write_message_one(responder.static_public, b"salut")
    ) == b"salut"
    # IK: le répondeur apprend la clé statique de l'initiateur dans le premier
    # message. C'est ce qui permet de refuser un pair absent de la carte avant
    # d'ouvrir quoi que ce soit.
    assert second.rs == initiator.static_public
    message, responder_send, responder_receive = second.write_message_two(b"ok")
    payload, initiator_send, initiator_receive = first.read_message_two(message)
    assert payload == b"ok"
    assert responder_receive.decrypt(initiator_send.encrypt(b"ping")) == b"ping"
    assert initiator_receive.decrypt(responder_send.encrypt(b"pong")) == b"pong"


def test_dialling_the_wrong_static_key_produces_no_session(node: ModuleType) -> None:
    """Une adresse onion recopiée de travers donne « aucun pair », pas un pair.

    C'est toute la raison de chiffrer par-dessus Tor: le service onion
    authentifie le service joint, Noise authentifie l'identité du pair.
    """
    initiator, responder, stranger = (node.Identity.generate() for _ in range(3))
    first = node.HandshakeState(initiator.static_private)
    second = node.HandshakeState(responder.static_private)
    with pytest.raises(ValueError):
        second.read_message_one(first.write_message_one(stranger.static_public))


# ------------------------------------------------- identité et adresse ---


def test_both_halves_derive_the_same_address(node: ModuleType) -> None:
    identity = node.Identity.generate()
    assert appliance.mesh_address(identity.identity_public) == identity.address
    assert identity.address.startswith("fd7a:")


def test_the_address_is_a_function_of_the_key_alone(node: ModuleType) -> None:
    first, second = node.Identity.generate(), node.Identity.generate()
    assert first.address != second.address
    # Reconstruite depuis la seule clé publique: c'est ce qui la rend
    # vérifiable hors ligne, sans annuaire.
    reloaded = node.Identity(first.identity_seed, first.static_private)
    assert reloaded.address == first.address


def test_the_identity_file_is_created_once_and_only_readable_by_its_owner(
    node: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "identity.json"
    first = node.Identity.load(path)
    assert path.stat().st_mode & 0o077 == 0
    assert node.Identity.load(path).identity_public == first.identity_public


def test_the_appliance_refuses_a_static_key_it_would_have_chosen(
    node: ModuleType,
) -> None:
    """La baie autorise un nœud; elle ne décide pas avec quoi on chiffre pour lui."""
    honest, forged = node.Identity.generate(), node.Identity.generate()
    checked = appliance.verify_announcement(honest.announcement())
    assert checked["address"] == honest.address
    swapped = {**honest.announcement(), "static": forged.announcement()["static"]}
    with pytest.raises(appliance.MeshError):
        appliance.verify_announcement(swapped)


# ------------------------------------------------------------- cartes ---


def build(
    coordinator: appliance.MeshCoordinator,
    target: str,
    peers: list[dict[str, Any]],
    now: int | None = None,
) -> dict[str, Any]:
    body = coordinator.body(
        {"id": target, "rules": {"mesh": {"enabled": True, "ports": [22], "forwards": []}}},
        peers,
        now,
    )
    return coordinator.issue(body, now)


def test_a_map_signed_here_verifies_over_there(
    node: ModuleType, coordinator: appliance.MeshCoordinator
) -> None:
    peer = node.Identity.generate()
    document = build(coordinator, "b" * 16, [announce(node, peer)])
    key = node.decode_key(node.IDENTITY_PREFIX, coordinator.public_key())
    checked = node.verify_netmap(document, key, last_serial=0, now=int(time.time()))
    assert checked["serial"] == document["serial"]
    assert checked["peers"][0]["identity"] == peer.announcement()["identity"]


@pytest.mark.parametrize(
    ("label", "mutate", "last_serial", "clock"),
    [
        ("rejeu", lambda document: document, 99, 0),
        ("péremption", lambda document: document, 0, 10**10),
        ("signature", lambda document: {**document, "signature": "0" * 128}, 0, 0),
        ("version", lambda document: {**document, "version": 99}, 0, 0),
    ],
)
def test_a_map_is_refused_whole_or_not_at_all(
    node: ModuleType,
    coordinator: appliance.MeshCoordinator,
    label: str,
    mutate: Any,
    last_serial: int,
    clock: int,
) -> None:
    document = build(coordinator, "b" * 16, [announce(node, node.Identity.generate())])
    key = node.decode_key(node.IDENTITY_PREFIX, coordinator.public_key())
    with pytest.raises(node.NetmapError):
        node.verify_netmap(
            mutate(document),
            key,
            last_serial=last_serial,
            now=clock or int(time.time()),
        )


def test_a_peer_whose_static_key_is_unbound_takes_the_whole_map_down(
    node: ModuleType, coordinator: appliance.MeshCoordinator
) -> None:
    """Le coordinateur signe la carte; c'est le nœud qui lie sa clé statique.

    Sans ce refus, un coordinateur compromis choisirait la clé avec laquelle
    chacun chiffre pour un pair, et la signature de la carte le couvrirait.
    """
    honest, forged = node.Identity.generate(), node.Identity.generate()
    peer = {
        **announce(node, honest),
        "static": forged.announcement()["static"],
    }
    document = build(coordinator, "b" * 16, [peer])
    key = node.decode_key(node.IDENTITY_PREFIX, coordinator.public_key())
    with pytest.raises(node.NetmapError):
        node.verify_netmap(document, key, last_serial=0, now=int(time.time()))


def test_serials_only_ever_climb(coordinator: appliance.MeshCoordinator) -> None:
    serials = [coordinator.next_serial() for _ in range(5)]
    assert serials == sorted(set(serials))
    # Relu depuis la base: un redémarrage qui repartirait de zéro rendrait
    # toutes les cartes suivantes refusées par les nœuds.
    revived = appliance.MeshCoordinator(coordinator.key_path, coordinator.database)
    assert revived.next_serial() > serials[-1]


def test_the_digest_ignores_numbering_but_not_content(
    node: ModuleType, coordinator: appliance.MeshCoordinator
) -> None:
    peers = [announce(node, node.Identity.generate())]
    target = {"id": "b" * 16, "rules": {"mesh": {"enabled": True, "ports": [22]}}}
    first = coordinator.body(target, peers, now=1000)
    second = coordinator.body(target, peers, now=2000)
    assert coordinator.digest(first) == coordinator.digest(second)
    other = coordinator.body(target, [*peers, announce(node, node.Identity.generate(), "c" * 16)], now=1000)
    assert coordinator.digest(other) != coordinator.digest(first)


def test_a_revoked_key_is_announced_and_then_forgotten(
    coordinator: appliance.MeshCoordinator,
) -> None:
    identity = "ed25519:" + "1" * 64
    coordinator.revoke(identity, now=1000)
    assert identity in coordinator.revoked(now=1000)
    assert identity not in coordinator.revoked(now=1000 + appliance.REVOCATION_LIFETIME + 1)


# ------------------------------------------------------------- verrou ---


def test_the_lock_needs_k_distinct_trustees(node: ModuleType) -> None:
    trustees = [node.Identity.generate() for _ in range(3)]
    keys = [
        node.encode_key(node.IDENTITY_PREFIX, trustee.identity_public)
        for trustee in trustees
    ]
    lock = node.MeshLock(2, keys)
    peer = node.Identity.generate()
    identity = peer.announcement()["identity"]
    signatures = {
        keys[index]: node.sign_endorsement(
            trustees[index].identity_seed, "a" * 16, identity
        )
        for index in range(2)
    }
    assert lock.accepts("a" * 16, identity, signatures)
    # Une seule signature ne suffit pas, et un garant non déclaré ne compte pas.
    assert not lock.accepts("a" * 16, identity, dict(list(signatures.items())[:1]))
    outsider = node.Identity.generate()
    assert not lock.accepts(
        "a" * 16,
        identity,
        {
            keys[0]: signatures[keys[0]],
            node.encode_key(node.IDENTITY_PREFIX, outsider.identity_public): (
                node.sign_endorsement(outsider.identity_seed, "a" * 16, identity)
            ),
        },
    )
    # Une signature valide pour un autre nœud ne vaut pas pour celui-ci.
    assert not lock.accepts("b" * 16, identity, signatures)


def test_under_lock_a_new_peer_key_needs_its_endorsements(
    node: ModuleType, coordinator: appliance.MeshCoordinator
) -> None:
    trustees = [node.Identity.generate() for _ in range(2)]
    keys = [
        node.encode_key(node.IDENTITY_PREFIX, trustee.identity_public)
        for trustee in trustees
    ]
    coordinator.set_lock(True, 2, keys)
    lock = node.MeshLock(2, keys)
    peer = node.Identity.generate()
    entry = announce(node, peer)
    key = node.decode_key(node.IDENTITY_PREFIX, coordinator.public_key())
    bare = build(coordinator, "b" * 16, [entry])
    with pytest.raises(node.NetmapError):
        node.verify_netmap(bare, key, last_serial=0, now=int(time.time()), lock=lock)
    # Une clé déjà connue du nœud n'a pas à être re-signée à chaque carte.
    assert node.verify_netmap(
        bare,
        key,
        last_serial=0,
        now=int(time.time()),
        lock=lock,
        known_identities={entry["node"]: entry["identity"]},
    )
    endorsed = coordinator.check_endorsements(
        entry["node"],
        entry["identity"],
        {
            keys[index]: node.sign_endorsement(
                trustees[index].identity_seed, entry["node"], entry["identity"]
            )
            for index in range(2)
        },
    )
    signed = build(coordinator, "b" * 16, [{**entry, "endorsements": endorsed}])
    assert node.verify_netmap(signed, key, last_serial=0, now=int(time.time()), lock=lock)


def test_the_appliance_refuses_to_store_an_endorsement_it_cannot_check(
    node: ModuleType, coordinator: appliance.MeshCoordinator
) -> None:
    trustee, outsider = node.Identity.generate(), node.Identity.generate()
    trusted = node.encode_key(node.IDENTITY_PREFIX, trustee.identity_public)
    coordinator.set_lock(True, 1, [trusted])
    identity = node.Identity.generate().announcement()["identity"]
    with pytest.raises(appliance.MeshError):
        coordinator.check_endorsements("a" * 16, identity, {trusted: "00" * 64})
    with pytest.raises(appliance.MeshError):
        coordinator.check_endorsements(
            "a" * 16,
            identity,
            {
                node.encode_key(node.IDENTITY_PREFIX, outsider.identity_public): (
                    node.sign_endorsement(outsider.identity_seed, "a" * 16, identity)
                )
            },
        )


def test_a_lock_whose_threshold_exceeds_its_trustees_is_refused(
    node: ModuleType, coordinator: appliance.MeshCoordinator
) -> None:
    key = node.encode_key(node.IDENTITY_PREFIX, node.Identity.generate().identity_public)
    with pytest.raises(appliance.MeshError):
        coordinator.set_lock(True, 2, [key])
    with pytest.raises(ValueError):
        node.MeshLock(2, [key])


# -------------------------------------------------------------- règles ---


def test_mesh_rules_bound_what_a_sheet_can_express() -> None:
    cleaned = appliance.clean_mesh_rules(
        {"enabled": True, "ports": [22, 22, 9080], "forwards": []}
    )
    assert cleaned == {"enabled": True, "ports": [22, 9080], "forwards": []}
    for broken in (
        {"ports": [0]},
        {"ports": [True]},
        {"forwards": [{"listen": 2222, "node": "zz", "port": 22}]},
        {"forwards": [{"listen": 2222, "node": "a" * 16, "port": 70000}]},
        {
            "forwards": [
                {"listen": 2222, "node": "a" * 16, "port": 22},
                {"listen": 2222, "node": "b" * 16, "port": 22},
            ]
        },
    ):
        with pytest.raises(appliance.MeshError):
            appliance.clean_mesh_rules(broken)
    with pytest.raises(appliance.MeshError):
        appliance.clean_mesh_rules(
            {"forwards": [{"listen": 2222, "node": "a" * 16, "port": 22}]},
            known_nodes={"b" * 16},
        )


def test_a_node_without_a_key_or_an_address_is_not_in_the_map(
    node: ModuleType, coordinator: appliance.MeshCoordinator
) -> None:
    identity = node.Identity.generate().announcement()
    complete = {
        "id": "a" * 16,
        "name": "vps",
        "onion": "b" * 56,
        "rules": {"mesh": {"enabled": True, "ports": [22], "forwards": []}},
        "mesh_identity": identity["identity"],
        "mesh_static": identity["static"],
        "mesh_static_signature": identity["static_signature"],
        "mesh_address": identity["address"],
    }
    assert coordinator.peer_entry(complete) is not None
    assert coordinator.peer_entry({**complete, "onion": ""}) is None
    assert coordinator.peer_entry({**complete, "mesh_identity": ""}) is None
    assert coordinator.peer_entry({**complete, "rules": {"mesh": {"enabled": False}}}) is None


# ------------------------------------------------------- plan de données ---


class Echo:
    """Un service local trivial, pour vérifier que des octets traversent."""

    def __init__(self) -> None:
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                stream, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._echo, args=(stream,), daemon=True).start()

    @staticmethod
    def _echo(stream: socket.socket) -> None:
        with stream:
            while True:
                data = stream.recv(4096)
                if not data:
                    return
                stream.sendall(data.upper())


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def signed_map(
    node: ModuleType,
    seed: bytes,
    target: str,
    peers: list[dict[str, Any]],
    *,
    grants: list[int],
    forwards: list[dict[str, Any]],
    serial: int = 1,
) -> dict[str, Any]:
    return node.sign_netmap(
        seed,
        {
            "version": node.NETMAP_VERSION,
            "serial": serial,
            "issued_at": int(time.time()),
            "not_after": int(time.time()) + 3600,
            "node": target,
            "grants": grants,
            "peers": peers,
            "forwards": forwards,
            "revoked": [],
        },
    )


@pytest.fixture
def wired(
    node: ModuleType, runtime_module: ModuleType, tmp_path: Path
) -> Any:
    """Deux nœuds, une carte chacun, un service à joindre au bout."""
    echo = Echo()
    boss = node.Identity.generate()
    caller, callee = node.Identity.generate(), node.Identity.generate()
    caller_id, callee_id = "a" * 16, "b" * 16
    mesh_port, listen = free_port(), free_port()

    def entry(identity: Any, node_id: str, port: int = 0) -> dict[str, Any]:
        line = {"node": node_id, **identity.announcement(), "onion": "c" * 56}
        if port:
            line |= {"mesh_v4": "127.0.0.1", "mesh_port": port}
        return line

    responder = runtime_module.MeshRuntime(
        callee,
        tmp_path / "callee",
        callee_id,
        coordinator_key=node.encode_key(node.IDENTITY_PREFIX, boss.identity_public),
        port=mesh_port,
        direct_address="",
    )
    (tmp_path / "callee").mkdir()
    (tmp_path / "caller").mkdir()
    responder.state_dir = tmp_path / "callee"
    responder.netmap_path = tmp_path / "callee" / "netmap.json"
    responder.accept(
        signed_map(
            node,
            boss.identity_seed,
            callee_id,
            [entry(caller, caller_id)],
            grants=[echo.port],
            forwards=[],
        )
    )
    responder.start()

    initiator = runtime_module.MeshRuntime(
        caller,
        tmp_path / "caller",
        caller_id,
        coordinator_key=node.encode_key(node.IDENTITY_PREFIX, boss.identity_public),
        port=free_port(),
    )
    initiator.netmap_path = tmp_path / "caller" / "netmap.json"
    initiator.accept(
        signed_map(
            node,
            boss.identity_seed,
            caller_id,
            [entry(callee, callee_id, mesh_port)],
            grants=[],
            forwards=[{"listen": listen, "node": callee_id, "port": echo.port}],
        )
    )
    initiator.start()
    try:
        yield {
            "echo": echo,
            "listen": listen,
            "mesh_port": mesh_port,
            "boss": boss,
            "caller": caller,
            "callee": callee,
            "responder": responder,
            "initiator": initiator,
        }
    finally:
        initiator.stop()
        responder.stop()
        echo.server.close()


def talk(port: int, message: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as stream:
        stream.sendall(message)
        return stream.recv(4096)


def test_a_forward_carries_bytes_end_to_end_over_the_direct_path(wired: Any) -> None:
    assert talk(wired["listen"], b"bonjour") == b"BONJOUR"


def test_a_peer_absent_from_the_map_gets_nothing(
    node: ModuleType, runtime_module: ModuleType, wired: Any
) -> None:
    """Aucune réponse, pas même un refus: un inconnu n'apprend pas qu'il y a une carte."""
    stranger = node.Identity.generate()
    handshake = node.HandshakeState(stranger.static_private)
    with socket.create_connection(("127.0.0.1", wired["mesh_port"]), timeout=10) as stream:
        stream.sendall(
            node.frame(
                handshake.write_message_one(
                    wired["callee"].static_public, json.dumps({"port": 22}).encode()
                )
            )
        )
        stream.settimeout(5)
        with pytest.raises((ConnectionError, OSError)):
            node.read_frame(stream)


def test_a_port_outside_the_grants_is_refused_by_the_responder(
    node: ModuleType, wired: Any
) -> None:
    """Les deux extrémités appliquent la carte, pas seulement l'initiateur."""
    handshake = node.HandshakeState(wired["caller"].static_private)
    with socket.create_connection(("127.0.0.1", wired["mesh_port"]), timeout=10) as stream:
        stream.sendall(
            node.frame(
                handshake.write_message_one(
                    wired["callee"].static_public, json.dumps({"port": 4242}).encode()
                )
            )
        )
        payload, _, _ = handshake.read_message_two(node.read_frame(stream))
        answer = json.loads(payload.decode())
    assert answer["ok"] is False
    assert "habilité" in answer["detail"]


def test_a_replayed_map_does_not_reinstate_a_removed_peer(
    node: ModuleType, wired: Any
) -> None:
    initiator = wired["initiator"]
    serial = initiator.serial()
    empty = signed_map(
        node,
        wired["boss"].identity_seed,
        "a" * 16,
        [],
        grants=[],
        forwards=[],
        serial=serial + 1,
    )
    initiator.accept(empty)
    assert initiator.peers() == []
    with pytest.raises(node.NetmapError):
        initiator.accept(empty)


def test_a_stored_map_is_verified_again_when_it_is_read_back(
    node: ModuleType, runtime_module: ModuleType, tmp_path: Path
) -> None:
    """Le fichier appartient à l'agent: le relire sans vérifier ferait d'un
    compte capable d'y écrire un coordinateur."""
    boss = node.Identity.generate()
    identity = node.Identity.generate()
    state = tmp_path / "state"
    state.mkdir()
    key = node.encode_key(node.IDENTITY_PREFIX, boss.identity_public)
    document = signed_map(
        node,
        boss.identity_seed,
        "a" * 16,
        [{"node": "b" * 16, **node.Identity.generate().announcement(), "onion": "c" * 56}],
        grants=[22],
        forwards=[],
    )
    (state / "netmap.json").write_text(json.dumps(document), encoding="utf-8")
    revived = runtime_module.MeshRuntime(
        identity, state, "a" * 16, coordinator_key=key, port=free_port()
    )
    assert len(revived.peers()) == 1
    tampered = {**document, "grants": [22, 3389]}
    (state / "netmap.json").write_text(json.dumps(tampered), encoding="utf-8")
    forged = runtime_module.MeshRuntime(
        identity, state, "a" * 16, coordinator_key=key, port=free_port()
    )
    assert forged.peers() == []


def test_a_map_addressed_to_another_node_is_refused(
    node: ModuleType, runtime_module: ModuleType, tmp_path: Path
) -> None:
    boss = node.Identity.generate()
    state = tmp_path / "state"
    state.mkdir()
    runtime = runtime_module.MeshRuntime(
        node.Identity.generate(),
        state,
        "a" * 16,
        coordinator_key=node.encode_key(node.IDENTITY_PREFIX, boss.identity_public),
        port=free_port(),
    )
    with pytest.raises(node.NetmapError):
        runtime.accept(
            signed_map(node, boss.identity_seed, "b" * 16, [], grants=[], forwards=[])
        )


def test_without_a_pinned_coordinator_no_map_is_accepted(
    node: ModuleType, runtime_module: ModuleType, tmp_path: Path
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    runtime = runtime_module.MeshRuntime(
        node.Identity.generate(), state, "a" * 16, port=free_port()
    )
    boss = node.Identity.generate()
    with pytest.raises(node.NetmapError):
        runtime.accept(
            signed_map(node, boss.identity_seed, "a" * 16, [], grants=[], forwards=[])
        )
