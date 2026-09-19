from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
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


def bridge_request(arguments: dict[str, Any] | None = None) -> bytes:
    return json.dumps(
        {
            "request_id": "resource-boundary",
            "artifact_id": "artifact-id",
            "attachment": "folio-lattice",
            "tool": "artifact_search",
            "arguments": arguments or {"query": "marker"},
        }
    ).encode()


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

    async def test_oversized_human_body_is_retryable_with_zero_delay(self) -> None:
        caller = CountingCaller({"ok": True})
        app = InspectionApp(
            caller,
            control_origin=CONTROL_ORIGIN,
            render_origin=RENDER_ORIGIN,
            max_request_bytes=100,
            rate_limits={"tenant": 100, "actor": 100, "ip": 100},
        )
        status, headers, response = await asyncio.wait_for(
            invoke(
                app,
                "/api/mcp",
                body=b"x" * 101,
            ),
            timeout=1,
        )
        self.assertEqual(status, 413)
        self.assertEqual(headers["retry-after"], "0")
        self.assertEqual(headers["cache-control"], "no-store")
        error = json.loads(response)
        self.assertEqual(error["code"], "request_too_large")
        self.assertEqual(error["error"], "request body too large")
        self.assertTrue(error["request_id"])
        self.assertEqual(caller.calls, 0)
        self.assertEqual(app._concurrency_limiter._total, 0)
        self.assertEqual(app._concurrency_limiter._active, {})

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

    async def test_bridge_path_rejects_oversize_and_recovers_after_timeout(self) -> None:
        caller = CountingCaller([{"artifact_id": "artifact-id"}], delay=0.03)
        app = self.make_app(
            caller,
            timeout_seconds=0.005,
            rate_limits={"tenant": 100, "actor": 100, "ip": 100},
            concurrency_limit=1,
            concurrency_per_key=1,
        )
        oversized = bridge_request({"query": "x" * 70_000})
        status, headers, response = await invoke(app, "/api/bridge", body=oversized)
        self.assertEqual(status, 413)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(json.loads(response)["code"], "request_too_large")
        self.assertEqual(caller.calls, 0)

        status, headers, response = await invoke(app, "/api/bridge", body=bridge_request())
        self.assertEqual(status, 504)
        self.assertEqual(headers["retry-after"], "1")
        self.assertEqual(json.loads(response)["code"], "resource_timeout")
        self.assertEqual(app._concurrency_limiter._total, 0)

        caller.delay = 0
        status, _, response = await invoke(app, "/api/bridge", body=bridge_request())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response)["result"], [{"artifact_id": "artifact-id"}])

    async def test_actor_and_ip_buckets_are_fair_and_isolated(self) -> None:
        body = gateway_request({"artifact_id": "a"})
        actor_app = self.make_app(
            CountingCaller({"ok": True}),
            rate_limits={"tenant": 100, "actor": 1, "ip": 100},
        )
        principal_a = set_request_principal(Principal("tenant-a", "actor-a", "issuer", "subject-a"))
        try:
            self.assertEqual((await invoke(actor_app, "/api/mcp", body=body))[0], 200)
            self.assertEqual(
                (
                    await invoke(
                        actor_app,
                        "/api/mcp",
                        body=body,
                        client=("127.0.0.2", 1),
                    )
                )[0],
                429,
            )
        finally:
            reset_request_principal(principal_a)
        principal_b = set_request_principal(Principal("tenant-a", "actor-b", "issuer", "subject-b"))
        try:
            self.assertEqual((await invoke(actor_app, "/api/mcp", body=body))[0], 200)
        finally:
            reset_request_principal(principal_b)

        ip_app = self.make_app(
            CountingCaller({"ok": True}),
            rate_limits={"tenant": 100, "actor": 100, "ip": 1},
        )
        self.assertEqual((await invoke(ip_app, "/api/mcp", body=body))[0], 200)
        self.assertEqual((await invoke(ip_app, "/api/mcp", body=body))[0], 429)
        self.assertEqual(
            (
                await invoke(
                    ip_app,
                    "/api/mcp",
                    body=body,
                    client=("127.0.0.2", 1),
                )
            )[0],
            200,
        )

    async def test_concurrency_key_separates_control_character_tuples(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def call(_tool: str, _arguments: dict[str, Any]) -> dict[str, bool]:
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
            return {"ok": True}

        app = self.make_app(
            SimpleNamespace(call=call),
            rate_limits={"tenant": 100, "actor": 100, "ip": 100},
            concurrency_limit=2,
            concurrency_per_key=1,
        )
        principal_a = Principal("a", "b\x1fc", "issuer", "subject-a")
        principal_b = Principal("a\x1fb", "c", "issuer", "subject-b")

        async def invoke_as(principal: Principal) -> tuple[int, dict[str, str], bytes]:
            token = set_request_principal(principal)
            try:
                return await invoke(app, "/api/mcp", body=gateway_request({}))
            finally:
                reset_request_principal(token)

        first = asyncio.create_task(invoke_as(principal_a))
        await started.wait()
        second = await invoke_as(principal_b)
        self.assertEqual(second[0], 200)
        release.set()
        self.assertEqual((await first)[0], 200)


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

    def test_registration_size_is_rejected_before_transport_and_recovers(self) -> None:
        class Transport:
            def __init__(self) -> None:
                self.validation_calls = 0

            def validate_registration(self, endpoint: str) -> None:
                del endpoint
                self.validation_calls += 1

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = FolioLattice(root / "folio.db", root / "blobs")
            transport = Transport()
            broker = ExternalMcpBroker(
                service,
                transport=transport,
                rate_limits={"tenant": 100, "actor": 100, "connection": 100},
            )
            with self.assertRaisesRegex(ExternalMcpError, "item is too large"):
                broker.register(
                    tenant_id="tenant-a",
                    actor="admin",
                    name="oversized",
                    endpoint="https://example.com/mcp",
                    approved_tools=["x" * 2_049],
                    approved_resources=[],
                    allowed_origins=["https://example.com"],
                    credential_ref=None,
                    reason="test",
                )
            self.assertEqual(transport.validation_calls, 0)
            record = broker.register(
                tenant_id="tenant-a",
                actor="admin",
                name="approved",
                endpoint="https://example.com/mcp",
                approved_tools=["calendar.list"],
                approved_resources=[],
                allowed_origins=["https://example.com"],
                credential_ref=None,
                reason="test",
            )
            self.assertEqual(record["name"], "approved")
            self.assertEqual(broker._concurrency_limiter._total, 0)

    def test_approved_call_concurrency_rejects_noisy_neighbor_and_recovers(self) -> None:
        class BlockingTransport:
            def __init__(self) -> None:
                self.entered = threading.Event()
                self.release = threading.Event()
                self.blocking = True

            def validate_registration(self, endpoint: str) -> None:
                del endpoint

            def call_tool(
                self,
                endpoint: str,
                tool_name: str,
                arguments: dict[str, Any],
                *,
                credential: str | None,
            ) -> Any:
                del endpoint, tool_name, arguments, credential
                if self.blocking:
                    self.entered.set()
                    self.release.wait(timeout=2)
                return {"ok": True}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = FolioLattice(root / "folio.db", root / "blobs")
            transport = BlockingTransport()
            broker = ExternalMcpBroker(
                service,
                transport=transport,
                rate_limits={"tenant": 100, "actor": 100, "connection": 100},
                concurrency_limit=1,
                concurrency_per_key=1,
            )
            record = broker.register(
                tenant_id="tenant-a",
                actor="admin",
                name="approved",
                endpoint="https://example.com/mcp",
                approved_tools=["calendar.list"],
                approved_resources=[],
                allowed_origins=["https://example.com"],
                credential_ref=None,
                reason="test",
            )
            connection_id = record["id"]
            result: list[Any] = []
            errors: list[BaseException] = []

            def first_call() -> None:
                try:
                    result.append(
                        broker.call_tool(
                            tenant_id="tenant-a",
                            actor="admin",
                            connection_id=connection_id,
                            tool_name="calendar.list",
                            arguments={},
                        )
                    )
                except BaseException as exc:  # pragma: no cover - assertion below
                    errors.append(exc)

            thread = threading.Thread(target=first_call)
            thread.start()
            self.assertTrue(transport.entered.wait(timeout=2))
            with self.assertRaisesRegex(ExternalMcpError, "concurrency limit"):
                broker.call_tool(
                    tenant_id="tenant-a",
                    actor="admin",
                    connection_id=connection_id,
                    tool_name="calendar.list",
                    arguments={},
                )
            transport.release.set()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(result, [{"ok": True}])
            self.assertEqual(broker._concurrency_limiter._total, 0)
            transport.blocking = False
            self.assertEqual(
                broker.call_tool(
                    tenant_id="tenant-a",
                    actor="admin",
                    connection_id=connection_id,
                    tool_name="calendar.list",
                    arguments={},
                ),
                {"ok": True},
            )

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
