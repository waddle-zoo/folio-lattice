from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from starlette.responses import JSONResponse

from folio_lattice.auth import AuthenticationError, MembershipStore, Principal
from folio_lattice.server import FolioHttpApp
from folio_lattice.sessions import SESSION_COOKIE_NAME, SessionStore


class _Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _BearerAuthenticator:
    def authenticate(self, _scope: Any) -> Principal:
        raise AuthenticationError("bearer not configured for session test")

    def ready(self) -> bool:
        return True


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

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await self.app(
            {
                "type": "http",
                "method": method,
                "path": path,
                "headers": headers or [],
                "asgi": {"version": "3.0"},
            },
            receive,
            send,
        )
        start = next(item for item in sent if item["type"] == "http.response.start")
        response_headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in start["headers"]
        }
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

    async def test_logout_expires_secure_host_cookie_and_invalidates_server_session(self) -> None:
        session_id = self.sessions.create(self.principal)
        cookie = f"{SESSION_COOKIE_NAME}={session_id}".encode()
        status, headers, body = await self.call(
            "/auth/logout", method="POST", headers=[(b"cookie", cookie)]
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
        self.memberships.set_status(self.principal.issuer, self.principal.subject, "disabled")
        self.assertIsNone(self.sessions.lookup(membership_session))


if __name__ == "__main__":
    unittest.main()
