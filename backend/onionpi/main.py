from __future__ import annotations

import asyncio
import os
import shutil
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .http_limits import BodyLimitMiddleware
from .routes.auth import create_router as create_auth_router
from .routes.chat import ChatManager
from .routes.chat import create_router as create_chat_router
from .routes.context import COOKIE_NAME as COOKIE_NAME
from .routes.context import build_route_context
from .routes.files import MULTIPART_ENVELOPE_BYTES, upload_body_budget
from .routes.files import create_router as create_files_router
from .routes.network import create_router as create_network_router
from .routes.protection import create_router as create_protection_router
from .routes.rack import create_router as create_rack_router
from .routes.updates import MAX_IMPORTED_DEVICES as MAX_IMPORTED_DEVICES
from .routes.updates import create_router as create_updates_router
from .services import AppServices, build_app_services

settings: Settings = get_settings()
services: AppServices = build_app_services(settings)

# Compatibility aliases for scripts and extensions that imported the original
# composition root. HTTP handlers themselves receive the AppServices instance
# through RouteContext rather than reaching back into this module.
database = services.database
tor = services.tor
metrics = services.metrics
login_limiter = services.login_limiter
circumvention = services.circumvention
relay = services.relay
agent = services.agent
device_guard = services.device_guard
access = services.access
dns_filter = services.dns_filter
tor_policy = services.tor_policy
onion = services.onion
updates = services.updates
rack = services.rack
speedtest_limiter = services.speedtest_limiter


def _seed_demo_files() -> None:
    if not settings.demo_mode or any(settings.shared_dir.iterdir()):
        return
    (settings.shared_dir / "Photos").mkdir(exist_ok=True)
    (settings.shared_dir / "Documents").mkdir(exist_ok=True)
    (settings.shared_dir / "onionpi-guide.pdf").write_bytes(b"OnionPi demo\n")
    (settings.shared_dir / "sauvegarde-config.zip").write_bytes(
        b"OnionPi demo archive\n"
    )
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
        database.add_message(
            None, "Camille", "J’ai ajouté les photos dans le dossier partagé."
        )
        database.add_message(
            None, "Alice", "Parfait, merci ! Je vais les regarder ce soir."
        )
        database.add_message(
            None, "OnionPi", "Sauvegarde automatique des fichiers activée."
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    _seed_demo_files()
    _seed_demo_content()
    metrics.start()
    circumvention.start()
    tor_policy.start()
    await asyncio.to_thread(device_guard.resync)
    # The scheduler owns the time-based half of the block list: it has to run
    # before the first tick so a device paused before a reboot stays paused.
    access.start()
    await asyncio.to_thread(onion.ensure_published)
    rack.seed_demo()
    # Tor holds client authorisations in memory: a restart of the daemon, or of
    # this service, leaves every authorised node unresolvable until they are
    # taught again. Same reason the onion service is republished just above.
    await asyncio.to_thread(rack.republish_client_auth)
    rack.start()
    if not database.activities(1):
        database.add_activity("secure", "OnionPi est prêt")
    yield
    rack.stop()
    access.stop()
    tor_policy.stop()
    circumvention.stop()
    metrics.stop()


def upload_body_limit() -> int:
    """Whole-body ceiling for an import, tracking the free space of the moment.

    A part of a multipart body is streamed to a spooled temporary file with no
    ceiling of its own, so this middleware is the only place a body that
    declares no length can be stopped. Reading it here keeps the storage
    reserve meaningful against a client that simply never stops sending.
    """
    try:
        free = shutil.disk_usage(settings.shared_dir).free
    except OSError:
        free = 0
    budget = upload_body_budget(
        free, settings.storage_reserve_bytes, settings.max_upload_bytes
    )
    return budget + MULTIPART_ENVELOPE_BYTES


app = FastAPI(
    title="OnionPi API",
    version=settings.version,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    BodyLimitMiddleware,
    request_limit=settings.max_request_bytes,
    upload_limit=upload_body_limit,
)


@app.middleware("http")
async def security_headers(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
        "object-src 'none'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


chat = ChatManager()
route_context = build_route_context(settings, services, chat)
current_session = route_context.current_session
csrf_session = route_context.csrf_session
app.state.services = services
app.state.route_context = route_context

for router_factory in (
    create_auth_router,
    create_protection_router,
    create_network_router,
    create_rack_router,
    create_updates_router,
    create_files_router,
    create_chat_router,
):
    app.include_router(router_factory(route_context))


@app.exception_handler(HTTPException)
async def http_exception(_: Request, error: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code, content={"detail": error.detail}
    )


if settings.frontend_dir.exists():
    assets = settings.frontend_dir / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Ressource inconnue")
        requested = (settings.frontend_dir / path).resolve()
        if (
            path
            and os.path.commonpath(
                [str(settings.frontend_dir), str(requested)]
            )
            == str(settings.frontend_dir)
            and requested.is_file()
        ):
            return FileResponse(requested)
        return FileResponse(settings.frontend_dir / "index.html")

else:

    @app.get("/", include_in_schema=False)
    def frontend_missing() -> dict[str, str]:
        return {
            "message": "Interface non construite. Lancez npm run build dans frontend/."
        }
