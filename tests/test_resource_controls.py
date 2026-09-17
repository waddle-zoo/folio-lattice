from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from folio_lattice.external_mcp import (
    ExternalMcpBroker,
    ExternalMcpError,
    HttpExternalMcpTransport,
)
from folio_lattice.inspection import InspectionApp
from folio_lattice.renderer import RendererApp
from folio_lattice.resource_limits import BoundedConcurrencyLimiter, DimensionRateLimiter
from folio_lattice.server import Settings
from folio_lattice.service import FolioLattice

CONTROL_ORIGIN = "http://127.0.0.1:8000"
RENDER_ORIGIN = "http://127.0.0.1:8001"


async def invoke(
    app: Any,
    path: str,
    *,
    body: bytes = b"",
    method: str = "POST",
    content_type: str | None = "application/json",
    origin: str | None = CONTROL_ORIGIN,
    client: tuple[str, int] = ("127.0.0.1", 1),
    fetch_dest: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    request = {"type": "http.request", "body": body, "more_body": False}

    async def receive() -> dict[str, Any]:
        return request

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers: list[tuple[bytes, bytes]] = []
    if content_type is not None:
        headers.append((b"content-type", content_type.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if fetch_dest is not None:
        headers.append((b"sec-fetch-dest", fetch_dest.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": client,
        "server": ("127.0.0.1", 2),
    }
    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return int(start["status"]), response_headers, response_body


def gateway_request(arguments: dict[str, Any]) -> bytes:
    return json.dumps({"tool": "artifact_read", "arguments": arguments}).encode()


class CountingCaller:
    def __init__(self, value: Any, *, delay: float = 0) -> None:
        self.value = value
        self.delay = delay
        self.calls = 0

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        del tool, arguments
        self.calls += 1
        await asyncio.sleep(self.delay)
        return self.value

    async def ready(self) -> bool:
        await asyncio.sleep(self.delay)
        return True


class ResourceLimitPrimitiveTests(unittest.TestCase):
    def test_dimension_buckets_are_atomic_and_recover(self) -> None:
        now = [0.0]
        limiter = DimensionRateLimiter(
            limits={"tenant": 1, "actor": 2},
            window_seconds=60,
            clock=lambda: now[0],
        )
        self.assertEqual(limiter.allow({"tenant": "a", "actor": "a"}), (True, 0))
        self.assertFalse(limiter.allow({"tenant": "a", "actor": "b"})[0])
        self.assertEqual(limiter.allow({"tenant": "b", "actor": "b"}), (True, 0))
        now[0] = 60
        self.assertEqual(limiter.allow({"tenant": "a", "actor": "b"}), (True, 0))

    def test_concurrency_releases_and_bounds_keys(self) -> None:
        limiter = BoundedConcurrencyLimiter(limit=1, per_key_limit=1, max_keys=2)
        self.assertTrue(limiter.try_acquire("a"))
        self.assertFalse(limiter.try_acquire("b"))
        limiter.release("a")
        self.assertTrue(limiter.try_acquire("b"))
        limiter.release("b")
        self.assertEqual(limiter._total, 0)
        self.assertEqual(limiter._active, {})


class HumanGatewayResourceTests(unittest.IsolatedAsyncioTestCase):
    def make_app(
        self,
        caller: CountingCaller,
        **kwargs: Any,
    ) -> InspectionApp:
        return InspectionApp(
            caller,
            control_origin=CONTROL_ORIGIN,
            render_origin=RENDER_ORIGIN,
            max_request_bytes=200_000,
            **kwargs,
        )

    async def test_argument_rejection_happens_before_downstream_and_stays_bounded(self) -> None:
        caller = CountingCaller({"ok": True})
        app = self.make_app(
            caller,
            rate_limits={"tenant": 1_000, "actor": 1_000, "ip": 1_000},
        )
        body = gateway_request({"artifact_id": "x", "padding": "x" * 70_000})
        for _ in range(100):
            status, headers, response = await invoke(app, "/api/mcp", body=body)
            self.assertEqual(status, 422)
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertTrue(json.loads(response)["request_id"])
        self.assertEqual(caller.calls, 0)
        self.assertEqual(app._concurrency_limiter._total, 0)
        self.assertEqual(len(app._concurrency_limiter._active), 0)

    async def test_rate_is_tenant_isolated_and_retryable(self) -> None:
        caller_a = CountingCaller({"ok": "a"})
        app_a = self.make_app(
            caller_a,
            organization="tenant-a",
            actor="actor-a",
            rate_limits={"tenant": 1, "actor": 100, "ip": 100},
        )
        body = gateway_request({"artifact_id": "a"})
        self.assertEqual((await invoke(app_a, "/api/mcp", body=body))[0], 200)
        status, headers, response = await invoke(app_a, "/api/mcp", body=body)
        self.assertEqual(status, 429)
        self.assertEqual(headers["retry-after"], "60")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(json.loads(response)["code"], "resource_rate_limited")

        caller_b = CountingCaller({"ok": "b"})
        app_b = self.make_app(
            caller_b,
            organization="tenant-b",
            actor="actor-b",
            rate_limits={"tenant": 1, "actor": 100, "ip": 100},
        )
        self.assertEqual((await invoke(app_b, "/api/mcp", body=body))[0], 200)

    async def test_timeout_and_concurrency_slots_recover(self) -> None:
        caller = CountingCaller({"ok": True}, delay=0.05)
        app = self.make_app(
            caller,
            timeout_seconds=0.005,
            rate_limits={"tenant": 100, "actor": 100, "ip": 100},
            concurrency_limit=1,
            concurrency_per_key=1,
        )
        first = asyncio.create_task(invoke(app, "/api/mcp", body=gateway_request({})))
        await asyncio.sleep(0.001)
        status, headers, response = await invoke(app, "/api/mcp", body=gateway_request({}))
        self.assertEqual(status, 429)
        self.assertEqual(headers["retry-after"], "1")
        self.assertEqual(json.loads(response)["code"], "resource_concurrency_limited")
        self.assertEqual((await first)[0], 504)
        self.assertEqual(app._concurrency_limiter._total, 0)
        caller.delay = 0
        self.assertEqual(
            (await invoke(app, "/api/mcp", body=gateway_request({})))[0],
            200,
        )

    async def test_response_ceiling_is_stable(self) -> None:
        caller = CountingCaller({"text": "x" * 500})
        app = self.make_app(
            caller,
            max_response_bytes=100,
            rate_limits={"tenant": 100, "actor": 100, "ip": 100},
        )
        status, headers, response = await invoke(app, "/api/mcp", body=gateway_request({}))
        self.assertEqual(status, 502)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(json.loads(response)["code"], "response_too_large")


class RendererResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_recovery_and_ip_isolation(self) -> None:
        read = {"version": {"id": "v1", "media_type": "text/html"}, "text": "<p>ok</p>"}
        caller = CountingCaller(read, delay=0.03)
        app = RendererApp(
            caller,
            control_origin=CONTROL_ORIGIN,
            timeout_seconds=0.005,
            rate_limits={"ip": 100},
            concurrency_limit=1,
            concurrency_per_key=1,
        )
        status, headers, _ = await invoke(
            app, "/render/art", method="GET", content_type=None, fetch_dest="iframe"
        )
        self.assertEqual(status, 504)
        self.assertEqual(headers["retry-after"], "1")
        self.assertEqual(app._concurrency_limiter._total, 0)
        caller.delay = 0
        status, _, _ = await invoke(
            app, "/render/art", method="GET", content_type=None, fetch_dest="iframe"
        )
        self.assertEqual(status, 200)

        limited = RendererApp(
            CountingCaller(read),
            control_origin=CONTROL_ORIGIN,
            rate_limits={"ip": 1},
        )
        self.assertEqual(
            (
                await invoke(
                    limited,
                    "/render/art",
                    method="GET",
                    content_type=None,
                    fetch_dest="iframe",
                )
            )[0],
            200,
        )
        status, headers, response = await invoke(
            limited,
            "/render/art",
            method="GET",
            content_type=None,
            fetch_dest="iframe",
        )
        self.assertEqual(status, 429)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(json.loads(response)["code"], "renderer_rate_limited")
        self.assertEqual(
            (
                await invoke(
                    limited,
                    "/render/art",
                    method="GET",
                    content_type=None,
                    fetch_dest="iframe",
                    client=("127.0.0.2", 1),
                )
            )[0],
            200,
        )


class ExternalResourceTests(unittest.TestCase):
    def test_transport_rejects_large_requests_before_dns(self) -> None:
        def forbidden_dns(*args: Any, **kwargs: Any) -> list[Any]:
            raise AssertionError("DNS was reached before request rejection")

        transport = HttpExternalMcpTransport(dns_resolver=forbidden_dns)
        with self.assertRaisesRegex(ExternalMcpError, "arguments exceed"):
            transport.call_tool(
                "https://example.com/mcp",
                "tool",
                {"padding": "x" * 70_000},
                credential=None,
            )
        with self.assertRaisesRegex(ExternalMcpError, "URI exceeds"):
            transport.read_resource("https://example.com/mcp", "x" * 3_000, credential=None)

    def test_broker_rate_isolation_and_slot_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = FolioLattice(root / "folio.db", root / "blobs")
            broker = ExternalMcpBroker(
                service,
                rate_limits={"tenant": 1, "actor": 100, "connection": 100},
            )
            self.assertEqual(broker.list_connections(tenant_id="a", actor="actor"), [])
            with self.assertRaisesRegex(ExternalMcpError, "rate limited"):
                broker.list_connections(tenant_id="a", actor="actor")
            self.assertEqual(broker.list_connections(tenant_id="b", actor="actor"), [])
            self.assertEqual(broker._concurrency_limiter._total, 0)

    def test_settings_expose_configurable_resource_bounds(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "FOLIO_DEPLOYMENT_MODE": "local",
                "FOLIO_TENANT_RATE_LIMIT": "7",
                "FOLIO_ACTOR_RATE_LIMIT": "8",
                "FOLIO_IP_RATE_LIMIT": "9",
                "FOLIO_RESOURCE_CONCURRENCY_LIMIT": "3",
                "FOLIO_RESOURCE_CONCURRENCY_PER_KEY": "2",
                "FOLIO_RESOURCE_TIMEOUT_SECONDS": "4",
                "FOLIO_RESOURCE_MAX_RESPONSE_BYTES": "4096",
            },
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.tenant_rate_limit, 7)
        self.assertEqual(settings.actor_rate_limit, 8)
        self.assertEqual(settings.ip_rate_limit, 9)
        self.assertEqual(settings.resource_concurrency_limit, 3)
        self.assertEqual(settings.resource_concurrency_per_key, 2)
        self.assertEqual(settings.resource_timeout_seconds, 4)
        self.assertEqual(settings.resource_max_response_bytes, 4096)
