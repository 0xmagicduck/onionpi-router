from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from onionpi.database import (
    RACK_NODE_FIELDS,
    RACK_NODE_INSERT,
    RACK_NODE_UPDATE,
    Database,
)
from onionpi.nodeclient import NodeClient, NodeError, sign
from onionpi.rack import (
    MAX_NODES,
    MAX_PROFILES,
    MAX_RACKS,
    RackError,
    RackManager,
    clean_onion,
    clean_rules,
    policy_document,
)

ONION = "a" * 56
OTHER_ONION = "b" * 56


class FakeGuard:
    def __init__(self) -> None:
        self.blocked: set[str] = set()

    def blocked_macs(self) -> set[str]:
        return set(self.blocked)

    def block(self, mac: str, label: str = "") -> list[dict[str, Any]]:
        self.blocked.add(mac)
        return []

    def unblock(self, mac: str) -> list[dict[str, Any]]:
        self.blocked.discard(mac)
        return []


class FakeAccess:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str, Any]] = []
        self.removed: list[str] = []

    def update(
        self, mac: str, alias: str = "", paused: Any = None, schedule: Any = None
    ) -> dict[str, Any]:
        self.updates.append((mac, alias, schedule))
        return {}

    def remove(self, mac: str) -> dict[str, Any]:
        self.removed.append(mac)
        return {}


class FakeController:
    def __init__(self) -> None:
        self.authorised: dict[str, str] = {}
        self.demo_mode = False

    def add_client_auth(self, address: str, private_key: str) -> None:
        self.authorised[address] = private_key

    def remove_client_auth(self, address: str) -> None:
        self.authorised.pop(address, None)


class RecordingClient(NodeClient):
    """A node that answers, and remembers what it was asked."""

    def __init__(self) -> None:
        super().__init__(demo_mode=True)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail = False

    def call(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((kwargs["node_id"], kwargs["verb"], kwargs.get("payload") or {}))
        if self.fail:
            raise NodeError("nœud injoignable")
        return super().call(**kwargs)


WIFI = [
    {
        "name": "Portable Camille",
        "ip": "10.42.0.10",
        "mac": "aa:bb:cc:dd:ee:01",
        "download": 1_000,
        "upload": 200,
        "online": True,
    },
    {
        "name": "Tablette",
        "ip": "10.42.0.11",
        "mac": "aa:bb:cc:dd:ee:02",
        "download": 0,
        "upload": 0,
        "online": False,
    },
]


@pytest.fixture
def manager(tmp_path: Path) -> RackManager:
    database = Database(tmp_path / "rack.db")
    database.initialize()
    return RackManager(
        database,
        tmp_path / "rack.key",
        FakeGuard(),
        FakeAccess(),
        RecordingClient(),
        FakeController(),
        wifi_view=lambda: [dict(device) for device in WIFI],
    )


def remote(manager: RackManager, name: str = "vps", onion: str = ONION) -> dict[str, Any]:
    return manager.create_node("remote", name, "Relais", onion=onion)


# ------------------------------------------------------------- validation ---


def test_the_written_statements_cover_every_mutable_column() -> None:
    """The two literal statements and the field order must not drift apart."""
    for statement, placeholders in ((RACK_NODE_INSERT, 18), (RACK_NODE_UPDATE, 16)):
        assert statement.count("?") == placeholders
        for field in RACK_NODE_FIELDS:
            assert field in statement
    insert_order = [
        field for field in RACK_NODE_FIELDS if field in RACK_NODE_INSERT
    ]
    assert sorted(insert_order, key=RACK_NODE_INSERT.index) == list(RACK_NODE_FIELDS)
    assert sorted(insert_order, key=RACK_NODE_UPDATE.index) == list(RACK_NODE_FIELDS)


def test_onion_address_is_accepted_in_the_shapes_people_paste() -> None:
    assert clean_onion(f"http://{ONION}.onion:9080/") == ONION
    assert clean_onion(f"{ONION}.onion") == ONION
    assert clean_onion(ONION.upper()) == ONION
    assert clean_onion("") == ""
    with pytest.raises(RackError):
        clean_onion("pas-une-adresse.onion")


def test_rules_are_normalised_and_refused_when_absurd() -> None:
    rules = clean_rules({"egress": "tor-only", "keep_open_ports": [22, 22, 443]})
    assert rules["keep_open_ports"] == [22, 443]
    assert rules["access"] == "allowed"
    # An empty list means "the default", never "close everything": a node with
    # no reachable port and no console is a node nobody can repair.
    assert clean_rules({})["keep_open_ports"] == [22]
    for broken in (
        {"access": "quelquefois"},
        {"egress": "clair"},
        {"exit_country": "Suède"},
        {"keep_open_ports": [70000]},
        {"keep_open_ports": ["vingt-deux"]},
    ):
        with pytest.raises(RackError):
            clean_rules(broken)


def test_policy_digest_ignores_key_order_but_not_content() -> None:
    first = policy_document(clean_rules({"keep_open_ports": [443, 22]}), False)
    second = policy_document(clean_rules({"keep_open_ports": [22, 443]}), False)
    assert first["digest"] == second["digest"]
    assert policy_document(clean_rules({}), True)["digest"] != first["digest"]


# --------------------------------------------------------------- topology ---


def test_racks_and_nodes_respect_their_ceilings(manager: RackManager) -> None:
    for index in range(MAX_RACKS):
        manager.create_rack(f"Baie {index}", "Salon", 4)
    with pytest.raises(RackError):
        manager.create_rack("Une de trop", "", 4)
    snapshot = manager.snapshot()
    assert len(snapshot["racks"]) == MAX_RACKS
    assert snapshot["limits"]["max_nodes"] == MAX_NODES


def test_moving_into_an_occupied_slot_swaps_instead_of_overwriting(
    manager: RackManager,
) -> None:
    rack_id = manager.create_rack("Baie", "", 6)["racks"][0]["id"]
    first = remote(manager, "vps-a", ONION)["id"]
    second = remote(manager, "vps-b", OTHER_ONION)["id"]
    manager.move_node(first, rack_id, 2)
    manager.move_node(second, rack_id, 5)
    manager.move_node(second, rack_id, 2)
    positions = {node["id"]: node["position"] for node in manager.snapshot()["nodes"]}
    assert positions[second] == 2
    assert positions[first] == 5


def test_a_slot_outside_the_frame_is_refused(manager: RackManager) -> None:
    rack_id = manager.create_rack("Baie", "", 3)["racks"][0]["id"]
    node_id = remote(manager)["id"]
    with pytest.raises(RackError):
        manager.move_node(node_id, rack_id, 4)
    manager.move_node(node_id, rack_id, 3)
    with pytest.raises(RackError):
        manager.update_rack(rack_id, "Baie", "", 2)


def test_deleting_a_rack_keeps_its_machines(manager: RackManager) -> None:
    rack_id = manager.create_rack("Baie", "", 4)["racks"][0]["id"]
    node_id = remote(manager)["id"]
    manager.move_node(node_id, rack_id, 1)
    snapshot = manager.delete_rack(rack_id)
    assert snapshot["racks"] == []
    assert [node["id"] for node in snapshot["nodes"]] == [node_id]
    assert snapshot["nodes"][0]["rack_id"] == ""


def test_the_same_machine_cannot_be_racked_twice(manager: RackManager) -> None:
    manager.create_node("local", "Portable", mac="AA:BB:CC:DD:EE:01")
    with pytest.raises(RackError):
        manager.create_node("local", "Le même", mac="aa:bb:cc:dd:ee:01")
    remote(manager)
    with pytest.raises(RackError):
        remote(manager, "doublon", ONION)


def test_cables_persist_and_one_port_cannot_be_used_twice(manager: RackManager) -> None:
    rack_id = manager.create_rack("Baie", "Salon", 6)["racks"][0]["id"]
    source = remote(manager, "routeur", ONION)["id"]
    target = remote(manager, "stockage", OTHER_ONION)["id"]
    manager.move_node(source, rack_id, 1)
    manager.move_node(target, rack_id, 2)

    snapshot = manager.create_cable(
        rack_id, source, 1, target, 2, "Sauvegardes", "violet", "1-gbps"
    )
    assert snapshot["cables"][0]["label"] == "Sauvegardes"
    assert snapshot["cables"][0]["status"] in {"online", "offline"}
    assert manager.database.stats()["rack_cables"] == 1

    with pytest.raises(RackError, match="déjà câblé"):
        manager.create_cable(rack_id, source, 1, target, 3)
    with pytest.raises(RackError, match="deux appareils"):
        manager.create_cable(rack_id, source, 2, source, 3)

    cable_id = snapshot["cables"][0]["id"]
    assert manager.delete_cable(cable_id)["cables"] == []


def test_moving_a_node_to_another_rack_removes_its_old_cables(
    manager: RackManager,
) -> None:
    manager.create_rack("Première", "", 4)
    racks = manager.create_rack("Seconde", "", 4)["racks"]
    first_rack = next(rack["id"] for rack in racks if rack["name"] == "Première")
    second_rack = next(rack["id"] for rack in racks if rack["name"] == "Seconde")
    source = remote(manager, "a", ONION)["id"]
    target = remote(manager, "b", OTHER_ONION)["id"]
    manager.move_node(source, first_rack, 1)
    manager.move_node(target, first_rack, 2)
    manager.create_cable(first_rack, source, 1, target, 1)

    assert manager.move_node(source, second_rack, 1)["cables"] == []


# ------------------------------------------------------------ enforcement ---


def test_local_rules_are_delegated_never_reimplemented(manager: RackManager) -> None:
    node = manager.create_node("local", "Console", mac="aa:bb:cc:dd:ee:02")
    manager.set_rules(node["id"], {"access": "blocked"})
    assert "aa:bb:cc:dd:ee:02" in manager.guard.blocked_macs()
    assert manager.node(node["id"])["status"] == "isolated"
    manager.set_rules(node["id"], {"access": "allowed"})
    assert manager.guard.blocked_macs() == set()
    # The alias and the window went to the access manager, not to a second
    # copy of the schedule logic living in the rack.
    assert manager.access.updates[-1][0] == "aa:bb:cc:dd:ee:02"


def test_remote_rules_are_pushed_to_the_agent(manager: RackManager) -> None:
    node = manager.create_node("remote", "vps", onion=ONION)
    manager.set_rules(node["id"], {"egress": "tor-only", "keep_open_ports": [22, 2222]})
    pushed = [call for call in manager.client.calls if call[1] == "apply-policy"]
    assert pushed, "les règles doivent partir vers le nœud"
    assert pushed[-1][2]["keep_open_ports"] == [22, 2222]
    assert pushed[-1][2]["digest"] == manager.node(node["id"])["policy_digest"]


def test_a_node_without_an_address_is_pending_and_pushes_nothing(
    manager: RackManager,
) -> None:
    node = manager.create_node("remote", "vps-en-attente")
    assert node["status"] == "pending"
    manager.set_rules(node["id"], {"access": "blocked"})
    assert not [call for call in manager.client.calls if call[1] == "apply-policy"]


def test_a_refused_push_still_stores_the_intent(manager: RackManager) -> None:
    node = manager.create_node("remote", "vps", onion=ONION)
    manager.client.fail = True
    manager.set_rules(node["id"], {"access": "blocked"})
    stored = manager.node(node["id"])
    assert stored["rules"]["access"] == "blocked"
    assert "injoignable" in stored["last_error"]


def test_only_the_published_verbs_can_be_fired_by_hand(manager: RackManager) -> None:
    node = manager.create_node("remote", "vps", onion=ONION)
    manager.run_action(node["id"], "new-identity")
    for refused in ("apply-policy", "rm", "status; reboot"):
        with pytest.raises(RackError):
            manager.run_action(node["id"], refused)
    with pytest.raises(RackError):
        manager.run_action(node["id"], "journal", "tor; rm -rf /")


def test_a_status_reading_is_folded_into_the_node(manager: RackManager) -> None:
    node = manager.create_node("remote", "vps", onion=ONION)
    manager.refresh(node["id"])
    refreshed = manager.node(node["id"])
    assert refreshed["state"]["agent_version"]
    assert refreshed["last_seen"] > 0
    assert refreshed["status"] == "online"


# ---------------------------------------------------------------- profils ---


def test_a_profile_writes_the_same_rules_a_node_form_writes(manager: RackManager) -> None:
    manager.save_profile("", "Serveur exposé", {"keep_open_ports": [22, 443]})
    profile = manager.snapshot()["profiles"][0]
    assert profile["rules"]["keep_open_ports"] == [22, 443]
    node = manager.create_node("remote", "vps", onion=ONION)
    manager.bulk("profile", [node["id"]], profile["id"])
    assert manager.node(node["id"])["rules"]["keep_open_ports"] == [22, 443]
    with pytest.raises(RackError):
        manager.save_profile("", "Impossible", {"egress": "clair"})


def test_profiles_respect_their_ceiling_and_can_be_replaced(manager: RackManager) -> None:
    for index in range(MAX_PROFILES):
        manager.save_profile("", f"Profil {index}", {})
    with pytest.raises(RackError):
        manager.save_profile("", "Un de trop", {})
    first = manager.snapshot()["profiles"][0]
    manager.save_profile(first["id"], "Renommé", {"access": "blocked"})
    replaced = next(
        item for item in manager.snapshot()["profiles"] if item["id"] == first["id"]
    )
    assert replaced["name"] == "Renommé"
    assert replaced["rules"]["access"] == "blocked"
    assert len(manager.snapshot()["profiles"]) == MAX_PROFILES
    manager.delete_profile(first["id"])
    assert len(manager.snapshot()["profiles"]) == MAX_PROFILES - 1


# ------------------------------------------------------------- en nombre ----


def test_a_group_action_reports_what_it_could_not_do(manager: RackManager) -> None:
    reachable = manager.create_node("remote", "vps", onion=ONION)["id"]
    pending = manager.create_node("remote", "vps-sans-adresse")["id"]
    manager.client.fail = True
    answer = manager.bulk("refresh", [reachable, pending, "0" * 16])
    # The node without an address is not a failure: refreshing it is a no-op.
    assert answer["applied"] == 1
    assert [failure["id"] for failure in answer["failures"]] == [reachable, "0" * 16]
    assert "injoignable" in answer["failures"][0]["message"]
    with pytest.raises(RackError):
        manager.bulk("rm -rf", [reachable])


def test_isolating_a_group_goes_through_the_same_rule_sheet(manager: RackManager) -> None:
    first = manager.create_node("local", "Poste", mac="aa:bb:cc:dd:ee:01")["id"]
    second = manager.create_node("local", "Console", mac="aa:bb:cc:dd:ee:02")["id"]
    manager.bulk("isolate", [first, second])
    assert manager.guard.blocked_macs() == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"}
    manager.bulk("allow", [first, second])
    assert manager.guard.blocked_macs() == set()


# ------------------------------------------------------------ découverte ----


def test_wifi_clients_are_offered_until_they_are_racked(manager: RackManager) -> None:
    assert {device["mac"] for device in manager.snapshot()["discovered"]} == {
        "aa:bb:cc:dd:ee:01",
        "aa:bb:cc:dd:ee:02",
    }
    rack_id = manager.create_rack("Baie", "", 4)["racks"][0]["id"]
    answer = manager.import_devices(["aa:bb:cc:dd:ee:01", "pas-une-adresse"], rack_id)
    assert answer["applied"] == 1
    assert answer["failures"][0]["id"] == "pas-une-adresse"
    snapshot = answer["snapshot"]
    imported = next(node for node in snapshot["nodes"] if node["mac"] == "aa:bb:cc:dd:ee:01")
    assert imported["name"] == "Portable Camille"
    assert (imported["rack_id"], imported["position"]) == (rack_id, 1)
    # Its lease is read on the node's line, and it is no longer offered twice.
    assert imported["link"] == {"ip": "10.42.0.10", "online": True, "download": 1_000, "upload": 200}
    assert [device["mac"] for device in snapshot["discovered"]] == ["aa:bb:cc:dd:ee:02"]


def test_arranging_a_rack_closes_the_gaps_without_reordering(manager: RackManager) -> None:
    rack_id = manager.create_rack("Baie", "", 8)["racks"][0]["id"]
    first = manager.create_node("remote", "vps-a", onion=ONION)["id"]
    second = manager.create_node("remote", "vps-b", onion=OTHER_ONION)["id"]
    manager.move_node(first, rack_id, 4)
    manager.move_node(second, rack_id, 7)
    positions = {
        node["id"]: node["position"] for node in manager.arrange_rack(rack_id)["nodes"]
    }
    assert (positions[first], positions[second]) == (1, 2)


# --------------------------------------------------------------- lecture ----


def test_availability_counts_answered_probes_not_elapsed_time(
    manager: RackManager,
) -> None:
    node_id = manager.create_node("remote", "vps", onion=ONION)["id"]
    manager.refresh(node_id)
    manager.refresh(node_id)
    manager.client.fail = True
    with pytest.raises(NodeError):
        manager.refresh(node_id)
    history = manager.history(node_id)
    assert history["readings"] == 3
    assert history["availability"] == pytest.approx(66.7)
    assert history["samples"][0]["memory_percent"] > 0
    assert history["samples"][-1]["reachable"] == 0


def test_a_node_carries_the_alerts_its_own_sheet_justifies(manager: RackManager) -> None:
    node_id = manager.create_node("remote", "vps", onion=ONION)["id"]
    manager.set_rules(node_id, {"egress": "direct"})
    messages = " ".join(alert["message"] for alert in manager.node(node_id)["alerts"])
    assert "Sortie directe" in messages
    quiet = manager.create_node("local", "Poste", mac="aa:bb:cc:dd:ee:01")
    assert quiet["alerts"] == []
    waiting = manager.create_node("remote", "vps-neuf")
    assert [alert["level"] for alert in waiting["alerts"]] == ["info"]


# ---------------------------------------------------------------- secrets ---


def test_credentials_are_derived_and_never_stored(manager: RackManager) -> None:
    node = manager.create_node("remote", "vps", onion=ONION)
    bundle = manager.enrollment(node["id"])
    assert len(bundle["token"]) == 64
    assert bundle["token"] == manager.enrollment(node["id"])["token"]
    assert bundle["command"] == bundle["commands"]["linux"]
    assert "curl --proto '=https' --tlsv1.2 -fsSL" in bundle["commands"]["linux"]
    assert "--platform macos" in bundle["commands"]["macos"]
    assert "curl.exe --proto '=https' --tlsv1.2 -fsSL" in bundle["commands"]["windows"]
    assert "if ($LASTEXITCODE -ne 0)" in bundle["commands"]["windows"]
    assert bundle["token"] in bundle["commands"]["windows"]
    row = manager.database.rack_node(node["id"])
    assert row is not None
    assert bundle["token"] not in json.dumps(row)
    # Tor reads a client public key as unpadded base32: 52 characters, 32 bytes.
    assert len(bundle["client_public_key"]) == 52
    assert len(base64.b32decode(bundle["client_public_key"] + "====")) == 32


def test_rotation_invalidates_the_previous_token_and_key(manager: RackManager) -> None:
    node = manager.create_node("remote", "vps", onion=ONION)
    before = manager.enrollment(node["id"])
    after = manager.rotate_token(node["id"])
    assert after["token"] != before["token"]
    assert after["client_public_key"] != before["client_public_key"]
    assert after["token_epoch"] == before["token_epoch"] + 1
    assert manager.controller.authorised[ONION]


def test_two_nodes_never_share_a_credential(manager: RackManager) -> None:
    first = manager.create_node("remote", "a", onion=ONION)["id"]
    second = manager.create_node("remote", "b", onion=OTHER_ONION)["id"]
    assert manager.enrollment(first)["token"] != manager.enrollment(second)["token"]
    assert (
        manager.enrollment(first)["client_public_key"]
        != manager.enrollment(second)["client_public_key"]
    )


def test_the_master_secret_is_written_once_and_only_readable_by_its_owner(
    manager: RackManager,
) -> None:
    node = manager.create_node("remote", "vps", onion=ONION)
    token = manager.enrollment(node["id"])["token"]
    assert manager.key_path.stat().st_mode & 0o077 == 0
    assert manager.enrollment(node["id"])["token"] == token


def test_changing_the_address_drops_the_reading_and_the_authorisation(
    manager: RackManager,
) -> None:
    node = manager.create_node("remote", "vps", onion=ONION)
    manager.refresh(node["id"])
    manager.update_node(node["id"], "vps", onion=OTHER_ONION)
    moved = manager.node(node["id"])
    assert moved["last_seen"] == 0
    assert moved["state"] == {}
    assert ONION not in manager.controller.authorised
    assert OTHER_ONION in manager.controller.authorised


def test_deleting_a_node_revokes_its_onion_authorisation(manager: RackManager) -> None:
    node = manager.create_node("remote", "vps", onion=ONION)
    assert ONION in manager.controller.authorised
    manager.delete_node(node["id"])
    assert ONION not in manager.controller.authorised


# --------------------------------------------------------------- protocol ---


def test_the_signature_covers_the_verb_the_time_and_the_body() -> None:
    token = "f" * 64
    body = b'{"a":1}'
    reference = sign(token, "status", 1_700_000_000, "abcd", body)
    assert reference != sign(token, "reboot", 1_700_000_000, "abcd", body)
    assert reference != sign(token, "status", 1_700_000_001, "abcd", body)
    assert reference != sign(token, "status", 1_700_000_000, "abce", body)
    assert reference != sign(token, "status", 1_700_000_000, "abcd", b'{"a":2}')
    assert reference != sign("e" * 64, "status", 1_700_000_000, "abcd", body)
    assert hashlib.sha256(body).hexdigest() not in reference


def test_an_unknown_verb_never_reaches_the_network() -> None:
    client = NodeClient(demo_mode=False)
    with pytest.raises(NodeError):
        client.call(
            node_id="0" * 16,
            name="vps",
            onion=ONION,
            port=9080,
            token="f" * 64,
            verb="rm-rf",
        )
