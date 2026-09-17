"""Deterministic real-HTTP OIDC/JWKS target for hosted adversarial checks.

This is a test deployment, not hosted product authentication. It puts a small
issuer and membership boundary in front of the existing public MCP server so
the consumer tests exercise HTTP, signed tokens, two tenant namespaces, and a
real process restart without importing private state from the consumer.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

SOURCE_ROOT = Path(__file__).resolve().parents[1]
ALGORITHM = "EdDSA"
KEY_ID = "fl-hosted-test-1"
AUDIENCE = "https://folio-lattice.test/mcp"
TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
MAX_REQUEST_BYTES = 13 * 1024 * 1024
APPROVED_UPSTREAM_ENDPOINT = "https://approved-upstream.test/mcp"
PRIVATE_SEED = bytes(range(32))
RESTART_READY_TIMEOUT_SECONDS = 10.0
RESTART_POLL_INTERVAL_SECONDS = 0.05
_BOOT_ID = os.urandom(16).hex()

PROFILES = {
    "A_OWNER": {
        "subject": "a-owner",
        "tenant": TENANT_A,
        "actor": "a-owner",
        "scope": "artifact:read artifact:write artifact:search graph:read graph:write artifact:share tenant:admin",
    },
    "A_MEMBER": {
        "subject": "a-member",
        "tenant": TENANT_A,
        "actor": "a-member",
        "scope": "artifact:read artifact:search graph:read",
    },
    "B": {
        "subject": "b-owner",
        "tenant": TENANT_B,
        "actor": "b-owner",
        "scope": "artifact:read artifact:write artifact:search graph:read graph:write artifact:share tenant:admin",
    },
}
TOOL_SCOPES = {
    "artifact_create": "artifact:write",
    "artifact_write": "artifact:write",
    "artifact_read": "artifact:read",
    "artifact_read_chunk": "artifact:read",
    "artifact_search": "artifact:search",
    "artifact_grep": "artifact:search",
    "graph_link": "graph:write",
    "graph_traverse": "graph:read",
    "graph_component": "graph:read",
    "artifact_versions": "artifact:read",
    "artifact_share": "artifact:share",
    "artifact_revoke": "artifact:share",
    "artifact_acl": "artifact:share",
    "slack_search": "artifact:search",
    "slack_save": "artifact:write",
    "external_mcp_connection_register": "tenant:admin",
    "external_mcp_connection_list": "tenant:admin",
    "external_mcp_connection_status": "tenant:admin",
    "external_mcp_connection_revoke": "tenant:admin",
    "external_mcp_audit": "tenant:admin",
    "external_mcp_tool_call": "tenant:admin",
    "external_mcp_resource_read": "tenant:admin",
}


def _b64u(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _private_key() -> Any:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.from_private_bytes(PRIVATE_SEED)


def _public_jwk() -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization

    public = (
        _private_key()
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": _b64u(public),
        "use": "sig",
        "alg": ALGORITHM,
        "kid": KEY_ID,
    }


class Memberships:
    def __init__(self, path: Path):
        self.path = path
        if not path.exists():
            self._write(
                {profile["subject"]: {**profile, "active": True} for profile in PROFILES.values()}
            )

    def _read(self) -> dict[str, dict[str, Any]]:
        return json.loads(self.path.read_text())

    def _write(self, value: dict[str, dict[str, Any]]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, sort_keys=True) + "\n")
        temporary.replace(self.path)

    def get(self, subject: str) -> dict[str, Any] | None:
        return self._read().get(subject)

    def revoke(self, subject: str) -> bool:
        memberships = self._read()
        membership = memberships.get(subject)
        if membership is None:
            return False
        membership["active"] = False
        self._write(memberships)
        return True


class Issuer:
    def __init__(self, issuer_url: str, audience: str):
        self.issuer_url = issuer_url
        self.audience = audience

    def token(self, profile: str, variant: str = "") -> str:
        if profile not in PROFILES:
            raise ValueError("unknown token profile")
        now = int(time.time())
        claims = {
            "iss": self.issuer_url,
            "sub": PROFILES[profile]["subject"],
            "aud": self.audience,
            "iat": now,
            "nbf": now - 1,
            "exp": now + 600,
            "jti": f"{profile.lower()}-{now}",
            "scope": PROFILES[profile]["scope"],
        }
        if variant == "expired":
            claims["exp"] = now - 60
        elif variant == "wrong-issuer":
            claims["iss"] = self.issuer_url + "/wrong"
        elif variant == "wrong-audience":
            claims["aud"] = self.audience + "/wrong"
        elif variant == "missing-scope":
            claims["scope"] = ""
        elif variant:
            raise ValueError("unknown token variant")
        import jwt

        return jwt.encode(
            claims,
            _private_key(),
            algorithm=ALGORITHM,
            headers={"kid": KEY_ID, "typ": "at+jwt"},
        )

    def metadata(self) -> dict[str, str]:
        return {
            "issuer": self.issuer_url,
            "jwks_uri": self.issuer_url + "/jwks.json",
            "token_endpoint": self.issuer_url + "/token",
        }


def _header(scope: dict[str, Any], name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _token_from(scope: dict[str, Any]) -> str | None:
    value = _header(scope, b"authorization")
    if value is None or not value.startswith("Bearer "):
        return None
    token = value[7:].strip()
    return token or None


def _verify(token: str, issuer: Issuer) -> dict[str, Any] | None:
    try:
        import jwt

        claims = jwt.decode(
            token,
            _private_key().public_key(),
            algorithms=[ALGORITHM],
            audience=issuer.audience,
            issuer=issuer.issuer_url,
            options={"require": ["iss", "sub", "aud", "iat", "nbf", "exp"]},
        )
    except Exception:
        return None
    return claims if isinstance(claims, dict) else None


class _StaticCredentials:
    def issue(self, **_: str) -> str:
        from approved_upstream_target import UPSTREAM_SECRET

        return UPSTREAM_SECRET


class _LoopbackExternalTransport:
    """Real MCP HTTP transport pinned to one local fixture endpoint."""

    def __init__(self, local_endpoint: str):
        from folio_lattice.external_mcp import ExternalMcpError, HttpExternalMcpTransport

        class TestOnlyLoopbackHttpTransport(HttpExternalMcpTransport):
            """Pin the deterministic HTTP fixture without weakening production policy."""

            def _resolve_endpoint(self, endpoint: str) -> tuple[str, tuple[str, ...]]:
                parsed = urlsplit(endpoint)
                if (
                    endpoint != local_endpoint
                    or parsed.scheme != "http"
                    or parsed.hostname != "127.0.0.1"
                    or parsed.query
                    or parsed.fragment
                    or parsed.path != "/mcp"
                ):
                    raise ExternalMcpError("test upstream endpoint is invalid")
                return "127.0.0.1", ("127.0.0.1",)

        self.local_endpoint = local_endpoint
        self.transport = TestOnlyLoopbackHttpTransport(timeout_seconds=0.25)

    def validate_registration(self, endpoint: str) -> None:
        self._endpoint(endpoint)

    def _endpoint(self, endpoint: str) -> str:
        if endpoint != APPROVED_UPSTREAM_ENDPOINT:
            raise ValueError("unexpected approved upstream endpoint")
        return self.local_endpoint

    def health(self, endpoint: str, *, credential: str | None) -> str:
        return self.transport.health(self._endpoint(endpoint), credential=credential)

    def call_tool(
        self,
        endpoint: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        credential: str | None,
    ) -> Any:
        return self.transport.call_tool(
            self._endpoint(endpoint), tool_name, arguments, credential=credential
        )

    def read_resource(self, endpoint: str, resource_uri: str, *, credential: str | None) -> Any:
        return self.transport.read_resource(
            self._endpoint(endpoint), resource_uri, credential=credential
        )


class _ApprovedUpstream:
    def __init__(self) -> None:
        script = Path(__file__).with_name("approved_upstream_target.py")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
        self.process = subprocess.Popen(
            [sys.executable, str(script), "--host", "127.0.0.1", "--port", str(port)],
            cwd=SOURCE_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urlopen(f"{base_url}/health", timeout=0.2) as response:
                    if json.loads(response.read()).get("ready"):
                        self.endpoint = f"{base_url}/mcp"
                        return
            except (OSError, URLError, json.JSONDecodeError):
                if self.process.poll() is not None:
                    break
                time.sleep(0.05)
        self.close()
        raise RuntimeError("approved upstream did not become ready")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


async def _read_body(receive: Callable[[], Awaitable[dict[str, Any]]]) -> bytes:
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        if message["type"] != "http.request":
            continue
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break
        if len(body) > MAX_REQUEST_BYTES:
            break
    return bytes(body)


def _requested_tools(body: bytes) -> set[str]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return set()
    messages = value if isinstance(value, list) else [value]
    names = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("method") != "tools/call":
            continue
        params = message.get("params")
        if isinstance(params, dict) and isinstance(params.get("name"), str):
            names.add(params["name"])
    return names


class AuthenticatedMcpApp:
    def __init__(self, app: Any, tenant: str, issuer: Issuer, memberships: Memberships):
        self.app = app
        self.tenant = tenant
        self.issuer = issuer
        self.memberships = memberships

    async def _deny(self, scope: dict[str, Any], receive: Any, send: Any, status: int) -> None:
        from starlette.responses import JSONResponse

        headers = {"Cache-Control": "no-store"}
        if status == 401:
            headers["WWW-Authenticate"] = 'Bearer realm="folio-hosted-test"'
        await JSONResponse(
            {"error": "unauthorized" if status == 401 else "forbidden"}, status, headers
        )(scope, receive, send)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        token = _token_from(scope)
        claims = _verify(token, self.issuer) if token else None
        if claims is None:
            await self._deny(scope, receive, send, 401)
            return
        subject = claims.get("sub")
        membership = self.memberships.get(subject) if isinstance(subject, str) else None
        if (
            membership is None
            or not membership.get("active")
            or membership.get("tenant") != self.tenant
        ):
            await self._deny(scope, receive, send, 403)
            return
        profile = membership
        body = b""
        if scope.get("method") == "POST":
            body = await _read_body(receive)
            if len(body) > MAX_REQUEST_BYTES:
                await self._deny(scope, receive, send, 413)
                return
            scopes = set(str(claims.get("scope", "")).split())
            for tool in _requested_tools(body):
                required = TOOL_SCOPES.get(tool)
                if required is not None and required not in scopes:
                    await self._deny(scope, receive, send, 403)
                    return

        delivered = False

        async def replay() -> dict[str, Any]:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
        from folio_lattice.server import _bind_mcp_actor

        principal = Principal(
            self.tenant,
            str(profile["actor"]),
            self.issuer.issuer_url,
            subject,
            frozenset(str(profile.get("scope", "")).split()),
        )
        principal_token = set_request_principal(principal)
        _bind_mcp_actor(scope, principal)
        try:
            await self.app(scope, replay if scope.get("method") == "POST" else receive, send)
        finally:
            reset_request_principal(principal_token)


def _server_app(
    host: str, port: int, state_dir: Path, audience: str, upstream: _ApprovedUpstream
) -> Any:
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    from folio_lattice.external_mcp import ExternalMcpBroker
    from folio_lattice.mcp_protocol import build_mcp_server
    from folio_lattice.service import FolioLattice

    issuer_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    issuer_url = f"http://{issuer_host}:{port}/issuer"
    issuer = Issuer(issuer_url, audience)
    memberships = Memberships(state_dir / "memberships.json")
    service = FolioLattice(state_dir / "folio.db", state_dir / "blobs")
    broker = ExternalMcpBroker(
        service,
        credentials=_StaticCredentials(),
        transport=_LoopbackExternalTransport(upstream.endpoint),
    )
    child_apps = {}
    session_apps = []
    for route, tenant in (("tenant-a", TENANT_A), ("tenant-b", TENANT_B)):
        # Keep the fixture transport faithful to hosted production: one
        # tenant MCP app derives actor/tenant from the verified request
        # principal.  Per-profile fixed servers masked actor/session bugs.
        child = build_mcp_server(service, external_broker=broker).streamable_http_app(
            json_response=True
        )
        session_apps.append(child)
        child_apps[route] = AuthenticatedMcpApp(child, tenant, issuer, memberships)

    async def health(_: Any) -> Any:
        return JSONResponse(
            {
                "ready": True,
                "boot_id": _BOOT_ID,
                "authentication": "deterministic-test-jwks",
                "tenants": [TENANT_A, TENANT_B],
            }
        )

    async def metadata(_: Any) -> Any:
        return JSONResponse(issuer.metadata())

    async def jwks(_: Any) -> Any:
        return JSONResponse({"keys": [_public_jwk()]})

    async def token(request: Any) -> Any:
        query = parse_qs(request.scope.get("query_string", b"").decode("ascii", "replace"))
        profile = query.get("profile", [""])[0]
        variant = query.get("variant", [""])[0]
        try:
            value = issuer.token(profile, variant)
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return JSONResponse({"access_token": value, "token_type": "Bearer", "expires_in": 600})

    async def revoke(request: Any) -> Any:
        try:
            value = json.loads(await request.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            value = {}
        subject = value.get("subject") if isinstance(value, dict) else None
        if not isinstance(subject, str) or not memberships.revoke(subject):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return JSONResponse({"status": "revoked"})

    async def restart(_: Any) -> Any:
        loop = asyncio.get_running_loop()
        loop.call_later(0.2, os.execv, sys.executable, [sys.executable, *sys.argv])
        return JSONResponse({"status": "restarting"})

    @asynccontextmanager
    async def lifespan(_: Any):
        async with AsyncExitStack() as stack:
            for app in session_apps:
                await stack.enter_async_context(app.router.lifespan_context(app))
            yield

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/issuer/.well-known/openid-configuration", metadata, methods=["GET"]),
            Route("/issuer/jwks.json", jwks, methods=["GET"]),
            Route("/issuer/token", token, methods=["GET"]),
            Route("/__test/revoke", revoke, methods=["POST"]),
            Route("/__test/restart", restart, methods=["POST"]),
            Mount("/tenant-a", app=child_apps["tenant-a"]),
            Mount("/tenant-b", app=child_apps["tenant-b"]),
        ],
    )


def serve(host: str, port: int, state_dir: Path, audience: str) -> None:
    import uvicorn

    state_dir.mkdir(parents=True, exist_ok=True)
    upstream = _ApprovedUpstream()
    try:
        uvicorn.run(
            _server_app(host, port, state_dir, audience, upstream),
            host=host,
            port=port,
            log_level="warning",
        )
    finally:
        upstream.close()


def stdio(state_dir: Path, issuer_url: str, audience: str, profile: str, token: str) -> None:
    from folio_lattice.external_mcp import ExternalMcpBroker
    from folio_lattice.mcp_protocol import build_mcp_server
    from folio_lattice.service import FolioLattice

    if profile not in PROFILES:
        raise ValueError("unknown stdio profile")
    issuer = Issuer(issuer_url, audience)
    claims = _verify(token, issuer)
    profile_data = PROFILES[profile]
    if claims is None or claims.get("sub") != profile_data["subject"]:
        raise RuntimeError("stdio bearer authentication failed")
    memberships = Memberships(state_dir / "memberships.json")
    if not memberships.get(profile_data["subject"]):
        raise RuntimeError("stdio membership is unavailable")
    upstream = _ApprovedUpstream()
    try:
        service = FolioLattice(state_dir / "folio.db", state_dir / "blobs")
        broker = ExternalMcpBroker(
            service,
            credentials=_StaticCredentials(),
            transport=_LoopbackExternalTransport(upstream.endpoint),
        )
        build_mcp_server(
            service,
            tenant_id=profile_data["tenant"],
            actor=profile_data["actor"],
            external_broker=broker,
        ).run("stdio")
    finally:
        upstream.close()


def _url_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 3,
) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read())


def token_command(base_url: str, profile: str, variant: str) -> None:
    query = urlencode({"profile": profile, **({"variant": variant} if variant else {})})
    value = _url_json(f"{base_url.rstrip('/')}/issuer/token?{query}")
    print(value["access_token"])


def control_command(base_url: str, action: str, subject: str | None) -> None:
    if action == "revoke":
        subject = subject or os.environ.get("FOLIO_TEST_REVOKED_SUBJECT")
        if not subject:
            raise SystemExit("FOLIO_TEST_REVOKED_SUBJECT is required")
        _url_json(
            f"{base_url.rstrip('/')}/__test/revoke",
            method="POST",
            payload={"subject": subject},
        )
    else:
        health = _url_json(f"{base_url.rstrip('/')}/health")
        previous_boot_id = health.get("boot_id") if isinstance(health, dict) else None
        if not isinstance(previous_boot_id, str) or not previous_boot_id:
            raise RuntimeError("restart readiness marker is unavailable")
        _url_json(f"{base_url.rstrip('/')}/__test/restart", method="POST", payload={})
        _wait_for_restart(base_url, previous_boot_id)


def _wait_for_restart(base_url: str, previous_boot_id: str) -> None:
    deadline = time.monotonic() + RESTART_READY_TIMEOUT_SECONDS
    health_url = f"{base_url.rstrip('/')}/health"
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            health = _url_json(
                health_url,
                timeout_seconds=min(0.5, max(0.01, remaining)),
            )
            if (
                isinstance(health, dict)
                and health.get("ready") is True
                and health.get("boot_id") != previous_boot_id
            ):
                return
        except (HTTPError, OSError, URLError, ValueError):
            pass
        time.sleep(min(RESTART_POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
    raise RuntimeError("hosted auth target did not become ready after restart")


def _wait_ready(base_url: str, process: subprocess.Popen[bytes]) -> None:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("hosted auth target exited before readiness")
        try:
            if _url_json(f"{base_url}/health").get("ready"):
                _url_json(f"{base_url}/issuer/.well-known/openid-configuration")
                _url_json(f"{base_url}/issuer/jwks.json")
                return
        except (HTTPError, OSError, URLError, ValueError):
            time.sleep(0.05)
    raise RuntimeError("hosted auth target did not become ready")


def _start(state_dir: Path, port: int, audience: str) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--state-dir",
            str(state_dir),
            "--audience",
            audience,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _wait_ready(f"http://127.0.0.1:{port}", process)
    return process


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def e2e(evidence_path: Path) -> int:
    adversarial = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/adversarial/test_hosted_auth.py"],
        check=False,
    )
    if adversarial.returncode:
        print(json.dumps({"status": "adversarial_failed", "exit_code": adversarial.returncode}))
        return adversarial.returncode
    port = int(os.environ.get("FOLIO_HOSTED_AUTH_PORT", "8787"))
    audience = os.environ.get("FOLIO_HOSTED_AUTH_AUDIENCE", AUDIENCE)
    with tempfile.TemporaryDirectory(prefix="folio-hosted-auth-") as directory:
        state_dir = Path(directory)
        process = _start(state_dir, port, audience)
        try:
            base_url = f"http://127.0.0.1:{port}"
            script = str(Path(__file__).resolve())
            env = os.environ.copy()
            env.update(
                {
                    "FOLIO_GATE_COMMAND": "make hosted-auth-e2e",
                    "FOLIO_TEST_ISSUER_URL": f"{base_url}/issuer",
                    "FOLIO_TEST_ISSUER_METADATA_URL": f"{base_url}/issuer/.well-known/openid-configuration",
                    "FOLIO_EXPECTED_AUDIENCE": audience,
                    "FOLIO_EXPECTED_TENANT_A": TENANT_A,
                    "FOLIO_EXPECTED_TENANT_B": TENANT_B,
                    "FOLIO_TENANT_A_MCP_URL": f"{base_url}/tenant-a/mcp",
                    "FOLIO_TENANT_B_MCP_URL": f"{base_url}/tenant-b/mcp",
                    "FOLIO_TOKEN_COMMAND_A_OWNER": shlex.join(
                        [
                            sys.executable,
                            script,
                            "token",
                            "--base-url",
                            base_url,
                            "--profile",
                            "A_OWNER",
                        ]
                    ),
                    "FOLIO_TOKEN_COMMAND_A_MEMBER": shlex.join(
                        [
                            sys.executable,
                            script,
                            "token",
                            "--base-url",
                            base_url,
                            "--profile",
                            "A_MEMBER",
                        ]
                    ),
                    "FOLIO_TOKEN_COMMAND_B": shlex.join(
                        [sys.executable, script, "token", "--base-url", base_url, "--profile", "B"]
                    ),
                    "FOLIO_REVOKE_COMMAND": shlex.join(
                        [
                            sys.executable,
                            script,
                            "control",
                            "--base-url",
                            base_url,
                            "--action",
                            "revoke",
                        ]
                    ),
                    "FOLIO_RESTART_COMMAND": shlex.join(
                        [
                            sys.executable,
                            script,
                            "control",
                            "--base-url",
                            base_url,
                            "--action",
                            "restart",
                        ]
                    ),
                }
            )
            gate = subprocess.run(
                [
                    sys.executable,
                    "tests/hyperset_hosted_consumer.py",
                    "--evidence",
                    str(evidence_path),
                ],
                check=False,
                env=env,
            )
            print(
                json.dumps(
                    {
                        "status": "ok" if gate.returncode == 0 else "gate_failed",
                        "evidence": str(evidence_path),
                    }
                )
            )
            return gate.returncode
        finally:
            _stop(process)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, required=True)
    serve_parser.add_argument("--state-dir", type=Path, required=True)
    serve_parser.add_argument("--audience", default=AUDIENCE)

    stdio_parser = subparsers.add_parser("stdio")
    stdio_parser.add_argument("--state-dir", type=Path, required=True)
    stdio_parser.add_argument("--issuer", required=True)
    stdio_parser.add_argument("--audience", default=AUDIENCE)
    stdio_parser.add_argument("--profile", choices=tuple(PROFILES), required=True)
    stdio_parser.add_argument("--token", default="")

    token_parser = subparsers.add_parser("token")
    token_parser.add_argument("--base-url", required=True)
    token_parser.add_argument("--profile", choices=tuple(PROFILES), required=True)
    token_parser.add_argument("--variant", default="")

    control_parser = subparsers.add_parser("control")
    control_parser.add_argument("--base-url", required=True)
    control_parser.add_argument("--action", choices=("revoke", "restart"), required=True)
    control_parser.add_argument("--subject")

    e2e_parser = subparsers.add_parser("e2e")
    e2e_parser.add_argument(
        "--evidence",
        type=Path,
        default=Path(os.environ.get("FOLIO_EVIDENCE_PATH", "/tmp/folio-lattice-fl-urj-5.2.json")),
    )
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.host, args.port, args.state_dir, args.audience)
    elif args.command == "stdio":
        args.state_dir.mkdir(parents=True, exist_ok=True)
        stdio(
            args.state_dir,
            args.issuer,
            args.audience,
            args.profile,
            args.token or os.environ.get("FOLIO_TEST_BEARER", ""),
        )
    elif args.command == "token":
        token_command(args.base_url, args.profile, args.variant)
    elif args.command == "control":
        control_command(args.base_url, args.action, args.subject)
    else:
        raise SystemExit(e2e(args.evidence))


if __name__ == "__main__":
    main()
