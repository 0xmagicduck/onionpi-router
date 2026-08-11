"""Injected runtime shared by the versioned HTTP route modules."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, WebSocket

from ..auth import token_hash
from ..config import Settings
from ..services import AppServices

COOKIE_NAME = "onionpi_session"
SessionDependency = Callable[..., dict[str, Any]]


@dataclass(slots=True)
class RouteContext:
    """Application-scoped services and request dependencies for routers."""

    settings: Settings
    services: AppServices
    chat: Any
    current_session: SessionDependency
    csrf_session: SessionDependency
    allowed_origins: Callable[[], set[str]]
    is_onion_request: Callable[[Request | WebSocket], bool]


def build_route_context(settings: Settings, services: AppServices, chat: Any) -> RouteContext:
    database = services.database

    def request_host(request: Request | WebSocket) -> str:
        return (request.headers.get("host") or "").split(":")[0].strip().lower()

    def is_onion_request(request: Request | WebSocket) -> bool:
        return request_host(request).endswith(".onion")

    def allowed_origins() -> set[str]:
        origins = set(settings.allowed_origins)
        if services.onion.address:
            origins.add(f"http://{services.onion.address}.onion")
            origins.add(f"https://{services.onion.address}.onion")
        return origins

    def session_for_token(token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        return database.session(token_hash(token, settings.session_secret))

    def current_session(request: Request) -> dict[str, Any]:
        session = session_for_token(request.cookies.get(COOKIE_NAME))
        if not session:
            raise HTTPException(status_code=401, detail="Authentification requise")
        return session

    def csrf_session(
        request: Request,
        x_csrf_token: str | None = Header(default=None),
        session: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in allowed_origins():
            raise HTTPException(status_code=403, detail="Origine refusée")
        if not x_csrf_token or not secrets.compare_digest(
            x_csrf_token, session["csrf_token"]
        ):
            raise HTTPException(status_code=403, detail="Jeton CSRF invalide")
        return session

    return RouteContext(
        settings=settings,
        services=services,
        chat=chat,
        current_session=current_session,
        csrf_session=csrf_session,
        allowed_origins=allowed_origins,
        is_onion_request=is_onion_request,
    )
