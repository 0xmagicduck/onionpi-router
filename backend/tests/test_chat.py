from __future__ import annotations

import pytest
from onionpi.main import ChatManager


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed_with: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int) -> None:
        self.closed_with = code

    async def send_json(self, _: dict[str, object]) -> None:
        pass


@pytest.mark.asyncio
async def test_chat_manager_revokes_token_and_user_connections() -> None:
    manager = ChatManager()
    first = FakeWebSocket()
    second = FakeWebSocket()
    third = FakeWebSocket()
    await manager.connect(first, "token-a", 1)  # type: ignore[arg-type]
    await manager.connect(second, "token-b", 1)  # type: ignore[arg-type]
    await manager.connect(third, "token-c", 2)  # type: ignore[arg-type]

    await manager.disconnect_token("token-a")
    assert first.closed_with == 1008
    assert second.closed_with is None
    assert manager.online == 2

    await manager.disconnect_user(1)
    assert second.closed_with == 1008
    assert third.closed_with is None
    assert manager.online == 1
