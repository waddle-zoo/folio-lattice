from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .auth import AuthenticationError, MembershipError, MembershipStore, Principal
from .identity import IdentityClaims

SESSION_COOKIE_NAME = "__Host-folio_session"
SESSION_COOKIE_PATH = "/"
AUTH_STATE_COOKIE_NAME = "__Host-folio_auth_state"
DEFAULT_SESSION_ABSOLUTE_TTL_SECONDS = 8 * 60 * 60
DEFAULT_SESSION_IDLE_TTL_SECONDS = 60 * 60
SESSION_ID_BYTES = 32
AUTH_STATE_BYTES = 32
AUTH_TRANSACTION_TTL_SECONDS = 10 * 60


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    principal: Principal
    created_at: float
    last_seen_at: float
    expires_at: float


@dataclass(frozen=True, slots=True)
class AuthTransaction:
    state: str
    nonce: str
    code_verifier: str
    code_challenge: str
    return_to: str
    redirect_uri: str


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
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_transactions (
                    state_hash TEXT PRIMARY KEY,
                    nonce TEXT NOT NULL,
                    code_verifier TEXT NOT NULL,
                    code_challenge TEXT NOT NULL,
                    return_to TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )

    def create(self, principal: Principal) -> str:
        membership = self.membership_store.lookup(principal.issuer, principal.subject)
        if membership is None or membership.status != "active":
            raise AuthenticationError("principal is not active")
        session_id = secrets.token_urlsafe(SESSION_ID_BYTES)
        now = self._clock()
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
                    membership.tenant_id,
                    membership.actor_id,
                    json.dumps(sorted(membership.scopes), separators=(",", ":")),
                    now,
                    now,
                    now + self.absolute_ttl_seconds,
                ),
            )
        return session_id

    def create_for_identity(self, identity: IdentityClaims) -> str:
        membership = self.membership_store.lookup(identity.issuer, identity.subject)
        if membership is None or membership.status != "active":
            raise AuthenticationError("principal is not active")
        return self.create(
            Principal(
                tenant_id=membership.tenant_id,
                actor_id=membership.actor_id,
                issuer=identity.issuer,
                subject=identity.subject,
                scopes=membership.scopes,
            )
        )

    def begin_auth(
        self,
        *,
        return_to: str,
        redirect_uri: str,
        ttl_seconds: float = AUTH_TRANSACTION_TTL_SECONDS,
    ) -> AuthTransaction:
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("auth transaction TTL must be positive")
        state = secrets.token_urlsafe(AUTH_STATE_BYTES)
        nonce = secrets.token_urlsafe(AUTH_STATE_BYTES)
        code_verifier = secrets.token_urlsafe(AUTH_STATE_BYTES)
        code_challenge = _pkce_challenge(code_verifier)
        now = self._clock()
        with self.connect() as db:
            db.execute("DELETE FROM auth_transactions WHERE expires_at <= ?", (now,))
            db.execute(
                """
                INSERT INTO auth_transactions(
                    state_hash, nonce, code_verifier, code_challenge, return_to,
                    redirect_uri, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._hash(state),
                    nonce,
                    code_verifier,
                    code_challenge,
                    return_to,
                    redirect_uri,
                    now,
                    now + ttl_seconds,
                ),
            )
        return AuthTransaction(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            code_challenge=code_challenge,
            return_to=return_to,
            redirect_uri=redirect_uri,
        )

    def consume_auth(self, state: str) -> AuthTransaction | None:
        if not isinstance(state, str) or not state or len(state) > 256:
            return None
        now = self._clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM auth_transactions WHERE state_hash = ? AND expires_at > ?",
                (self._hash(state), now),
            ).fetchone()
            if row is None:
                return None
            result = db.execute(
                "DELETE FROM auth_transactions WHERE state_hash = ?", (row["state_hash"],)
            )
            if result.rowcount != 1:
                return None
        return AuthTransaction(
            state=state,
            nonce=row["nonce"],
            code_verifier=row["code_verifier"],
            code_challenge=row["code_challenge"],
            return_to=row["return_to"],
            redirect_uri=row["redirect_uri"],
        )

    def discard_auth(self, state: str) -> None:
        if not isinstance(state, str) or not state:
            return
        with self.connect() as db:
            db.execute("DELETE FROM auth_transactions WHERE state_hash = ?", (self._hash(state),))

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
            try:
                membership = self.membership_store.lookup(row["issuer"], row["subject"])
            except MembershipError:
                membership = None
            membership_scopes = (
                json.dumps(sorted(membership.scopes), separators=(",", ":"))
                if membership is not None
                else None
            )
            if (
                membership is None
                or membership.status != "active"
                or membership.tenant_id != row["tenant_id"]
                or membership.actor_id != row["actor_id"]
                or membership_scopes != row["scopes"]
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
                scopes=membership.scopes,
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


def _pkce_challenge(code_verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
