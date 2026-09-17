from __future__ import annotations

import asyncio
import io
import json
import logging
import unittest
from types import SimpleNamespace
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from folio_lattice.auth import Principal
from folio_lattice.server import FolioHttpApp, _BoundedRateLimiter


async def invoke(
    app: FolioHttpApp,
    *,
    principal: str = "member-a",
    body: bytes = b"{}",
    headers: dict[str, str] | None = None,
    client: tuple[str, int] = ("127.0.0.1", 1),
    chunks: list[bytes] | None = None,
    fail_if_received: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    request_chunks = list(chunks if chunks is not None else [body])
    sent: list[Message] = []
    encoded_headers = [
        (name.lower().encode(), value.encode()) for name, value in (headers or {}).items()
    ]
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": encoded_headers + [(b"x-fixture-principal", principal.encode())],
        "client": client,
        "server": ("127.0.0.1", 2),
    }

    async def receive() -> Message:
        if fail_if_received:
            raise AssertionError("oversized request reached the body stream")
        if request_chunks:
            chunk = request_chunks.pop(0)
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": bool(request_chunks),
            }
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return int(start["status"]), response_headers, response_body


class _FixtureAuthenticator:
    def __init__(self) -> None:
        self.calls = 0
        self.principals = {
            "member-a": Principal("tenant-a", "actor-a", "issuer", "subject-a"),
            "member-b": Principal("tenant-b", "actor-b", "issuer", "subject-b"),
        }

    def authenticate(self, scope: Scope) -> Principal:
        self.calls += 1
        marker = next(
            value for header, value in scope["headers"] if header == b"x-fixture-principal"
        )
        return self.principals[marker.decode()]


def build_app(
    downstream: Any,
    *,
    max_request_bytes: int = 1024,
    rate_limit: int = 100,
    concurrency_limit: int = 8,
    concurrency_per_key: int = 2,
    authenticator: _FixtureAuthenticator | None = None,
) -> FolioHttpApp:
    return FolioHttpApp(
        downstream,
        SimpleNamespace(),
        JSONResponse({"error": "not found"}, status_code=404),
        deployment_mode="hosted",
        authenticator=authenticator or _FixtureAuthenticator(),
        max_request_bytes=max_request_bytes,
        public_mcp_rate_limit=rate_limit,
        public_mcp_concurrency_limit=concurrency_limit,
        public_mcp_concurrency_per_key=concurrency_per_key,
    )


class PublicMcpBoundsTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_body_is_rejected_before_auth_and_recovers(self) -> None:
        calls: list[bytes] = []
        authenticator = _FixtureAuthenticator()

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            chunks: list[bytes] = []
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    break
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            calls.append(b"".join(chunks))
            await JSONResponse({"ok": True})(scope, receive, send)

        app = build_app(
            downstream,
            max_request_bytes=8,
            authenticator=authenticator,
        )
        status, headers, body = await invoke(
            app,
            body=b"oversized",
            headers={"Content-Length": "9"},
            fail_if_received=True,
        )
        error = json.loads(body)
        self.assertEqual(status, 413)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["retry-after"], "0")
        self.assertEqual(error["code"], "request_too_large")
        self.assertFalse(error["retryable"])
        self.assertTrue(error["request_id"])
        self.assertEqual(authenticator.calls, 0)
        self.assertEqual(calls, [])

        status, _, body = await invoke(
            app,
            body=b"small",
            headers={"Content-Length": "5"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertEqual(calls, [b"small"])

    async def test_chunked_oversize_is_rejected_before_dispatch(self) -> None:
        calls = 0

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal calls
            calls += 1
            await JSONResponse({"ok": True})(scope, receive, send)

        app = build_app(downstream, max_request_bytes=8)
        status, headers, body = await invoke(
            app,
            chunks=[b"1234", b"56789"],
        )
        error = json.loads(body)
        self.assertEqual(status, 413)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(error["code"], "request_too_large")
        self.assertEqual(calls, 0)

    async def test_rate_limit_is_identity_fair_and_recovers(self) -> None:
        audit_stream = io.StringIO()
        audit_logger = logging.Logger("public-mcp-bounds-rate")
        audit_logger.addHandler(logging.StreamHandler(audit_stream))

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            await JSONResponse({"ok": True})(scope, receive, send)

        app = build_app(downstream, rate_limit=1)
        app.audit_logger = audit_logger
        status, _, _ = await invoke(app, principal="member-a")
        self.assertEqual(status, 200)
        status, headers, body = await invoke(app, principal="member-a")
        error = json.loads(body)
        self.assertEqual(status, 429)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["retry-after"], "60")
        self.assertEqual(error["code"], "mcp_rate_limited")
        self.assertTrue(error["retryable"])
        self.assertTrue(error["request_id"])

        status, _, _ = await invoke(app, principal="member-b")
        self.assertEqual(status, 200)
        self.assertNotIn("tenant-a", audit_stream.getvalue())
        self.assertNotIn("actor-a", audit_stream.getvalue())

        clock = _Clock()
        app._public_mcp_rate_limiter = _BoundedRateLimiter(limit=1, clock=clock)
        status, _, _ = await invoke(app, principal="member-a")
        self.assertEqual(status, 200)
        status, _, _ = await invoke(app, principal="member-a")
        self.assertEqual(status, 429)
        clock.value += 60
        status, _, _ = await invoke(app, principal="member-a")
        self.assertEqual(status, 200)

    async def test_concurrency_is_per_identity_and_recovers_after_release(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        blocked = False

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal blocked
            marker = next(
                value for header, value in scope["headers"] if header == b"x-fixture-principal"
            )
            if marker == b"member-a" and not blocked:
                blocked = True
                started.set()
                await release.wait()
            await JSONResponse({"principal": marker.decode()})(scope, receive, send)

        app = build_app(
            downstream,
            rate_limit=100,
            concurrency_limit=2,
            concurrency_per_key=1,
        )
        first = asyncio.create_task(invoke(app, principal="member-a"))
        await started.wait()

        same_identity = await invoke(app, principal="member-a")
        other_identity = await invoke(app, principal="member-b")
        self.assertEqual(same_identity[0], 429)
        self.assertEqual(json.loads(same_identity[2])["code"], "mcp_concurrency_limited")
        self.assertEqual(other_identity[0], 200)
        self.assertEqual(json.loads(other_identity[2]), {"principal": "member-b"})
        self.assertEqual(same_identity[1]["cache-control"], "no-store")
        self.assertEqual(same_identity[1]["retry-after"], "1")

        release.set()
        self.assertEqual((await first)[0], 200)
        recovered = await invoke(app, principal="member-a")
        self.assertEqual(recovered[0], 200)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


if __name__ == "__main__":
    unittest.main()
