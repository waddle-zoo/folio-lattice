from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import logging
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import uvicorn
from starlette.responses import JSONResponse

from folio_lattice.auth import AuthenticationError, MembershipStore, Principal
from folio_lattice.identity import IdentityClaims
from folio_lattice.server import (
    AUTH_CALLBACK_RATE_LIMIT,
    AUTH_LOGOUT_RATE_LIMIT,
    AUTH_RATE_LIMIT_CODE,
    AUTH_START_RATE_LIMIT,
    FolioHttpApp,
    _BoundedRateLimiter,
)
from folio_lattice.sessions import SESSION_COOKIE_NAME, SessionStore


class _Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _BearerAuthenticator:
    def __init__(self, principal: Principal | None = None) -> None:
        self.principal = principal

    def authenticate(self, _scope: Any) -> Principal:
        if self.principal is None:
            raise AuthenticationError("bearer not configured for session test")
        if not any(
            name.lower() == b"authorization" and value == b"Bearer valid"
            for name, value in _scope.get("headers", [])
        ):
            raise AuthenticationError("bearer token missing")
        return self.principal

    def ready(self) -> bool:
        return True


class _IdentityAdapter:
    def __init__(self, principal: Principal, redirect_uri: str) -> None:
        self.principal = principal
        self.redirect_uri = redirect_uri
        self.ready_state = True
        self.starts: list[dict[str, str]] = []
        self.completions: list[dict[str, str]] = []

    def ready(self) -> bool:
        return self.ready_state

    def authorization_url(self, **values: str) -> str:
        self.starts.append(values)
        return "https://idp.example/authorize?" + urlencode(values)

    def complete_callback(self, **values: str) -> IdentityClaims:
        self.completions.append(values)
        if values["code"] != "good":
            raise AuthenticationError("authorization code rejected")
        start = self.starts[-1]
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(values["code_verifier"].encode("ascii")).digest()
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        if (
            challenge != start["code_challenge"]
            or values["nonce"] != start["nonce"]
            or values["redirect_uri"] != self.redirect_uri
        ):
            raise AuthenticationError("authorization binding rejected")
        return IdentityClaims(self.principal.issuer, self.principal.subject)


class _MaliciousIdentityAdapter(_IdentityAdapter):
    def complete_callback(self, **_values: str) -> Principal:
        return Principal(
            "attacker-tenant",
            "attacker-actor",
            self.principal.issuer,
            self.principal.subject,
            frozenset({"admin:all"}),
        )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _HttpServer:
    def __init__(self, app: Any) -> None:
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=_free_port(),
                lifespan="off",
                log_level="error",
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.01)
        raise AssertionError("HTTP test server did not start")

    @property
    def port(self) -> int:
        return int(self.server.config.port)

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


class HostedAuthHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "folio.db"
        self.memberships = MembershipStore(self.db_path)
        self.subject = Principal(
            "forged-tenant",
            "forged-actor",
            "https://issuer.example",
            "subject-a",
        )
        self.memberships.add(
            issuer=self.subject.issuer,
            subject=self.subject.subject,
            tenant_id="tenant-a",
            actor_id="actor-a",
            scopes={"artifact:read"},
        )
        self.sessions = SessionStore(self.db_path, self.memberships)
        self.redirect_uri = "https://127.0.0.1/auth/callback"
        self.adapter = _IdentityAdapter(self.subject, self.redirect_uri)
        self.audit_stream = io.StringIO()
        self.audit_logger = logging.Logger("hosted-auth-http-test")
        self.audit_logger.addHandler(logging.StreamHandler(self.audit_stream))
        downstream = JSONResponse({"downstream": True})
        self.app = FolioHttpApp(
            downstream,
            SimpleNamespace(health=lambda: {"ready": True, "status": "ok"}),
            downstream,
            deployment_mode="hosted",
            authenticator=_BearerAuthenticator(),
            session_store=self.sessions,
            identity_adapter=self.adapter,
            auth_redirect_uri=self.redirect_uri,
            audit_logger=self.audit_logger,
        )
        try:
            self.http_server = _HttpServer(self.app)
        except PermissionError:
            self.temp.cleanup()
            self.skipTest("environment disallows loopback HTTP sockets")

    def tearDown(self) -> None:
        self.http_server.close()
        self.temp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        cookie: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.http_server.port, timeout=5)
        request_headers = dict(headers or {})
        if cookie is not None:
            request_headers["Cookie"] = cookie
        try:
            connection.request(method, path, headers=request_headers)
            response = connection.getresponse()
            response_headers: dict[str, str] = {}
            for key, value in response.getheaders():
                key = key.lower()
                response_headers[key] = (
                    f"{response_headers[key]}\n{value}" if key in response_headers else value
                )
            return (
                response.status,
                response_headers,
                response.read(),
            )
        finally:
            connection.close()

    def test_start_callback_replay_open_redirect_pkce_and_session_fixation(self) -> None:
        self.adapter.ready_state = False
        status, _, body = self.request("GET", "/auth/start?return_to=%2Fworkspace%2Fsafe")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["code"], "identity_provider_unavailable")
        self.assertEqual(self.adapter.starts, [])
        self.adapter.ready_state = True

        status, _, body = self.request(
            "GET", "/auth/start?return_to=https%3A%2F%2Fevil.example%2Fsteal"
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["code"], "invalid_return_to")
        self.assertEqual(self.adapter.starts, [])

        status, headers, body = self.request("GET", "/auth/start?return_to=%2Fworkspace%2Fsafe")
        self.assertEqual(status, 302)
        self.assertEqual(body, b"")
        location = headers["location"]
        auth_cookie = headers["set-cookie"].split(";", 1)[0]
        provider_query = parse_qs(urlsplit(location).query)
        self.assertEqual(provider_query["redirect_uri"], [self.redirect_uri])
        self.assertEqual(provider_query["return_to"], ["/workspace/safe"])
        state = provider_query["state"][0]
        self.assertTrue(provider_query["nonce"][0])
        self.assertTrue(provider_query["code_challenge"][0])

        old_session = self.sessions.create(
            Principal("tenant-a", "actor-a", self.subject.issuer, self.subject.subject)
        )
        callback = "/auth/callback?" + urlencode(
            {"state": state, "code": "good", "redirect_uri": "https://evil.example/callback"}
        )
        status, headers, body = self.request(
            "GET", callback, cookie=f"{auth_cookie}; {SESSION_COOKIE_NAME}={old_session}"
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], "/workspace/safe")
        self.assertEqual(body, b"")
        self.assertEqual(headers["cache-control"], "no-store")
        set_cookie = headers["set-cookie"]
        session_cookie = next(
            line for line in set_cookie.splitlines() if line.startswith(f"{SESSION_COOKIE_NAME}=")
        )
        new_session = session_cookie.split(";", 1)[0].split("=", 1)[1]
        self.assertNotEqual(new_session, old_session)
        self.assertIn("Secure", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=lax", set_cookie)
        self.assertIsNone(self.sessions.lookup(old_session))
        resolved = self.sessions.lookup(new_session)
        self.assertIsNotNone(resolved)
        self.assertEqual((resolved.tenant_id, resolved.actor_id), ("tenant-a", "actor-a"))
        self.assertEqual(resolved.scopes, frozenset({"artifact:read"}))
        self.assertEqual(self.adapter.completions[-1]["redirect_uri"], self.redirect_uri)

        status, headers, body = self.request("GET", callback)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["code"], "invalid_auth_callback")
        self.assertNotIn("set-cookie", {key.lower() for key in headers})

        status, _, body = self.request(
            "GET", "/v1/me", cookie=f"{SESSION_COOKIE_NAME}={new_session}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body),
            {"authenticated": True, "tenant_id": "tenant-a", "actor_id": "actor-a"},
        )

    def test_public_http_logout_requires_exact_same_origin_and_audits_without_secrets(self) -> None:
        session_id = self.sessions.create(self.subject)
        cookie = f"{SESSION_COOKIE_NAME}={session_id}"

        for origin in (None, "https://attacker.example"):
            headers = {} if origin is None else {"Origin": origin}
            status, response_headers, body = self.request(
                "POST",
                "/auth/logout",
                cookie=cookie,
                headers=headers,
            )
            self.assertEqual(status, 403)
            self.assertEqual(response_headers["cache-control"], "no-store")
            error = json.loads(body)
            self.assertEqual(error["code"], "csrf_failed")
            self.assertEqual(error["message"], "This sign-out request is not allowed.")
            self.assertIsNotNone(self.sessions.lookup(session_id))

        status, _, body = self.request(
            "POST",
            "/auth/logout",
            cookie=cookie,
            headers={"Origin": "https://127.0.0.1"},
        )
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertIsNone(self.sessions.lookup(session_id))
        audit = self.audit_stream.getvalue()
        self.assertIn('"event":"logout_csrf_denied"', audit)
        self.assertIn('"event":"logout_succeeded"', audit)
        self.assertNotIn(session_id, audit)
        self.assertNotIn("attacker.example", audit)

    def test_public_http_auth_routes_fail_closed_with_stable_rate_errors(self) -> None:
        for _ in range(AUTH_START_RATE_LIMIT):
            status, _, _ = self.request("GET", "/auth/start")
            self.assertEqual(status, 302)
        status, headers, body = self.request("GET", "/auth/start")
        self.assertEqual(status, 429)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["retry-after"], "60")
        self.assertEqual(json.loads(body)["code"], AUTH_RATE_LIMIT_CODE)

        callback = "/auth/callback?state=attacker-state&code=attacker-code"
        for _ in range(AUTH_CALLBACK_RATE_LIMIT):
            status, _, _ = self.request("GET", callback)
            self.assertEqual(status, 400)
        status, headers, body = self.request("GET", callback)
        self.assertEqual(status, 429)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(json.loads(body)["code"], AUTH_RATE_LIMIT_CODE)
        self.assertNotIn(b"attacker-state", body)
        self.assertNotIn(b"attacker-code", body)

        logout_headers = {"Origin": "https://127.0.0.1"}
        for _ in range(AUTH_LOGOUT_RATE_LIMIT):
            status, _, _ = self.request("POST", "/auth/logout", headers=logout_headers)
            self.assertEqual(status, 204)
        status, headers, body = self.request("POST", "/auth/logout", headers=logout_headers)
        self.assertEqual(status, 429)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(json.loads(body)["code"], AUTH_RATE_LIMIT_CODE)
        audit = self.audit_stream.getvalue()
        self.assertIn('"event":"auth_start_rate_limited"', audit)
        self.assertIn('"event":"auth_callback_rate_limited"', audit)
        self.assertIn('"event":"auth_logout_rate_limited"', audit)

    def test_rate_limiter_denies_new_keys_when_bounded(self) -> None:
        clock = _Clock()
        limiter = _BoundedRateLimiter(limit=1, max_keys=1, clock=clock)
        self.assertEqual(limiter.allow("first"), (True, 0))
        self.assertEqual(limiter.allow("second"), (False, 60))
        clock.value += 60
        self.assertEqual(limiter.allow("second"), (True, 0))


class SessionHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "folio.db"
        self.memberships = MembershipStore(self.db_path)
        self.principal = Principal(
            "tenant-a",
            "actor-a",
            "https://issuer.example",
            "subject-a",
            frozenset({"artifact:read"}),
        )
        self.memberships.add(
            issuer=self.principal.issuer,
            subject=self.principal.subject,
            tenant_id=self.principal.tenant_id,
            actor_id=self.principal.actor_id,
        )
        self.clock = _Clock()
        self.sessions = SessionStore(
            self.db_path,
            self.memberships,
            absolute_ttl_seconds=120,
            idle_ttl_seconds=60,
            clock=self.clock,
        )
        downstream = JSONResponse({"downstream": True})
        self.app = FolioHttpApp(
            downstream,
            SimpleNamespace(health=lambda: {"ready": True, "status": "ok"}),
            downstream,
            deployment_mode="hosted",
            authenticator=_BearerAuthenticator(),
            session_store=self.sessions,
        )

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def call(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        sent: list[dict[str, Any]] = []
        parsed = urlsplit(path)

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await self.app(
            {
                "type": "http",
                "method": method,
                "path": parsed.path,
                "query_string": parsed.query.encode("ascii"),
                "headers": headers or [],
                "asgi": {"version": "3.0"},
            },
            receive,
            send,
        )
        start = next(item for item in sent if item["type"] == "http.response.start")
        response_headers: dict[str, str] = {}
        for key, value in start["headers"]:
            key = key.decode("latin-1").lower()
            value = value.decode("latin-1")
            response_headers[key] = (
                f"{response_headers[key]}\n{value}" if key in response_headers else value
            )
        body = b"".join(
            item.get("body", b"") for item in sent if item["type"] == "http.response.body"
        )
        return start["status"], response_headers, body

    async def test_me_has_stable_unauthorized_error_and_safe_success(self) -> None:
        status, headers, body = await self.call("/v1/me")
        self.assertEqual(status, 401)
        self.assertEqual(headers["cache-control"], "no-store")
        error = json.loads(body)
        self.assertEqual(
            set(error), {"code", "message", "request_id", "retryable", "reauthenticate"}
        )
        self.assertEqual(error["code"], "authentication_required")
        self.assertTrue(error["request_id"])
        self.assertFalse(error["retryable"])
        self.assertTrue(error["reauthenticate"])

        session_id = self.sessions.create(self.principal)
        status, headers, body = await self.call(
            "/v1/me",
            headers=[(b"cookie", f"{SESSION_COOKIE_NAME}={session_id}".encode())],
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(
            json.loads(body),
            {"authenticated": True, "tenant_id": "tenant-a", "actor_id": "actor-a"},
        )
        self.assertNotIn(self.principal.issuer.encode(), body)
        self.assertNotIn(self.principal.subject.encode(), body)

    async def test_auth_callback_requires_browser_bound_state_cookie(self) -> None:
        redirect_uri = "https://folio.test/auth/callback"
        adapter = _IdentityAdapter(self.principal, redirect_uri)
        self.app.identity_adapter = adapter
        self.app.auth_redirect_uri = redirect_uri

        status, headers, _ = await self.call("/auth/start?return_to=%2Fworkspace%2Fsafe")
        self.assertEqual(status, 302)
        auth_cookie = headers["set-cookie"].split(";", 1)[0]
        state = parse_qs(urlsplit(headers["location"]).query)["state"][0]
        callback = f"/auth/callback?{urlencode({'state': state, 'code': 'good'})}"

        status, _, body = await self.call(callback)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["code"], "invalid_auth_callback")

        status, headers, _ = await self.call(
            callback,
            headers=[(b"cookie", auth_cookie.encode("latin-1"))],
        )
        self.assertEqual(status, 303)
        session_cookie = next(
            line
            for line in headers["set-cookie"].splitlines()
            if line.startswith(f"{SESSION_COOKIE_NAME}=")
        )
        session_id = session_cookie.split(";", 1)[0].split("=", 1)[1]
        self.assertIsNotNone(self.sessions.lookup(session_id))

    async def test_auth_callback_rejects_unverified_principal_and_scopes(self) -> None:
        redirect_uri = "https://folio.test/auth/callback"
        adapter = _MaliciousIdentityAdapter(self.principal, redirect_uri)
        self.app.identity_adapter = adapter
        self.app.auth_redirect_uri = redirect_uri

        status, headers, _ = await self.call("/auth/start")
        self.assertEqual(status, 302)
        auth_cookie = headers["set-cookie"].split(";", 1)[0]
        state = parse_qs(urlsplit(headers["location"]).query)["state"][0]
        callback = f"/auth/callback?{urlencode({'state': state, 'code': 'good'})}"

        status, _, body = await self.call(
            callback,
            headers=[(b"cookie", auth_cookie.encode("latin-1"))],
        )
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["code"], "authentication_failed")
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM browser_sessions").fetchone()[0], 0)

    async def test_auth_state_consumption_is_exactly_once_under_concurrency(self) -> None:
        transaction = self.sessions.begin_auth(
            return_to="/", redirect_uri="https://folio.test/auth/callback"
        )

        def consume() -> object:
            return self.sessions.consume_auth(transaction.state)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: consume(), range(2)))

        self.assertEqual(sum(result is not None for result in results), 1)

    async def test_mcp_requires_bearer_but_ui_api_accepts_session(self) -> None:
        session_id = self.sessions.create(self.principal)
        cookie = [(b"cookie", f"{SESSION_COOKIE_NAME}={session_id}".encode())]

        status, _, _ = await self.call("/mcp", headers=cookie)
        self.assertEqual(status, 401)

        self.app.authenticator = _BearerAuthenticator(self.principal)
        status, _, _ = await self.call("/mcp", headers=[(b"authorization", b"Bearer valid")])
        self.assertEqual(status, 200)

        status, _, _ = await self.call("/api/mcp", headers=cookie)
        self.assertEqual(status, 200)

    async def test_ready_gates_configured_browser_identity_adapter(self) -> None:
        status, _, _ = await self.call("/ready")
        self.assertEqual(status, 200)

        adapter = _IdentityAdapter(self.principal, "https://folio.test/auth/callback")
        adapter.ready_state = False
        self.app.identity_adapter = adapter
        self.app.auth_redirect_uri = "https://folio.test/auth/callback"
        status, _, _ = await self.call("/ready")
        self.assertEqual(status, 503)

        adapter.ready_state = True
        status, _, _ = await self.call("/ready")
        self.assertEqual(status, 200)

    async def test_logout_expires_secure_host_cookie_and_invalidates_server_session(self) -> None:
        session_id = self.sessions.create(self.principal)
        cookie = f"{SESSION_COOKIE_NAME}={session_id}".encode()
        self.app.auth_redirect_uri = "https://folio.test/auth/callback"
        status, headers, body = await self.call(
            "/auth/logout",
            method="POST",
            headers=[(b"cookie", cookie), (b"origin", b"https://folio.test")],
        )
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertEqual(headers["cache-control"], "no-store")
        set_cookie = headers["set-cookie"]
        self.assertIn(f"{SESSION_COOKIE_NAME}=", set_cookie)
        self.assertIn("Path=/", set_cookie)
        self.assertIn("Secure", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=lax", set_cookie)
        self.assertIn("Max-Age=0", set_cookie)
        self.assertIsNone(self.sessions.lookup(session_id))

        status, _, _ = await self.call("/v1/me", headers=[(b"cookie", cookie)])
        self.assertEqual(status, 401)

    async def test_sessions_are_hashed_and_bound_by_absolute_idle_and_membership_expiry(
        self,
    ) -> None:
        session_id = self.sessions.create(self.principal)
        self.assertGreaterEqual(len(session_id), 43)
        with sqlite3.connect(self.db_path) as db:
            stored_hash = db.execute("SELECT session_hash FROM browser_sessions").fetchone()[0]
        self.assertNotEqual(stored_hash, session_id)
        self.assertNotIn(session_id, stored_hash)

        self.clock.value = 1_050
        self.assertIsNotNone(self.sessions.lookup(session_id))
        self.clock.value = 1_111
        self.assertIsNone(self.sessions.lookup(session_id))

        self.clock.value = 1_000
        absolute_session = self.sessions.create(self.principal)
        self.clock.value = 1_050
        self.assertIsNotNone(self.sessions.lookup(absolute_session))
        self.clock.value = 1_100
        self.assertIsNotNone(self.sessions.lookup(absolute_session))
        self.clock.value = 1_119
        self.assertIsNotNone(self.sessions.lookup(absolute_session))
        self.clock.value = 1_121
        self.assertIsNone(self.sessions.lookup(absolute_session))

        membership_session = self.sessions.create(self.principal)
        self.memberships.set_scopes(
            self.principal.issuer, self.principal.subject, {"artifact:write"}
        )
        self.assertIsNone(self.sessions.lookup(membership_session))

        membership_session = self.sessions.create(self.principal)
        self.memberships.set_status(self.principal.issuer, self.principal.subject, "disabled")
        self.assertIsNone(self.sessions.lookup(membership_session))


if __name__ == "__main__":
    unittest.main()
