from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt

OIDC_ALGORITHM = "RS256"
MAX_JWKS_BYTES = 1_024 * 1024
MAX_TOKEN_LENGTH = 128 * 1024
MEMBERSHIP_STATUSES = frozenset({"active", "disabled", "revoked"})


class AuthenticationError(Exception):
    """Authentication failed without a client-visible reason."""


class MembershipError(ValueError):
    """Membership data violates the server-owned identity contract."""


class JwksError(AuthenticationError):
    """Configured JWKS could not provide a usable signing key."""


@dataclass(frozen=True, slots=True)
class Principal:
    tenant_id: str
    actor_id: str
    issuer: str
    subject: str
    scopes: frozenset[str] = frozenset()

    @property
    def actor(self) -> str:
        return self.actor_id


_request_principal: ContextVar[Principal | None] = ContextVar(
    "folio_request_principal", default=None
)


def get_request_principal() -> Principal | None:
    return _request_principal.get()


def set_request_principal(principal: Principal | None) -> Any:
    return _request_principal.set(principal)


def reset_request_principal(token: Any) -> None:
    _request_principal.reset(token)


@dataclass(frozen=True, slots=True)
class Membership:
    issuer: str
    subject: str
    tenant_id: str
    actor_id: str
    status: str
    scopes: frozenset[str]


def _required_text(field: str, value: object) -> str:
    if not isinstance(value, str) or not value or not value.strip() or value != value.strip():
        raise MembershipError(f"{field} must be a non-empty string")
    return value


def _server_scopes(value: object) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise MembershipError("membership scopes must be a list of strings")
    try:
        scopes = frozenset(value)
    except TypeError as exc:
        raise MembershipError("membership scopes must be a list of strings") from exc
    if len(scopes) > 128 or any(
        not isinstance(scope, str)
        or not scope
        or any(
            ord(character) < 0x21 or ord(character) > 0x7E or character in {'"', "\\"}
            for character in scope
        )
        for scope in scopes
    ):
        raise MembershipError("membership scopes are invalid")
    return scopes


class MembershipStore:
    """Server-owned identity mapping, scopes, and mutable status."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS oidc_memberships (
                    issuer TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'disabled', 'revoked')),
                    scopes TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (issuer, subject)
                );
                CREATE TRIGGER IF NOT EXISTS oidc_memberships_immutable_mapping
                BEFORE UPDATE OF issuer, subject, tenant_id, actor_id
                ON oidc_memberships
                BEGIN
                    SELECT RAISE(ABORT, 'OIDC membership mapping is immutable');
                END;
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(oidc_memberships)")}
            if "scopes" not in columns:
                db.execute(
                    "ALTER TABLE oidc_memberships ADD COLUMN scopes TEXT NOT NULL DEFAULT '[]'"
                )

    def add(
        self,
        *,
        issuer: str,
        subject: str,
        tenant_id: str,
        actor_id: str,
        status: str = "active",
        scopes: Iterable[str] = (),
    ) -> Membership:
        issuer = _required_text("issuer", issuer)
        subject = _required_text("subject", subject)
        tenant_id = _required_text("tenant_id", tenant_id)
        actor_id = _required_text("actor_id", actor_id)
        if not isinstance(status, str) or status not in MEMBERSHIP_STATUSES:
            raise MembershipError("membership status is invalid")
        scopes = _server_scopes(scopes)
        now = str(time.time())
        try:
            with self.connect() as db:
                db.execute(
                    """
                    INSERT INTO oidc_memberships(
                        issuer, subject, tenant_id, actor_id, status, scopes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issuer,
                        subject,
                        tenant_id,
                        actor_id,
                        status,
                        json.dumps(sorted(scopes), separators=(",", ":")),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            existing = self.lookup(issuer, subject)
            if existing is not None and (
                existing.tenant_id != tenant_id or existing.actor_id != actor_id
            ):
                raise MembershipError("OIDC membership mapping is immutable") from exc
            raise MembershipError("OIDC membership already exists") from exc
        return Membership(issuer, subject, tenant_id, actor_id, status, scopes)

    def set_status(self, issuer: str, subject: str, status: str) -> Membership:
        issuer = _required_text("issuer", issuer)
        subject = _required_text("subject", subject)
        if not isinstance(status, str) or status not in MEMBERSHIP_STATUSES:
            raise MembershipError("membership status is invalid")
        with self.connect() as db:
            result = db.execute(
                "UPDATE oidc_memberships SET status = ?, updated_at = ? WHERE issuer = ? AND subject = ?",
                (status, str(time.time()), issuer, subject),
            )
            if result.rowcount != 1:
                raise MembershipError("OIDC membership not found")
        membership = self.lookup(issuer, subject)
        assert membership is not None
        return membership

    def set_scopes(self, issuer: str, subject: str, scopes: Iterable[str]) -> Membership:
        issuer = _required_text("issuer", issuer)
        subject = _required_text("subject", subject)
        scopes = _server_scopes(scopes)
        with self.connect() as db:
            result = db.execute(
                "UPDATE oidc_memberships SET scopes = ?, updated_at = ? "
                "WHERE issuer = ? AND subject = ?",
                (
                    json.dumps(sorted(scopes), separators=(",", ":")),
                    str(time.time()),
                    issuer,
                    subject,
                ),
            )
            if result.rowcount != 1:
                raise MembershipError("OIDC membership not found")
        membership = self.lookup(issuer, subject)
        assert membership is not None
        return membership

    def lookup(self, issuer: str, subject: str) -> Membership | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT issuer, subject, tenant_id, actor_id, status, scopes
                FROM oidc_memberships WHERE issuer = ? AND subject = ?
                """,
                (issuer, subject),
            ).fetchone()
        if row is None:
            return None
        values = dict(row)
        try:
            values["scopes"] = _server_scopes(json.loads(values["scopes"]))
        except (TypeError, ValueError) as exc:
            raise MembershipError("membership scopes are invalid") from exc
        return Membership(**values)

    def seed_file(self, path: str | Path) -> None:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MembershipError("OIDC membership file is invalid") from exc
        records = payload.get("memberships") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise MembershipError("OIDC membership file must contain a list")
        for record in records:
            if not isinstance(record, dict):
                raise MembershipError("OIDC membership record is invalid")
            values: dict[str, Any] = {
                "issuer": record.get("issuer"),
                "subject": record.get("subject"),
                "tenant_id": record.get("tenant_id"),
                "actor_id": record.get("actor_id"),
                "status": record.get("status", "active"),
                "scopes": record.get("scopes", []),
            }
            issuer = _required_text("issuer", values["issuer"])
            subject = _required_text("subject", values["subject"])
            existing = self.lookup(issuer, subject)
            if existing is None:
                self.add(**values)
            elif (existing.tenant_id, existing.actor_id) != (
                values["tenant_id"],
                values["actor_id"],
            ):
                raise MembershipError("OIDC membership mapping is immutable")
            else:
                if existing.status != values["status"]:
                    self.set_status(issuer, subject, values["status"])
                if existing.scopes != _server_scopes(values["scopes"]):
                    self.set_scopes(issuer, subject, values["scopes"])


class JwksClient:
    """Bounded, cached JWKS fetcher that refreshes on key rotation."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 5,
        cache_seconds: float = 300,
        opener: Callable[..., Any] = urllib.request.urlopen,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if (
            not math.isfinite(timeout_seconds)
            or not math.isfinite(cache_seconds)
            or timeout_seconds <= 0
            or cache_seconds <= 0
        ):
            raise ValueError("JWKS client limits must be positive")
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self._opener = opener
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._keys: dict[str, Any] = {}
        self._expires_at = 0.0

    def refresh(self) -> None:
        request = urllib.request.Request(self.url, headers={"Accept": "application/json"})
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                body = response.read(MAX_JWKS_BYTES + 1)
        except (OSError, TimeoutError) as exc:
            raise JwksError("OIDC JWKS unavailable") from exc
        if len(body) > MAX_JWKS_BYTES:
            raise JwksError("OIDC JWKS is too large")
        try:
            payload = json.loads(body)
            keys = payload["keys"]
        except (TypeError, ValueError, KeyError) as exc:
            raise JwksError("OIDC JWKS is invalid") from exc
        if not isinstance(keys, list):
            raise JwksError("OIDC JWKS is invalid")
        parsed: dict[str, Any] = {}
        for jwk in keys:
            if not isinstance(jwk, dict) or jwk.get("kty") != "RSA":
                continue
            if jwk.get("alg") not in (None, OIDC_ALGORITHM):
                continue
            if jwk.get("use") not in (None, "sig"):
                continue
            key_ops = jwk.get("key_ops")
            if key_ops is not None and (not isinstance(key_ops, list) or "verify" not in key_ops):
                continue
            kid = jwk.get("kid")
            if not isinstance(kid, str) or not kid:
                continue
            if kid in parsed:
                raise JwksError("OIDC JWKS contains duplicate key ids")
            try:
                parsed[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
            except (TypeError, ValueError, jwt.exceptions.PyJWTError) as exc:
                raise JwksError("OIDC JWKS contains an invalid signing key") from exc
        if not parsed:
            raise JwksError("OIDC JWKS has no usable RSA signing keys")
        with self._lock:
            self._keys = parsed
            self._expires_at = self._monotonic() + self.cache_seconds

    def key(self, kid: str) -> Any:
        with self._lock:
            if kid in self._keys and self._monotonic() < self._expires_at:
                return self._keys[kid]
        self.refresh()
        with self._lock:
            key = self._keys.get(kid)
        if key is None:
            raise JwksError("OIDC signing key is unknown")
        return key


class OidcVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks: JwksClient,
        memberships: MembershipStore,
        clock_skew_seconds: float = 0,
        now: Callable[[], float] = time.time,
    ):
        if (
            not isinstance(issuer, str)
            or not isinstance(audience, str)
            or not issuer
            or not audience
            or not math.isfinite(clock_skew_seconds)
            or clock_skew_seconds < 0
        ):
            raise ValueError("OIDC verifier configuration is invalid")
        self.issuer = issuer
        self.audience = audience
        self.jwks = jwks
        self.memberships = memberships
        self.clock_skew_seconds = clock_skew_seconds
        self._now = now

    def warm_up(self) -> None:
        self.jwks.refresh()

    def verify(self, token: str) -> Principal:
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
            raise AuthenticationError("invalid bearer token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.exceptions.PyJWTError as exc:
            raise AuthenticationError("invalid bearer token") from exc
        if header.get("alg") != OIDC_ALGORITHM:
            raise AuthenticationError("invalid bearer token")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthenticationError("invalid bearer token")
        try:
            key = self.jwks.key(kid)
            claims = jwt.decode(
                token,
                key,
                algorithms=[OIDC_ALGORITHM],
                options={
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                    "verify_iss": False,
                    "verify_aud": False,
                    "verify_sub": False,
                },
            )
        except (JwksError, jwt.exceptions.PyJWTError) as exc:
            raise AuthenticationError("invalid bearer token") from exc
        if not isinstance(claims, dict):
            raise AuthenticationError("invalid bearer token")
        if claims.get("iss") != self.issuer:
            raise AuthenticationError("invalid bearer token")
        if not self._audience_matches(claims.get("aud")):
            raise AuthenticationError("invalid bearer token")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("invalid bearer token")
        now = self._now()
        expiration = self._numeric_claim(claims, "exp", required=True)
        assert expiration is not None
        if expiration <= now - self.clock_skew_seconds:
            raise AuthenticationError("invalid bearer token")
        not_before = self._numeric_claim(claims, "nbf")
        if not_before is not None and not_before > now + self.clock_skew_seconds:
            raise AuthenticationError("invalid bearer token")
        issued_at = self._numeric_claim(claims, "iat")
        if issued_at is not None and issued_at > now + self.clock_skew_seconds:
            raise AuthenticationError("invalid bearer token")
        try:
            membership = self.memberships.lookup(self.issuer, subject)
        except MembershipError as exc:
            raise AuthenticationError("principal is not active") from exc
        if membership is None or membership.status != "active":
            raise AuthenticationError("principal is not active")
        return Principal(
            tenant_id=membership.tenant_id,
            actor_id=membership.actor_id,
            issuer=self.issuer,
            subject=subject,
            scopes=membership.scopes,
        )

    def _audience_matches(self, value: object) -> bool:
        if isinstance(value, str):
            return value == self.audience
        return (
            isinstance(value, list)
            and all(isinstance(item, str) for item in value)
            and (self.audience in value)
        )

    @staticmethod
    def _numeric_claim(
        claims: Mapping[str, Any], name: str, *, required: bool = False
    ) -> float | None:
        value = claims.get(name)
        if value is None and not required:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise AuthenticationError("invalid bearer token")
        return float(value)


def bearer_token(scope: Mapping[str, Any]) -> str:
    values = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
    if len(values) != 1:
        raise AuthenticationError("authorization is required")
    try:
        value = values[0].decode("latin-1")
    except UnicodeDecodeError as exc:
        raise AuthenticationError("authorization is invalid") from exc
    scheme, separator, token = value.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not token
        or any(char.isspace() for char in token)
    ):
        raise AuthenticationError("authorization is invalid")
    return token


class OidcAuthenticator:
    def __init__(self, verifier: OidcVerifier):
        self.verifier = verifier

    def warm_up(self) -> None:
        self.verifier.warm_up()

    def authenticate(self, scope: Mapping[str, Any]) -> Principal:
        return self.verifier.verify(bearer_token(scope))

    def ready(self) -> bool:
        return True
