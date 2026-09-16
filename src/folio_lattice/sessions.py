from __future__ import annotations

import hashlib
import json
import math
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .auth import MembershipStore, Principal

SESSION_COOKIE_NAME = "__Host-folio_session"
SESSION_COOKIE_PATH = "/"
DEFAULT_SESSION_ABSOLUTE_TTL_SECONDS = 8 * 60 * 60
DEFAULT_SESSION_IDLE_TTL_SECONDS = 60 * 60
SESSION_ID_BYTES = 32


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    principal: Principal
    created_at: float
    last_seen_at: float
    expires_at: float


class HostedIdentityAdapter(Protocol):
    """Provider-neutral seam for a future browser authorization-code flow."""

    def authorization_url(self, *, state: str, return_to: str) -> str: ...

    def complete_callback(self, *, code: str, state: str) -> Principal: ...


class SessionStore:
    """SQLite-backed opaque browser sessions with server-side revocation."""

    def __init__(
        self,
        db_path: str | Path,
        membership_store: MembershipStore,
        *,
        absolute_ttl_seconds: float = DEFAULT_SESSION_ABSOLUTE_TTL_SECONDS,
        idle_ttl_seconds: float = DEFAULT_SESSION_IDLE_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (
            not math.isfinite(absolute_ttl_seconds)
            or not math.isfinite(idle_ttl_seconds)
            or absolute_ttl_seconds <= 0
            or idle_ttl_seconds <= 0
        ):
            raise ValueError("session TTLs must be positive")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.membership_store = membership_store
        self.absolute_ttl_seconds = absolute_ttl_seconds
        self.idle_ttl_seconds = idle_ttl_seconds
        self._clock = clock
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS browser_sessions (
                    session_hash TEXT PRIMARY KEY,
                    issuer TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at REAL
                )
                """
            )

    def create(self, principal: Principal) -> str:
        session_id = secrets.token_urlsafe(SESSION_ID_BYTES)
        now = self._clock()
        scopes = json.dumps(sorted(principal.scopes), separators=(",", ":"))
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO browser_sessions(
                    session_hash, issuer, subject, tenant_id, actor_id, scopes,
                    created_at, last_seen_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._hash(session_id),
                    principal.issuer,
                    principal.subject,
                    principal.tenant_id,
                    principal.actor_id,
                    scopes,
                    now,
                    now,
                    now + self.absolute_ttl_seconds,
                ),
            )
        return session_id

    def resolve(self, session_id: str) -> SessionRecord | None:
        if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
            return None
        now = self._clock()
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM browser_sessions WHERE session_hash = ?",
                (self._hash(session_id),),
            ).fetchone()
            if row is None:
                return None
            if (
                row["revoked_at"] is not None
                or row["expires_at"] <= now
                or row["last_seen_at"] + self.idle_ttl_seconds <= now
            ):
                return None
            membership = self.membership_store.lookup(row["issuer"], row["subject"])
            if (
                membership is None
                or membership.status != "active"
                or membership.tenant_id != row["tenant_id"]
                or membership.actor_id != row["actor_id"]
            ):
                db.execute(
                    "UPDATE browser_sessions SET revoked_at = ? WHERE session_hash = ?",
                    (now, row["session_hash"]),
                )
                return None
            db.execute(
                "UPDATE browser_sessions SET last_seen_at = ? WHERE session_hash = ?",
                (now, row["session_hash"]),
            )
            principal = Principal(
                tenant_id=row["tenant_id"],
                actor_id=row["actor_id"],
                issuer=row["issuer"],
                subject=row["subject"],
                scopes=frozenset(json.loads(row["scopes"])),
            )
            return SessionRecord(
                session_id=session_id,
                principal=principal,
                created_at=row["created_at"],
                last_seen_at=now,
                expires_at=row["expires_at"],
            )

    def revoke(self, session_id: str) -> bool:
        if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
            return False
        with self.connect() as db:
            result = db.execute(
                """
                UPDATE browser_sessions
                SET revoked_at = COALESCE(revoked_at, ?)
                WHERE session_hash = ? AND revoked_at IS NULL
                """,
                (self._clock(), self._hash(session_id)),
            )
        return result.rowcount == 1

    def lookup(self, session_id: str) -> Principal | None:
        record = self.resolve(session_id)
        return record.principal if record is not None else None

    @staticmethod
    def _hash(session_id: str) -> str:
        return hashlib.sha256(session_id.encode("utf-8")).hexdigest()
