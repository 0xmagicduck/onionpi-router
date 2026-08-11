from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import secrets
import shutil
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import ACTIONS, AgentError, PrivilegedAgent
from .auth import LoginLimiter, RateLimiter, hash_password, token_hash, verify_password
from .circumvention import CircumventionError, CircumventionManager
from .config import Settings, get_settings
from .database import Database
from .diagnostics import build_diagnostics
from .netcontrol import DeviceGuard, DnsFilter, NetControlError
from .onion import OnionError, OnionService
from .policy import PolicyError, TorPolicy
from .relay import RelayError, SnowflakeRelay
from .system import MetricsSampler, connected_devices, journal, system_snapshot, wifi_details
from .tor_control import TorControlError, TorController
from .updates import CHANNELS, UpdateError, UpdateManager

COOKIE_NAME = "onionpi_session"
settings: Settings = get_settings()
database = Database(settings.database_path)
tor = TorController(
    settings.tor_control_host,
    settings.tor_control_port,
    settings.tor_cookie_path,
    settings.demo_mode,
)
metrics = MetricsSampler(settings.upstream_interface, settings.demo_mode)
login_limiter = LoginLimiter()
circumvention = CircumventionManager(
    config_path=settings.bridge_config_path,
    state_path=settings.circumvention_state_path,
    cache_path=settings.circumvention_cache_path,
    controller=tor,
    country=settings.country,
    demo_mode=settings.demo_mode,
    on_event=lambda kind, message: database.add_activity(kind, message),
)
relay = SnowflakeRelay(settings.relay_state_path, settings.demo_mode)
agent = PrivilegedAgent(
    settings.agent_request_path, settings.agent_result_path, settings.demo_mode
)
device_guard = DeviceGuard(
    database,
    settings.blocked_macs_path,
    agent,
    settings.demo_mode,
    on_event=lambda kind, message: database.add_activity(kind, message),
)
dns_filter = DnsFilter(
    database,
    settings.dns_block_path,
    agent,
    demo_mode=settings.demo_mode,
    on_event=lambda kind, message: database.add_activity(kind, message),
)
tor_policy = TorPolicy(
    database,
    settings.tor_policy_path,
    tor,
    settings.demo_mode,
    on_event=lambda kind, message: database.add_activity(kind, message),
)
onion = OnionService(
    database,
    settings.onion_key_path,
    tor,
    target=f"127.0.0.1:{settings.app_port}",
    demo_mode=settings.demo_mode,
    on_event=lambda kind, message: database.add_activity(kind, message),
)
updates = UpdateManager(
    settings.update_state_path,
    settings.update_settings_path,
    settings.version,
    settings.demo_mode,
)
speedtest_limiter = RateLimiter(events=3, window_seconds=120)
# Reaching GitHub through Tor is slow, and the privileged agent runs the check
# inline. Waiting less than the script does would report a false failure.
UPDATE_CHECK_TIMEOUT = 110.0


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class FolderRequest(BaseModel):
    parent: str = Field(default="", max_length=500)
    name: str = Field(min_length=1, max_length=120)


class CircumventionRequest(BaseModel):
    mode: str = Field(pattern=r"^(direct|auto|manual)$")
    transport: str = Field(default="snowflake", max_length=32)
    country: str = Field(default="", max_length=2)
    custom_bridges: list[str] = Field(default_factory=list, max_length=40)


class RelayRequest(BaseModel):
    enabled: bool


class DeviceBlockRequest(BaseModel):
    mac: str = Field(min_length=11, max_length=17)
    label: str = Field(default="", max_length=64)
    blocked: bool


class DnsFilterRequest(BaseModel):
    profiles: list[str] = Field(default_factory=list, max_length=8)
    custom_blocked: list[str] = Field(default_factory=list, max_length=2000)
    allowed: list[str] = Field(default_factory=list, max_length=2000)


class TorPolicyRequest(BaseModel):
    exit_country: str = Field(default="", max_length=2)
    rotation_seconds: int = Field(default=0, ge=0, le=86_400)


class OnionRequest(BaseModel):
    enabled: bool


class SystemActionRequest(BaseModel):
    action: str = Field(pattern=r"^[a-z-]{3,24}$")


class ConfigImportRequest(BaseModel):
    document: dict[str, Any]


class UpdateSettingsRequest(BaseModel):
    channel: str = Field(pattern=r"^(stable|edge)$")
    # "04:30" or "03:00,15:00". The exact grammar is enforced in updates.py and
    # a third time by onionpi-update before it writes the systemd timer.
    schedule: str = Field(min_length=5, max_length=41)
    enabled: bool = True
    apply: bool = True


def _seed_demo_files() -> None:
    if not settings.demo_mode or any(settings.shared_dir.iterdir()):
        return
    (settings.shared_dir / "Photos").mkdir(exist_ok=True)
    (settings.shared_dir / "Documents").mkdir(exist_ok=True)
    (settings.shared_dir / "onionpi-guide.pdf").write_bytes(b"OnionPi demo\n")
    (settings.shared_dir / "sauvegarde-config.zip").write_bytes(b"OnionPi demo archive\n")
    (settings.shared_dir / "vacances.jpg").write_bytes(b"OnionPi demo image\n")


def _seed_demo_content() -> None:
    if not settings.demo_mode:
        return
    demo_activities = [
        ("secure", "Circuit Tor établi"),
        ("device", "iPhone connecté"),
        ("identity", "Nouvelle identité Tor"),
        ("secure", "Listes pare-feu mises à jour"),
        ("upload", "Sauvegarde automatique des fichiers"),
    ]
    if not database.activities(1):
        for kind, message in reversed(demo_activities):
            database.add_activity(kind, message)
    if not database.messages(1):
        database.add_message(None, "Camille", "J’ai ajouté les photos dans le dossier partagé.")
        database.add_message(None, "Alice", "Parfait, merci ! Je vais les regarder ce soir.")
        database.add_message(None, "OnionPi", "Sauvegarde automatique des fichiers activée.")


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    _seed_demo_files()
    _seed_demo_content()
    metrics.start()
    circumvention.start()
    tor_policy.start()
    # nftables and Tor both forget everything on restart; the stored intent is
    # the source of truth, so push it back as soon as the service is up.
    await asyncio.to_thread(device_guard.resync)
    await asyncio.to_thread(onion.ensure_published)
    if not database.activities(1):
        database.add_activity("secure", "OnionPi est prêt")
    yield
    tor_policy.stop()
    circumvention.stop()
    metrics.stop()


app = FastAPI(
    title="OnionPi API",
    # The deployed tree owns the version number: /opt/onionpi/VERSION is what
    # the update client compares against a published release.
    version=settings.version,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def security_headers(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'; object-src 'none'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _request_host(request: Request | WebSocket) -> str:
    return (request.headers.get("host") or "").split(":")[0].strip().lower()


def _is_onion_request(request: Request | WebSocket) -> bool:
    return _request_host(request).endswith(".onion")


def _allowed_origins() -> set[str]:
    """Static origins plus the onion address, which only exists at runtime."""
    origins = set(settings.allowed_origins)
    if onion.address:
        origins.add(f"http://{onion.address}.onion")
        origins.add(f"https://{onion.address}.onion")
    return origins


def _session_for_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    return database.session(token_hash(token, settings.session_secret))


def current_session(request: Request) -> dict[str, Any]:
    session = _session_for_token(request.cookies.get(COOKIE_NAME))
    if not session:
        raise HTTPException(status_code=401, detail="Authentification requise")
    return session


def csrf_session(
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    session: dict[str, Any] = Depends(current_session),
) -> dict[str, Any]:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in _allowed_origins():
        raise HTTPException(status_code=403, detail="Origine refusée")
    if not x_csrf_token or not secrets.compare_digest(x_csrf_token, session["csrf_token"]):
        raise HTTPException(status_code=403, detail="Jeton CSRF invalide")
    return session


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    """Unauthenticated liveness probe used by onionpi-verify and first boot."""
    return {"status": "ok", "version": app.version}


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    address = request.client.host if request.client else "unknown"
    if not login_limiter.allow(address):
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez dans quelques minutes.")
    user = database.user_by_name(payload.username)
    if not user or not verify_password(payload.password, user["password_hash"]):
        login_limiter.failure(address)
        time.sleep(0.2)
        raise HTTPException(status_code=401, detail="Identifiants incorrects")
    login_limiter.success(address)
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    database.create_session(
        token_hash(token, settings.session_secret), csrf, int(user["id"]), settings.session_ttl_seconds
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        # Tor already encrypts an onion connection end to end, but browsers
        # that do not treat http://…onion as a secure context would simply drop
        # a Secure cookie and make the login look broken.
        secure=settings.cookie_secure and not _is_onion_request(request),
        samesite="strict",
        path="/",
    )
    return {"user": {"username": user["username"], "display_name": user["display_name"]}, "csrf": csrf}


@app.get("/api/v1/auth/session")
def session_info(session: dict[str, Any] = Depends(current_session)) -> dict[str, Any]:
    return {
        "user": {"username": session["username"], "display_name": session["display_name"]},
        "csrf": session["csrf_token"],
    }


@app.post("/api/v1/auth/password")
def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    session: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    user = database.user_by_name(session["username"])
    if not user or not verify_password(payload.current_password, user["password_hash"]):
        time.sleep(0.2)
        raise HTTPException(status_code=403, detail="Mot de passe actuel incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="Choisissez un mot de passe différent")
    try:
        new_hash = hash_password(payload.new_password)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    # create_user drops every session of the account, including this one, so a
    # stolen cookie cannot survive a password change.
    database.create_user(user["username"], user["display_name"], new_hash)
    database.add_activity("secure", f"Mot de passe modifié pour {user['username']}")
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True, "message": "Mot de passe modifié. Reconnectez-vous."}


@app.post("/api/v1/auth/logout", status_code=204)
def logout(
    response: Response,
    request: Request,
    _: dict[str, Any] = Depends(csrf_session),
) -> Response:
    raw_token = request.cookies.get(COOKIE_NAME)
    if raw_token:
        database.delete_session(token_hash(raw_token, settings.session_secret))
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@app.get("/api/v1/status")
async def status(_: dict[str, Any] = Depends(current_session)) -> dict[str, Any]:
    system_task = asyncio.to_thread(system_snapshot, settings.shared_dir, settings.demo_mode)
    tor_task = asyncio.to_thread(tor.status)
    network_task = asyncio.to_thread(
        wifi_details,
        settings.wifi_interface,
        settings.upstream_interface,
        settings.gateway_ip,
        settings.demo_mode,
    )
    system_data, tor_data, network_data = await asyncio.gather(system_task, tor_task, network_task)
    return {
        "device_name": settings.device_name,
        "demo_mode": settings.demo_mode,
        "version": settings.version,
        "system": system_data,
        "tor": tor_data,
        "network": network_data,
        "activities": database.activities(6),
    }


@app.get("/api/v1/traffic")
def traffic(_: dict[str, Any] = Depends(current_session)) -> dict[str, Any]:
    return {"samples": metrics.history()}


@app.get("/api/v1/devices")
async def devices(_: dict[str, Any] = Depends(current_session)) -> dict[str, Any]:
    values = await asyncio.to_thread(connected_devices, settings.wifi_interface, settings.demo_mode)
    blocked = device_guard.entries()
    blocked_macs = {entry["mac"] for entry in blocked}
    seen = set()
    for device in values:
        device["blocked"] = device["mac"] in blocked_macs
        seen.add(device["mac"])
    # A blocked device stops answering ARP, so it would vanish from the list
    # and could never be unblocked from the interface.
    for entry in blocked:
        if entry["mac"] in seen:
            continue
        values.append(
            {
                "name": entry["label"] or "Appareil bloqué",
                "ip": "—",
                "mac": entry["mac"],
                "download": 0,
                "upload": 0,
                "online": False,
                "blocked": True,
            }
        )
    return {"devices": values, "blocked": blocked}


@app.post("/api/v1/devices/block")
async def block_device(
    payload: DeviceBlockRequest,
    _: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    try:
        if payload.blocked:
            entries = await asyncio.to_thread(device_guard.block, payload.mac, payload.label)
        else:
            entries = await asyncio.to_thread(device_guard.unblock, payload.mac)
    except NetControlError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"blocked": entries}


@app.post("/api/v1/tor/new-identity")
async def new_identity(session: dict[str, Any] = Depends(csrf_session)) -> dict[str, Any]:
    try:
        await asyncio.to_thread(tor.new_identity)
    except TorControlError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    database.add_activity("identity", f"Nouvelle identité demandée par {session['display_name']}")
    return {"ok": True, "message": "Nouvelle identité Tor demandée"}


@app.get("/api/v1/circumvention")
async def circumvention_state(_: dict[str, Any] = Depends(current_session)) -> dict[str, Any]:
    snapshot, relay_state = await asyncio.gather(
        asyncio.to_thread(circumvention.snapshot),
        asyncio.to_thread(relay.status),
    )
    return {**snapshot, "relay": relay_state}


@app.post("/api/v1/circumvention")
async def update_circumvention(
    payload: CircumventionRequest,
    session: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    try:
        snapshot = await asyncio.to_thread(
            circumvention.update,
            mode=payload.mode,
            transport=payload.transport,
            country=payload.country,
            custom_bridges=payload.custom_bridges,
        )
    except CircumventionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    relay_state = await asyncio.to_thread(relay.status)
    return {**snapshot, "relay": relay_state}


@app.post("/api/v1/circumvention/refresh")
async def refresh_circumvention(
    _: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    """Updates the built-in bridge list straight from the Tor Project.

    Requires a working direct connection to bridges.torproject.org, which is
    exactly what censorship removes: failure here is expected, not fatal.
    """
    try:
        snapshot = await asyncio.to_thread(circumvention.refresh_catalog)
    except CircumventionError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except Exception as error:  # httpx raises a whole family of transport errors
        raise HTTPException(
            status_code=502,
            detail="Liste de ponts injoignable. Elle est peut-être bloquée sur cette connexion.",
        ) from error
    relay_state = await asyncio.to_thread(relay.status)
    return {**snapshot, "relay": relay_state}


@app.post("/api/v1/relay/snowflake")
async def set_snowflake_relay(
    payload: RelayRequest,
    session: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    try:
        state = await asyncio.to_thread(relay.set_enabled, payload.enabled)
    except RelayError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    verb = "activé" if payload.enabled else "arrêté"
    database.add_activity("secure", f"Relais Snowflake {verb} par {session['display_name']}")
    if payload.enabled and not state["active"]:
        raise HTTPException(
            status_code=503,
            detail="Le proxy Snowflake n’a pas démarré. Consultez les journaux snowflake-proxy.",
        )
    return {**circumvention.snapshot(), "relay": state}


@app.get("/api/v1/dns-filter")
async def dns_filter_state(_: dict[str, Any] = Depends(current_session)) -> dict[str, Any]:
    return await asyncio.to_thread(dns_filter.snapshot)


@app.post("/api/v1/dns-filter")
async def update_dns_filter(
    payload: DnsFilterRequest,
    _: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            dns_filter.update,
            payload.profiles,
            payload.custom_blocked,
            payload.allowed,
        )
    except NetControlError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/v1/dns-filter/refresh")
async def refresh_dns_filter(_: dict[str, Any] = Depends(csrf_session)) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(dns_filter.rebuild)
    except NetControlError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/v1/tor/advanced")
async def tor_advanced(_: dict[str, Any] = Depends(current_session)) -> dict[str, Any]:
    policy_state, circuits, onion_state = await asyncio.gather(
        asyncio.to_thread(tor_policy.snapshot),
        asyncio.to_thread(tor.circuits),
        asyncio.to_thread(onion.snapshot),
    )
    return {"policy": policy_state, "circuits": circuits, "onion": onion_state}


@app.post("/api/v1/tor/policy")
async def update_tor_policy(
    payload: TorPolicyRequest,
    _: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            tor_policy.update, payload.exit_country, payload.rotation_seconds
        )
    except PolicyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/v1/tor/speedtest")
async def tor_speedtest(_: dict[str, Any] = Depends(csrf_session)) -> dict[str, Any]:
    # A speed test costs real Tor bandwidth that volunteers pay for; three
    # measurements every two minutes is already generous.
    if not speedtest_limiter.allow():
        raise HTTPException(status_code=429, detail="Patientez avant une nouvelle mesure")
    try:
        return await asyncio.to_thread(tor.speed_test)
    except TorControlError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/v1/onion")
async def set_onion(
    payload: OnionRequest,
    _: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(onion.set_enabled, payload.enabled)
    except OnionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/v1/onion/rotate")
async def rotate_onion(_: dict[str, Any] = Depends(csrf_session)) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(onion.rotate_address)
    except OnionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/v1/system/actions")
def system_actions(_: dict[str, Any] = Depends(current_session)) -> dict[str, Any]:
    return {
        "available": agent.available,
        "actions": [{"id": key, "label": label} for key, (label, _wait) in ACTIONS.items()],
    }


@app.get("/api/v1/system/diagnostics")
async def system_diagnostics(
    _: dict[str, Any] = Depends(current_session),
) -> dict[str, Any]:
    """Authenticated readiness report with no secrets or client identifiers."""
    return await asyncio.to_thread(build_diagnostics, settings, database, tor, agent)


@app.post("/api/v1/system/action")
async def run_system_action(
    payload: SystemActionRequest,
    session: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    if payload.action not in ACTIONS:
        raise HTTPException(status_code=400, detail="Action inconnue")
    label = ACTIONS[payload.action][0]
    database.add_activity("secure", f"{label} demandé par {session['display_name']}")
    try:
        return await asyncio.to_thread(agent.submit, payload.action)
    except AgentError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _update_payload() -> dict[str, Any]:
    return updates.demo_state() if settings.demo_mode else updates.state()


@app.get("/api/v1/system/update")
async def update_state(_: dict[str, Any] = Depends(current_session)) -> dict[str, Any]:
    return await asyncio.to_thread(_update_payload)


@app.post("/api/v1/system/update/settings")
async def save_update_settings(
    payload: UpdateSettingsRequest,
    session: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    if payload.channel not in CHANNELS:
        raise HTTPException(status_code=400, detail="Canal de mise à jour inconnu")

    def persist() -> None:
        updates.save_preferences(
            payload.channel, payload.schedule, payload.enabled, payload.apply
        )

    try:
        await asyncio.to_thread(persist)
    except UpdateError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    database.add_activity(
        "secure", f"Horaire de mise à jour modifié par {session['display_name']}"
    )
    # The preferences file alone changes nothing: only the root agent can
    # rewrite the systemd timer that decides when the check actually happens.
    warning = ""
    if not settings.demo_mode:
        try:
            await asyncio.to_thread(agent.submit, "update-schedule")
        except AgentError as error:
            warning = str(error)
    payload_state = await asyncio.to_thread(_update_payload)
    return {**payload_state, "warning": warning}


@app.post("/api/v1/system/update/check")
async def check_for_update(_: dict[str, Any] = Depends(csrf_session)) -> dict[str, Any]:
    if not settings.demo_mode:
        try:
            await asyncio.to_thread(agent.submit, "update-check", UPDATE_CHECK_TIMEOUT)
        except AgentError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
    return await asyncio.to_thread(_update_payload)


@app.post("/api/v1/system/update/run")
async def run_update(session: dict[str, Any] = Depends(csrf_session)) -> dict[str, Any]:
    state = await asyncio.to_thread(_update_payload)
    if state["running"]:
        raise HTTPException(status_code=409, detail="Une mise à jour est déjà en cours.")
    database.add_activity(
        "secure", f"Mise à jour lancée par {session['display_name']}"
    )
    if not settings.demo_mode:
        try:
            await asyncio.to_thread(agent.submit, "update")
        except AgentError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        **(await asyncio.to_thread(_update_payload)),
        "message": "Mise à jour lancée. L’interface peut se recharger pendant l’opération.",
    }


def _configuration_document() -> dict[str, Any]:
    """Everything the interface can restore, and nothing secret."""
    circumvention_state = circumvention.snapshot()
    return {
        "version": 1,
        "exported_at": int(time.time()),
        "device_name": settings.device_name,
        "tor_policy": tor_policy.state(),
        "dns_filter": {
            key: dns_filter.state()[key] for key in ("profiles", "custom_blocked", "allowed")
        },
        "blocked_devices": device_guard.entries(),
        "circumvention": {
            key: circumvention_state[key]
            for key in ("mode", "transport", "country", "custom_bridges")
        },
    }


@app.get("/api/v1/system/config")
async def export_configuration(_: dict[str, Any] = Depends(current_session)) -> Response:
    document = await asyncio.to_thread(_configuration_document)
    return JSONResponse(
        document,
        headers={"Content-Disposition": 'attachment; filename="onionpi-config.json"'},
    )


@app.post("/api/v1/system/config")
async def import_configuration(
    payload: ConfigImportRequest,
    session: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    document = payload.document
    if int(document.get("version", 0)) != 1:
        raise HTTPException(status_code=400, detail="Format de configuration non reconnu")
    applied: list[str] = []
    failures: list[str] = []

    def restore() -> None:
        policy = document.get("tor_policy") or {}
        if policy:
            try:
                tor_policy.update(
                    str(policy.get("exit_country", "")),
                    int(policy.get("rotation_seconds", 0)),
                )
                applied.append("Politique Tor")
            except (PolicyError, TypeError, ValueError) as error:
                failures.append(f"Politique Tor: {error}")
        blocked = document.get("blocked_devices") or []
        if isinstance(blocked, list):
            restored = 0
            for entry in blocked:
                if not isinstance(entry, dict):
                    continue
                try:
                    device_guard.block(str(entry.get("mac", "")), str(entry.get("label", "")))
                    restored += 1
                except NetControlError as error:
                    failures.append(f"Appareil bloqué: {error}")
            if restored:
                applied.append(f"{restored} appareil(s) bloqué(s)")
        dns = document.get("dns_filter") or {}
        if dns:
            try:
                dns_filter.update(
                    list(dns.get("profiles", [])),
                    list(dns.get("custom_blocked", [])),
                    list(dns.get("allowed", [])),
                )
                applied.append("Filtrage DNS")
            except (NetControlError, TypeError) as error:
                failures.append(f"Filtrage DNS: {error}")
        bridges = document.get("circumvention") or {}
        if bridges:
            try:
                circumvention.update(
                    mode=str(bridges.get("mode", "direct")),
                    transport=str(bridges.get("transport", "snowflake")),
                    country=str(bridges.get("country", "")),
                    custom_bridges=list(bridges.get("custom_bridges", [])),
                )
                applied.append("Contournement")
            except (CircumventionError, TypeError) as error:
                failures.append(f"Contournement: {error}")

    await asyncio.to_thread(restore)
    database.add_activity("secure", f"Configuration importée par {session['display_name']}")
    return {"applied": applied, "failures": failures}


def _safe_path(relative: str, require_exists: bool = False) -> Path:
    cleaned = relative.strip().lstrip("/")
    candidate = (settings.shared_dir / cleaned).resolve()
    try:
        inside = os.path.commonpath([str(settings.shared_dir), str(candidate)]) == str(settings.shared_dir)
    except ValueError:
        inside = False
    if not inside:
        raise HTTPException(status_code=400, detail="Chemin invalide")
    if require_exists and not candidate.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    return candidate


def _clean_filename(value: str) -> str:
    name = unicodedata.normalize("NFC", Path(value).name).strip()
    name = "".join(character for character in name if character.isprintable())
    if not name or name in {".", ".."} or name.startswith("."):
        raise HTTPException(status_code=400, detail="Nom de fichier invalide")
    return name[:180]


def _file_item(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": path.relative_to(settings.shared_dir).as_posix(),
        "is_directory": path.is_dir(),
        "size": 0 if path.is_dir() else stat.st_size,
        "modified_at": int(stat.st_mtime),
        "mime": "inode/directory" if path.is_dir() else (mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
    }


@app.get("/api/v1/files")
def list_files(
    path: str = Query(default="", max_length=500),
    _: dict[str, Any] = Depends(current_session),
) -> dict[str, Any]:
    directory = _safe_path(path, require_exists=True)
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="Ce chemin n’est pas un dossier")
    items = [
        _file_item(item)
        for item in directory.iterdir()
        if not item.name.startswith(".") and not item.is_symlink()
    ]
    items.sort(key=lambda item: (not item["is_directory"], item["name"].casefold()))
    usage = shutil.disk_usage(settings.shared_dir)
    storage = (
        {"used": 9_400_000_000, "total": 32_000_000_000, "free": 22_600_000_000}
        if settings.demo_mode
        else {"used": usage.used, "total": usage.total, "free": usage.free}
    )
    return {
        "path": "" if directory == settings.shared_dir else directory.relative_to(settings.shared_dir).as_posix(),
        "items": items,
        "storage": storage,
    }


@app.post("/api/v1/files/upload", status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    path: str = Form(default=""),
    session: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    directory = _safe_path(path, require_exists=True)
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="Destination invalide")
    filename = _clean_filename(file.filename or "")
    target = directory / filename
    if target.exists():
        raise HTTPException(status_code=409, detail="Un fichier porte déjà ce nom")
    # Never let an upload consume the last blocks of the SD card: a full root
    # filesystem stops Tor, dnsmasq and the journal at the same time.
    budget = shutil.disk_usage(settings.shared_dir).free - settings.storage_reserve_bytes
    if budget <= 0:
        raise HTTPException(status_code=507, detail="Espace disque insuffisant")
    limit = min(settings.max_upload_bytes, budget)
    temporary = directory / f".upload-{secrets.token_hex(10)}"
    total = 0
    try:
        with temporary.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        status_code=413 if limit == settings.max_upload_bytes else 507,
                        detail="Fichier trop volumineux"
                        if limit == settings.max_upload_bytes
                        else "Espace disque insuffisant",
                    )
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()
    database.add_activity("upload", f"{session['display_name']} a importé {filename}")
    return {"item": _file_item(target)}


@app.post("/api/v1/files/folders", status_code=201)
def create_folder(
    payload: FolderRequest,
    session: dict[str, Any] = Depends(csrf_session),
) -> dict[str, Any]:
    parent = _safe_path(payload.parent, require_exists=True)
    target = parent / _clean_filename(payload.name)
    try:
        target.mkdir(mode=0o750)
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail="Ce dossier existe déjà") from error
    database.add_activity("folder", f"{session['display_name']} a créé {target.name}")
    return {"item": _file_item(target)}


@app.get("/api/v1/files/download")
def download_file(
    path: str = Query(max_length=500),
    _: dict[str, Any] = Depends(current_session),
) -> FileResponse:
    target = _safe_path(path, require_exists=True)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Téléchargement de dossier non pris en charge")
    return FileResponse(target, filename=target.name, media_type="application/octet-stream")


@app.delete("/api/v1/files", status_code=204)
def delete_file(
    path: str = Query(min_length=1, max_length=500),
    session: dict[str, Any] = Depends(csrf_session),
) -> Response:
    target = _safe_path(path, require_exists=True)
    if target == settings.shared_dir:
        raise HTTPException(status_code=400, detail="Suppression interdite")
    try:
        target.rmdir() if target.is_dir() else target.unlink()
    except OSError as error:
        raise HTTPException(status_code=409, detail="Le dossier doit être vide") from error
    database.add_activity("delete", f"{session['display_name']} a supprimé {target.name}")
    return Response(status_code=204)


@app.get("/api/v1/logs")
async def logs(
    service: str = Query(
        default="tor",
        pattern=r"^(tor|NetworkManager|dnsmasq|nftables|onionpi-firewall|onionpi|snowflake-proxy)$",
    ),
    _: dict[str, Any] = Depends(current_session),
) -> dict[str, Any]:
    return {"service": service, "lines": await asyncio.to_thread(journal, service, 150)}


class ChatManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self.connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self.connections.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for connection in list(self.connections):
            try:
                await connection.send_json(payload)
            except Exception:
                stale.append(connection)
        if stale:
            async with self._lock:
                for connection in stale:
                    self.connections.discard(connection)


chat = ChatManager()


@app.websocket("/api/v1/chat/ws")
async def chat_socket(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin and origin.rstrip("/") not in _allowed_origins():
        await websocket.close(code=1008)
        return
    session = _session_for_token(websocket.cookies.get(COOKIE_NAME))
    if not session:
        await websocket.close(code=1008)
        return
    await chat.connect(websocket)
    limiter = RateLimiter(events=15, window_seconds=10)
    await websocket.send_json({"type": "history", "messages": database.messages(100)})
    await chat.broadcast({"type": "presence", "online": len(chat.connections)})
    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > 8192:
                await websocket.send_json({"type": "error", "message": "Message invalide"})
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            body = str(payload.get("body", "")).strip()
            if not body or len(body) > 2000:
                await websocket.send_json({"type": "error", "message": "Message invalide"})
                continue
            if not limiter.allow():
                await websocket.send_json(
                    {"type": "error", "message": "Trop de messages. Patientez un instant."}
                )
                continue
            message = database.add_message(int(session["user_id"]), session["display_name"], body)
            await chat.broadcast({"type": "message", "message": message})
    except WebSocketDisconnect:
        pass
    finally:
        await chat.disconnect(websocket)
        await chat.broadcast({"type": "presence", "online": len(chat.connections)})


@app.exception_handler(HTTPException)
async def http_exception(_: Request, error: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})


if settings.frontend_dir.exists():
    assets = settings.frontend_dir / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        # The SPA fallback must not answer for unknown API routes, otherwise a
        # typo in a client call returns index.html with status 200.
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Ressource inconnue")
        requested = (settings.frontend_dir / path).resolve()
        if (
            path
            and os.path.commonpath([str(settings.frontend_dir), str(requested)]) == str(settings.frontend_dir)
            and requested.is_file()
        ):
            return FileResponse(requested)
        return FileResponse(settings.frontend_dir / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def frontend_missing() -> dict[str, str]:
        return {"message": "Interface non construite. Lancez npm run build dans frontend/."}
