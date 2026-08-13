from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..access import MAX_PAUSE_MINUTES, AccessError
from ..circumvention import CircumventionError
from ..netcontrol import DnsFilterBusy, NetControlError
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


class DeviceScheduleRequest(BaseModel):
    enabled: bool = False
    days: list[int] = Field(default_factory=list, max_length=7)
    start: str = Field(default="", max_length=5)
    end: str = Field(default="", max_length=5)


class DeviceAccessRequest(BaseModel):
    mac: str = Field(min_length=11, max_length=17)
    alias: str = Field(default="", max_length=64)
    schedule: DeviceScheduleRequest | None = None


class DevicePauseRequest(BaseModel):
    mac: str = Field(min_length=11, max_length=17)
    minutes: int = Field(ge=0, le=MAX_PAUSE_MINUTES)


class DeviceMacRequest(BaseModel):
    mac: str = Field(min_length=11, max_length=17)


class OnionClientRequest(BaseModel):
    name: str = Field(min_length=1, max_length=32)


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
        access = services.access.snapshot()
        rules = {rule["mac"]: rule for rule in access["rules"]}
        blocked_macs = {entry["mac"] for entry in blocked}
        # Folds the firewall counters into the stored totals before reading
        # them: on real hardware this is the only source of per-device traffic.
        totals = await asyncio.to_thread(services.traffic.update, values)

        def describe(device: dict[str, Any]) -> dict[str, Any]:
            rule = rules.get(device["mac"])
            alias = str(rule["alias"]) if rule else ""
            # Demonstration mode keeps its invented figures; on hardware the
            # counters are the only source, and a device the firewall has not
            # seen yet stays at zero.
            measured = totals.get(device["mac"], {})
            return {
                **device,
                "download": measured.get("download", device["download"]),
                "upload": measured.get("upload", device["upload"]),
                # The name a household gives a device is more useful than the
                # hostname its manufacturer chose, so it wins on every screen.
                "name": alias or device["name"],
                "alias": alias,
                "access_state": str(rule["state"])
                if rule
                else ("blocked" if device["blocked"] else "allowed"),
                "paused_until": int(rule["paused_until"]) if rule else 0,
                "schedule": rule["schedule"] if rule else None,
            }

        seen = set()
        for device in values:
            device["blocked"] = device["mac"] in blocked_macs
            seen.add(device["mac"])
        devices = [describe(device) for device in values]
        for entry in blocked:
            if entry["mac"] in seen:
                continue
            devices.append(
                describe(
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
            )
        return {
            "devices": devices,
            "blocked": blocked,
            "access": access,
            "traffic": services.traffic.snapshot(),
        }

    @router.post("/api/v1/devices/traffic/reset")
    async def reset_traffic(
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        snapshot = await asyncio.to_thread(services.traffic.reset)
        database.add_activity("device", "Compteurs de trafic remis à zéro")
        return {"traffic": snapshot}

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

    @router.get("/api/v1/devices/access")
    async def device_access(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(services.access.snapshot)

    @router.post("/api/v1/devices/access")
    async def update_device_access(
        payload: DeviceAccessRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                services.access.update,
                payload.mac,
                payload.alias,
                None,
                payload.schedule.model_dump() if payload.schedule else None,
            )
        except AccessError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/api/v1/devices/access/pause")
    async def pause_device(
        payload: DevicePauseRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                services.access.pause, payload.mac, payload.minutes
            )
        except AccessError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/api/v1/devices/access/remove")
    async def remove_device_access(
        payload: DeviceMacRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(services.access.remove, payload.mac)
        except AccessError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

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
        except DnsFilterBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except NetControlError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/api/v1/dns-filter/refresh")
    async def refresh_dns_filter(
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(services.dns_filter.rebuild)
        except DnsFilterBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
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

    @router.post("/api/v1/onion/clients")
    async def add_onion_client(
        payload: OnionClientRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        """Authorises one device. The returned key is never stored in clear."""
        try:
            return await asyncio.to_thread(services.onion.add_client, payload.name)
        except OnionError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/api/v1/onion/clients/remove")
    async def remove_onion_client(
        payload: OnionClientRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(services.onion.remove_client, payload.name)
        except OnionError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
