from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import RateLimiter, token_hash
from .context import COOKIE_NAME, RouteContext


class ChatManager:
    def __init__(self) -> None:
        self._connections: dict[WebSocket, tuple[str, int]] = {}
        self._lock = asyncio.Lock()

    @property
    def online(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket, digest: str, user_id: int) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[websocket] = (digest, user_id)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.pop(websocket, None)

    async def disconnect_token(self, digest: str) -> None:
        await self._disconnect_matching(lambda metadata: metadata[0] == digest)

    async def disconnect_user(self, user_id: int) -> None:
        await self._disconnect_matching(lambda metadata: metadata[1] == user_id)

    async def disconnect_all(self) -> None:
        await self._disconnect_matching(lambda _metadata: True)

    async def _disconnect_matching(
        self, predicate: Callable[[tuple[str, int]], bool]
    ) -> None:
        async with self._lock:
            selected = [
                websocket
                for websocket, metadata in self._connections.items()
                if predicate(metadata)
            ]
            for websocket in selected:
                self._connections.pop(websocket, None)
        for websocket in selected:
            try:
                await websocket.close(code=1008)
            except RuntimeError:
                pass

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        async with self._lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                await connection.send_json(payload)
            except Exception:
                stale.append(connection)
        if stale:
            async with self._lock:
                for connection in stale:
                    self._connections.pop(connection, None)


def create_router(context: RouteContext) -> APIRouter:
    router = APIRouter()
    settings = context.settings
    database = context.services.database
    chat = context.chat

    @router.websocket("/api/v1/chat/ws")
    async def chat_socket(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if origin and origin.rstrip("/") not in context.allowed_origins():
            await websocket.close(code=1008)
            return
        raw_token = websocket.cookies.get(COOKIE_NAME)
        digest = token_hash(raw_token, settings.session_secret) if raw_token else ""
        session = database.session(digest) if digest else None
        if not session:
            await websocket.close(code=1008)
            return
        await chat.connect(websocket, digest, int(session["user_id"]))
        limiter = RateLimiter(events=15, window_seconds=10)
        await websocket.send_json(
            {"type": "history", "messages": database.messages(100)}
        )
        await chat.broadcast({"type": "presence", "online": chat.online})
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(), timeout=30
                    )
                except TimeoutError:
                    if database.session(digest) is None:
                        await websocket.close(code=1008)
                        return
                    continue
                refreshed_session = database.session(digest)
                if refreshed_session is None:
                    await websocket.close(code=1008)
                    return
                session = refreshed_session
                if len(raw) > 8192:
                    await websocket.send_json(
                        {"type": "error", "message": "Message invalide"}
                    )
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
                    await websocket.send_json(
                        {"type": "error", "message": "Message invalide"}
                    )
                    continue
                if not limiter.allow():
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Trop de messages. Patientez un instant.",
                        }
                    )
                    continue
                message = database.add_message(
                    int(session["user_id"]), session["display_name"], body
                )
                await chat.broadcast({"type": "message", "message": message})
        except WebSocketDisconnect:
            pass
        finally:
            await chat.disconnect(websocket)
            await chat.broadcast({"type": "presence", "online": chat.online})

    return router
