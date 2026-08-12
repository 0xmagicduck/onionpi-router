from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="onionpi-tests-"))
os.environ.update(
    {
        "ONIONPI_DATA_DIR": str(TEST_ROOT),
        "ONIONPI_SHARED_DIR": str(TEST_ROOT / "shared"),
        "ONIONPI_DB_PATH": str(TEST_ROOT / "test.db"),
        "ONIONPI_SESSION_SECRET": "test-secret-that-is-not-used-in-production",
        "ONIONPI_DEMO_MODE": "1",
    }
)

from fastapi.testclient import TestClient  # noqa: E402
from onionpi.auth import hash_password  # noqa: E402
from onionpi.main import app, database  # noqa: E402

PASSWORD = "phrase-de-test-tres-solide"


def login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
    )
    assert response.status_code == 200
    assert response.cookies.get("onionpi_session")
    return response.json()["csrf"]


def test_auth_status_and_security_headers() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        assert client.get("/api/v1/status").status_code == 401
        csrf = login(client)
        response = client.get("/api/v1/status")
        assert response.status_code == 200
        assert response.json()["tor"]["connected"] is True
        assert response.json()["protection"]["status"] == "demo"
        assert response.json()["protection"]["safe"] is False
        assert response.headers["x-frame-options"] == "DENY"
        assert csrf


def test_password_reset_revokes_existing_session() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        login(client)
        assert client.get("/api/v1/auth/session").status_code == 200
        database.create_user("admin", "Camille", hash_password("nouvelle-phrase-secrete-solide"))
        assert client.get("/api/v1/auth/session").status_code == 401


def test_mutations_require_csrf_and_paths_stay_in_share() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        rejected = client.post(
            "/api/v1/files/folders",
            json={"parent": "", "name": "Sans jeton"},
        )
        assert rejected.status_code == 403

        created = client.post(
            "/api/v1/files/folders",
            json={"parent": "", "name": "Partage test"},
            headers={"X-CSRF-Token": csrf},
        )
        assert created.status_code == 201

        uploaded = client.post(
            "/api/v1/files/upload",
            data={"path": "Partage test"},
            files={"file": ("bonjour.txt", b"bonjour onionpi", "text/plain")},
            headers={"X-CSRF-Token": csrf},
        )
        assert uploaded.status_code == 201

        listing = client.get("/api/v1/files", params={"path": "Partage test"})
        assert [item["name"] for item in listing.json()["items"]] == ["bonjour.txt"]
        download = client.get(
            "/api/v1/files/download", params={"path": "Partage test/bonjour.txt"}
        )
        assert download.content == b"bonjour onionpi"
        assert client.get("/api/v1/files", params={"path": "../"}).status_code == 400


def test_password_change_requires_the_current_one_and_closes_sessions() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        wrong = client.post(
            "/api/v1/auth/password",
            json={"current_password": "mauvais-mot-de-passe", "new_password": "phrase-de-remplacement"},
            headers={"X-CSRF-Token": csrf},
        )
        assert wrong.status_code == 403

        changed = client.post(
            "/api/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": "phrase-de-remplacement"},
            headers={"X-CSRF-Token": csrf},
        )
        assert changed.status_code == 200
        assert client.get("/api/v1/auth/session").status_code == 401
        assert client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "phrase-de-remplacement"},
        ).status_code == 200


def test_health_is_public_and_unknown_api_routes_return_json_404() -> None:
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        # The one endpoint an unauthenticated Wi-Fi client can reach must not
        # say which advisories apply to this appliance.
        assert "version" not in health.json()
        # The SPA fallback must not answer 200 with index.html for the API.
        unknown = client.get("/api/v1/does-not-exist")
        assert unknown.status_code == 404
        assert unknown.json()["detail"]


def test_request_body_limits_apply_before_parsing() -> None:
    with TestClient(app) as client:
        oversized = client.post(
            "/api/v1/auth/login",
            content=b"x" * (1024**2 + 1),
            headers={"Content-Type": "application/json"},
        )
        assert oversized.status_code == 413
        assert oversized.json()["detail"] == "Requête trop volumineuse"

        # A valid-size multipart body reaches the auth dependency before the
        # multipart parser or upload destination is touched.
        unauthenticated = client.post(
            "/api/v1/files/upload",
            files={"file": ("intrus.txt", b"contenu", "text/plain")},
        )
        assert unauthenticated.status_code == 401


def test_diagnostics_are_authenticated_and_run_real_database_checks() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        assert client.get("/api/v1/system/diagnostics").status_code == 401
        login(client)

        response = client.get("/api/v1/system/diagnostics")

        assert response.status_code == 200
        report = response.json()
        assert report["status"] == "ok"
        assert {check["id"] for check in report["checks"]} >= {
            "database",
            "storage",
            "tor-bootstrap",
            "agent",
        }
        assert report["database"]["users"] >= 1


def test_upload_refuses_when_storage_reserve_is_not_met() -> None:
    from onionpi import main as main_module

    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        original = main_module.settings
        object.__setattr__(original, "storage_reserve_bytes", 1 << 62)
        try:
            response = client.post(
                "/api/v1/files/upload",
                data={"path": ""},
                files={"file": ("plein.txt", b"x" * 32, "text/plain")},
                headers={"X-CSRF-Token": csrf},
            )
        finally:
            object.__setattr__(original, "storage_reserve_bytes", 512 * 1024**2)
        assert response.status_code == 507


def test_foreign_origin_is_rejected_on_mutations() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        response = client.post(
            "/api/v1/files/folders",
            json={"parent": "", "name": "Origine externe"},
            headers={"X-CSRF-Token": csrf, "Origin": "https://exemple.invalid"},
        )
        assert response.status_code == 403


def test_circumvention_requires_a_session_and_reports_transports() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        assert client.get("/api/v1/circumvention").status_code == 401
        login(client)
        payload = client.get("/api/v1/circumvention").json()
        assert payload["mode"] in {"direct", "auto", "manual"}
        assert {item["id"] for item in payload["transports"]} == {"snowflake", "obfs4", "meek"}
        assert payload["relay"]["installed"] is True


def test_circumvention_rejects_an_invalid_bridge_line() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        response = client.post(
            "/api/v1/circumvention",
            json={
                "mode": "manual",
                "transport": "obfs4",
                "country": "FR",
                "custom_bridges": ["SocksPort 9050"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 400
        assert client.post(
            "/api/v1/circumvention",
            json={"mode": "elsewhere", "transport": "obfs4", "country": "FR", "custom_bridges": []},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 422


def test_snowflake_relay_toggle_requires_csrf() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        assert client.post("/api/v1/relay/snowflake", json={"enabled": True}).status_code == 403
        response = client.post(
            "/api/v1/relay/snowflake",
            json={"enabled": True},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert response.json()["relay"]["active"] is True
        disabled = client.post(
            "/api/v1/relay/snowflake",
            json={"enabled": False},
            headers={"X-CSRF-Token": csrf},
        )
        assert disabled.status_code == 200
        assert disabled.json()["relay"]["active"] is False


def test_chat_persists_messages() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        login(client)
        with client.websocket_connect("/api/v1/chat/ws") as websocket:
            history = websocket.receive_json()
            assert history["type"] == "history"
            presence = websocket.receive_json()
            assert presence == {"type": "presence", "online": 1}
            websocket.send_json({"type": "message", "body": "Bonjour le réseau"})
            message = websocket.receive_json()
            assert message["type"] == "message"
            assert message["message"]["body"] == "Bonjour le réseau"


def test_device_blocking_round_trip() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        assert client.post(
            "/api/v1/devices/block",
            json={"mac": "aa:bb:cc:dd:ee:ff", "blocked": True},
        ).status_code == 403

        response = client.post(
            "/api/v1/devices/block",
            json={"mac": "AA:BB:CC:DD:EE:FF", "label": "Tablette", "blocked": True},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert response.json()["blocked"][0]["mac"] == "aa:bb:cc:dd:ee:ff"

        # A blocked device stops answering ARP, so it is listed even though it
        # no longer shows up in the neighbour table.
        listing = client.get("/api/v1/devices").json()
        blocked = [device for device in listing["devices"] if device["blocked"]]
        assert [device["mac"] for device in blocked] == ["aa:bb:cc:dd:ee:ff"]

        # Too short for the schema, then well-sized but not a MAC: both refused.
        assert client.post(
            "/api/v1/devices/block",
            json={"mac": "aa:bb", "blocked": True},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 422
        assert client.post(
            "/api/v1/devices/block",
            json={"mac": "pas-une-adresse", "blocked": True},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 400
        assert client.post(
            "/api/v1/devices/block",
            json={"mac": "aa:bb:cc:dd:ee:ff", "blocked": False},
            headers={"X-CSRF-Token": csrf},
        ).json()["blocked"] == []


def test_dns_filter_endpoints() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        assert client.get("/api/v1/dns-filter").json()["enabled"] is False

        response = client.post(
            "/api/v1/dns-filter",
            json={"profiles": [], "custom_blocked": ["pub.example.com"], "allowed": []},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert response.json()["custom_blocked"] == ["pub.example.com"]

        assert client.post(
            "/api/v1/dns-filter",
            json={"profiles": ["inconnue"], "custom_blocked": [], "allowed": []},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 400


def test_tor_policy_speedtest_and_onion() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        advanced = client.get("/api/v1/tor/advanced").json()
        assert advanced["policy"]["exit_country"] == ""
        assert advanced["circuits"]

        policy = client.post(
            "/api/v1/tor/policy",
            json={"exit_country": "SE", "rotation_seconds": 3600},
            headers={"X-CSRF-Token": csrf},
        )
        assert policy.status_code == 200
        assert policy.json()["exit_country"] == "SE"
        assert client.post(
            "/api/v1/tor/policy",
            json={"exit_country": "ZZ", "rotation_seconds": 0},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 400

        speed = client.post("/api/v1/tor/speedtest", headers={"X-CSRF-Token": csrf})
        assert speed.status_code == 200
        assert speed.json()["download_mbps"] > 0

        onion = client.post(
            "/api/v1/onion", json={"enabled": True}, headers={"X-CSRF-Token": csrf}
        )
        assert onion.status_code == 200
        assert onion.json()["address"].endswith(".onion")


def test_system_actions_and_configuration_export() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        assert client.post(
            "/api/v1/system/action",
            json={"action": "reboot"},
        ).status_code == 403
        # Rejected by the schema, then by the allow-list.
        assert client.post(
            "/api/v1/system/action",
            json={"action": "rm -rf /"},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 422
        assert client.post(
            "/api/v1/system/action",
            json={"action": "rm-rf-slash"},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 400

        assert client.post(
            "/api/v1/system/action",
            json={"action": "restart-tor"},
            headers={"X-CSRF-Token": csrf},
        ).json()["status"] == "ok"

        document = client.get("/api/v1/system/config").json()
        assert document["version"] == 1
        # An export must never carry a secret out of the appliance.
        serialized = str(document)
        assert "password" not in serialized and "onion.key" not in serialized

        restored = client.post(
            "/api/v1/system/config",
            json={"document": document},
            headers={"X-CSRF-Token": csrf},
        )
        assert restored.status_code == 200
        assert restored.json()["failures"] == []
        assert client.post(
            "/api/v1/system/config",
            json={"document": {"version": 99}},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 400


def test_imported_configuration_obeys_the_same_limits_as_the_endpoints() -> None:
    from onionpi import main as main_module

    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        oversized = {
            "version": 1,
            # Well past the 40 the POST /circumvention schema accepts, and past
            # the 2000 domains and 512 devices the other two allow.
            "circumvention": {
                "mode": "manual",
                "transport": "obfs4",
                "country": "FR",
                "custom_bridges": ["192.0.2.1:443"] * 60,
            },
            "dns_filter": {
                "profiles": [],
                "custom_blocked": [f"pub{index}.example.com" for index in range(2500)],
                "allowed": [],
            },
            "blocked_devices": [
                {"mac": "aa:bb:cc:dd:ee:ff", "label": "x"} for _ in range(main_module.MAX_IMPORTED_DEVICES + 5)
            ],
        }
        response = client.post(
            "/api/v1/system/config",
            json={"document": oversized},
            headers={"X-CSRF-Token": csrf},
        )

        assert response.status_code == 200
        failures = " ".join(response.json()["failures"])
        assert "Contournement" in failures
        assert "Filtrage DNS" in failures
        assert "Appareils bloqués" in failures
        # None of the oversized sections was applied.
        state = client.get("/api/v1/circumvention").json()
        assert len(state["custom_bridges"]) < 60


def test_a_path_with_a_null_byte_is_refused_rather_than_crashing() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        login(client)
        assert client.get("/api/v1/files", params={"path": "photos\x00.jpg"}).status_code == 400


def test_update_page_reads_state_and_refuses_bad_schedules() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        assert client.get("/api/v1/system/update").status_code == 401
        csrf = login(client)

        state = client.get("/api/v1/system/update").json()
        assert state["installed"]
        assert state["channel"] in {"stable", "edge"}

        assert client.post(
            "/api/v1/system/update/settings",
            json={"channel": "edge", "schedule": "03:00,15:00", "enabled": True, "apply": False},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 200
        assert client.get("/api/v1/system/update").json()["channel"] == "edge"

        # A schedule systemd would not understand never reaches the timer.
        assert client.post(
            "/api/v1/system/update/settings",
            json={"channel": "stable", "schedule": "25:00", "enabled": True, "apply": True},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 400
        # Neither does a channel outside the two published ones.
        assert client.post(
            "/api/v1/system/update/settings",
            json={"channel": "nightly", "schedule": "04:30", "enabled": True, "apply": True},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 422
        # Mutations still need the CSRF token.
        assert client.post("/api/v1/system/update/run").status_code == 403


def test_device_access_rules_drive_the_effective_block_list() -> None:
    from onionpi.main import services

    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        assert client.get("/api/v1/devices/access").json()["rules"] == []

        assert client.post(
            "/api/v1/devices/access/pause",
            json={"mac": "6a:4f:12:8b:33:21", "minutes": 30},
        ).status_code == 403

        paused = client.post(
            "/api/v1/devices/access/pause",
            json={"mac": "6a:4f:12:8b:33:21", "minutes": 30},
            headers={"X-CSRF-Token": csrf},
        )
        assert paused.status_code == 200
        assert paused.json()["rules"][0]["state"] == "paused"
        assert services.device_guard.policy_blocks == {"6a:4f:12:8b:33:21"}

        # The listing carries the alias and the state, so one poll feeds the page.
        named = client.post(
            "/api/v1/devices/access",
            json={
                "mac": "6a:4f:12:8b:33:21",
                "alias": "Portable de Camille",
                "schedule": {
                    "enabled": True,
                    "days": [0, 1, 2, 3, 4],
                    "start": "07:00",
                    "end": "21:00",
                },
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert named.status_code == 200
        listing = client.get("/api/v1/devices").json()
        device = next(
            item for item in listing["devices"] if item["mac"] == "6a:4f:12:8b:33:21"
        )
        assert device["name"] == "Portable de Camille"
        assert device["access_state"] in {"allowed", "outside", "paused"}
        assert listing["access"]["rules"]

        # An hour outside the accepted range never reaches the scheduler.
        assert client.post(
            "/api/v1/devices/access",
            json={
                "mac": "6a:4f:12:8b:33:21",
                "alias": "Portable",
                "schedule": {"enabled": True, "days": [0], "start": "31:00", "end": "21:00"},
            },
            headers={"X-CSRF-Token": csrf},
        ).status_code == 400
        assert client.post(
            "/api/v1/devices/access/pause",
            json={"mac": "6a:4f:12:8b:33:21", "minutes": 100000},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 422

        removed = client.post(
            "/api/v1/devices/access/remove",
            json={"mac": "6a:4f:12:8b:33:21"},
            headers={"X-CSRF-Token": csrf},
        )
        assert removed.status_code == 200
        assert removed.json()["rules"] == []
        assert client.post(
            "/api/v1/devices/access/remove",
            json={"mac": "6a:4f:12:8b:33:21"},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 404


def test_onion_client_authorisation_endpoints() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        client.post("/api/v1/onion", json={"enabled": True}, headers={"X-CSRF-Token": csrf})

        created = client.post(
            "/api/v1/onion/clients",
            json={"name": "Téléphone"},
            headers={"X-CSRF-Token": csrf},
        )
        assert created.status_code == 200
        assert ":descriptor:x25519:" in created.json()["private_key"]
        assert created.json()["onion"]["client_auth"] is True

        # The key is shown once: no endpoint hands it out a second time.
        advanced = client.get("/api/v1/tor/advanced").json()
        assert advanced["onion"]["clients"][0]["name"] == "Téléphone"
        assert "private_key" not in str(advanced["onion"])

        assert client.post(
            "/api/v1/onion/clients",
            json={"name": "Téléphone"},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 400
        assert client.post(
            "/api/v1/onion/clients/remove",
            json={"name": "Absent"},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 404
        assert client.post(
            "/api/v1/onion/clients/remove",
            json={"name": "Téléphone"},
            headers={"X-CSRF-Token": csrf},
        ).json()["client_auth"] is False


def test_security_audit_is_authenticated_and_actionable() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        assert client.get("/api/v1/security/audit").status_code == 401
        login(client)

        report = client.get("/api/v1/security/audit").json()
        assert 0 <= report["score"] <= 100
        assert report["counts"]["total"] == len(report["findings"])
        # Every unresolved finding says what to do about it.
        assert all(item["remedy"] for item in report["findings"] if not item["ok"])
        assert "scrypt" not in str(report)


def test_exported_configuration_carries_the_access_rules() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        client.post(
            "/api/v1/devices/access",
            json={
                "mac": "3c:07:54:2a:91:8f",
                "alias": "Tablette",
                "schedule": {"enabled": True, "days": [5, 6], "start": "09:00", "end": "20:00"},
            },
            headers={"X-CSRF-Token": csrf},
        )
        document = client.get("/api/v1/system/config").json()
        assert document["device_access"][0]["alias"] == "Tablette"

        client.post(
            "/api/v1/devices/access/remove",
            json={"mac": "3c:07:54:2a:91:8f"},
            headers={"X-CSRF-Token": csrf},
        )
        restored = client.post(
            "/api/v1/system/config",
            json={"document": document},
            headers={"X-CSRF-Token": csrf},
        )
        assert restored.status_code == 200
        assert any("règle" in item for item in restored.json()["applied"])
        assert client.get("/api/v1/devices/access").json()["rules"][0]["alias"] == "Tablette"
