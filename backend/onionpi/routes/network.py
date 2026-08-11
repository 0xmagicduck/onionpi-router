from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..circumvention import CircumventionError
from ..netcontrol import NetControlError
from ..onion import OnionError
from ..policy import PolicyError
from ..relay import RelayError
from ..system import connected_devices
from ..tor_control import TorControlError
from .context import RouteContext


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


def create_router(context: RouteContext) -> APIRouter:
    router = APIRouter()
    settings = context.settings
    services = context.services
    database = services.database
    current_session = context.current_session
    csrf_session = context.csrf_session

    @router.get("/api/v1/devices")
    async def devices(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        values = await asyncio.to_thread(
            connected_devices, settings.wifi_interface, settings.demo_mode
        )
        blocked = services.device_guard.entries()
        blocked_macs = {entry["mac"] for entry in blocked}
        seen = set()
        for device in values:
            device["blocked"] = device["mac"] in blocked_macs
            seen.add(device["mac"])
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

    @router.post("/api/v1/devices/block")
    async def block_device(
        payload: DeviceBlockRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            if payload.blocked:
                entries = await asyncio.to_thread(
                    services.device_guard.block, payload.mac, payload.label
                )
            else:
                entries = await asyncio.to_thread(
                    services.device_guard.unblock, payload.mac
                )
        except NetControlError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"blocked": entries}

    @router.post("/api/v1/tor/new-identity")
    async def new_identity(
        session: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            await asyncio.to_thread(services.tor.new_identity)
        except TorControlError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        database.add_activity(
            "identity", f"Nouvelle identité demandée par {session['display_name']}"
        )
        return {"ok": True, "message": "Nouvelle identité Tor demandée"}

    @router.get("/api/v1/circumvention")
    async def circumvention_state(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        snapshot, relay_state = await asyncio.gather(
            asyncio.to_thread(services.circumvention.snapshot),
            asyncio.to_thread(services.relay.status),
        )
        return {**snapshot, "relay": relay_state}

    @router.post("/api/v1/circumvention")
    async def update_circumvention(
        payload: CircumventionRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            snapshot = await asyncio.to_thread(
                services.circumvention.update,
                mode=payload.mode,
                transport=payload.transport,
                country=payload.country,
                custom_bridges=payload.custom_bridges,
            )
        except CircumventionError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        relay_state = await asyncio.to_thread(services.relay.status)
        return {**snapshot, "relay": relay_state}

    @router.post("/api/v1/circumvention/refresh")
    async def refresh_circumvention(
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        """Updates the built-in bridge list straight from the Tor Project.

        Requires a working direct connection to bridges.torproject.org, which is
        exactly what censorship removes: failure here is expected, not fatal.
        """
        try:
            snapshot = await asyncio.to_thread(services.circumvention.refresh_catalog)
        except CircumventionError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Liste de ponts injoignable. Elle est peut-être bloquée "
                    "sur cette connexion."
                ),
            ) from error
        relay_state = await asyncio.to_thread(services.relay.status)
        return {**snapshot, "relay": relay_state}

    @router.post("/api/v1/relay/snowflake")
    async def set_snowflake_relay(
        payload: RelayRequest,
        session: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            state = await asyncio.to_thread(
                services.relay.set_enabled, payload.enabled
            )
        except RelayError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        verb = "activé" if payload.enabled else "arrêté"
        database.add_activity(
            "secure", f"Relais Snowflake {verb} par {session['display_name']}"
        )
        if payload.enabled and not state["active"]:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Le proxy Snowflake n’a pas démarré. Consultez les journaux "
                    "snowflake-proxy."
                ),
            )
        return {**services.circumvention.snapshot(), "relay": state}

    @router.get("/api/v1/dns-filter")
    async def dns_filter_state(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(services.dns_filter.snapshot)

    @router.post("/api/v1/dns-filter")
    async def update_dns_filter(
        payload: DnsFilterRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                services.dns_filter.update,
                payload.profiles,
                payload.custom_blocked,
                payload.allowed,
            )
        except NetControlError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/api/v1/dns-filter/refresh")
    async def refresh_dns_filter(
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(services.dns_filter.rebuild)
        except NetControlError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @router.get("/api/v1/tor/advanced")
    async def tor_advanced(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        policy_state, circuits, onion_state = await asyncio.gather(
            asyncio.to_thread(services.tor_policy.snapshot),
            asyncio.to_thread(services.tor.circuits),
            asyncio.to_thread(services.onion.snapshot),
        )
        return {"policy": policy_state, "circuits": circuits, "onion": onion_state}

    @router.post("/api/v1/tor/policy")
    async def update_tor_policy(
        payload: TorPolicyRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                services.tor_policy.update,
                payload.exit_country,
                payload.rotation_seconds,
            )
        except PolicyError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/api/v1/tor/speedtest")
    async def tor_speedtest(
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        if not services.speedtest_limiter.allow():
            raise HTTPException(
                status_code=429, detail="Patientez avant une nouvelle mesure"
            )
        try:
            return await asyncio.to_thread(services.tor.speed_test)
        except TorControlError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.post("/api/v1/onion")
    async def set_onion(
        payload: OnionRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                services.onion.set_enabled, payload.enabled
            )
        except OnionError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.post("/api/v1/onion/rotate")
    async def rotate_onion(
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(services.onion.rotate_address)
        except OnionError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    return router
