from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..agent import AgentError
from ..auth import PasswordHashingBusy, hashing_slot
from ..hardening import security_audit
from ..protection import protection_snapshot
from ..schemas import StatusResponse
from ..system import system_snapshot
from .context import RouteContext


class InterfaceConfirmationRequest(BaseModel):
    wan: str = Field(min_length=1, max_length=32)
    access_point: str = Field(min_length=1, max_length=32)


def create_router(context: RouteContext) -> APIRouter:
    router = APIRouter()
    settings = context.settings
    services = context.services
    database = services.database
    current_session = context.current_session
    csrf_session = context.csrf_session

    @router.get("/api/v1/health")
    def health() -> dict[str, Any]:
        """Unauthenticated liveness probe used by onionpi-verify and first boot.

        Deliberately says nothing else: this is the one endpoint any Wi-Fi client
        can reach without an account, and the installed version would tell whoever
        asks which published advisories apply to this appliance. The interface
        reads the version from /api/v1/status, behind the session.
        """
        return {"status": "ok"}

    @router.get("/api/v1/status", response_model=StatusResponse)
    async def status(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        system_task = asyncio.to_thread(
            system_snapshot, settings.shared_dir, settings.demo_mode
        )
        tor_task = asyncio.to_thread(services.tor.status)
        network_task = asyncio.to_thread(services.access_point.snapshot)
        system_data, tor_data, network_data = await asyncio.gather(
            system_task, tor_task, network_task
        )
        protection = protection_snapshot(
            tor_data, system_data.get("services", []), settings.demo_mode
        )
        protection["metrics"] = services.onboarding.observe_protection(
            str(protection["status"])
        )
        return {
            "device_name": settings.device_name,
            "demo_mode": settings.demo_mode,
            "version": settings.version,
            "system": system_data,
            "tor": tor_data,
            "network": network_data,
            "protection": protection,
            "activities": database.activities(6),
        }

    @router.get("/api/v1/security/audit")
    async def security_report(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        """Hardening review: exposure, freshness and secrets hygiene.

        Deliberately not part of /status: it queries systemd, the certificate
        and the update client, and nothing on a dashboard needs that every ten
        seconds.
        """
        system_data, tor_data = await asyncio.gather(
            asyncio.to_thread(system_snapshot, settings.shared_dir, settings.demo_mode),
            asyncio.to_thread(services.tor.status),
        )
        protection = protection_snapshot(
            tor_data, system_data.get("services", []), settings.demo_mode
        )
        return await asyncio.to_thread(security_audit, services, protection)

    @router.get("/api/v1/onboarding")
    def onboarding_state(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        return services.onboarding.status()

    @router.post("/api/v1/onboarding/interfaces")
    def onboarding_interfaces(
        payload: InterfaceConfirmationRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            services.onboarding.confirm_interfaces(payload.wan, payload.access_point)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return services.onboarding.status()

    @router.post("/api/v1/onboarding/firewall")
    async def onboarding_firewall(
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(services.onboarding.test_firewall)
        except (AgentError, RuntimeError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return {"onboarding": services.onboarding.status(), "test": result}

    @router.post("/api/v1/onboarding/clock")
    def onboarding_clock(
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            services.onboarding.confirm_clock()
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return services.onboarding.status()

    @router.post("/api/v1/onboarding/recovery-code")
    def onboarding_recovery_code(
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            with hashing_slot():
                code = services.onboarding.issue_recovery_code()
        except PasswordHashingBusy as error:
            raise HTTPException(
                status_code=429, detail="Génération occupée, réessayez"
            ) from error
        return {"code": code, "onboarding": services.onboarding.status()}

    @router.post("/api/v1/onboarding/recovery-code/confirm")
    def onboarding_recovery_code_confirm(
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        if not services.onboarding.recovery_hash():
            raise HTTPException(
                status_code=409, detail="Créez d’abord un code de récupération"
            )
        services.onboarding.mark("recovery")
        return services.onboarding.status()

    @router.get("/api/v1/traffic")
    def traffic(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        return {"samples": services.metrics.history()}

    return router
