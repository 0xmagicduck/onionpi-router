from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 2000),
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity(created_at);

-- Preferences the web interface owns: exit country, identity rotation, DNS
-- filtering profile, onion service switch. Values are JSON documents.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

MAX_MESSAGES = 2_000
MAX_ACTIVITIES = 4_000
TRIM_QUERIES = {
    "messages": (
        "DELETE FROM messages WHERE id NOT IN "
        "(SELECT id FROM messages ORDER BY id DESC LIMIT ?)"
    ),
    "activity": (
        "DELETE FROM activity WHERE id NOT IN "
        "(SELECT id FROM activity ORDER BY id DESC LIMIT ?)"
    ),
}
COUNT_QUERIES = {
    "users": "SELECT count(*) FROM users",
    "sessions": "SELECT count(*) FROM sessions",
    "messages": "SELECT count(*) FROM messages",
    "activity": "SELECT count(*) FROM activity",
    "settings": "SELECT count(*) FROM settings",
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        # SQLite already serializes writers, but keeping every short-lived
        # connection from this process behind one lock also avoids overlapping
        # WAL checkpoints on distribution builds with older SQLite libraries.
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            try:
                yield connection
                connection.commit()
            finally:
                connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._maintain(connection)

    @staticmethod
    def _trim(connection: sqlite3.Connection, table: str, limit: int) -> None:
        # LIMIT on DELETE is an optional SQLite compile-time feature.  The
        # nested SELECT form works on every Raspberry Pi OS build.
        connection.execute(TRIM_QUERIES[table], (limit,))

    def _maintain(self, connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
        self._trim(connection, "messages", MAX_MESSAGES)
        self._trim(connection, "activity", MAX_ACTIVITIES)
        connection.execute("PRAGMA optimize")

    def maintain(self) -> dict[str, int]:
        """Bound persistent history and return the remaining row counts."""
        with self.connect() as connection:
            self._maintain(connection)
            return self._stats(connection)

    @staticmethod
    def _stats(connection: sqlite3.Connection) -> dict[str, int]:
        return {
            table: int(connection.execute(query).fetchone()[0])
            for table, query in COUNT_QUERIES.items()
        }

    def stats(self) -> dict[str, int]:
        with self.connect() as connection:
            counts = self._stats(connection)
        try:
            counts["bytes"] = self.path.stat().st_size
        except OSError:
            counts["bytes"] = 0
        return counts

    def quick_check(self) -> str:
        try:
            with self.connect() as connection:
                rows = connection.execute("PRAGMA quick_check").fetchall()
        except sqlite3.DatabaseError as error:
            return str(error)
        return "\n".join(str(row[0]) for row in rows) or "résultat vide"

    def create_user(self, username: str, display_name: str, password_hash: str) -> int:
        now = int(time.time())
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO users(username, display_name, password_hash, created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name, "
                "password_hash=excluded.password_hash",
                (username, display_name, password_hash, now),
            )
            row = connection.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()
            user_id = int(row["id"] if row else cursor.lastrowid)
            # A password reset must revoke every browser that still holds an
            # older session for this account.
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            return user_id

    def user_by_name(self, username: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()
            return dict(row) if row else None

    def create_session(
        self, token_hash: str, csrf_token: str, user_id: int, ttl_seconds: int
    ) -> None:
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            connection.execute(
                "INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) "
                "VALUES(?,?,?,?,?)",
                (token_hash, user_id, csrf_token, now + ttl_seconds, now),
            )

    def session(self, token_hash: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT s.token_hash,s.csrf_token,s.expires_at,u.id AS user_id,u.username,u.display_name "
                "FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token_hash=? AND s.expires_at>?",
                (token_hash, int(time.time())),
            ).fetchone()
            return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def add_message(self, user_id: int | None, author: str, body: str) -> dict[str, Any]:
        now = int(time.time())
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO messages(user_id,author,body,created_at) VALUES(?,?,?,?)",
                (user_id, author, body, now),
            )
            self._trim(connection, "messages", MAX_MESSAGES)
            return {
                "id": cursor.lastrowid,
                "author": author,
                "body": body,
                "created_at": now,
            }

    def messages(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,author,body,created_at FROM messages ORDER BY id DESC LIMIT ?",
                (min(max(limit, 1), 250),),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def add_activity(self, kind: str, message: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO activity(kind,message,created_at) VALUES(?,?,?)",
                (kind[:32], message[:300], int(time.time())),
            )
            self._trim(connection, "activity", MAX_ACTIVITIES)

    def setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            # A hand-edited or truncated row must not take the service down.
            return default

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key[:64], json.dumps(value), int(time.time())),
            )

    def activities(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,kind,message,created_at FROM activity ORDER BY id DESC LIMIT ?",
                (min(max(limit, 1), 50),),
            ).fetchall()
        return [dict(row) for row in rows]
