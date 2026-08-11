from __future__ import annotations

import pytest
from onionpi.http_limits import BodyLimitMiddleware
from starlette.types import Message, Receive, Scope, Send


@pytest.mark.asyncio
async def test_body_limit_counts_stream_without_content_length() -> None:
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

    async def downstream(_: Scope, get_message: Receive, __: Send) -> None:
        await get_message()
        await get_message()

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/auth/login",
        "raw_path": b"/api/v1/auth/login",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "state": {},
    }
    middleware = BodyLimitMiddleware(downstream, request_limit=5, upload_limit=10)

    await middleware(scope, receive, send)

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
