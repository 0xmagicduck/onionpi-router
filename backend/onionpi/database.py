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
    created_at INTEGER NOT NULL,
    password_changed_at INTEGER NOT NULL DEFAULT 0
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

-- Virtual rack. A rack is a drawing: what it is worth comes from the rules
-- attached to the machines placed in it, which are applied by the firewall
-- here and by the node agent over there.
CREATE TABLE IF NOT EXISTS racks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    units INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rack_nodes (
    id TEXT PRIMARY KEY,
    rack_id TEXT REFERENCES racks(id) ON DELETE SET NULL,
    -- 0 means "not racked yet": the node exists and keeps its rules, it simply
    -- occupies no slot. Partial index below leaves those rows unconstrained.
    position INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    mac TEXT NOT NULL DEFAULT '',
    onion TEXT NOT NULL DEFAULT '',
    agent_port INTEGER NOT NULL DEFAULT 9080,
    token_epoch INTEGER NOT NULL DEFAULT 1,
    client_auth INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    rules TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT '{}',
    last_seen INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Two machines in one slot is not a topology anyone can reason about, and the
-- move endpoint would have to trust its own bookkeeping to prevent it. SQLite
-- enforces it instead: a bad move fails, it does not overwrite.
CREATE UNIQUE INDEX IF NOT EXISTS idx_rack_nodes_slot
    ON rack_nodes(rack_id, position) WHERE rack_id IS NOT NULL AND position > 0;
CREATE INDEX IF NOT EXISTS idx_rack_nodes_rack ON rack_nodes(rack_id);
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
    "racks": "SELECT count(*) FROM racks",
    "rack_nodes": "SELECT count(*) FROM rack_nodes",
}

#: Mutable columns of a rack node, in the order both statements below use.
#: Nothing outside this tuple is ever written from a caller's dictionary.
RACK_NODE_DEFAULTS: dict[str, Any] = {
    "rack_id": None,
    "position": 0,
    "name": "",
    "role": "",
    "mac": "",
    "onion": "",
    "agent_port": 9080,
    "token_epoch": 1,
    "client_auth": 0,
    "notes": "",
    "rules": "{}",
    "state": "{}",
    "last_seen": 0,
    "last_error": "",
}
RACK_NODE_FIELDS = tuple(RACK_NODE_DEFAULTS)
# Written out rather than assembled: a statement built by a loop is a statement
# a reader has to reconstruct, and `test_rack` checks the two lists agree.
RACK_NODE_INSERT = """
INSERT INTO rack_nodes(
    id, kind, created_at, updated_at,
    rack_id, position, name, role, mac, onion, agent_port, token_epoch,
    client_auth, notes, rules, state, last_seen, last_error
) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""
RACK_NODE_UPDATE = """
UPDATE rack_nodes SET
    rack_id=?, position=?, name=?, role=?, mac=?, onion=?, agent_port=?,
    token_epoch=?, client_auth=?, notes=?, rules=?, state=?, last_seen=?,
    last_error=?, updated_at=?
WHERE id=?
"""


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
            self._migrate(connection)
            self._maintain(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Brings a database created by an older release up to this schema.

        `CREATE TABLE IF NOT EXISTS` leaves an existing table untouched, so a
        column added after an appliance was installed has to be added here too.
        """
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        if "password_changed_at" not in columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN password_changed_at INTEGER NOT NULL DEFAULT 0"
            )
            # The account was created with its password: that date is the best
            # estimate available for an appliance upgraded in place.
            connection.execute("UPDATE users SET password_changed_at = created_at")

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
                "INSERT INTO users(username, display_name, password_hash, created_at, "
                "password_changed_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name, "
                "password_hash=excluded.password_hash, "
                "password_changed_at=excluded.password_changed_at",
                (username, display_name, password_hash, now, now),
            )
            row = connection.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()
            user_id = int(row["id"] if row else cursor.lastrowid)
            # A password reset must revoke every browser that still holds an
            # older session for this account.
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            return user_id

    def administrator(self) -> dict[str, Any] | None:
        """The account that administers this appliance, or None on a blank one.

        install.sh creates exactly one, and nothing in the interface adds a
        second. Recovery still asks rather than assuming the name "admin": an
        appliance whose account was created by hand under another name would
        otherwise gain a *second* administrator, leaving the first password —
        and whoever knew it — valid after a recovery meant to revoke it.
        """
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users ORDER BY id LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def user_by_name(self, username: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()
            return dict(row) if row else None

    def oldest_password_age(self) -> int:
        """Seconds since the least recently changed administrator password."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT min(password_changed_at) AS changed_at FROM users"
            ).fetchone()
        changed_at = int(row["changed_at"] or 0) if row else 0
        return max(0, int(time.time()) - changed_at) if changed_at else 0

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

    def delete_all_sessions(self) -> None:
        """Closes every browser on the appliance, whichever account it holds."""
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions")

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

    # -------------------------------------------------------------- rack ---

    def racks(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,name,location,units,created_at FROM racks ORDER BY created_at, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def rack(self, rack_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id,name,location,units,created_at FROM racks WHERE id=?", (rack_id,)
            ).fetchone()
        return dict(row) if row else None

    def create_rack(self, rack_id: str, name: str, location: str, units: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO racks(id,name,location,units,created_at) VALUES(?,?,?,?,?)",
                (rack_id, name, location, units, int(time.time())),
            )

    def update_rack(self, rack_id: str, name: str, location: str, units: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE racks SET name=?, location=?, units=? WHERE id=?",
                (name, location, units, rack_id),
            )

    def delete_rack(self, rack_id: str) -> None:
        """Removes the frame. Its machines survive, unracked and still ruled."""
        with self.connect() as connection:
            connection.execute(
                "UPDATE rack_nodes SET rack_id=NULL, position=0, updated_at=? WHERE rack_id=?",
                (int(time.time()), rack_id),
            )
            connection.execute("DELETE FROM racks WHERE id=?", (rack_id,))

    def rack_nodes(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM rack_nodes ORDER BY rack_id IS NULL, rack_id, position, name"
            ).fetchall()
        return [dict(row) for row in rows]

    def rack_node(self, node_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM rack_nodes WHERE id=?", (node_id,)
            ).fetchone()
        return dict(row) if row else None

    def create_rack_node(self, node_id: str, kind: str, values: dict[str, Any]) -> None:
        now = int(time.time())
        row = {**RACK_NODE_DEFAULTS, **{
            field: values[field] for field in RACK_NODE_FIELDS if field in values
        }}
        with self.connect() as connection:
            connection.execute(RACK_NODE_INSERT, (node_id, kind, now, now, *row.values()))

    def update_rack_node(self, node_id: str, values: dict[str, Any]) -> None:
        """Replaces the mutable half of one node row.

        Every column is written every time, from a fixed statement: a partial
        update assembled from the caller's keys would put a name this module
        does not choose into an SQL string, which is exactly the shape of
        mistake a rack full of remote machines cannot afford.
        """
        with self.connect() as connection:
            current = connection.execute(
                "SELECT * FROM rack_nodes WHERE id=?", (node_id,)
            ).fetchone()
            if current is None:
                return
            row = {
                field: values[field] if field in values else current[field]
                for field in RACK_NODE_FIELDS
            }
            connection.execute(
                RACK_NODE_UPDATE, (*row.values(), int(time.time()), node_id)
            )

    def delete_rack_node(self, node_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM rack_nodes WHERE id=?", (node_id,))

    def move_rack_node(self, node_id: str, rack_id: str | None, position: int) -> str:
        """Places one node, swapping with whatever already holds the slot.

        The whole exchange is one transaction, and the occupant is parked at
        position 0 first: the partial unique index ignores that row, so the
        intermediate state never trips the constraint it exists to enforce.
        """
        now = int(time.time())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT rack_id, position FROM rack_nodes WHERE id=?", (node_id,)
            ).fetchone()
            if row is None:
                return ""
            occupant = None
            if rack_id and position > 0:
                occupant = connection.execute(
                    "SELECT id FROM rack_nodes WHERE rack_id=? AND position=? AND id<>?",
                    (rack_id, position, node_id),
                ).fetchone()
            if occupant is not None:
                connection.execute(
                    "UPDATE rack_nodes SET position=0, updated_at=? WHERE id=?",
                    (now, occupant["id"]),
                )
            connection.execute(
                "UPDATE rack_nodes SET rack_id=?, position=?, updated_at=? WHERE id=?",
                (rack_id, position, now, node_id),
            )
            if occupant is not None:
                connection.execute(
                    "UPDATE rack_nodes SET rack_id=?, position=?, updated_at=? WHERE id=?",
                    (row["rack_id"], row["position"], now, occupant["id"]),
                )
                return str(occupant["id"])
        return ""

    def activities(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,kind,message,created_at FROM activity ORDER BY id DESC LIMIT ?",
                (min(max(limit, 1), 50),),
            ).fetchall()
        return [dict(row) for row in rows]
