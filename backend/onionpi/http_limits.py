from __future__ import annotations

from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

UPLOAD_PATH = "/api/v1/files/upload"


class _BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Rejects oversized HTTP bodies before parsers allocate for them.

    Content-Length is only an optimization: the wrapped receive channel also
    counts chunked bodies, so omitting or forging the header cannot bypass the
    appliance-wide memory/SD-card budget.

    `upload_limit` may be a callable, evaluated per request. The import budget
    depends on the free space of the moment, and it is this layer — not the
    handler — that sees a body announcing no length at all.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        request_limit: int,
        upload_limit: int | Callable[[], int],
    ) -> None:
        self.app = app
        self.request_limit = request_limit
        self.upload_limit = upload_limit

    def _limit_for(self, path: str) -> int:
        if path != UPLOAD_PATH:
            return self.request_limit
        limit = self.upload_limit
        return limit() if callable(limit) else limit

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") != "http" or scope.get("method") not in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(str(scope.get("path", "")))
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared:
            try:
                declared_size = int(declared)
                if declared_size < 0:
                    raise ValueError
                if declared_size > limit:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse(status_code=400, content={"detail": "Taille de requête invalide"})
                await response(scope, receive, send)
                return

        consumed = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message.get("type") == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise _BodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _BodyTooLarge:
            if response_started:
                raise
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        response = JSONResponse(status_code=413, content={"detail": "Requête trop volumineuse"})
        await response(scope, receive, send)
