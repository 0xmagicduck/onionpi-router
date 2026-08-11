from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from onionpi.database import MAX_ACTIVITIES, MAX_MESSAGES, Database


def test_database_maintenance_bounds_history_and_removes_expired_sessions(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "onionpi.db")
    database.initialize()
    with database.connect() as connection:
        connection.executemany(
            "INSERT INTO messages(user_id,author,body,created_at) VALUES(NULL,?,?,?)",
            (("Test", f"message-{index}", index) for index in range(MAX_MESSAGES + 25)),
        )
        connection.executemany(
            "INSERT INTO activity(kind,message,created_at) VALUES(?,?,?)",
            (("test", f"event-{index}", index) for index in range(MAX_ACTIVITIES + 25)),
        )
        connection.execute(
            "INSERT INTO users(username,display_name,password_hash,created_at) VALUES(?,?,?,?)",
            ("admin", "Admin", "digest", 0),
        )
        connection.execute(
            "INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) "
            "VALUES(?,?,?,?,?)",
            ("expired", 1, "csrf", int(time.time()) - 1, 0),
        )

    stats = database.maintain()

    assert stats["messages"] == MAX_MESSAGES
    assert stats["activity"] == MAX_ACTIVITIES
    assert stats["sessions"] == 0
    with database.connect() as connection:
        oldest = connection.execute("SELECT body FROM messages ORDER BY id LIMIT 1").fetchone()[0]
    assert oldest == "message-25"
    assert database.quick_check() == "ok"


def test_quick_check_reports_an_invalid_database(tmp_path: Path) -> None:
    path = tmp_path / "broken.db"
    path.write_bytes(b"this is not sqlite")

    result = Database(path).quick_check()

    assert result != "ok"
    assert "database" in result.lower()


def test_multiple_real_connections_are_serialised_without_losing_writes(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "concurrent.db")
    database.initialize()

    # Each call opens and commits a real SQLite connection from competing
    # threads, like FastAPI's worker pool does in production.
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda index: database.add_activity("parallel", f"write-{index}"),
                range(80),
            )
        )

    with sqlite3.connect(database.path) as connection:
        count = connection.execute("SELECT count(*) FROM activity").fetchone()[0]
    assert count == 80
