from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import math
import os
import ssl
import string
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import uvicorn
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from starlette.authentication import AuthCredentials
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import (
    AuthenticationError,
    JwksClient,
    MembershipError,
    MembershipStore,
    OidcAuthenticator,
    OidcVerifier,
    Principal,
    get_request_principal,
    reset_request_capability,
    reset_request_principal,
    set_request_capability,
    set_request_principal,
)
from .backup_ops import BackupOperationsMonitor
from .bridge import AttachedMcpBridge
from .identity import (
    DEFAULT_IDENTITY_MAX_RESPONSE_BYTES,
    DEFAULT_IDENTITY_TIMEOUT_SECONDS,
    MAX_IDENTITY_RESPONSE_BYTES,
    MAX_IDENTITY_TIMEOUT_SECONDS,
    HostedIdentityAdapter,
    HostedIdentityConfig,
    HttpHostedIdentityAdapter,
    IdentityClaims,
)
from .inspection import InspectionApp
from .mcp_protocol import build_mcp_server
from .public_mcp import (
    IDENTITY_RELAY_TOOL,
    INTERNAL_PRINCIPAL_HEADER,
    AdminMcpClient,
    HttpMcpClient,
    SignedPrincipalRelay,
    TrustedPrincipalRelay,
)
from .renderer import RendererApp
from .service import DEFAULT_MAX_ARTIFACT_BYTES, FolioLattice
from .sessions import (
    AUTH_STATE_COOKIE_NAME,
    AUTH_TRANSACTION_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_PATH,
    SessionStore,
)

DEFAULT_MAX_REQUEST_BYTES = 13 * 1024 * 1024
PUBLIC_MCP_RATE_LIMIT = 600
PUBLIC_MCP_RATE_MAX_KEYS = 4096
PUBLIC_MCP_CONCURRENCY_LIMIT = 32
PUBLIC_MCP_CONCURRENCY_PER_KEY = 8
PUBLIC_MCP_CONCURRENCY_MAX_KEYS = 4096
PUBLIC_MCP_TIMEOUT_SECONDS = 30.0
MAX_PUBLIC_MCP_TIMEOUT_SECONDS = 300.0
RESOURCE_RATE_LIMIT = 600
RESOURCE_RATE_WINDOW_SECONDS = 60.0
RESOURCE_CONCURRENCY_LIMIT = 32
RESOURCE_CONCURRENCY_PER_KEY = 8
RESOURCE_TIMEOUT_SECONDS = 30.0
RESOURCE_MAX_RESPONSE_BYTES = 1 * 1024 * 1024
MAX_RESOURCE_RESPONSE_BYTES = 4 * 1024 * 1024
MCP_REQUEST_TOO_LARGE_CODE = "request_too_large"
MCP_REQUEST_TOO_LARGE_MESSAGE = "Request body exceeds the allowed size."
MCP_RATE_LIMIT_CODE = "mcp_rate_limited"
MCP_RATE_LIMIT_MESSAGE = "Too many MCP requests. Try again later."
MCP_CONCURRENCY_LIMIT_CODE = "mcp_concurrency_limited"
MCP_CONCURRENCY_LIMIT_MESSAGE = "MCP service is busy. Try again later."
MCP_TIMEOUT_CODE = "mcp_timeout"
MCP_TIMEOUT_MESSAGE = "MCP request timed out. Try again later."
MAX_AUTH_QUERY_BYTES = 8 * 1024
MAX_AUTH_QUERY_VALUE = 4 * 1024
AUTH_RATE_LIMIT_WINDOW_SECONDS = 60.0
AUTH_RATE_LIMIT_MAX_KEYS = 4096
AUTH_START_RATE_LIMIT = 10
AUTH_CALLBACK_RATE_LIMIT = 20
AUTH_LOGOUT_RATE_LIMIT = 20
AUTH_RATE_LIMIT_CODE = "auth_rate_limited"
AUTH_RATE_LIMIT_MESSAGE = "Too many authentication requests. Try again later."
DEFAULT_HSTS_MAX_AGE = 31_536_000


class _BoundedRateLimiter:
    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float = AUTH_RATE_LIMIT_WINDOW_SECONDS,
        max_keys: int = AUTH_RATE_LIMIT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1 or window_seconds <= 0 or max_keys < 1:
            raise ValueError("rate limiter bounds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> tuple[bool, int]:
        now = self._clock()
        with self._lock:
            expired = [
                candidate
                for candidate, (started_at, _count) in self._windows.items()
                if now - started_at >= self.window_seconds
            ]
            for candidate in expired:
                del self._windows[candidate]
            window = self._windows.get(key)
            if window is None:
                if len(self._windows) >= self.max_keys:
                    return False, max(1, math.ceil(self.window_seconds))
                self._windows[key] = (now, 1)
                return True, 0
            started_at, count = window
            if count >= self.limit:
                return False, max(1, math.ceil(self.window_seconds - (now - started_at)))
            self._windows[key] = (started_at, count + 1)
            return True, 0


class _BoundedConcurrencyLimiter:
    def __init__(self, *, limit: int, per_key_limit: int, max_keys: int) -> None:
        if limit < 1 or per_key_limit < 1 or max_keys < 1:
            raise ValueError("concurrency bounds must be positive")
        self.limit = limit
        self.per_key_limit = per_key_limit
        self.max_keys = max_keys
        self._active: dict[str, int] = {}
        self._total = 0
        self._lock = threading.Lock()

    def try_acquire(self, key: str) -> bool:
        with self._lock:
            active = self._active.get(key, 0)
            if (
                self._total >= self.limit
                or active >= self.per_key_limit
                or (active == 0 and len(self._active) >= self.max_keys)
            ):
                return False
            self._active[key] = active + 1
            self._total += 1
            return True

    def release(self, key: str) -> None:
        with self._lock:
            active = self._active.get(key, 0)
            if active == 0:
                return
            if active <= 1:
                self._active.pop(key, None)
            else:
                self._active[key] = active - 1
            if self._total > 0:
                self._total -= 1


@dataclass(frozen=True)
class Settings:
    db_path: str
    blob_root: str
    tenant_id: str
    actor: str
    max_artifact_bytes: int
    max_request_bytes: int
    control_origin: str
    render_origin: str
    mcp_url: str | None
    renderer_capability_secret: str | None
    deployment_mode: str
    bridge_timeout_seconds: float
    public_mcp_timeout_seconds: float
    tenant_rate_limit: int = RESOURCE_RATE_LIMIT
    actor_rate_limit: int = RESOURCE_RATE_LIMIT
    ip_rate_limit: int = RESOURCE_RATE_LIMIT
    resource_rate_window_seconds: float = RESOURCE_RATE_WINDOW_SECONDS
    resource_concurrency_limit: int = RESOURCE_CONCURRENCY_LIMIT
    resource_concurrency_per_key: int = RESOURCE_CONCURRENCY_PER_KEY
    resource_timeout_seconds: float = RESOURCE_TIMEOUT_SECONDS
    resource_max_response_bytes: int = RESOURCE_MAX_RESPONSE_BYTES
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_memberships_file: str | None = None
    oidc_jwks_timeout_seconds: float = 5
    oidc_jwks_cache_seconds: float = 300
    oidc_clock_skew_seconds: float = 0
    auth_authorization_endpoint: str | None = None
    auth_token_endpoint: str | None = None
    auth_client_id: str | None = None
    auth_redirect_uri: str | None = None
    auth_timeout_seconds: float = DEFAULT_IDENTITY_TIMEOUT_SECONDS
    auth_max_response_bytes: int = DEFAULT_IDENTITY_MAX_RESPONSE_BYTES
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    hsts_max_age: int = DEFAULT_HSTS_MAX_AGE
    session_cookie_secure: bool = True

    @property
    def browser_auth_configured(self) -> bool:
        return self.auth_authorization_endpoint is not None

    @classmethod
    def from_env(cls) -> Settings:
        deployment_mode = os.environ.get("FOLIO_DEPLOYMENT_MODE", "local").strip().lower()
        if deployment_mode not in {"local", "hosted"}:
            raise ValueError("FOLIO_DEPLOYMENT_MODE must be local or hosted")
        tenant_id = os.environ.get("FOLIO_TENANT_ID", "dev") if deployment_mode == "local" else ""
        actor = os.environ.get("FOLIO_ACTOR", "folio-client") if deployment_mode == "local" else ""
        oidc_issuer = oidc_audience = oidc_jwks_url = oidc_memberships_file = None
        oidc_jwks_timeout_seconds = 5.0
        oidc_jwks_cache_seconds = 300.0
        oidc_clock_skew_seconds = 0.0
        auth_authorization_endpoint = None
        auth_token_endpoint = None
        auth_client_id = None
        auth_redirect_uri = None
        auth_timeout_seconds = DEFAULT_IDENTITY_TIMEOUT_SECONDS
        auth_max_response_bytes = DEFAULT_IDENTITY_MAX_RESPONSE_BYTES
        if deployment_mode == "local":
            if not tenant_id.strip() or not actor.strip():
                raise ValueError("FOLIO_TENANT_ID and FOLIO_ACTOR must not be empty")
        else:
            try:
                oidc_issuer = _oidc_url_env("FOLIO_OIDC_ISSUER", required=True)
                oidc_audience = _required_env("FOLIO_OIDC_AUDIENCE")
                oidc_jwks_url = _oidc_url_env("FOLIO_OIDC_JWKS_URL", required=True)
                configured_algorithm = os.environ.get("FOLIO_OIDC_ALGORITHM", "RS256")
                if configured_algorithm != "RS256":
                    raise ValueError("FOLIO_OIDC_ALGORITHM must be RS256")
                oidc_memberships_file = os.environ.get("FOLIO_OIDC_MEMBERSHIPS_FILE")
                if oidc_memberships_file is not None and not oidc_memberships_file.strip():
                    raise ValueError("FOLIO_OIDC_MEMBERSHIPS_FILE must not be empty")
                oidc_jwks_timeout_seconds = _positive_float_env(
                    "FOLIO_OIDC_JWKS_TIMEOUT_SECONDS", 5
                )
                oidc_jwks_cache_seconds = _positive_float_env("FOLIO_OIDC_JWKS_CACHE_SECONDS", 300)
                oidc_clock_skew_seconds = _nonnegative_float_env("FOLIO_OIDC_CLOCK_SKEW_SECONDS", 0)
                browser_env = (
                    "FOLIO_OIDC_AUTHORIZATION_ENDPOINT",
                    "FOLIO_OIDC_TOKEN_ENDPOINT",
                    "FOLIO_OIDC_CLIENT_ID",
                    "FOLIO_OIDC_REDIRECT_URI",
                    "FOLIO_OIDC_AUTH_TIMEOUT_SECONDS",
                    "FOLIO_OIDC_AUTH_MAX_RESPONSE_BYTES",
                )
                if any(os.environ.get(name) is not None for name in browser_env):
                    oidc_issuer = _https_url_env("FOLIO_OIDC_ISSUER")
                    oidc_jwks_url = _https_url_env("FOLIO_OIDC_JWKS_URL")
                    auth_authorization_endpoint = _https_url_env(
                        "FOLIO_OIDC_AUTHORIZATION_ENDPOINT"
                    )
                    auth_token_endpoint = _https_url_env("FOLIO_OIDC_TOKEN_ENDPOINT")
                    auth_client_id = _required_env("FOLIO_OIDC_CLIENT_ID")
                    auth_redirect_uri = _https_callback_env("FOLIO_OIDC_REDIRECT_URI")
                    auth_timeout_seconds = _bounded_positive_float_env(
                        "FOLIO_OIDC_AUTH_TIMEOUT_SECONDS",
                        DEFAULT_IDENTITY_TIMEOUT_SECONDS,
                        MAX_IDENTITY_TIMEOUT_SECONDS,
                    )
                    auth_max_response_bytes = _bounded_positive_int_env(
                        "FOLIO_OIDC_AUTH_MAX_RESPONSE_BYTES",
                        DEFAULT_IDENTITY_MAX_RESPONSE_BYTES,
                        MAX_IDENTITY_RESPONSE_BYTES,
                    )
            except ValueError as exc:
                raise ValueError(f"hosted mode requires an authentication adapter; {exc}") from exc
        settings = cls(
            db_path=_path_env("FOLIO_DB_PATH", ".data/folio.db"),
            blob_root=_path_env("FOLIO_BLOB_ROOT", ".data/blobs"),
            tenant_id=tenant_id,
            actor=actor,
            max_artifact_bytes=_positive_env(
                "FOLIO_MAX_ARTIFACT_BYTES", DEFAULT_MAX_ARTIFACT_BYTES
            ),
            max_request_bytes=_positive_env("FOLIO_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES),
            control_origin=_origin_env("FOLIO_CONTROL_ORIGIN", "http://127.0.0.1:8000"),
            render_origin=_origin_env("FOLIO_RENDER_ORIGIN", "http://127.0.0.1:8001"),
            mcp_url=_optional_mcp_url_env("FOLIO_MCP_URL"),
            renderer_capability_secret=os.environ.get("FOLIO_RENDERER_CAPABILITY_SECRET"),
            deployment_mode=deployment_mode,
            bridge_timeout_seconds=_positive_float_env("FOLIO_BRIDGE_TIMEOUT_SECONDS", 5),
            public_mcp_timeout_seconds=_bounded_positive_float_env(
                "FOLIO_MCP_TIMEOUT_SECONDS",
                PUBLIC_MCP_TIMEOUT_SECONDS,
                MAX_PUBLIC_MCP_TIMEOUT_SECONDS,
            ),
            tenant_rate_limit=_bounded_positive_int_env(
                "FOLIO_TENANT_RATE_LIMIT", RESOURCE_RATE_LIMIT, 100_000
            ),
            actor_rate_limit=_bounded_positive_int_env(
                "FOLIO_ACTOR_RATE_LIMIT", RESOURCE_RATE_LIMIT, 100_000
            ),
            ip_rate_limit=_bounded_positive_int_env(
                "FOLIO_IP_RATE_LIMIT", RESOURCE_RATE_LIMIT, 100_000
            ),
            resource_rate_window_seconds=_bounded_positive_float_env(
                "FOLIO_RATE_LIMIT_WINDOW_SECONDS",
                RESOURCE_RATE_WINDOW_SECONDS,
                3_600,
            ),
            resource_concurrency_limit=_bounded_positive_int_env(
                "FOLIO_RESOURCE_CONCURRENCY_LIMIT", RESOURCE_CONCURRENCY_LIMIT, 10_000
            ),
            resource_concurrency_per_key=_bounded_positive_int_env(
                "FOLIO_RESOURCE_CONCURRENCY_PER_KEY", RESOURCE_CONCURRENCY_PER_KEY, 1_000
            ),
            resource_timeout_seconds=_bounded_positive_float_env(
                "FOLIO_RESOURCE_TIMEOUT_SECONDS",
                RESOURCE_TIMEOUT_SECONDS,
                MAX_PUBLIC_MCP_TIMEOUT_SECONDS,
            ),
            resource_max_response_bytes=_bounded_positive_int_env(
                "FOLIO_RESOURCE_MAX_RESPONSE_BYTES",
                RESOURCE_MAX_RESPONSE_BYTES,
                MAX_RESOURCE_RESPONSE_BYTES,
            ),
            oidc_issuer=oidc_issuer,
            oidc_audience=oidc_audience,
            oidc_jwks_url=oidc_jwks_url,
            oidc_memberships_file=oidc_memberships_file,
            oidc_jwks_timeout_seconds=oidc_jwks_timeout_seconds,
            oidc_jwks_cache_seconds=oidc_jwks_cache_seconds,
            oidc_clock_skew_seconds=oidc_clock_skew_seconds,
            auth_authorization_endpoint=auth_authorization_endpoint,
            auth_token_endpoint=auth_token_endpoint,
            auth_client_id=auth_client_id,
            auth_redirect_uri=auth_redirect_uri,
            auth_timeout_seconds=auth_timeout_seconds,
            auth_max_response_bytes=auth_max_response_bytes,
            tls_certfile=_optional_file_env("FOLIO_TLS_CERTFILE"),
            tls_keyfile=_optional_file_env("FOLIO_TLS_KEYFILE"),
            hsts_max_age=_nonnegative_int_env("FOLIO_HSTS_MAX_AGE", DEFAULT_HSTS_MAX_AGE),
            session_cookie_secure=_strict_bool_env("FOLIO_SESSION_COOKIE_SECURE", "true"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.db_path.strip() or not self.blob_root.strip():
            raise ValueError("FOLIO_DB_PATH and FOLIO_BLOB_ROOT must not be empty")
        if _has_control(self.db_path + self.blob_root):
            raise ValueError("Folio paths must not contain control characters")
        if not self.session_cookie_secure:
            raise ValueError("FOLIO_SESSION_COOKIE_SECURE must be true")
        if (
            self.renderer_capability_secret is not None
            and len(self.renderer_capability_secret) < 32
        ):
            raise ValueError("FOLIO_RENDERER_CAPABILITY_SECRET must be at least 32 characters")
        if (self.tls_certfile is None) != (self.tls_keyfile is None):
            raise ValueError("FOLIO_TLS_CERTFILE and FOLIO_TLS_KEYFILE must be set together")
        if self.tls_certfile is not None and (
            not Path(self.tls_certfile).is_file()
            or not Path(self.tls_keyfile or "").is_file()
            or not os.access(self.tls_certfile, os.R_OK)
            or not os.access(self.tls_keyfile or "", os.R_OK)
        ):
            raise ValueError("TLS certificate and key must be readable files")
        if self.tls_certfile is not None and (
            not self.control_origin.startswith("https://")
            or not self.render_origin.startswith("https://")
        ):
            raise ValueError("TLS requires HTTPS control and render origins")
        if self.tls_certfile is not None and self.mcp_url is not None:
            if not self.mcp_url.startswith("https://"):
                raise ValueError("TLS requires an HTTPS FOLIO_MCP_URL")
        if self.tls_certfile is not None and self.hsts_max_age < 1:
            raise ValueError("TLS requires a positive FOLIO_HSTS_MAX_AGE")


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _path_env(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    if not value.strip() or _has_control(value):
        raise ValueError(f"{name} must be a non-empty path without control characters")
    return value


def _nonnegative_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _strict_bool_env(name: str, default: str) -> bool:
    value = os.environ.get(name, default).strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def _optional_file_env(name: str) -> str | None:
    if name not in os.environ:
        return None
    value = _path_env(name, "")
    path = Path(value)
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ValueError(f"{name} must name a readable file")
    return value


def _positive_env(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float_env(name: str, default: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _bounded_positive_float_env(name: str, default: float, maximum: float) -> float:
    value = _positive_float_env(name, default)
    if value > maximum:
        raise ValueError(f"{name} must not exceed {maximum:g}")
    return value


def _bounded_positive_int_env(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1 or value > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def _nonnegative_float_env(name: str, default: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if (
        value is None
        or not value
        or value != value.strip()
        or any(char.isspace() for char in value)
    ):
        raise ValueError(f"{name} must not be empty")
    return value


def _oidc_url_env(name: str, *, required: bool) -> str | None:
    value = os.environ.get(name)
    if value is None and not required:
        return None
    if (
        value is None
        or not value
        or value != value.strip()
        or any(char.isspace() for char in value)
    ):
        raise ValueError(f"{name} must be an HTTPS URL without credentials")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an HTTPS URL without credentials")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be an HTTPS URL without credentials") from exc
    return value


def _https_url_env(name: str) -> str:
    value = os.environ.get(name)
    if (
        value is None
        or not value
        or value != value.strip()
        or len(value) > 4 * 1024
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"{name} must be an exact HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an exact HTTPS URL")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be an exact HTTPS URL") from exc
    return value


def _https_callback_env(name: str) -> str:
    value = _https_url_env(name)
    if urlsplit(value).path != "/auth/callback":
        raise ValueError(f"{name} must use the exact /auth/callback path")
    return value


def _origin_env(name: str, default: str) -> str:
    value = os.environ.get(name, default).rstrip("/")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{name} must be an HTTP origin without a path") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or _has_control(value)
        or any(character not in string.ascii_letters + string.digits + ".:-" for character in host)
    ):
        raise ValueError(f"{name} must be an HTTP origin without a path")
    bracketed_host = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{bracketed_host}{f':{port}' if port is not None else ''}"


def _optional_mcp_url_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{name} must be an HTTP URL ending in /mcp without credentials") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
        or _has_control(value)
        or any(character not in string.ascii_letters + string.digits + ".:-" for character in host)
    ):
        raise ValueError(f"{name} must be an HTTP URL ending in /mcp without credentials")
    bracketed_host = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{bracketed_host}{f':{port}' if port is not None else ''}/mcp"


class FolioHttpApp:
    """Add readiness and inspection routes to the SDK's MCP application."""

    def __init__(
        self,
        app: ASGIApp,
        service: FolioLattice,
        inspection: InspectionApp,
        *,
        deployment_mode: str,
        authenticator: OidcAuthenticator | None = None,
        session_store: SessionStore | None = None,
        identity_adapter: HostedIdentityAdapter | None = None,
        auth_redirect_uri: str | None = None,
        control_origin: str | None = None,
        audit_logger: logging.Logger | None = None,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        public_mcp_rate_limit: int = PUBLIC_MCP_RATE_LIMIT,
        public_mcp_concurrency_limit: int = PUBLIC_MCP_CONCURRENCY_LIMIT,
        public_mcp_concurrency_per_key: int = PUBLIC_MCP_CONCURRENCY_PER_KEY,
        public_mcp_timeout_seconds: float = PUBLIC_MCP_TIMEOUT_SECONDS,
        hsts_max_age: int = 0,
        config_ready: bool = True,
        local_tenant_id: str | None = None,
        local_actor_id: str | None = None,
        backup_operations: BackupOperationsMonitor | None = None,
        principal_relay: TrustedPrincipalRelay | SignedPrincipalRelay | None = None,
    ):
        if max_request_bytes < 1:
            raise ValueError("max_request_bytes must be positive")
        if (
            not math.isfinite(public_mcp_timeout_seconds)
            or public_mcp_timeout_seconds <= 0
            or public_mcp_timeout_seconds > MAX_PUBLIC_MCP_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "public_mcp_timeout_seconds must be between 0 and "
                f"{MAX_PUBLIC_MCP_TIMEOUT_SECONDS:g} seconds"
            )
        self.app = app
        self.service = service
        self.inspection = inspection
        self.deployment_mode = deployment_mode
        self.max_request_bytes = max_request_bytes
        self.public_mcp_timeout_seconds = public_mcp_timeout_seconds
        self.authenticator = authenticator
        self.session_store = session_store
        self.identity_adapter = identity_adapter
        self.auth_redirect_uri = _auth_redirect_uri(auth_redirect_uri)
        self.control_origin = control_origin
        self.hsts_max_age = hsts_max_age
        self.config_ready = config_ready
        self.local_tenant_id = local_tenant_id
        self.local_actor_id = local_actor_id
        self.backup_operations = backup_operations
        self.principal_relay = principal_relay
        self.audit_logger = audit_logger or logging.getLogger("folio_lattice.audit")
        self.audit_logger.setLevel(logging.INFO)
        self._auth_rate_limiters = {
            "/auth/start": _BoundedRateLimiter(limit=AUTH_START_RATE_LIMIT),
            "/auth/callback": _BoundedRateLimiter(limit=AUTH_CALLBACK_RATE_LIMIT),
            "/auth/logout": _BoundedRateLimiter(limit=AUTH_LOGOUT_RATE_LIMIT),
        }
        self._public_mcp_rate_limiter = _BoundedRateLimiter(
            limit=public_mcp_rate_limit,
            max_keys=PUBLIC_MCP_RATE_MAX_KEYS,
        )
        self._public_mcp_concurrency_limiter = _BoundedConcurrencyLimiter(
            limit=public_mcp_concurrency_limit,
            per_key_limit=public_mcp_concurrency_per_key,
            max_keys=PUBLIC_MCP_CONCURRENCY_MAX_KEYS,
        )
        if deployment_mode == "hosted" and authenticator is None:
            raise ValueError("hosted mode requires configured OIDC authentication")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)
            return

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {key.lower() for key, _ in headers}
                if self.hsts_max_age and scope.get("scheme") == "https":
                    if b"strict-transport-security" not in names:
                        headers.append(
                            (
                                b"strict-transport-security",
                                f"max-age={self.hsts_max_age}; includeSubDomains".encode(),
                            )
                        )
                message["headers"] = headers
            await send(message)

        public_mcp = scope["path"] == "/mcp"
        public_receive = receive
        if public_mcp and scope["method"] == "POST":
            try:
                async with asyncio.timeout(self.public_mcp_timeout_seconds):
                    accepted, public_receive = await self._check_public_mcp_body(
                        scope, receive, secure_send
                    )
            except TimeoutError:
                await self._public_mcp_error(
                    scope,
                    receive,
                    secure_send,
                    status_code=504,
                    code=MCP_TIMEOUT_CODE,
                    message=MCP_TIMEOUT_MESSAGE,
                    retry_after=1,
                    retryable=True,
                    event="mcp_timeout",
                )
                return
            if not accepted:
                return
        if scope["method"] == "GET" and scope["path"] == "/sign-in":
            await self.inspection(scope, receive, secure_send)
            return
        if scope["method"] == "GET" and scope["path"] == "/auth/start":
            await self._auth_start(scope, receive, secure_send)
            return
        if scope["method"] == "GET" and scope["path"] == "/auth/callback":
            await self._auth_callback(scope, receive, secure_send)
            return
        if scope["method"] == "GET" and scope["path"] == "/health":
            await JSONResponse(
                {
                    "status": "ok",
                    "live": True,
                    "deployment_mode": self.deployment_mode,
                    "authentication": (
                        "oidc-bearer"
                        if self.authenticator is not None
                        else "none-local-development"
                    ),
                }
            )(scope, receive, secure_send)
            return
        if scope["method"] == "GET" and scope["path"] in {"/readyz", "/ready"}:
            readiness = self._readiness()
            await JSONResponse(readiness, status_code=200 if readiness["ready"] else 503)(
                scope, receive, secure_send
            )
            return
        if scope["method"] == "GET" and scope["path"] == "/metrics":
            metrics: dict[str, Any] = dict(self.service.audit_metrics())
            if self.deployment_mode == "hosted":
                metrics.update(
                    {
                        "backup_ready": 0,
                        "backup_age_seconds": -1,
                        "backup_store_ready": 0,
                        "backup_key_custody_ready": 0,
                    }
                )
            if self.backup_operations is not None:
                try:
                    metrics.update(self.backup_operations.metrics())
                except Exception:
                    metrics.update(
                        {
                            "backup_ready": 0,
                            "backup_age_seconds": -1,
                            "backup_store_ready": 0,
                            "backup_key_custody_ready": 0,
                        }
                    )
            await JSONResponse(metrics, headers={"Cache-Control": "no-store"})(
                scope, receive, secure_send
            )
            return
        if scope["method"] == "GET" and scope["path"] == "/v1/me":
            try:
                me_principal = self._authenticate(scope)
            except AuthenticationError:
                await self._error(
                    scope,
                    receive,
                    secure_send,
                    401,
                    "authentication_required",
                    "Sign-in required. Sign in to continue.",
                    reauthenticate=True,
                )
                return
            await JSONResponse(
                {
                    "authenticated": True,
                    "tenant_id": me_principal.tenant_id,
                    "actor_id": me_principal.actor_id,
                },
                headers={"Cache-Control": "no-store"},
            )(scope, receive, secure_send)
            return
        if scope["method"] == "POST" and scope["path"] == "/auth/logout":
            if not await self._check_auth_rate(scope, receive, secure_send, "/auth/logout"):
                return
            if not self._same_origin(scope):
                request_id = uuid.uuid4().hex
                self._audit(
                    scope,
                    event="logout_csrf_denied",
                    outcome="denied",
                    status=403,
                    request_id=request_id,
                )
                await self._error(
                    scope,
                    receive,
                    secure_send,
                    403,
                    "csrf_failed",
                    "This sign-out request is not allowed.",
                    request_id=request_id,
                )
                return
            session_id = _session_cookie(scope)
            if session_id is not None and self.session_store is not None:
                self.session_store.revoke(session_id)
            self._audit(
                scope,
                event="logout_succeeded",
                outcome="revoked" if session_id is not None else "no_session",
                status=204,
                request_id=uuid.uuid4().hex,
            )
            response = Response(status_code=204, headers={"Cache-Control": "no-store"})
            response.set_cookie(
                SESSION_COOKIE_NAME,
                "",
                max_age=0,
                expires=0,
                path=SESSION_COOKIE_PATH,
                secure=True,
                httponly=True,
                samesite="lax",
            )
            await response(scope, receive, secure_send)
            return
        principal_token = None
        capability_token = None
        principal: Principal | None = None
        internal_principal = _header_value(scope, INTERNAL_PRINCIPAL_HEADER)
        if self.authenticator is not None or (
            internal_principal is not None and self.principal_relay is not None
        ):
            try:
                principal, capability = self._authenticate_context(
                    scope, allow_session=scope["path"] != "/mcp"
                )
            except AuthenticationError:
                if public_mcp and not await self._check_public_mcp_rate(
                    scope, public_receive, secure_send, None
                ):
                    return
                await self._error(
                    scope,
                    receive,
                    secure_send,
                    401,
                    "authentication_required",
                    "Sign-in required. Sign in to continue.",
                    reauthenticate=True,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                return
            principal_token = set_request_principal(principal)
            if capability is not None:
                capability_token = set_request_capability(capability)
            if scope["path"] == "/mcp":
                _bind_mcp_actor(scope, principal)
        path = scope["path"]
        try:
            if (
                path == "/"
                or path.startswith("/inspect/")
                or path.startswith("/artifacts/")
                or path.startswith("/standalone/")
                or path.startswith("/workspace/")
                or path
                in {
                    "/api/mcp",
                    "/api/admin/mcp",
                    "/api/bridge",
                    "/connections.css",
                    "/connections.js",
                    "/settings/connections",
                    "/ui.css",
                    "/ui.js",
                }
            ):
                await self.inspection(scope, receive, secure_send)
                return
            if path == "/mcp":
                if not await self._check_public_mcp_rate(
                    scope, public_receive, secure_send, principal
                ):
                    return
                key = _public_mcp_key(scope, principal)
                if not self._public_mcp_concurrency_limiter.try_acquire(key):
                    request_id = uuid.uuid4().hex
                    self._audit(
                        scope,
                        event="mcp_concurrency_limited",
                        outcome="denied",
                        status=429,
                        request_id=request_id,
                    )
                    await self._error(
                        scope,
                        public_receive,
                        secure_send,
                        429,
                        MCP_CONCURRENCY_LIMIT_CODE,
                        MCP_CONCURRENCY_LIMIT_MESSAGE,
                        retryable=True,
                        headers={"Retry-After": "1"},
                        request_id=request_id,
                    )
                    return
                try:
                    response_started = False

                    async def tracked_send(message: Message) -> None:
                        nonlocal response_started
                        if message["type"] == "http.response.start":
                            response_started = True
                        await secure_send(message)

                    try:
                        async with asyncio.timeout(self.public_mcp_timeout_seconds):
                            await self.app(scope, public_receive, tracked_send)
                    except TimeoutError:
                        if not response_started:
                            await self._public_mcp_error(
                                scope,
                                public_receive,
                                secure_send,
                                status_code=504,
                                code=MCP_TIMEOUT_CODE,
                                message=MCP_TIMEOUT_MESSAGE,
                                retry_after=1,
                                retryable=True,
                                event="mcp_timeout",
                            )
                finally:
                    self._public_mcp_concurrency_limiter.release(key)
                return
            await self.app(scope, receive, secure_send)
        finally:
            if capability_token is not None:
                reset_request_capability(capability_token)
            if principal_token is not None:
                reset_request_principal(principal_token)

    def _authenticate(self, scope: Scope, *, allow_session: bool = True) -> Principal:
        principal, _ = self._authenticate_context(scope, allow_session=allow_session)
        return principal

    def _authenticate_context(
        self, scope: Scope, *, allow_session: bool = True
    ) -> tuple[Principal, Any | None]:
        internal_token = _header_value(scope, INTERNAL_PRINCIPAL_HEADER)
        if internal_token is not None:
            if scope.get("path") != "/mcp":
                raise AuthenticationError("internal principal is limited to MCP")
            if self.principal_relay is None:
                raise AuthenticationError("internal principal relay is unavailable")
            capability = self.principal_relay.resolve_capability(internal_token)
            if capability is None or not isinstance(capability.principal, Principal):
                raise AuthenticationError("internal principal is invalid")
            principal = capability.principal
            if self.session_store is not None:
                try:
                    membership = self.session_store.membership_store.lookup(
                        principal.issuer, principal.subject
                    )
                except MembershipError as exc:
                    raise AuthenticationError("internal principal is not active") from exc
                if (
                    membership is None
                    or membership.status != "active"
                    or membership.tenant_id != principal.tenant_id
                    or membership.actor_id != principal.actor_id
                    or not principal.scopes.issubset(membership.scopes)
                ):
                    raise AuthenticationError("internal principal is not active")
                principal = Principal(
                    tenant_id=membership.tenant_id,
                    actor_id=membership.actor_id,
                    issuer=membership.issuer,
                    subject=membership.subject,
                    scopes=membership.scopes,
                )
            if capability.binding.tool == IDENTITY_RELAY_TOOL:
                return principal, None
            return principal, capability.binding
        session_id = _session_cookie(scope)
        if session_id is not None and allow_session:
            if self.session_store is None:
                raise AuthenticationError("session authentication is unavailable")
            session_principal = self.session_store.lookup(session_id)
            if session_principal is None:
                raise AuthenticationError("session is invalid")
            return session_principal, None
        if self.authenticator is not None:
            return self.authenticator.authenticate(scope), None
        raise AuthenticationError("authentication is required")

    def _readiness(self) -> dict[str, Any]:
        checker = getattr(self.service, "readiness", None)
        if not callable(checker):
            checker = self.service.health
        try:
            readiness = dict(checker())
        except Exception:
            readiness = {"status": "not_ready", "ready": False, "dependencies": {}}
        dependencies = dict(readiness.get("dependencies", {}))
        dependencies["config"] = {"ready": self.config_ready}
        provider_required = self.authenticator is not None
        provider_ready = True
        if self.authenticator is not None:
            try:
                provider_ready = bool(self.authenticator.ready())
            except Exception:
                provider_ready = False
        identity_required = self.identity_adapter is not None or self.auth_redirect_uri is not None
        identity_ready = True
        if self.identity_adapter is not None or self.auth_redirect_uri is not None:
            identity_ready = self._identity_ready()
        dependencies["provider"] = {"ready": provider_ready, "required": provider_required}
        dependencies["identity"] = {"ready": identity_ready, "required": identity_required}
        if self.deployment_mode == "hosted":
            if self.backup_operations is None:
                dependencies["backup_operations"] = {
                    "ready": False,
                    "required": True,
                    "reason": "hosted backup operations monitor is not configured",
                }
            else:
                try:
                    backup_status = self.backup_operations.status()
                    dependencies["backup_operations"] = {
                        "ready": backup_status["ready"],
                        "required": True,
                        "alerts": backup_status["alerts"],
                        "metrics": backup_status["metrics"],
                    }
                except Exception:
                    dependencies["backup_operations"] = {
                        "ready": False,
                        "required": True,
                        "reason": "hosted backup operations check failed",
                    }
        readiness["dependencies"] = dependencies
        readiness["ready"] = all(
            isinstance(dependency, dict) and dependency.get("ready") is True
            for dependency in dependencies.values()
        )
        readiness["status"] = "ok" if readiness["ready"] else "not_ready"
        readiness.update(
            {
                "deployment_mode": self.deployment_mode,
                "authentication": (
                    "oidc-bearer" if self.authenticator is not None else "none-local-development"
                ),
            }
        )
        return readiness

    def _identity_ready(self) -> bool:
        if (
            self.identity_adapter is None
            or self.session_store is None
            or self.auth_redirect_uri is None
        ):
            return False
        try:
            return bool(self.identity_adapter.ready())
        except Exception:
            return False

    async def _auth_start(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not await self._check_auth_rate(scope, receive, send, "/auth/start"):
            return
        if not self._identity_ready():
            await self._error(
                scope,
                receive,
                send,
                503,
                "identity_provider_unavailable",
                "Sign-in is temporarily unavailable. Try again.",
                retryable=True,
            )
            return
        return_to, valid = _request_return_to(scope)
        if not valid:
            await self._error(
                scope,
                receive,
                send,
                400,
                "invalid_return_to",
                "The sign-in destination is not available.",
            )
            return
        assert self.identity_adapter is not None
        assert self.session_store is not None
        assert self.auth_redirect_uri is not None
        transaction = self.session_store.begin_auth(
            return_to=return_to,
            redirect_uri=self.auth_redirect_uri,
        )
        try:
            provider_url = self.identity_adapter.authorization_url(
                state=transaction.state,
                nonce=transaction.nonce,
                code_challenge=transaction.code_challenge,
                redirect_uri=transaction.redirect_uri,
                return_to=transaction.return_to,
            )
            if not _valid_provider_url(provider_url):
                raise ValueError("provider authorization URL is invalid")
        except Exception:
            self.session_store.discard_auth(transaction.state)
            await self._error(
                scope,
                receive,
                send,
                503,
                "identity_provider_unavailable",
                "Sign-in is temporarily unavailable. Try again.",
                retryable=True,
            )
            return
        response = RedirectResponse(
            provider_url,
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )
        response.set_cookie(
            AUTH_STATE_COOKIE_NAME,
            transaction.state,
            max_age=int(AUTH_TRANSACTION_TTL_SECONDS),
            path=SESSION_COOKIE_PATH,
            secure=True,
            httponly=True,
            samesite="lax",
        )
        await response(scope, receive, send)

    async def _auth_callback(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not await self._check_auth_rate(scope, receive, send, "/auth/callback"):
            return
        if not self._identity_ready():
            await self._error(
                scope,
                receive,
                send,
                503,
                "identity_provider_unavailable",
                "Sign-in is temporarily unavailable. Try again.",
                retryable=True,
            )
            return
        state = _query_value(scope, "state")
        code = _query_value(scope, "code")
        if state is None or code is None:
            await self._error(
                scope,
                receive,
                send,
                400,
                "invalid_auth_callback",
                "Sign-in could not be completed.",
            )
            return
        auth_state_cookie = _cookie(scope, AUTH_STATE_COOKIE_NAME)
        if auth_state_cookie is None or not hmac.compare_digest(auth_state_cookie, state):
            await self._error(
                scope,
                receive,
                send,
                400,
                "invalid_auth_callback",
                "Sign-in could not be completed.",
            )
            return
        assert self.identity_adapter is not None
        assert self.session_store is not None
        transaction = self.session_store.consume_auth(state)
        if transaction is None:
            await self._error(
                scope,
                receive,
                send,
                400,
                "invalid_auth_callback",
                "Sign-in could not be completed.",
            )
            return
        try:
            identity = self.identity_adapter.complete_callback(
                code=code,
                nonce=transaction.nonce,
                code_verifier=transaction.code_verifier,
                redirect_uri=transaction.redirect_uri,
            )
            if not isinstance(identity, IdentityClaims):
                raise AuthenticationError("identity provider returned no verified identity")
            session_id = self.session_store.create_for_identity(identity)
        except AuthenticationError:
            await self._error(
                scope,
                receive,
                send,
                401,
                "authentication_failed",
                "Sign-in could not be completed.",
                reauthenticate=True,
            )
            return
        except Exception:
            await self._error(
                scope,
                receive,
                send,
                503,
                "identity_provider_unavailable",
                "Sign-in is temporarily unavailable. Try again.",
                retryable=True,
            )
            return
        old_session_id = _session_cookie(scope)
        if old_session_id is not None:
            self.session_store.revoke(old_session_id)
        response = RedirectResponse(
            transaction.return_to,
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_id,
            max_age=max(1, int(self.session_store.absolute_ttl_seconds)),
            path=SESSION_COOKIE_PATH,
            secure=True,
            httponly=True,
            samesite="lax",
        )
        response.set_cookie(
            AUTH_STATE_COOKIE_NAME,
            "",
            max_age=0,
            expires=0,
            path=SESSION_COOKIE_PATH,
            secure=True,
            httponly=True,
            samesite="lax",
        )
        await response(scope, receive, send)

    async def _check_public_mcp_body(
        self, scope: Scope, receive: Receive, send: Send
    ) -> tuple[bool, Receive]:
        content_lengths = [
            value
            for header, value in scope.get("headers", [])
            if header.lower() == b"content-length"
        ]
        if len(content_lengths) == 1:
            try:
                declared_size = int(content_lengths[0])
            except ValueError:
                pass
            else:
                if declared_size > self.max_request_bytes:
                    await self._public_mcp_error(
                        scope,
                        receive,
                        send,
                        status_code=413,
                        code=MCP_REQUEST_TOO_LARGE_CODE,
                        message=MCP_REQUEST_TOO_LARGE_MESSAGE,
                        retry_after=0,
                        retryable=False,
                        event="mcp_request_too_large",
                    )
                    return False, receive

        messages: deque[Message] = deque()
        body_size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                messages.append(message)
                break
            body = message.get("body", b"")
            body_size += len(body)
            if body_size > self.max_request_bytes:
                await self._public_mcp_error(
                    scope,
                    receive,
                    send,
                    status_code=413,
                    code=MCP_REQUEST_TOO_LARGE_CODE,
                    message=MCP_REQUEST_TOO_LARGE_MESSAGE,
                    retry_after=0,
                    retryable=False,
                    event="mcp_request_too_large",
                )
                return False, receive
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            if messages:
                return messages.popleft()
            return await receive()

        return True, replay

    async def _check_public_mcp_rate(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        principal: Principal | None,
    ) -> bool:
        key = _public_mcp_key(scope, principal)
        try:
            allowed, retry_after = self._public_mcp_rate_limiter.allow(key)
        except Exception:
            allowed, retry_after = False, math.ceil(AUTH_RATE_LIMIT_WINDOW_SECONDS)
        if allowed:
            return True
        await self._public_mcp_error(
            scope,
            receive,
            send,
            status_code=429,
            code=MCP_RATE_LIMIT_CODE,
            message=MCP_RATE_LIMIT_MESSAGE,
            retry_after=retry_after,
            retryable=True,
            event="mcp_rate_limited",
        )
        return False

    async def _public_mcp_error(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        code: str,
        message: str,
        retry_after: int,
        retryable: bool,
        event: str,
    ) -> None:
        request_id = uuid.uuid4().hex
        self._audit(
            scope,
            event=event,
            outcome="denied",
            status=status_code,
            request_id=request_id,
        )
        await self._error(
            scope,
            receive,
            send,
            status_code,
            code,
            message,
            retryable=retryable,
            headers={"Retry-After": str(retry_after)},
            request_id=request_id,
        )

    async def _check_auth_rate(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        route: str,
    ) -> bool:
        limiter = self._auth_rate_limiters[route]
        try:
            allowed, retry_after = limiter.allow(_client_key(scope))
        except Exception:
            allowed, retry_after = False, math.ceil(AUTH_RATE_LIMIT_WINDOW_SECONDS)
        if allowed:
            return True
        request_id = uuid.uuid4().hex
        self._audit(
            scope,
            event=f"{route.removeprefix('/').replace('/', '_')}_rate_limited",
            outcome="denied",
            status=429,
            request_id=request_id,
        )
        await self._error(
            scope,
            receive,
            send,
            429,
            AUTH_RATE_LIMIT_CODE,
            AUTH_RATE_LIMIT_MESSAGE,
            retryable=True,
            headers={"Retry-After": str(retry_after)},
            request_id=request_id,
        )
        return False

    def _same_origin(self, scope: Scope) -> bool:
        expected_origin = _auth_origin(self.auth_redirect_uri)
        if self.control_origin is not None:
            expected_origin = self.control_origin
        values = [
            value for header, value in scope.get("headers", []) if header.lower() == b"origin"
        ]
        if expected_origin is None or len(values) != 1:
            return False
        try:
            origin = values[0].decode("ascii")
        except UnicodeDecodeError:
            return False
        return hmac.compare_digest(origin, expected_origin)

    def _audit(
        self,
        scope: Scope,
        *,
        event: str,
        outcome: str,
        status: int,
        request_id: str,
    ) -> None:
        principal = get_request_principal()
        headers = dict(scope.get("headers", []))
        correlation_id = headers.get(b"x-correlation-id", b"").decode("ascii", "ignore")
        if (
            not correlation_id
            or len(correlation_id) > 255
            or any(ord(character) < 0x21 or ord(character) == 0x7F for character in correlation_id)
        ):
            correlation_id = request_id
        tenant_id = principal.tenant_id if principal is not None else self.local_tenant_id
        actor_id = principal.actor_id if principal is not None else self.local_actor_id
        tenant_id = tenant_id or "unknown"
        actor_id = actor_id or "unknown"
        record = {
            "event": event,
            "correlation_id": correlation_id,
            "method": scope.get("method", ""),
            "outcome": outcome,
            "path": scope.get("path", ""),
            "request_id": request_id,
            "status": status,
        }
        self.audit_logger.info(
            "auth_audit %s",
            json.dumps(record, sort_keys=True, separators=(",", ":")),
        )
        try:
            self.service.record_audit_event(
                tenant_id=tenant_id,
                actor_id=actor_id,
                request_id=request_id,
                correlation_id=correlation_id,
                action=event,
                outcome=outcome,
                resource_type="http_route",
                resource_id=scope.get("path", "") or None,
                reason=event,
                source="http",
                details={"status_code": status},
            )
        except Exception:
            # Persistence failures are deliberately logged without traceback or
            # exception text: adapters and database drivers may echo secrets.
            self.audit_logger.warning("audit_persist_failed")

    @staticmethod
    async def _error(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        reauthenticate: bool = False,
        headers: dict[str, str] | None = None,
        request_id: str | None = None,
    ) -> None:
        response_headers = {"Cache-Control": "no-store", **(headers or {})}
        await JSONResponse(
            {
                "code": code,
                "message": message,
                "request_id": request_id or uuid.uuid4().hex,
                "retryable": retryable,
                "reauthenticate": reauthenticate,
            },
            status_code=status_code,
            headers=response_headers,
        )(scope, receive, send)


def _session_cookie(scope: Scope) -> str | None:
    return _cookie(scope, SESSION_COOKIE_NAME)


def _header_value(scope: Scope, name: str) -> str | None:
    values = [
        value
        for header, value in scope.get("headers", [])
        if header.lower() == name.lower().encode()
    ]
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _client_key(scope: Scope) -> str:
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client and isinstance(client[0], str):
        return client[0]
    return "unknown"


def _public_mcp_key(scope: Scope, principal: Principal | None) -> str:
    client = _client_key(scope)
    if principal is None:
        return f"ip:{client}"
    return f"identity:{principal.tenant_id}\x1f{principal.actor_id}\x1f{client}"


def _bind_mcp_actor(scope: Scope, principal: Principal) -> None:
    """Expose the verified principal to MCP's stateful transport session guard.

    The SDK binds a stateful MCP session to ``scope['user']``.  Our OIDC layer
    runs outside that middleware, so leaving this unset would make every
    session owner ``None`` and allow a different bearer to reuse the session.
    The token value is deliberately a non-secret sentinel; only the stable
    issuer/subject identity is used for the SDK's ownership comparison.
    """
    scope["auth"] = AuthCredentials(sorted(principal.scopes))
    scope["user"] = AuthenticatedUser(
        AccessToken(
            token="request-bound",
            client_id="folio-lattice-hosted",
            scopes=sorted(principal.scopes),
            subject=principal.subject,
            claims={"iss": principal.issuer},
        )
    )


def _cookie(scope: Scope, name: str) -> str | None:
    values = [value for header, value in scope.get("headers", []) if header.lower() == b"cookie"]
    if len(values) != 1:
        return None
    try:
        header = values[0].decode("latin-1")
        if sum(part.strip().startswith(f"{name}=") for part in header.split(";")) != 1:
            return None
        cookie = SimpleCookie(header)
    except (UnicodeDecodeError, ValueError):
        return None
    morsel = cookie.get(name)
    if morsel is None or not morsel.value:
        return None
    return morsel.value


def _safe_return_to(value: object) -> str | None:
    if value == "/":
        return "/"
    if (
        not isinstance(value, str)
        or len(value) > MAX_AUTH_QUERY_VALUE
        or not value.startswith("/")
        or value.startswith("//")
    ):
        return None
    parts = value.split("/")
    if (
        len(parts) == 3
        and parts[1] in {"inspect", "artifacts", "standalone", "workspace"}
        and parts[2]
        and all(0x21 <= ord(character) < 0x7F and character not in "?#\\" for character in parts[2])
    ):
        return value
    return None


def _request_return_to(scope: Scope) -> tuple[str, bool]:
    try:
        raw_query = scope.get("query_string", b"")
        if len(raw_query) > MAX_AUTH_QUERY_BYTES:
            return "/", False
        query = raw_query.decode("ascii")
        values = parse_qs(query, keep_blank_values=True).get("return_to")
    except (UnicodeDecodeError, ValueError):
        return "/", False
    if values is None:
        return "/", True
    if len(values) != 1:
        return "/", False
    safe_value = _safe_return_to(values[0])
    return safe_value or "/", safe_value is not None


def _query_value(scope: Scope, name: str) -> str | None:
    try:
        raw_query = scope.get("query_string", b"")
        if len(raw_query) > MAX_AUTH_QUERY_BYTES:
            return None
        values = parse_qs(raw_query.decode("ascii"), keep_blank_values=False).get(name)
    except (UnicodeDecodeError, ValueError):
        return None
    if values is None or len(values) != 1 or not values[0] or len(values[0]) > MAX_AUTH_QUERY_VALUE:
        return None
    return values[0]


def _auth_redirect_uri(value: str | None) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_AUTH_QUERY_VALUE
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/auth/callback"
    ):
        return None
    try:
        _ = parsed.port
    except ValueError:
        return None
    return value


def _auth_origin(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.hostname is None:
        return None
    try:
        _ = parsed.port
    except ValueError:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _valid_provider_url(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_AUTH_QUERY_VALUE
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def build_runtime(settings: Settings) -> tuple[FolioLattice, Any, OidcAuthenticator | None]:
    settings.validate()
    service = FolioLattice(
        settings.db_path,
        settings.blob_root,
        max_artifact_bytes=settings.max_artifact_bytes,
    )
    if settings.deployment_mode == "local":
        return (
            service,
            build_mcp_server(
                service,
                tenant_id=settings.tenant_id,
                actor=settings.actor,
                external_rate_limits={
                    "tenant": settings.tenant_rate_limit,
                    "actor": settings.actor_rate_limit,
                },
                external_rate_window_seconds=settings.resource_rate_window_seconds,
                external_concurrency_limit=settings.resource_concurrency_limit,
                external_concurrency_per_key=settings.resource_concurrency_per_key,
            ),
            None,
        )
    assert settings.oidc_issuer and settings.oidc_audience and settings.oidc_jwks_url
    memberships = MembershipStore(settings.db_path)
    if settings.oidc_memberships_file is not None:
        memberships.seed_file(settings.oidc_memberships_file)
    verifier = OidcVerifier(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        jwks=JwksClient(
            settings.oidc_jwks_url,
            timeout_seconds=settings.oidc_jwks_timeout_seconds,
            cache_seconds=settings.oidc_jwks_cache_seconds,
        ),
        memberships=memberships,
        clock_skew_seconds=settings.oidc_clock_skew_seconds,
    )
    authenticator = OidcAuthenticator(verifier)
    authenticator.warm_up()
    return (
        service,
        build_mcp_server(
            service,
            external_rate_limits={
                "tenant": settings.tenant_rate_limit,
                "actor": settings.actor_rate_limit,
            },
            external_rate_window_seconds=settings.resource_rate_window_seconds,
            external_concurrency_limit=settings.resource_concurrency_limit,
            external_concurrency_per_key=settings.resource_concurrency_per_key,
        ),
        authenticator,
    )


def run_http(host: str, port: int) -> None:
    settings = Settings.from_env()
    service, mcp, authenticator = build_runtime(settings)
    session_store = None
    identity_adapter = None
    auth_redirect_uri = None
    if settings.deployment_mode == "hosted":
        session_store = SessionStore(settings.db_path, MembershipStore(settings.db_path))
        if settings.browser_auth_configured:
            assert (
                settings.oidc_issuer
                and settings.oidc_jwks_url
                and settings.auth_authorization_endpoint
                and settings.auth_token_endpoint
                and settings.auth_client_id
                and settings.auth_redirect_uri
            )
            identity_config = HostedIdentityConfig(
                issuer=settings.oidc_issuer,
                jwks_url=settings.oidc_jwks_url,
                authorization_endpoint=settings.auth_authorization_endpoint,
                token_endpoint=settings.auth_token_endpoint,
                client_id=settings.auth_client_id,
                redirect_uri=settings.auth_redirect_uri,
                timeout_seconds=settings.auth_timeout_seconds,
                max_response_bytes=settings.auth_max_response_bytes,
            )
            identity_adapter = HttpHostedIdentityAdapter(
                identity_config,
                clock_skew_seconds=settings.oidc_clock_skew_seconds,
            )
            identity_adapter.warm_up()
            auth_redirect_uri = identity_config.redirect_uri
    app = mcp.streamable_http_app(
        json_response=True,
        max_request_body_size=settings.max_request_bytes,
        host=host,
    )
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    scheme = "https" if settings.tls_certfile else "http"
    local_mcp_endpoint = f"{scheme}://{connect_host}:{port}/mcp"
    mcp_endpoint = settings.mcp_url or local_mcp_endpoint
    if settings.deployment_mode == "hosted" and mcp_endpoint != local_mcp_endpoint:
        raise ValueError(
            "hosted mode requires FOLIO_MCP_URL to resolve to this server's local /mcp endpoint"
        )
    principal_relay: SignedPrincipalRelay | TrustedPrincipalRelay | None
    if settings.renderer_capability_secret is not None:
        principal_relay = SignedPrincipalRelay(mcp_endpoint, settings.renderer_capability_secret)
    elif settings.deployment_mode == "hosted" and mcp_endpoint == local_mcp_endpoint:
        principal_relay = TrustedPrincipalRelay(mcp_endpoint)
    else:
        principal_relay = None
    caller = HttpMcpClient(
        mcp_endpoint,
        max_result_bytes=settings.max_request_bytes,
        principal_relay=principal_relay,
    )
    inspection = InspectionApp(
        caller,
        control_origin=settings.control_origin,
        render_origin=settings.render_origin,
        max_request_bytes=settings.max_request_bytes,
        bridge=AttachedMcpBridge(caller, timeout_seconds=settings.bridge_timeout_seconds),
        admin_caller=AdminMcpClient(mcp, timeout_seconds=settings.public_mcp_timeout_seconds),
        auth_state="hosted" if authenticator is not None else "local",
        organization=settings.tenant_id if authenticator is None else None,
        actor=settings.actor if authenticator is None else None,
        timeout_seconds=settings.resource_timeout_seconds,
        max_response_bytes=settings.resource_max_response_bytes,
        rate_limits={
            "tenant": settings.tenant_rate_limit,
            "actor": settings.actor_rate_limit,
            "ip": settings.ip_rate_limit,
        },
        rate_window_seconds=settings.resource_rate_window_seconds,
        concurrency_limit=settings.resource_concurrency_limit,
        concurrency_per_key=settings.resource_concurrency_per_key,
    )
    uvicorn.run(
        FolioHttpApp(
            app,
            service,
            inspection,
            deployment_mode=settings.deployment_mode,
            authenticator=authenticator,
            session_store=session_store,
            identity_adapter=identity_adapter,
            auth_redirect_uri=auth_redirect_uri,
            control_origin=settings.control_origin,
            max_request_bytes=settings.max_request_bytes,
            public_mcp_timeout_seconds=settings.public_mcp_timeout_seconds,
            hsts_max_age=settings.hsts_max_age,
            config_ready=True,
            local_tenant_id=settings.tenant_id if settings.deployment_mode == "local" else None,
            local_actor_id=settings.actor if settings.deployment_mode == "local" else None,
            principal_relay=principal_relay,
        ),
        host=host,
        port=port,
        access_log=False,
        **_tls_options(settings),
    )


def run_renderer(host: str, port: int) -> None:
    settings = Settings.from_env()
    if settings.deployment_mode == "hosted":
        raise ValueError("hosted renderer is not implemented")
    if settings.renderer_capability_secret is None:
        raise ValueError("renderer requires FOLIO_RENDERER_CAPABILITY_SECRET")
    scheme = "https" if settings.tls_certfile else "http"
    mcp_endpoint = settings.mcp_url or f"{scheme}://127.0.0.1:8000/mcp"
    caller = HttpMcpClient(
        mcp_endpoint,
        max_result_bytes=settings.max_request_bytes,
        principal_relay=SignedPrincipalRelay(mcp_endpoint, settings.renderer_capability_secret),
        principal=Principal(
            tenant_id=settings.tenant_id,
            actor_id=settings.actor,
            issuer="folio-local-renderer",
            subject=settings.actor,
            scopes=frozenset({"artifact:read", "artifact:search", "graph:read"}),
        ),
    )
    app = RendererApp(
        caller,
        control_origin=settings.control_origin,
        hsts_max_age=settings.hsts_max_age,
        timeout_seconds=settings.resource_timeout_seconds,
        max_response_bytes=settings.resource_max_response_bytes,
        rate_limits={"ip": settings.ip_rate_limit},
        rate_window_seconds=settings.resource_rate_window_seconds,
        concurrency_limit=settings.resource_concurrency_limit,
        concurrency_per_key=settings.resource_concurrency_per_key,
    )
    uvicorn.run(app, host=host, port=port, access_log=False, **_tls_options(settings))


def _tls_options(settings: Settings) -> dict[str, Any]:
    if settings.tls_certfile is None or settings.tls_keyfile is None:
        return {}
    return {
        "ssl_certfile": settings.tls_certfile,
        "ssl_keyfile": settings.tls_keyfile,
        "ssl_version": ssl.PROTOCOL_TLS_SERVER,
    }


def run_stdio() -> None:
    settings = Settings.from_env()
    if settings.deployment_mode == "hosted":
        raise ValueError("hosted mode requires HTTP bearer transport")
    _, mcp, _ = build_runtime(settings)
    mcp.run("stdio")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("http", "stdio", "renderer"), default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.transport == "stdio":
        run_stdio()
    elif args.transport == "renderer":
        run_renderer(args.host, args.port)
    else:
        run_http(args.host, args.port)


if __name__ == "__main__":
    main()
