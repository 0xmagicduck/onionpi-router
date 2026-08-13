from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from ..access import AccessError
from ..agent import ACTIONS, AgentError
from ..auth import PasswordHashingBusy
from ..backup import (
    BackupError,
    configuration_diff,
    decrypt_configuration,
    encrypt_configuration,
)
from ..circumvention import CircumventionError
from ..diagnostics import build_diagnostics
from ..netcontrol import NetControlError
from ..policy import PolicyError
from ..schemas import ConfigurationDocument
from ..system import journal
from ..updates import CHANNELS, UpdateError
from .context import RouteContext
from .network import (
    CircumventionRequest,
    DeviceBlockRequest,
    DnsFilterRequest,
    TorPolicyRequest,
)

MAX_IMPORTED_DEVICES = 512
UPDATE_CHECK_TIMEOUT = 110.0
BACKUP_BUSY_DETAIL = "Trop de sauvegardes simultanées. Réessayez dans un instant."


class SystemActionRequest(BaseModel):
    action: str = Field(pattern=r"^[a-z-]{3,24}$")


class ConfigImportRequest(BaseModel):
    document: dict[str, Any]


class BackupRequest(BaseModel):
    passphrase: str = Field(min_length=12, max_length=256)


class BackupRestoreRequest(BackupRequest):
    backup: dict[str, Any]


class UpdateSettingsRequest(BaseModel):
    channel: str = Field(pattern=r"^(stable|edge)$")
    schedule: str = Field(min_length=5, max_length=41)
    enabled: bool = True
    apply: bool = True


def create_router(context: RouteContext) -> APIRouter:
    router = APIRouter()
    settings = context.settings
    services = context.services
    database = services.database
    current_session = context.current_session
    csrf_session = context.csrf_session

    def update_payload() -> dict[str, Any]:
        return (
            services.updates.demo_state()
            if settings.demo_mode
            else services.updates.state()
        )

    def configuration_document() -> dict[str, Any]:
        circumvention_state = services.circumvention.snapshot()
        return {
            "version": 1,
            "exported_at": int(time.time()),
            "device_name": settings.device_name,
            "tor_policy": services.tor_policy.state(),
            "dns_filter": {
                key: services.dns_filter.state()[key]
                for key in ("profiles", "custom_blocked", "allowed")
            },
            "blocked_devices": services.device_guard.entries(),
            "device_access": services.access.rules(),
            "circumvention": {
                key: circumvention_state[key]
                for key in ("mode", "transport", "country", "custom_bridges")
            },
        }

    async def apply_configuration(
        document: dict[str, Any], session: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            version = int(document.get("version", 0))
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=400, detail="Format de configuration non reconnu"
            ) from error
        if version != 1:
            raise HTTPException(
                status_code=400, detail="Format de configuration non reconnu"
            )
        applied: list[str] = []
        failures: list[str] = []

        def restore() -> None:
            policy = document.get("tor_policy")
            if isinstance(policy, dict) and policy:
                try:
                    wanted = TorPolicyRequest.model_validate(policy)
                    services.tor_policy.update(
                        wanted.exit_country, wanted.rotation_seconds
                    )
                    applied.append("Politique Tor")
                except (PolicyError, ValidationError) as error:
                    failures.append(f"Politique Tor: {error}")
            blocked = document.get("blocked_devices")
            if isinstance(blocked, list):
                if len(blocked) > MAX_IMPORTED_DEVICES:
                    failures.append(
                        f"Appareils bloqués: {MAX_IMPORTED_DEVICES} au maximum"
                    )
                    blocked = blocked[:MAX_IMPORTED_DEVICES]
                requested: list[tuple[str, str]] = []
                for entry in blocked:
                    if not isinstance(entry, dict):
                        continue
                    try:
                        wanted_device = DeviceBlockRequest.model_validate(
                            {**entry, "blocked": True}
                        )
                    except ValidationError as error:
                        failures.append(f"Appareil bloqué: {error}")
                        continue
                    requested.append((wanted_device.mac, wanted_device.label))
                if requested:
                    try:
                        entries, rejected = services.device_guard.block_many(requested)
                        failures.extend(
                            f"Appareil bloqué: {reason}" for reason in rejected
                        )
                        if entries:
                            applied.append(f"{len(entries)} appareil(s) bloqué(s)")
                    except NetControlError as error:
                        failures.append(f"Appareils bloqués: {error}")
            rules = document.get("device_access")
            if isinstance(rules, list) and rules:
                try:
                    imported = services.access.restore(rules)
                    applied.append(f"{imported} règle(s) d’accès")
                except AccessError as error:
                    failures.append(f"Règles d’accès: {error}")
            dns = document.get("dns_filter")
            if isinstance(dns, dict) and dns:
                try:
                    wanted_dns = DnsFilterRequest.model_validate(dns)
                    services.dns_filter.update(
                        wanted_dns.profiles,
                        wanted_dns.custom_blocked,
                        wanted_dns.allowed,
                    )
                    applied.append("Filtrage DNS")
                except (NetControlError, ValidationError) as error:
                    failures.append(f"Filtrage DNS: {error}")
            bridges = document.get("circumvention")
            if isinstance(bridges, dict) and bridges:
                try:
                    wanted_bridges = CircumventionRequest.model_validate(bridges)
                    services.circumvention.update(
                        mode=wanted_bridges.mode,
                        transport=wanted_bridges.transport,
                        country=wanted_bridges.country,
                        custom_bridges=wanted_bridges.custom_bridges,
                    )
                    applied.append("Contournement")
                except (CircumventionError, ValidationError) as error:
                    failures.append(f"Contournement: {error}")

        await asyncio.to_thread(restore)
        database.add_activity(
            "secure", f"Configuration importée par {session['display_name']}"
        )
        return {"applied": applied, "failures": failures}

    @router.get("/api/v1/system/actions")
    def system_actions(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        return {
            "available": services.agent.available,
            "actions": [
                {"id": key, "label": label}
                for key, (label, _wait) in ACTIONS.items()
            ],
        }

    @router.get("/api/v1/system/diagnostics")
    async def system_diagnostics(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        """Authenticated readiness report with no secrets or client identifiers."""
        return await asyncio.to_thread(
            build_diagnostics,
            settings,
            database,
            services.tor,
            services.agent,
        )

    @router.post("/api/v1/system/action")
    async def run_system_action(
        payload: SystemActionRequest,
        session: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        if payload.action not in ACTIONS:
            raise HTTPException(status_code=400, detail="Action inconnue")
        label = ACTIONS[payload.action][0]
        database.add_activity(
            "secure", f"{label} demandé par {session['display_name']}"
        )
        try:
            return await asyncio.to_thread(services.agent.submit, payload.action)
        except AgentError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.get("/api/v1/system/update")
    async def update_state(
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(update_payload)

    @router.post("/api/v1/system/update/settings")
    async def save_update_settings(
        payload: UpdateSettingsRequest,
        session: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        if payload.channel not in CHANNELS:
            raise HTTPException(
                status_code=400, detail="Canal de mise à jour inconnu"
            )

        def persist() -> None:
            services.updates.save_preferences(
                payload.channel, payload.schedule, payload.enabled, payload.apply
            )

        try:
            await asyncio.to_thread(persist)
        except UpdateError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        database.add_activity(
            "secure", f"Horaire de mise à jour modifié par {session['display_name']}"
        )
        warning = ""
        if not settings.demo_mode:
            try:
                await asyncio.to_thread(services.agent.submit, "update-schedule")
            except AgentError as error:
                warning = str(error)
        payload_state = await asyncio.to_thread(update_payload)
        return {**payload_state, "warning": warning}

    @router.post("/api/v1/system/update/check")
    async def check_for_update(
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        if not settings.demo_mode:
            try:
                await asyncio.to_thread(
                    services.agent.submit, "update-check", UPDATE_CHECK_TIMEOUT
                )
            except AgentError as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
        return await asyncio.to_thread(update_payload)

    @router.post("/api/v1/system/update/run")
    async def run_update(
        session: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        state = await asyncio.to_thread(update_payload)
        if state["running"]:
            raise HTTPException(
                status_code=409, detail="Une mise à jour est déjà en cours."
            )
        database.add_activity(
            "secure", f"Mise à jour lancée par {session['display_name']}"
        )
        if not settings.demo_mode:
            try:
                await asyncio.to_thread(services.agent.submit, "update")
            except AgentError as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
        return {
            **(await asyncio.to_thread(update_payload)),
            "message": (
                "Mise à jour lancée. L’interface peut se recharger pendant l’opération."
            ),
        }

    @router.get("/api/v1/system/config", response_model=ConfigurationDocument)
    async def export_configuration(
        _: dict[str, Any] = Depends(current_session),
    ) -> Response:
        document = await asyncio.to_thread(configuration_document)
        return JSONResponse(
            document,
            headers={
                "Content-Disposition": 'attachment; filename="onionpi-config.json"'
            },
        )

    @router.post("/api/v1/system/config")
    async def import_configuration(
        payload: ConfigImportRequest,
        session: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        return await apply_configuration(payload.document, session)

    @router.post("/api/v1/system/backup")
    async def create_encrypted_backup(
        payload: BackupRequest,
        session: dict[str, Any] = Depends(csrf_session),
    ) -> Response:
        try:
            document = await asyncio.to_thread(configuration_document)
            envelope = await asyncio.to_thread(
                encrypt_configuration, document, payload.passphrase
            )
        except BackupError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except PasswordHashingBusy as error:
            raise HTTPException(status_code=429, detail=BACKUP_BUSY_DETAIL) from error
        database.add_activity(
            "secure", f"Sauvegarde chiffrée créée par {session['display_name']}"
        )
        return JSONResponse(
            envelope,
            headers={
                "Content-Disposition": 'attachment; filename="onionpi-backup.json"'
            },
        )

    @router.post("/api/v1/system/backup/preview")
    async def preview_encrypted_backup(
        payload: BackupRestoreRequest,
        _: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            restored = await asyncio.to_thread(
                decrypt_configuration, payload.backup, payload.passphrase
            )
        except BackupError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except PasswordHashingBusy as error:
            raise HTTPException(status_code=429, detail=BACKUP_BUSY_DETAIL) from error
        current = await asyncio.to_thread(configuration_document)
        return {
            "valid": True,
            "changes": configuration_diff(current, restored),
            "document_version": restored.get("version"),
        }

    @router.post("/api/v1/system/backup/restore")
    async def restore_encrypted_backup(
        payload: BackupRestoreRequest,
        session: dict[str, Any] = Depends(csrf_session),
    ) -> dict[str, Any]:
        try:
            restored = await asyncio.to_thread(
                decrypt_configuration, payload.backup, payload.passphrase
            )
        except BackupError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except PasswordHashingBusy as error:
            raise HTTPException(status_code=429, detail=BACKUP_BUSY_DETAIL) from error
        result = await apply_configuration(restored, session)
        database.add_activity(
            "secure", f"Sauvegarde chiffrée restaurée par {session['display_name']}"
        )
        return result

    @router.get("/api/v1/logs")
    async def logs(
        service: str = Query(
            default="tor",
            pattern=(
                r"^(tor|NetworkManager|dnsmasq|nftables|onionpi-firewall|"
                r"onionpi|snowflake-proxy)$"
            ),
        ),
        _: dict[str, Any] = Depends(current_session),
    ) -> dict[str, Any]:
        return {
            "service": service,
            "lines": await asyncio.to_thread(journal, service, 150),
        }

    return router
