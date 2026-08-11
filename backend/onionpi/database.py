from __future__ import annotations

import json
import sqlite3
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

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

-- Preferences the web interface owns: exit country, identity rotation, DNS
-- filtering profile, onion service switch. Values are JSON documents.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

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
