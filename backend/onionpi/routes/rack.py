"""HTTP surface of the virtual rack."""

from __future__ import annotations

import asyncio
import io
import tarfile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..nodeclient import NodeError
from ..rack import MAX_UNITS, RackError
from .context import RouteContext


class RackRequest(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    location: str = Field(default="", max_length=48)
    units: int = Field(default=12, ge=1, le=MAX_UNITS)


class RackUpdateRequest(RackRequest):
    id: str = Field(min_length=8, max_length=8)


class RackIdRequest(BaseModel):
    id: str = Field(min_length=8, max_length=8)


class NodeIdRequest(BaseModel):
    id: str = Field(min_length=16, max_length=16)


class NodeScheduleRequest(BaseModel):
    enabled: bool = False
    days: list[int] = Field(default_factory=list, max_length=7)
    start: str = Field(default="", max_length=5)
    end: str = Field(default="", max_length=5)


class NodeRulesDocument(BaseModel):
    access: str = Field(default="allowed", max_length=16)
    egress: str = Field(default="tor-only", max_length=16)
    exit_country: str = Field(default="", max_length=2)
    keep_open_ports: list[int] = Field(default_factory=list, max_length=8)
    schedule: NodeScheduleRequest | None = None


class NodeCreateRequest(BaseModel):
    kind: str = Field(pattern=r"^(local|remote)$")
    name: str = Field(min_length=1, max_length=48)
    role: str = Field(default="", max_length=32)
    mac: str = Field(default="", max_length=17)
    onion: str = Field(default="", max_length=80)
    agent_port: int = Field(default=9080, ge=1, le=65535)
    notes: str = Field(default="", max_length=200)
    rules: NodeRulesDocument | None = None


class NodeUpdateRequest(BaseModel):
    id: str = Field(min_length=16, max_length=16)
    name: str = Field(min_length=1, max_length=48)
    role: str = Field(default="", max_length=32)
    onion: str = Field(default="", max_length=80)
    agent_port: int = Field(default=0, ge=0, le=65535)
    notes: str = Field(default="", max_length=200)


class NodeMoveRequest(BaseModel):
    id: str = Field(min_length=16, max_length=16)
    rack_id: str = Field(default="", max_length=8)
    position: int = Field(default=0, ge=0, le=MAX_UNITS)


class NodeRulesRequest(BaseModel):
    id: str = Field(min_length=16, max_length=16)
    rules: NodeRulesDocument


class NodeActionRequest(BaseModel):
    id: str = Field(min_length=16, max_length=16)
    verb: str = Field(min_length=1, max_length=32)
    unit: str = Field(default="", max_length=64)


def create_router(context: RouteContext) -> APIRouter:
    router = APIRouter()
    services = context.services
    rack = services.rack
    current_session = context.current_session
    csrf_session = context.csrf_session

    def guard(call: Any, *args: Any) -> Any:
        """Runs a manager call off the event loop, mapping its refusals to 4xx."""

        async def run() -> Any:
            try:
                return await asyncio.to_thread(call, *args)
            except RackError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except NodeError as error:
                raise HTTPException(status_code=502, detail=str(error)) from error

        return run()

    @router.get("/api/v1/rack")
    async def rack_state(_: dict[str, Any] = Depends(current_session)) -> dict[str, Any]:
        return await asyncio.to_thread(rack.snapshot)

    @router.post("/api/v1/rack/racks")
    async def create_rack(
        payload: RackRequest, _: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        return await guard(rack.create_rack, payload.name, payload.location, payload.units)

    @router.post("/api/v1/rack/racks/update")
    async def update_rack(
        payload: RackUpdateRequest, _: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        return await guard(
            rack.update_rack, payload.id, payload.name, payload.location, payload.units
        )

    @router.post("/api/v1/rack/racks/remove")
    async def remove_rack(
        payload: RackIdRequest, _: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        return await guard(rack.delete_rack, payload.id)

    @router.post("/api/v1/rack/nodes")
    async def create_node(
        payload: NodeCreateRequest, _: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        return await guard(
            rack.create_node,
            payload.kind,
            payload.name,
            payload.role,
            payload.mac,
            payload.onion,
            payload.agent_port,
            payload.notes,
            payload.rules.model_dump() if payload.rules else None,
        )

    @router.post("/api/v1/rack/nodes/update")
    async def update_node(
        payload: NodeUpdateRequest, _: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        return await guard(
            rack.update_node,
            payload.id,
            payload.name,
            payload.role,
            payload.onion,
            payload.agent_port,
            payload.notes,
        )

    @router.post("/api/v1/rack/nodes/remove")
    async def remove_node(
        payload: NodeIdRequest, _: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        return await guard(rack.delete_node, payload.id)

    @router.post("/api/v1/rack/nodes/move")
    async def move_node(
        payload: NodeMoveRequest, _: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        return await guard(rack.move_node, payload.id, payload.rack_id, payload.position)

    @router.post("/api/v1/rack/nodes/rules")
    async def set_rules(
        payload: NodeRulesRequest, _: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        return await guard(rack.set_rules, payload.id, payload.rules.model_dump())

    @router.post("/api/v1/rack/nodes/refresh")
    async def refresh_node(
        payload: NodeIdRequest, _: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        return await guard(rack.refresh, payload.id)

    @router.post("/api/v1/rack/nodes/action")
    async def run_action(
        payload: NodeActionRequest, _: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        result = await guard(rack.run_action, payload.id, payload.verb, payload.unit)
        return {"verb": payload.verb, "result": result}

    @router.post("/api/v1/rack/nodes/enrollment")
    async def enrollment(
        payload: NodeIdRequest, session: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        bundle = await guard(rack.enrollment, payload.id)
        services.database.add_activity(
            "secure",
            f"Jeton d’enrôlement affiché par {session['display_name']}",
        )
        return bundle

    @router.post("/api/v1/rack/nodes/rotate-token")
    async def rotate_token(
        payload: NodeIdRequest, _: dict[str, Any] = Depends(csrf_session)
    ) -> dict[str, Any]:
        return await guard(rack.rotate_token, payload.id)

    @router.get("/api/v1/rack/agent-bundle")
    async def agent_bundle(_: dict[str, Any] = Depends(current_session)) -> Response:
        """The installer to run on the node, as a tar.gz built on the fly.

        Handing out an archive rather than a `curl … | sh` one-liner is
        deliberate: the operator reads four short files before running them as
        root on a machine this appliance is about to control.
        """
        archive = await asyncio.to_thread(_build_bundle, context.settings.node_agent_dir)
        if not archive:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Agent de nœud absent de cette installation. Récupérez "
                    "packaging/agent/ depuis le dépôt."
                ),
            )
        return Response(
            content=archive,
            media_type="application/gzip",
            headers={
                "Content-Disposition": 'attachment; filename="onionpi-node-agent.tar.gz"'
            },
        )

    return router


def _build_bundle(source: Any) -> bytes:
    if not source.is_dir():
        return b""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            info = archive.gettarinfo(path, arcname=f"onionpi-node-agent/{path.relative_to(source)}")
            # Reproducible and owner-free: the archive is unpacked as root on a
            # machine that has never heard of this appliance's user ids.
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = 0
            info.mode = 0o755 if path.suffix in {".sh", ".py"} else 0o644
            with path.open("rb") as handle:
                archive.addfile(info, handle)
    return buffer.getvalue()
