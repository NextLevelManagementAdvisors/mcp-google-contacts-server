"""SQLite-backed storage for multi-tenant OAuth state.

Single file at `config.db_path`. WAL mode, short-lived connections, no ORM.
All timestamps are unix epoch seconds (integers).
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS users (
    google_email TEXT PRIMARY KEY,
    google_refresh_token TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    mcp_client_id TEXT NOT NULL,
    mcp_redirect_uri TEXT NOT NULL,
    mcp_redirect_uri_explicit INTEGER NOT NULL,
    mcp_code_challenge TEXT NOT NULL,
    mcp_scopes TEXT NOT NULL,
    mcp_resource TEXT,
    mcp_state TEXT,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_codes (
    code TEXT PRIMARY KEY,
    google_email TEXT NOT NULL,
    mcp_client_id TEXT NOT NULL,
    mcp_redirect_uri TEXT NOT NULL,
    mcp_redirect_uri_explicit INTEGER NOT NULL,
    mcp_code_challenge TEXT NOT NULL,
    mcp_scopes TEXT NOT NULL,
    mcp_resource TEXT,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS access_tokens (
    token TEXT PRIMARY KEY,
    google_email TEXT NOT NULL,
    mcp_client_id TEXT NOT NULL,
    scopes TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    resource TEXT
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token TEXT PRIMARY KEY,
    google_email TEXT NOT NULL,
    mcp_client_id TEXT NOT NULL,
    scopes TEXT NOT NULL,
    expires_at INTEGER
);

CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    metadata_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_access_tokens_email ON access_tokens(google_email);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_email ON refresh_tokens(google_email);
"""


class Db:
    """Thin wrapper around sqlite3. Construct once at startup, share across requests."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA_V1)
            c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # ---- users -------------------------------------------------------------

    def upsert_user(self, google_email: str, google_refresh_token: str) -> None:
        now = int(time.time())
        with self._conn() as c:
            c.execute(
                """INSERT INTO users(google_email, google_refresh_token, created_at, last_seen_at)
                   VALUES(?, ?, ?, ?)
                   ON CONFLICT(google_email) DO UPDATE SET
                     google_refresh_token=excluded.google_refresh_token,
                     last_seen_at=excluded.last_seen_at""",
                (google_email, google_refresh_token, now, now),
            )

    def get_user(self, google_email: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM users WHERE google_email = ?", (google_email,)
            ).fetchone()
        return dict(row) if row else None

    def touch_user(self, google_email: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE users SET last_seen_at = ? WHERE google_email = ?",
                (int(time.time()), google_email),
            )

    # ---- oauth_states (CSRF / flow state for outbound Google redirect) -----

    def put_state(self, state: str, data: Dict[str, Any], ttl_seconds: int = 600) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO oauth_states(state, mcp_client_id, mcp_redirect_uri,
                     mcp_redirect_uri_explicit, mcp_code_challenge, mcp_scopes,
                     mcp_resource, mcp_state, expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    state,
                    data["mcp_client_id"],
                    data["mcp_redirect_uri"],
                    int(data["mcp_redirect_uri_explicit"]),
                    data["mcp_code_challenge"],
                    json.dumps(data["mcp_scopes"]),
                    data.get("mcp_resource"),
                    data.get("mcp_state"),
                    int(time.time()) + ttl_seconds,
                ),
            )

    def pop_state(self, state: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM oauth_states WHERE state = ? AND expires_at > ?",
                (state, int(time.time())),
            ).fetchone()
            c.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        if not row:
            return None
        d = dict(row)
        d["mcp_scopes"] = json.loads(d["mcp_scopes"])
        d["mcp_redirect_uri_explicit"] = bool(d["mcp_redirect_uri_explicit"])
        return d

    # ---- auth_codes (short-lived, one-time) --------------------------------

    def put_auth_code(self, code: str, data: Dict[str, Any], ttl_seconds: int = 600) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO auth_codes(code, google_email, mcp_client_id, mcp_redirect_uri,
                     mcp_redirect_uri_explicit, mcp_code_challenge, mcp_scopes, mcp_resource,
                     expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    code,
                    data["google_email"],
                    data["mcp_client_id"],
                    data["mcp_redirect_uri"],
                    int(data["mcp_redirect_uri_explicit"]),
                    data["mcp_code_challenge"],
                    json.dumps(data["mcp_scopes"]),
                    data.get("mcp_resource"),
                    int(time.time()) + ttl_seconds,
                ),
            )

    def get_auth_code(self, code: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM auth_codes WHERE code = ? AND expires_at > ?",
                (code, int(time.time())),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["mcp_scopes"] = json.loads(d["mcp_scopes"])
        d["mcp_redirect_uri_explicit"] = bool(d["mcp_redirect_uri_explicit"])
        return d

    def delete_auth_code(self, code: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM auth_codes WHERE code = ?", (code,))

    # ---- access_tokens -----------------------------------------------------

    def put_access_token(
        self,
        token: str,
        google_email: str,
        mcp_client_id: str,
        scopes: List[str],
        expires_at: int,
        resource: Optional[str] = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO access_tokens(token, google_email, mcp_client_id, scopes,
                     expires_at, resource)
                   VALUES(?,?,?,?,?,?)""",
                (token, google_email, mcp_client_id, json.dumps(scopes), expires_at, resource),
            )

    def get_access_token(self, token: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM access_tokens WHERE token = ? AND expires_at > ?",
                (token, int(time.time())),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["scopes"] = json.loads(d["scopes"])
        return d

    def delete_access_token(self, token: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM access_tokens WHERE token = ?", (token,))

    # ---- refresh_tokens ----------------------------------------------------

    def put_refresh_token(
        self,
        token: str,
        google_email: str,
        mcp_client_id: str,
        scopes: List[str],
        expires_at: Optional[int] = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO refresh_tokens(token, google_email, mcp_client_id, scopes, expires_at)
                   VALUES(?,?,?,?,?)""",
                (token, google_email, mcp_client_id, json.dumps(scopes), expires_at),
            )

    def get_refresh_token(self, token: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM refresh_tokens WHERE token = ? AND (expires_at IS NULL OR expires_at > ?)",
                (token, int(time.time())),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["scopes"] = json.loads(d["scopes"])
        return d

    def delete_refresh_token(self, token: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM refresh_tokens WHERE token = ?", (token,))

    def delete_tokens_for_user(self, google_email: str) -> None:
        """Revoke all access + refresh tokens for a user (e.g. on sign-out)."""
        with self._conn() as c:
            c.execute("DELETE FROM access_tokens WHERE google_email = ?", (google_email,))
            c.execute("DELETE FROM refresh_tokens WHERE google_email = ?", (google_email,))

    # ---- clients (Dynamic Client Registration) -----------------------------

    def put_client(self, client_id: str, metadata: Dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO clients(client_id, metadata_json, created_at)
                   VALUES(?, ?, ?)
                   ON CONFLICT(client_id) DO UPDATE SET metadata_json=excluded.metadata_json""",
                (client_id, json.dumps(metadata), int(time.time())),
            )

    def get_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(
                "SELECT metadata_json FROM clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        return json.loads(row["metadata_json"]) if row else None
