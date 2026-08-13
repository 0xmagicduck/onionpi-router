from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

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


def test_a_csrf_header_full_of_raw_bytes_is_refused_not_crashed() -> None:
    """Starlette hands headers over as latin-1, so every byte is reachable.

    `secrets.compare_digest` refuses non-ASCII str arguments: one accented
    character used to answer 500 with a traceback in the journal, and a 500
    escapes the middleware that adds the security headers.
    """
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        login(client)
        response = client.post(
            "/api/v1/tor/new-identity",
            headers=[(b"x-csrf-token", b"\xe9\xff")],
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Jeton CSRF invalide"
        assert response.headers["x-frame-options"] == "DENY"


def test_recovery_resets_the_existing_account_and_closes_every_session() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        code = client.post(
            "/api/v1/onboarding/recovery-code", headers={"X-CSRF-Token": csrf}
        ).json()["code"]

        # A second browser, standing in for whoever the recovery is meant to
        # lock out: it holds a session nobody is about to hand back.
        with TestClient(app) as intruder:
            login(intruder)
            assert intruder.get("/api/v1/auth/session").status_code == 200

            recovered = client.post(
                "/api/v1/auth/recover",
                json={"recovery_code": code, "new_password": "phrase-de-secours-solide"},
            )
            assert recovered.status_code == 200
            assert intruder.get("/api/v1/auth/session").status_code == 401

        # One administrator, not a second one beside the compromised account.
        assert database.stats()["users"] == 1
        assert database.administrator()["display_name"] == "Camille"
        assert client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
        ).status_code == 401
        assert client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "phrase-de-secours-solide"},
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


def test_upload_reserve_is_enforced_before_the_body_is_spooled() -> None:
    """The parser writes the whole body to a temporary file before the handler
    sees it, so a reserve consulted afterwards protects nothing: the card is
    already full by the time the 507 is answered."""
    from onionpi import main as main_module
    from starlette import formparsers

    parsed: list[bool] = []
    original_parse = formparsers.MultiPartParser.parse

    async def spy(self: formparsers.MultiPartParser) -> Any:
        parsed.append(True)
        return await original_parse(self)

    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        settings = main_module.settings
        formparsers.MultiPartParser.parse = spy  # type: ignore[method-assign]
        object.__setattr__(settings, "max_upload_bytes", 1024**2)
        try:
            response = client.post(
                "/api/v1/files/upload",
                data={"path": ""},
                files={"file": ("enorme.bin", b"x" * (1024**2 + 64 * 1024), "text/plain")},
                headers={"X-CSRF-Token": csrf},
            )
        finally:
            formparsers.MultiPartParser.parse = original_parse  # type: ignore[method-assign]
            object.__setattr__(settings, "max_upload_bytes", 1024**3)

    assert response.status_code == 413
    assert not parsed, "le corps ne doit pas être mis en cache avant le refus"


def test_upload_form_fields_cannot_be_accumulated_in_memory() -> None:
    """`max_part_size` only governs the text fields of the form: a file part is
    streamed to a spooled file and never measured against it. Passing the
    upload maximum therefore bounded `path` — 500 characters once it reaches
    the handler — by a gigabyte of bytearray first."""
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        response = client.post(
            "/api/v1/files/upload",
            data={"path": "x" * (128 * 1024)},
            files={"file": ("petit.txt", b"contenu", "text/plain")},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 400


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


def test_device_listing_reports_the_state_of_the_traffic_counters() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)

        traffic = client.get("/api/v1/devices").json()["traffic"]
        assert traffic["supported"] is True

        assert client.post("/api/v1/devices/traffic/reset").status_code == 403
        response = client.post(
            "/api/v1/devices/traffic/reset", headers={"X-CSRF-Token": csrf}
        )
        assert response.status_code == 200
        assert response.json()["traffic"]["since"] > 0


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


def test_rack_is_authenticated_and_every_mutation_needs_the_csrf_header() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        assert client.get("/api/v1/rack").status_code == 401
        csrf = login(client)

        assert client.post(
            "/api/v1/rack/racks", json={"name": "Baie", "location": "", "units": 6}
        ).status_code == 403

        created = client.post(
            "/api/v1/rack/racks",
            json={"name": "Baie de test", "location": "Bureau", "units": 6},
            headers={"X-CSRF-Token": csrf},
        )
        assert created.status_code == 200
        rack_id = next(
            item["id"] for item in created.json()["racks"] if item["name"] == "Baie de test"
        )

        node = client.post(
            "/api/v1/rack/nodes",
            json={
                "kind": "remote",
                "name": "vps-test",
                "role": "Relais",
                "onion": f"{'d' * 56}.onion",
                "agent_port": 9080,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert node.status_code == 200
        node_id = node.json()["id"]
        assert node.json()["address"].endswith(".onion")

        assert client.post(
            "/api/v1/rack/nodes/move",
            json={"id": node_id, "rack_id": rack_id, "position": 9},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 400

        moved = client.post(
            "/api/v1/rack/nodes/move",
            json={"id": node_id, "rack_id": rack_id, "position": 3},
            headers={"X-CSRF-Token": csrf},
        )
        assert moved.status_code == 200
        placed = next(item for item in moved.json()["nodes"] if item["id"] == node_id)
        assert placed["position"] == 3

        bundle = client.post(
            "/api/v1/rack/nodes/enrollment",
            json={"id": node_id},
            headers={"X-CSRF-Token": csrf},
        ).json()
        assert bundle["token"] not in client.get("/api/v1/rack").text
        assert f"--node {node_id}" in bundle["command"]

        refused = client.post(
            "/api/v1/rack/nodes/action",
            json={"id": node_id, "verb": "apply-policy"},
            headers={"X-CSRF-Token": csrf},
        )
        assert refused.status_code == 400

        assert client.post(
            "/api/v1/rack/nodes/remove",
            json={"id": node_id},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 200
        assert client.post(
            "/api/v1/rack/racks/remove",
            json={"id": rack_id},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 200


def test_rack_profiles_groups_and_discovery_travel_over_the_api() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}

        rack_id = client.post(
            "/api/v1/rack/racks",
            json={"name": "Baie groupée", "location": "", "units": 6},
            headers=headers,
        ).json()["racks"][-1]["id"]

        profiles = client.post(
            "/api/v1/rack/profiles",
            json={"id": "", "name": "Serveur exposé", "rules": {"keep_open_ports": [22, 443]}},
            headers=headers,
        )
        assert profiles.status_code == 200
        profile_id = next(
            item["id"]
            for item in profiles.json()["profiles"]
            if item["name"] == "Serveur exposé"
        )

        discovered = client.get("/api/v1/rack").json()["discovered"]
        assert discovered, "les clients du Wi-Fi non racks doivent être proposés"
        imported = client.post(
            "/api/v1/rack/nodes/import",
            json={"macs": [discovered[0]["mac"]], "rack_id": rack_id},
            headers=headers,
        )
        assert imported.status_code == 200
        assert imported.json()["applied"] == 1
        node_id = next(
            node["id"]
            for node in imported.json()["snapshot"]["nodes"]
            if node["mac"] == discovered[0]["mac"]
        )

        grouped = client.post(
            "/api/v1/rack/nodes/bulk",
            json={"operation": "profile", "ids": [node_id], "profile_id": profile_id},
            headers=headers,
        )
        assert grouped.status_code == 200
        assert grouped.json()["applied"] == 1
        assert client.post(
            "/api/v1/rack/nodes/bulk",
            json={"operation": "isolate", "ids": [node_id]},
        ).status_code == 403

        history = client.get(f"/api/v1/rack/nodes/{node_id}/history")
        assert history.status_code == 200
        assert history.json()["node_id"] == node_id
        assert client.get(f"/api/v1/rack/nodes/{'z' * 16}/history").status_code == 400

        assert client.post(
            "/api/v1/rack/racks/arrange", json={"id": rack_id}, headers=headers
        ).status_code == 200
        assert client.post(
            "/api/v1/rack/profiles/remove", json={"id": profile_id}, headers=headers
        ).status_code == 200
        assert client.post(
            "/api/v1/rack/nodes/remove", json={"id": node_id}, headers=headers
        ).status_code == 200
        assert client.post(
            "/api/v1/rack/racks/remove", json={"id": rack_id}, headers=headers
        ).status_code == 200


def test_rack_agent_bundle_is_a_readable_archive() -> None:
    with TestClient(app) as client:
        database.create_user("admin", "Camille", hash_password(PASSWORD))
        assert client.get("/api/v1/rack/agent-bundle").status_code == 401
        login(client)
        response = client.get("/api/v1/rack/agent-bundle")
        assert response.status_code == 200
        import io
        import tarfile

        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
            names = archive.getnames()
        assert "onionpi-node-agent/install-node-agent.sh" in names
        assert "onionpi-node-agent/onionpi-node-agent.py" in names
