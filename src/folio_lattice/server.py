from __future__ import annotations

import argparse
import hmac
import math
import os
import string
import uuid
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlsplit

import uvicorn
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .auth import (
    AuthenticationError,
    JwksClient,
    MembershipStore,
    OidcAuthenticator,
    OidcVerifier,
    Principal,
    reset_request_principal,
    set_request_principal,
)
from .bridge import AttachedMcpBridge
from .inspection import InspectionApp
from .mcp_protocol import build_mcp_server
from .public_mcp import HttpMcpClient
from .renderer import RendererApp
from .service import DEFAULT_MAX_ARTIFACT_BYTES, FolioLattice
from .sessions import (
    AUTH_STATE_COOKIE_NAME,
    AUTH_TRANSACTION_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_PATH,
    HostedIdentityAdapter,
    SessionStore,
)

DEFAULT_MAX_REQUEST_BYTES = 13 * 1024 * 1024
MAX_AUTH_QUERY_BYTES = 8 * 1024
MAX_AUTH_QUERY_VALUE = 4 * 1024


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
    deployment_mode: str
    bridge_timeout_seconds: float
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_memberships_file: str | None = None
    oidc_jwks_timeout_seconds: float = 5
    oidc_jwks_cache_seconds: float = 300
    oidc_clock_skew_seconds: float = 0

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
            except ValueError as exc:
                raise ValueError(f"hosted mode requires an authentication adapter; {exc}") from exc
        return cls(
            db_path=os.environ.get("FOLIO_DB_PATH", ".data/folio.db"),
            blob_root=os.environ.get("FOLIO_BLOB_ROOT", ".data/blobs"),
            tenant_id=tenant_id,
            actor=actor,
            max_artifact_bytes=_positive_env(
                "FOLIO_MAX_ARTIFACT_BYTES", DEFAULT_MAX_ARTIFACT_BYTES
            ),
            max_request_bytes=_positive_env("FOLIO_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES),
            control_origin=_origin_env("FOLIO_CONTROL_ORIGIN", "http://127.0.0.1:8000"),
            render_origin=_origin_env("FOLIO_RENDER_ORIGIN", "http://127.0.0.1:8001"),
            mcp_url=_optional_mcp_url_env("FOLIO_MCP_URL"),
            deployment_mode=deployment_mode,
            bridge_timeout_seconds=_positive_float_env("FOLIO_BRIDGE_TIMEOUT_SECONDS", 5),
            oidc_issuer=oidc_issuer,
            oidc_audience=oidc_audience,
            oidc_jwks_url=oidc_jwks_url,
            oidc_memberships_file=oidc_memberships_file,
            oidc_jwks_timeout_seconds=oidc_jwks_timeout_seconds,
            oidc_jwks_cache_seconds=oidc_jwks_cache_seconds,
            oidc_clock_skew_seconds=oidc_clock_skew_seconds,
        )


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
        raise ValueError(f"{name} must be an HTTP URL without credentials")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an HTTP URL without credentials")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be an HTTP URL without credentials") from exc
    return value


def _origin_env(name: str, default: str) -> str:
    value = os.environ.get(name, default).rstrip("/")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(character not in string.ascii_letters + string.digits + ".:-" for character in host)
    ):
        raise ValueError(f"{name} must be an HTTP origin without a path")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be an HTTP origin without a path") from exc
    bracketed_host = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{bracketed_host}{f':{port}' if port is not None else ''}"


def _optional_mcp_url_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an HTTP URL ending in /mcp without credentials")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be an HTTP URL ending in /mcp without credentials") from exc
    return value


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
    ):
        self.app = app
        self.service = service
        self.inspection = inspection
        self.deployment_mode = deployment_mode
        self.authenticator = authenticator
        self.session_store = session_store
        self.identity_adapter = identity_adapter
        self.auth_redirect_uri = _auth_redirect_uri(auth_redirect_uri)
        if deployment_mode == "hosted" and authenticator is None:
            raise ValueError("hosted mode requires configured OIDC authentication")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)
            return
        if scope["method"] == "GET" and scope["path"] == "/sign-in":
            await self.inspection(scope, receive, send)
            return
        if scope["method"] == "GET" and scope["path"] == "/auth/start":
            await self._auth_start(scope, receive, send)
            return
        if scope["method"] == "GET" and scope["path"] == "/auth/callback":
            await self._auth_callback(scope, receive, send)
            return
        if scope["path"] in {"/health", "/ready"}:
            health = self.service.health()
            identity_ready = self.authenticator is None or self.authenticator.ready()
            if self.identity_adapter is not None or self.auth_redirect_uri is not None:
                identity_ready = identity_ready and self._identity_ready()
            health["ready"] = bool(health["ready"] and identity_ready)
            health["status"] = "ok" if health["ready"] else "not_ready"
            health.update(
                {
                    "deployment_mode": self.deployment_mode,
                    "authentication": (
                        "oidc-bearer"
                        if self.authenticator is not None
                        else "none-local-development"
                    ),
                }
            )
            status = 200 if health["ready"] else 503
            await JSONResponse(health, status_code=status)(scope, receive, send)
            return
        if scope["method"] == "GET" and scope["path"] == "/v1/me":
            try:
                principal = self._authenticate(scope)
            except AuthenticationError:
                await self._error(
                    scope,
                    receive,
                    send,
                    401,
                    "authentication_required",
                    "Sign-in required. Sign in to continue.",
                    reauthenticate=True,
                )
                return
            await JSONResponse(
                {
                    "authenticated": True,
                    "tenant_id": principal.tenant_id,
                    "actor_id": principal.actor_id,
                },
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        if scope["method"] == "POST" and scope["path"] == "/auth/logout":
            session_id = _session_cookie(scope)
            if session_id is not None and self.session_store is not None:
                self.session_store.revoke(session_id)
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
            await response(scope, receive, send)
            return
        principal_token = None
        if self.authenticator is not None:
            try:
                principal = self._authenticate(scope, allow_session=scope["path"] != "/mcp")
            except AuthenticationError:
                await self._error(
                    scope,
                    receive,
                    send,
                    401,
                    "authentication_required",
                    "Sign-in required. Sign in to continue.",
                    reauthenticate=True,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                return
            principal_token = set_request_principal(principal)
        path = scope["path"]
        try:
            if (
                path == "/"
                or path.startswith("/inspect/")
                or path.startswith("/artifacts/")
                or path.startswith("/standalone/")
                or path.startswith("/workspace/")
                or path in {"/api/mcp", "/api/bridge", "/ui.css", "/ui.js"}
            ):
                await self.inspection(scope, receive, send)
                return
            await self.app(scope, receive, send)
        finally:
            if principal_token is not None:
                reset_request_principal(principal_token)

    def _authenticate(self, scope: Scope, *, allow_session: bool = True) -> Principal:
        session_id = _session_cookie(scope)
        if session_id is not None and allow_session:
            if self.session_store is None:
                raise AuthenticationError("session authentication is unavailable")
            principal = self.session_store.lookup(session_id)
            if principal is None:
                raise AuthenticationError("session is invalid")
            return principal
        if self.authenticator is not None:
            return self.authenticator.authenticate(scope)
        raise AuthenticationError("authentication is required")

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
            principal = self.identity_adapter.complete_callback(
                code=code,
                nonce=transaction.nonce,
                code_verifier=transaction.code_verifier,
                redirect_uri=transaction.redirect_uri,
            )
            if not isinstance(principal, Principal):
                raise AuthenticationError("identity provider returned no principal")
            session_id = self.session_store.create(principal)
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
    ) -> None:
        response_headers = {"Cache-Control": "no-store", **(headers or {})}
        await JSONResponse(
            {
                "code": code,
                "message": message,
                "request_id": uuid.uuid4().hex,
                "retryable": retryable,
                "reauthenticate": reauthenticate,
            },
            status_code=status_code,
            headers=response_headers,
        )(scope, receive, send)


def _session_cookie(scope: Scope) -> str | None:
    return _cookie(scope, SESSION_COOKIE_NAME)


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
        parsed.scheme not in {"http", "https"}
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
    service = FolioLattice(
        settings.db_path,
        settings.blob_root,
        max_artifact_bytes=settings.max_artifact_bytes,
    )
    if settings.deployment_mode == "local":
        return (
            service,
            build_mcp_server(service, tenant_id=settings.tenant_id, actor=settings.actor),
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
    return service, build_mcp_server(service), authenticator


def run_http(host: str, port: int) -> None:
    settings = Settings.from_env()
    service, mcp, authenticator = build_runtime(settings)
    session_store = None
    if settings.deployment_mode == "hosted":
        session_store = SessionStore(settings.db_path, MembershipStore(settings.db_path))
    app = mcp.streamable_http_app(
        json_response=True,
        max_request_body_size=settings.max_request_bytes,
        host=host,
    )
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    caller = HttpMcpClient(
        settings.mcp_url or f"http://{connect_host}:{port}/mcp",
        max_result_bytes=settings.max_request_bytes,
    )
    inspection = InspectionApp(
        caller,
        control_origin=settings.control_origin,
        render_origin=settings.render_origin,
        max_request_bytes=settings.max_request_bytes,
        bridge=AttachedMcpBridge(caller, timeout_seconds=settings.bridge_timeout_seconds),
        auth_state="hosted" if authenticator is not None else "local",
        organization=settings.tenant_id if authenticator is None else None,
        actor=settings.actor if authenticator is None else None,
    )
    uvicorn.run(
        FolioHttpApp(
            app,
            service,
            inspection,
            deployment_mode=settings.deployment_mode,
            authenticator=authenticator,
            session_store=session_store,
        ),
        host=host,
        port=port,
    )


def run_renderer(host: str, port: int) -> None:
    settings = Settings.from_env()
    if settings.deployment_mode == "hosted":
        raise ValueError("hosted renderer is not implemented")
    caller = HttpMcpClient(
        settings.mcp_url or "http://127.0.0.1:8000/mcp",
        max_result_bytes=settings.max_request_bytes,
    )
    app = RendererApp(
        caller,
        control_origin=settings.control_origin,
    )
    uvicorn.run(app, host=host, port=port)


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
