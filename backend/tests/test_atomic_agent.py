from __future__ import annotations

import threading
import time
from pathlib import Path

from onionpi.agent import PrivilegedAgent
from onionpi.atomic_io import atomic_write_text


def test_atomic_write_replaces_content_and_leaves_no_partial_file(tmp_path: Path) -> None:
    path = tmp_path / "state.txt"
    atomic_write_text(path, "first\n")
    atomic_write_text(path, "second\n", mode=0o640)

    assert path.read_text() == "second\n"
    assert path.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.glob(".state.txt.*")) == []


def test_privileged_agent_round_trip_over_real_files(tmp_path: Path) -> None:
    request = tmp_path / "agent.request"
    result = tmp_path / "agent.result"
    request.touch(mode=0o640)
    result.touch(mode=0o644)
    agent = PrivilegedAgent(request, result)

    def root_worker() -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            line = request.read_text().strip()
            if line:
                nonce, action = line.split()
                atomic_write_text(result, f"{nonce} ok {action} appliqué\n", mode=0o644)
                return
            time.sleep(0.01)

    worker = threading.Thread(target=root_worker)
    worker.start()
    response = agent.submit("devices", timeout=2)
    worker.join(timeout=2)

    assert response == {
        "action": "devices",
        "status": "ok",
        "message": "devices appliqué",
    }
    assert not worker.is_alive()
