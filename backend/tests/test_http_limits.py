from __future__ import annotations

from typing import Any

import pytest
from onionpi.http_limits import UPLOAD_PATH, BodyLimitMiddleware
from starlette.types import Message, Receive, Scope, Send


def build_scope(path: str) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "state": {},
    }


async def run(middleware: BodyLimitMiddleware, scope: Scope) -> list[Message]:
    messages = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )
    sent: list[Message] = []

    async def receive() -> Message:
        return next(messages)  # type: ignore[return-value]

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


async def consume_everything(_: Scope, get_message: Receive, send: Send) -> None:
    await get_message()
    await get_message()
    await send({"type": "http.response.start", "status": 201, "headers": []})
    await send({"type": "http.response.body", "body": b""})


@pytest.mark.asyncio
async def test_body_limit_counts_stream_without_content_length() -> None:
    middleware = BodyLimitMiddleware(
        consume_everything, request_limit=5, upload_limit=10
    )

    sent = await run(middleware, build_scope("/api/v1/auth/login"))

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_upload_limit_is_read_again_for_every_import() -> None:
    """The import budget follows the free space of the moment.

    A multipart file part is spooled with no ceiling of its own, so a body that
    announces no length can only be stopped here. Reading the limit once at
    startup would let an appliance that has since filled up keep buffering
    gigabytes it has nowhere to put.
    """
    budgets = iter([64, 4])
    calls: list[Any] = []

    def current_budget() -> int:
        calls.append(True)
        return next(budgets)

    middleware = BodyLimitMiddleware(
        consume_everything, request_limit=1024, upload_limit=current_budget
    )

    assert (await run(middleware, build_scope(UPLOAD_PATH)))[0]["status"] != 413
    # Same request, less room: the second import is refused mid-stream.
    assert (await run(middleware, build_scope(UPLOAD_PATH)))[0]["status"] == 413
    assert len(calls) == 2
