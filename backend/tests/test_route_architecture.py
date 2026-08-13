from __future__ import annotations

from fastapi.routing import APIRoute, APIWebSocketRoute
from onionpi.backends import (
    AccessPointBackend,
    FirewallBackend,
    TorBackend,
    UpdateBackend,
)
from onionpi.main import app, services


def _walk_routes(routes: list[object]):
    for route in routes:
        nested = getattr(route, "routes", None)
        original = getattr(route, "original_router", None)
        if nested is None and original is not None:
            nested = original.routes
        if nested is not None:
            yield from _walk_routes(nested)
        else:
            yield route


def test_versioned_api_handlers_live_in_route_modules() -> None:
    modules = {
        route.endpoint.__module__
        for route in _walk_routes(app.routes)
        if isinstance(route, (APIRoute, APIWebSocketRoute))
        and route.path.startswith("/api/v1/")
    }
    assert modules == {
        "onionpi.routes.auth",
        "onionpi.routes.chat",
        "onionpi.routes.files",
        "onionpi.routes.network",
        "onionpi.routes.protection",
        "onionpi.routes.rack",
        "onionpi.routes.updates",
    }
    assert app.state.route_context.services is services


def test_composed_platform_services_satisfy_stable_backend_contracts() -> None:
    assert isinstance(services.tor, TorBackend)
    assert isinstance(services.firewall, FirewallBackend)
    assert isinstance(services.access_point, AccessPointBackend)
    assert isinstance(services.updates, UpdateBackend)
