import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

from mcp import Client

try:
    import httpx2 as upstream_httpx
except ImportError:
    import httpx as upstream_httpx

from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
from folio_lattice.external_mcp import (
    MAX_CREDENTIAL_DECODE_PASSES,
    CredentialResolver,
    ExternalMcpBroker,
    ExternalMcpError,
    HttpExternalMcpTransport,
    UnavailableCredentialBroker,
    build_credential_broker,
)
from folio_lattice.mcp_protocol import build_mcp_server
from folio_lattice.service import FolioError, FolioLattice

ENDPOINT = "https://calendar.example/mcp"
ORIGIN = "https://calendar.example"
TOOL = "calendar.events.list"
RESOURCE = "calendar://events/today"
SECRET_REF = "secret://acme/calendar"
UPSTREAM_SECRET = "upstream-secret-must-not-escape"


class FakeCredentials:
    def __init__(self, secret: str = UPSTREAM_SECRET) -> None:
        self.secret = secret

    def issue(self, **kwargs: str) -> str:
        self.last_request = kwargs
        return self.secret


class FakeSecretStore:
    def __init__(self) -> None:
        self.requests: list[dict[str, str]] = []

    def resolve(self, **kwargs: str) -> str:
        self.requests.append(kwargs)
        return UPSTREAM_SECRET


class MockUpstream:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def __call__(self, request: Any) -> Any:
        if request.method == "DELETE":
            return upstream_httpx.Response(204)
        payload = json.loads(request.content)
        self.requests.append(payload)
        method = payload["method"]
        if method == "initialize":
            return upstream_httpx.Response(
                200,
                headers={"content-type": "application/json", "mcp-session-id": "fixture"},
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    },
                },
            )
        if method == "notifications/initialized":
            return upstream_httpx.Response(202)
        if method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": TOOL,
                        "description": "fixture tool",
                        "inputSchema": {"type": "object", "additionalProperties": True},
                    }
                ]
            }
        elif method == "tools/call":
            result = {
                "content": [{"type": "text", "text": "tool-ok"}],
                "structuredContent": {"value": "tool-ok"},
                "isError": False,
            }
        elif method == "resources/read":
            result = {
                "contents": [{"uri": RESOURCE, "mimeType": "text/plain", "text": "resource-ok"}]
            }
        else:
            return upstream_httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "error": {"code": -32601, "message": "method not found"},
                },
            )
        return upstream_httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"jsonrpc": "2.0", "id": payload["id"], "result": result},
        )


class FakeTransport:
    def __init__(self) -> None:
        self.last_credential: str | None = None
        self.echo_credential = False
        self.echo_encoded_credential = False
        self.transport_error_secret: str | None = None
        self.validation_error_secret: str | None = None

    def _raise_transport_error(self) -> None:
        if self.transport_error_secret is None:
            return
        secret = self.transport_error_secret
        try:
            raise RuntimeError(f"upstream cause contains credential={secret}")
        except RuntimeError as cause:
            raise ExternalMcpError(f"upstream failure credential={secret}") from cause

    def validate_registration(self, endpoint: str) -> None:
        if endpoint != ENDPOINT:
            raise ExternalMcpError("test upstream endpoint is invalid")
        if self.validation_error_secret is not None:
            secret = self.validation_error_secret
            try:
                raise RuntimeError(f"validation cause contains credential={secret}")
            except RuntimeError as cause:
                raise ExternalMcpError(f"validation failure credential={secret}") from cause

    def health(self, endpoint: str, *, credential: str | None) -> str:
        self.last_credential = credential
        self._raise_transport_error()
        return "healthy"

    def call_tool(
        self,
        endpoint: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        credential: str | None,
    ) -> Any:
        self.last_credential = credential
        self._raise_transport_error()
        if self.echo_credential:
            echoed = json.dumps(credential) if self.echo_encoded_credential else credential
            return {"credential": echoed}
        return {"endpoint": endpoint, "tool": tool_name, "arguments": arguments}

    def read_resource(self, endpoint: str, resource_uri: str, *, credential: str | None) -> Any:
        self.last_credential = credential
        self._raise_transport_error()
        if self.echo_credential:
            echoed = json.dumps(credential) if self.echo_encoded_credential else credential
            return {"credential": echoed}
        return {"endpoint": endpoint, "resource": resource_uri}


class HttpExternalMcpTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_result_unwraps_sdk_result_envelope(self) -> None:
        result = SimpleNamespace(
            is_error=False,
            structured_content={"result": {"upstream": "approved"}},
        )
        session = AsyncMock()
        session.initialize.return_value = None
        session.call_tool.return_value = result
        session_context = AsyncMock()
        session_context.__aenter__.return_value = session
        stream_context = AsyncMock()
        stream_context.__aenter__.return_value = (object(), object(), object())
        http_context = AsyncMock()
        http_context.__aenter__.return_value = object()

        with (
            patch("httpx2.AsyncClient", return_value=http_context),
            patch(
                "mcp.client.streamable_http.streamable_http_client",
                return_value=stream_context,
            ),
            patch("mcp.client.session.ClientSession", return_value=session_context),
        ):
            value = await HttpExternalMcpTransport(
                dns_resolver=lambda host, port, **kwargs: [(2, 1, 6, "", ("93.184.216.34", port))],
                http_transport=AsyncMock(),
            )._request(
                "https://calendar.example/mcp",
                "credential",
                "tool",
                (TOOL, {"limit": 5}),
            )

        self.assertEqual(value, {"upstream": "approved"})


class ExternalMcpServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = FolioLattice(root / "folio.db", root / "blobs")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_runtime_credential_factory_is_explicit_and_fail_closed(self) -> None:
        self.assertIsInstance(build_credential_broker({}), UnavailableCredentialBroker)
        module = ModuleType("test_external_credential_factory")
        module.factory = lambda: FakeCredentials()  # type: ignore[attr-defined]
        env = {"FOLIO_EXTERNAL_MCP_CREDENTIAL_BROKER_FACTORY": f"{module.__name__}:factory"}
        with patch.dict(sys.modules, {module.__name__: module}):
            self.assertIsInstance(build_credential_broker(env), FakeCredentials)
            with patch.dict(os.environ, env):
                transport = FakeTransport()
                broker = ExternalMcpBroker(self.service, transport=transport)
                record = broker.register(
                    tenant_id="acme",
                    actor="admin",
                    name="calendar",
                    endpoint=ENDPOINT,
                    approved_tools=[TOOL],
                    approved_resources=[RESOURCE],
                    allowed_origins=[ORIGIN],
                    credential_ref=SECRET_REF,
                )
                broker.call_tool(
                    tenant_id="acme",
                    actor="admin",
                    connection_id=record["id"],
                    tool_name=TOOL,
                    arguments={},
                )
                self.assertEqual(transport.last_credential, UPSTREAM_SECRET)
        self.assertIsInstance(
            build_credential_broker(
                {"FOLIO_EXTERNAL_MCP_CREDENTIAL_BROKER_FACTORY": "invalid reference"}
            ),
            UnavailableCredentialBroker,
        )

    def register(self, tenant_id: str = "acme") -> dict[str, Any]:
        return self.service.register_external_connection(
            tenant_id=tenant_id,
            actor="admin",
            name="calendar",
            endpoint=ENDPOINT,
            approved_tools=[TOOL],
            approved_resources=[RESOURCE],
            allowed_origins=[ORIGIN],
            credential_ref=SECRET_REF,
        )

    def test_durable_record_redacts_secret_and_preserves_exact_policy(self) -> None:
        record = self.register()
        self.assertEqual(record["approved_tools"], [TOOL])
        self.assertEqual(record["approved_resources"], [RESOURCE])
        self.assertEqual(record["allowed_origins"], [ORIGIN])
        self.assertTrue(record["credential_configured"])
        self.assertNotIn("credential_ref", record)
        with self.service.connect() as db:
            stored = db.execute(
                "SELECT credential_ref FROM external_mcp_connections WHERE id = ?",
                (record["id"],),
            ).fetchone()[0]
            audit = db.execute(
                "SELECT * FROM external_mcp_audit WHERE connection_id = ?",
                (record["id"],),
            ).fetchall()
        self.assertEqual(stored, SECRET_REF)
        self.assertTrue(audit)
        self.assertNotIn(SECRET_REF, repr([tuple(row) for row in audit]))

        with self.assertRaisesRegex(FolioError, "resource is not approved"):
            self.service.authorize_external_resource(
                "acme", record["id"], f"{RESOURCE}?token=should-not-persist", actor="admin"
            )
        with self.service.connect() as db:
            latest = db.execute(
                "SELECT resource_uri FROM external_mcp_audit WHERE connection_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (record["id"],),
            ).fetchone()[0]
        self.assertIsNone(latest)

    def test_exact_allowlists_fail_closed_and_revoke_is_immediate(self) -> None:
        record = self.register()
        connection_id = record["id"]
        allowed = self.service.authorize_external_tool(
            "acme", connection_id, TOOL, actor="admin", arguments={}
        )
        self.assertEqual(allowed["id"], connection_id)
        with self.assertRaisesRegex(FolioError, "tool is not approved"):
            self.service.authorize_external_tool(
                "acme", connection_id, "calendar.events.delete", actor="admin", arguments={}
            )
        with self.assertRaisesRegex(FolioError, "resource is not approved"):
            self.service.authorize_external_resource(
                "acme", connection_id, "calendar://events/all", actor="admin"
            )
        revoked = self.service.revoke_external_connection(
            "acme", connection_id, actor="admin", reason="retired"
        )
        self.assertEqual(revoked["status"], "revoked")
        with self.assertRaisesRegex(FolioError, "connection is revoked"):
            self.service.authorize_external_tool(
                "acme", connection_id, TOOL, actor="admin", arguments={}
            )

    def test_endpoint_origin_and_wildcards_are_rejected(self) -> None:
        common = {
            "tenant_id": "acme",
            "actor": "admin",
            "name": "bad",
        }
        with self.assertRaisesRegex(FolioError, "origin must be explicitly allowed"):
            self.service.register_external_connection(
                **common,
                endpoint=ENDPOINT,
                approved_tools=[TOOL],
                approved_resources=[],
                allowed_origins=["https://other.example"],
            )
        with self.assertRaisesRegex(FolioError, "non-wildcard"):
            self.service.register_external_connection(
                **common,
                endpoint=ENDPOINT,
                approved_tools=["calendar.*"],
                approved_resources=[],
                allowed_origins=["https://calendar.example"],
            )
        with self.assertRaisesRegex(FolioError, "not public"):
            self.service.register_external_connection(
                **common,
                endpoint="https://127.0.0.1/mcp",
                approved_tools=[TOOL],
                approved_resources=[],
                allowed_origins=["https://127.0.0.1"],
            )

    def test_resource_allowlist_rejects_encoded_secrets_and_public_projection_is_safe(self) -> None:
        for index, resource in enumerate(
            (
                "calendar://events/today?token=secret",
                "calendar://user:password@events/today",
                "calendar://events/today%3Ftoken%3Dsecret",
                "calendar://events/today%253Ftoken%253Dsecret",
            )
        ):
            with self.subTest(resource=resource):
                with self.assertRaisesRegex(FolioError, "safe resource URIs"):
                    self.service.register_external_connection(
                        tenant_id="acme",
                        actor="admin",
                        name=f"unsafe-{index}",
                        endpoint=ENDPOINT,
                        approved_tools=[],
                        approved_resources=[resource],
                        allowed_origins=[ORIGIN],
                    )

        unsafe = FolioLattice._external_public(
            {
                "id": "legacy",
                "name": "legacy",
                "endpoint": f"{ENDPOINT}?token=secret",
                "origin": "https://user:password@calendar.example",
                "approved_resources": [RESOURCE, "calendar://events/today%3Ftoken=secret"],
                "allowed_origins": [ORIGIN, "https://calendar.example%3Ftoken=secret"],
                "policy_json": "{}",
                "credential_ref": SECRET_REF,
            }
        )
        self.assertNotIn("endpoint", unsafe)
        self.assertNotIn("origin", unsafe)
        self.assertEqual(unsafe["approved_resources"], [RESOURCE])
        self.assertEqual(unsafe["allowed_origins"], [ORIGIN])
        self.assertNotIn(SECRET_REF, repr(unsafe))

        with self.assertRaisesRegex(FolioError, "external MCP origin is invalid"):
            self.service.register_external_connection(
                tenant_id="acme",
                actor="admin",
                name="encoded-endpoint",
                endpoint="https://calendar.example/mcp%3Ftoken%3Dsecret",
                approved_tools=[TOOL],
                approved_resources=[],
                allowed_origins=[ORIGIN],
            )
        with self.assertRaisesRegex(FolioError, "external MCP origin is invalid"):
            self.service.register_external_connection(
                tenant_id="acme",
                actor="admin",
                name="encoded-origin",
                endpoint=ENDPOINT,
                approved_tools=[TOOL],
                approved_resources=[],
                allowed_origins=["https://calendar.example%3Ftoken%3Dsecret"],
            )
        with self.assertRaisesRegex(FolioError, "opaque secret"):
            self.service.register_external_connection(
                tenant_id="acme",
                actor="admin",
                name="encoded-credential",
                endpoint=ENDPOINT,
                approved_tools=[TOOL],
                approved_resources=[],
                allowed_origins=[ORIGIN],
                credential_ref="secret://acme/calendar%3Ftoken%3Dsecret",
            )

    def test_broker_uses_secret_server_side_and_audits_without_it(self) -> None:
        credentials = FakeCredentials()
        transport = FakeTransport()
        broker = ExternalMcpBroker(self.service, credentials=credentials, transport=transport)
        record = broker.register(
            tenant_id="acme",
            actor="admin",
            name="calendar",
            endpoint=ENDPOINT,
            approved_tools=[TOOL],
            approved_resources=[RESOURCE],
            allowed_origins=[ORIGIN],
            credential_ref=SECRET_REF,
            reason="approved",
        )
        result = broker.call_tool(
            tenant_id="acme",
            actor="admin",
            connection_id=record["id"],
            tool_name=TOOL,
            arguments={"limit": 5},
        )
        self.assertEqual(result["tool"], TOOL)
        self.assertEqual(transport.last_credential, UPSTREAM_SECRET)
        audit = broker.audit(tenant_id="acme", actor="admin", connection_id=record["id"], limit=20)
        self.assertTrue(audit)
        self.assertNotIn(UPSTREAM_SECRET, repr(audit))
        self.assertNotIn(UPSTREAM_SECRET, repr(record))

    def test_broker_requires_transport_registration_validation(self) -> None:
        class MissingValidator:
            def health(self, endpoint: str, *, credential: str | None) -> str:
                del endpoint, credential
                return "healthy"

            def call_tool(
                self,
                endpoint: str,
                tool_name: str,
                arguments: dict[str, Any],
                *,
                credential: str | None,
            ) -> Any:
                del endpoint, tool_name, arguments, credential
                return {}

            def read_resource(
                self, endpoint: str, resource_uri: str, *, credential: str | None
            ) -> Any:
                del endpoint, resource_uri, credential
                return {}

        broker = ExternalMcpBroker(self.service, transport=MissingValidator())
        with self.assertRaisesRegex(ExternalMcpError, "cannot validate registration"):
            broker.register(
                tenant_id="acme",
                actor="admin",
                name="calendar",
                endpoint=ENDPOINT,
                approved_tools=[TOOL],
                approved_resources=[],
                allowed_origins=[ORIGIN],
                credential_ref=None,
                reason="approved",
            )

    def test_broker_rejects_malformed_registration_with_stable_error(self) -> None:
        broker = ExternalMcpBroker(self.service, transport=FakeTransport())
        cases = (
            {"approved_tools": None},
            {"approved_tools": [object()]},
            {"name": None},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                values: dict[str, Any] = {
                    "tenant_id": "acme",
                    "actor": "admin",
                    "name": "malformed",
                    "endpoint": ENDPOINT,
                    "approved_tools": [TOOL],
                    "approved_resources": [],
                    "allowed_origins": [ORIGIN],
                    "credential_ref": None,
                    "reason": "approved",
                }
                values.update(overrides)
                with self.assertRaisesRegex(
                    ExternalMcpError, "external MCP registration is invalid"
                ) as raised:
                    broker.register(**values)
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)

    def test_broker_sanitizes_transport_registration_error(self) -> None:
        secret = "registration-transport-secret"
        transport = FakeTransport()
        transport.validation_error_secret = secret
        broker = ExternalMcpBroker(self.service, transport=transport)
        with self.assertRaisesRegex(ExternalMcpError, "endpoint validation failed") as raised:
            broker.register(
                tenant_id="acme",
                actor="admin",
                name="calendar",
                endpoint=ENDPOINT,
                approved_tools=[TOOL],
                approved_resources=[],
                allowed_origins=[ORIGIN],
                credential_ref=None,
                reason="approved",
            )
        self.assertNotIn(secret, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_registration_validation_rejects_secret_bearing_ports_without_context(self) -> None:
        for endpoint, origin in (
            ("https://x.example:registration-secret/mcp", "https://x.example"),
            (ENDPOINT, "https://x.example:origin-secret"),
        ):
            with self.subTest(endpoint=endpoint, origin=origin):
                with self.assertRaisesRegex(FolioError, "origin is invalid") as raised:
                    self.service.register_external_connection(
                        tenant_id="acme",
                        actor="admin",
                        name="invalid-port",
                        endpoint=endpoint,
                        approved_tools=[TOOL],
                        approved_resources=[],
                        allowed_origins=[origin],
                    )
                self.assertNotIn("secret", str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)

    def test_audit_failures_are_bounded_and_sanitized(self) -> None:
        transport = FakeTransport()
        broker = ExternalMcpBroker(self.service, credentials=FakeCredentials(), transport=transport)
        record = broker.register(
            tenant_id="acme",
            actor="admin",
            name="audit-failure",
            endpoint=ENDPOINT,
            approved_tools=[TOOL],
            approved_resources=[RESOURCE],
            allowed_origins=[ORIGIN],
            credential_ref=SECRET_REF,
            reason="approved",
        )
        calls = 0

        def fail_audit(*args: Any, **kwargs: Any) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("sqlite audit secret")

        self.service.record_external_call = fail_audit  # type: ignore[method-assign]
        transport.transport_error_secret = "transport-secret"
        with self.assertRaisesRegex(ExternalMcpError, "tool call failed") as raised:
            broker.call_tool(
                tenant_id="acme",
                actor="admin",
                connection_id=record["id"],
                tool_name=TOOL,
                arguments={},
            )
        self.assertEqual(calls, 1)
        self.assertNotIn("sqlite audit secret", str(raised.exception))
        self.assertNotIn("transport-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        with self.assertRaisesRegex(ExternalMcpError, "resource read failed") as raised:
            broker.read_resource(
                tenant_id="acme",
                actor="admin",
                connection_id=record["id"],
                resource_uri=RESOURCE,
            )
        self.assertEqual(calls, 2)
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_health_persistence_failure_is_sanitized(self) -> None:
        transport = FakeTransport()
        broker = ExternalMcpBroker(self.service, credentials=FakeCredentials(), transport=transport)
        record = broker.register(
            tenant_id="acme",
            actor="admin",
            name="health-audit-failure",
            endpoint=ENDPOINT,
            approved_tools=[TOOL],
            approved_resources=[],
            allowed_origins=[ORIGIN],
            credential_ref=SECRET_REF,
            reason="approved",
        )
        calls = 0

        def fail_health(*args: Any, **kwargs: Any) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("sqlite health secret")

        self.service.record_external_health = fail_health  # type: ignore[method-assign]
        with self.assertRaisesRegex(ExternalMcpError, "health check failed") as raised:
            broker.health(
                tenant_id="acme",
                actor="admin",
                connection_id=record["id"],
                probe=True,
            )
        self.assertEqual(calls, 1)
        self.assertNotIn("sqlite health secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_registration_audit_failure_is_sanitized_and_rolls_back(self) -> None:
        transport = FakeTransport()
        broker = ExternalMcpBroker(self.service, credentials=FakeCredentials(), transport=transport)

        def fail_audit(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("registration audit secret")

        self.service._write_external_audit = fail_audit  # type: ignore[method-assign]
        with self.assertRaisesRegex(FolioError, "registration failed") as raised:
            broker.register(
                tenant_id="acme",
                actor="admin",
                name="registration-audit-failure",
                endpoint=ENDPOINT,
                approved_tools=[TOOL],
                approved_resources=[],
                allowed_origins=[ORIGIN],
            )
        self.assertNotIn("registration audit secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        self.assertEqual(self.service.list_external_connections("acme", actor="admin"), [])

    def test_generic_policy_requires_mapping_provider_payloads(self) -> None:
        with self.assertRaisesRegex(FolioError, "policy is invalid") as raised:
            self.service.register_external_connection(
                tenant_id="acme",
                actor="admin",
                name="non-mapping-policy",
                endpoint=ENDPOINT,
                approved_tools=[TOOL],
                approved_resources=[],
                allowed_origins=[ORIGIN],
                policy={"slack": "policy-secret"},  # type: ignore[dict-item]
            )
        self.assertNotIn("policy-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_revoke_wins_after_authorization_before_transport_start(self) -> None:
        started = threading.Event()
        release = threading.Event()
        transport = FakeTransport()
        transport_calls: list[str] = []

        class BlockingCredentials:
            def issue(self, **kwargs: str) -> str:
                del kwargs
                started.set()
                if not release.wait(2):
                    raise RuntimeError("credential test timed out")
                return UPSTREAM_SECRET

        original_call = transport.call_tool

        def count_call(*args: Any, **kwargs: Any) -> Any:
            transport_calls.append("started")
            return original_call(*args, **kwargs)

        transport.call_tool = count_call  # type: ignore[method-assign]
        broker = ExternalMcpBroker(
            self.service, credentials=BlockingCredentials(), transport=transport
        )
        record = broker.register(
            tenant_id="acme",
            actor="admin",
            name="race",
            endpoint=ENDPOINT,
            approved_tools=[TOOL],
            approved_resources=[],
            allowed_origins=[ORIGIN],
            credential_ref=SECRET_REF,
            reason="approved",
        )
        result: list[BaseException] = []

        def call() -> None:
            try:
                broker.call_tool(
                    tenant_id="acme",
                    actor="admin",
                    connection_id=record["id"],
                    tool_name=TOOL,
                    arguments={},
                )
            except BaseException as exc:  # pragma: no branch - assertion below
                result.append(exc)

        worker = threading.Thread(target=call)
        worker.start()
        self.assertTrue(started.wait(2))
        revoked = broker.revoke(
            tenant_id="acme", actor="admin", connection_id=record["id"], reason="race test"
        )
        self.assertEqual(revoked["status"], "revoked")
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(transport_calls, [])
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], ExternalMcpError)
        self.assertIn("revoked", str(result[0]))

    def test_revoke_waits_for_bounded_transport_fence(self) -> None:
        started = threading.Event()
        release = threading.Event()
        transport = FakeTransport()
        transport_calls: list[str] = []

        def blocking_call(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            transport_calls.append("started")
            started.set()
            self.assertTrue(release.wait(10))
            return {"ok": True}

        transport.call_tool = blocking_call  # type: ignore[method-assign]
        broker = ExternalMcpBroker(self.service, credentials=FakeCredentials(), transport=transport)
        record = broker.register(
            tenant_id="acme",
            actor="admin",
            name="slow-revoke",
            endpoint=ENDPOINT,
            approved_tools=[TOOL],
            approved_resources=[],
            allowed_origins=[ORIGIN],
            credential_ref=SECRET_REF,
            reason="approved",
        )
        call_result: list[Any] = []

        def call() -> None:
            try:
                call_result.append(
                    broker.call_tool(
                        tenant_id="acme",
                        actor="admin",
                        connection_id=record["id"],
                        tool_name=TOOL,
                        arguments={},
                    )
                )
            except BaseException as exc:  # pragma: no cover - assertion below
                call_result.append(exc)

        worker = threading.Thread(target=call)
        worker.start()
        self.assertTrue(started.wait(2))
        revoke_result: list[Any] = []
        revoke_done = threading.Event()

        def revoke() -> None:
            try:
                revoke_result.append(
                    broker.revoke(
                        tenant_id="acme",
                        actor="admin",
                        connection_id=record["id"],
                        reason="slow revoke",
                    )
                )
            except BaseException as exc:  # pragma: no cover - assertion below
                revoke_result.append(exc)
            finally:
                revoke_done.set()

        revoker = threading.Thread(target=revoke)
        revoker.start()
        self.assertFalse(revoke_done.wait(5.5))
        release.set()
        worker.join(2)
        revoker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(revoker.is_alive())
        self.assertEqual(call_result, [{"ok": True}])
        self.assertEqual(transport_calls, ["started"])
        self.assertEqual(len(revoke_result), 1)
        self.assertEqual(revoke_result[0]["status"], "revoked")

    def test_broker_rejects_credential_echo_from_upstream(self) -> None:
        credentials = FakeCredentials()
        transport = FakeTransport()
        transport.echo_credential = True
        broker = ExternalMcpBroker(self.service, credentials=credentials, transport=transport)
        record = broker.register(
            tenant_id="acme",
            actor="admin",
            name="calendar",
            endpoint=ENDPOINT,
            approved_tools=[TOOL],
            approved_resources=[],
            allowed_origins=[ORIGIN],
            credential_ref=SECRET_REF,
            reason="approved",
        )
        with self.assertRaisesRegex(FolioError, "credential material"):
            broker.call_tool(
                tenant_id="acme",
                actor="admin",
                connection_id=record["id"],
                tool_name=TOOL,
                arguments={},
            )
        self.assertNotIn(
            UPSTREAM_SECRET,
            repr(
                broker.audit(tenant_id="acme", actor="admin", connection_id=record["id"], limit=20)
            ),
        )

    def test_broker_rejects_decoded_credential_echoes_for_tool_and_resource(self) -> None:
        for index, secret in enumerate(("sécret", 'sec"ret', "sec\\ret", "sec\nret")):
            with self.subTest(secret=repr(secret)):
                credentials = FakeCredentials(secret)
                transport = FakeTransport()
                transport.echo_credential = True
                transport.echo_encoded_credential = True
                broker = ExternalMcpBroker(
                    self.service, credentials=credentials, transport=transport
                )
                record = broker.register(
                    tenant_id="acme",
                    actor="admin",
                    name=f"calendar-{index}",
                    endpoint=ENDPOINT,
                    approved_tools=[TOOL],
                    approved_resources=[RESOURCE],
                    allowed_origins=[ORIGIN],
                    credential_ref=SECRET_REF,
                    reason="approved",
                )
                for operation in ("tool", "resource"):
                    with self.subTest(operation=operation):
                        with self.assertRaisesRegex(
                            ExternalMcpError, "credential material"
                        ) as raised:
                            if operation == "tool":
                                broker.call_tool(
                                    tenant_id="acme",
                                    actor="admin",
                                    connection_id=record["id"],
                                    tool_name=TOOL,
                                    arguments={},
                                )
                            else:
                                broker.read_resource(
                                    tenant_id="acme",
                                    actor="admin",
                                    connection_id=record["id"],
                                    resource_uri=RESOURCE,
                                )
                        self.assertNotIn(secret, str(raised.exception))
                        self.assertIsNone(raised.exception.__cause__)
                        self.assertIsNone(raised.exception.__context__)
                audit = broker.audit(
                    tenant_id="acme", actor="admin", connection_id=record["id"], limit=20
                )
                self.assertNotIn(secret, repr(audit))

    def test_broker_rejects_percent_encoded_credential_echoes(self) -> None:
        secret = "percent secret/credential"
        credentials = FakeCredentials(secret)
        transport = FakeTransport()
        broker = ExternalMcpBroker(self.service, credentials=credentials, transport=transport)
        record = broker.register(
            tenant_id="acme",
            actor="admin",
            name="percent-encoded",
            endpoint=ENDPOINT,
            approved_tools=[TOOL],
            approved_resources=[RESOURCE],
            allowed_origins=[ORIGIN],
            credential_ref=SECRET_REF,
            reason="approved",
        )

        encoded = quote(secret, safe="")
        transport.call_tool = lambda *args, **kwargs: {"credential": encoded}  # type: ignore[method-assign]
        transport.read_resource = lambda *args, **kwargs: {  # type: ignore[method-assign]
            "credential": encoded
        }
        for operation in ("tool", "resource"):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ExternalMcpError, "credential material") as raised:
                    if operation == "tool":
                        broker.call_tool(
                            tenant_id="acme",
                            actor="admin",
                            connection_id=record["id"],
                            tool_name=TOOL,
                            arguments={},
                        )
                    else:
                        broker.read_resource(
                            tenant_id="acme",
                            actor="admin",
                            connection_id=record["id"],
                            resource_uri=RESOURCE,
                        )
                self.assertNotIn(secret, str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)

    def test_credential_scan_is_bounded_and_fails_closed_after_decode_budget(self) -> None:
        secret = "bounded-secret/credential"
        encoded = secret
        for decode_pass in range(MAX_CREDENTIAL_DECODE_PASSES + 2):
            with self.subTest(decode_pass=decode_pass):
                self.assertTrue(ExternalMcpBroker._contains_credential(encoded, secret))
            encoded = quote(encoded, safe="")

        benign = quote(quote("ordinary encoded content", safe=""), safe="")
        self.assertFalse(ExternalMcpBroker._contains_credential(benign, secret))

        exhausted_benign = quote(benign, safe="")
        self.assertTrue(ExternalMcpBroker._contains_credential(exhausted_benign, secret))

    def test_broker_sanitizes_transport_error_message_and_cause(self) -> None:
        for operation in ("tool", "resource", "health"):
            with self.subTest(operation=operation):
                secret = f"transport-secret-{operation}"
                transport = FakeTransport()
                transport.transport_error_secret = secret
                broker = ExternalMcpBroker(
                    self.service, credentials=FakeCredentials(secret), transport=transport
                )
                record = broker.register(
                    tenant_id="acme",
                    actor="admin",
                    name=f"failing-{operation}",
                    endpoint=ENDPOINT,
                    approved_tools=[TOOL],
                    approved_resources=[RESOURCE],
                    allowed_origins=[ORIGIN],
                    credential_ref=SECRET_REF,
                    reason="approved",
                )
                expected = {
                    "tool": "tool call failed",
                    "resource": "resource read failed",
                    "health": "health check failed",
                }[operation]
                with self.assertRaisesRegex(ExternalMcpError, expected) as raised:
                    if operation == "tool":
                        broker.call_tool(
                            tenant_id="acme",
                            actor="admin",
                            connection_id=record["id"],
                            tool_name=TOOL,
                            arguments={},
                        )
                    elif operation == "resource":
                        broker.read_resource(
                            tenant_id="acme",
                            actor="admin",
                            connection_id=record["id"],
                            resource_uri=RESOURCE,
                        )
                    else:
                        broker.health(
                            tenant_id="acme",
                            actor="admin",
                            connection_id=record["id"],
                            probe=True,
                        )
                self.assertNotIn(secret, str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)
                audit = broker.audit(
                    tenant_id="acme", actor="admin", connection_id=record["id"], limit=20
                )
                self.assertNotIn(secret, repr(audit))

    def test_cross_tenant_connection_is_not_resolvable(self) -> None:
        record = self.register(tenant_id="acme")
        self.assertEqual(self.service.list_external_connections("other", actor="other"), [])
        with self.assertRaisesRegex(FolioError, "connection not found"):
            self.service.external_connection_status("other", record["id"], actor="other")
        with self.assertRaisesRegex(FolioError, "connection not found"):
            self.service.revoke_external_connection(
                "other", record["id"], actor="other", reason="attack"
            )


class ExternalMcpHttpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = FolioLattice(root / "folio.db", root / "blobs")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_transport_timeout_has_an_upper_bound(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the allowed bound"):
            HttpExternalMcpTransport(timeout_seconds=30.1)

    @staticmethod
    def public_dns(host: str, port: int, **kwargs: Any) -> list[Any]:
        del host, kwargs
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    def test_resolver_passes_tenant_connection_and_audience_to_store(self) -> None:
        store = FakeSecretStore()
        resolver = CredentialResolver(store)
        self.assertEqual(
            resolver.issue(
                tenant_id="acme",
                connection_id="connection-1",
                credential_ref=SECRET_REF,
                audience=ORIGIN,
            ),
            UPSTREAM_SECRET,
        )
        self.assertEqual(
            store.requests,
            [
                {
                    "tenant_id": "acme",
                    "connection_id": "connection-1",
                    "credential_ref": SECRET_REF,
                    "audience": ORIGIN,
                }
            ],
        )

    def test_http_transport_calls_upstream_tool_and_resource(self) -> None:
        upstream = MockUpstream()
        transport = HttpExternalMcpTransport(
            dns_resolver=self.public_dns,
            http_transport=upstream_httpx.MockTransport(upstream),
        )
        broker = ExternalMcpBroker(
            self.service,
            credentials=CredentialResolver(FakeSecretStore()),
            transport=transport,
        )
        record = broker.register(
            tenant_id="acme",
            actor="admin",
            name="calendar",
            endpoint=ENDPOINT,
            approved_tools=[TOOL],
            approved_resources=[RESOURCE],
            allowed_origins=[ORIGIN],
            credential_ref=SECRET_REF,
            reason="approved",
        )

        tool_result = broker.call_tool(
            tenant_id="acme",
            actor="admin",
            connection_id=record["id"],
            tool_name=TOOL,
            arguments={"limit": 5},
        )
        resource_result = broker.read_resource(
            tenant_id="acme",
            actor="admin",
            connection_id=record["id"],
            resource_uri=RESOURCE,
        )

        self.assertEqual(tool_result, {"value": "tool-ok"})
        self.assertIn("resource-ok", repr(resource_result))
        self.assertEqual(
            [request["method"] for request in upstream.requests],
            [
                "initialize",
                "notifications/initialized",
                "tools/call",
                "tools/list",
                "initialize",
                "notifications/initialized",
                "resources/read",
            ],
        )

    def test_transport_rejects_private_and_rebinding_dns_answers(self) -> None:
        def private_dns(host: str, port: int, **kwargs: Any) -> list[Any]:
            del host, kwargs
            return [(2, 1, 6, "", ("10.0.0.1", port))]

        def rebinding_dns(host: str, port: int, **kwargs: Any) -> list[Any]:
            del host, kwargs
            return [
                (2, 1, 6, "", ("93.184.216.34", port)),
                (2, 1, 6, "", ("127.0.0.1", port)),
            ]

        with self.assertRaisesRegex(ExternalMcpError, "blocked address"):
            HttpExternalMcpTransport(dns_resolver=private_dns)._resolve_endpoint(ENDPOINT)
        with self.assertRaisesRegex(ExternalMcpError, "blocked address"):
            HttpExternalMcpTransport(dns_resolver=rebinding_dns)._resolve_endpoint(ENDPOINT)
        with self.assertRaisesRegex(ExternalMcpError, "blocked address"):
            HttpExternalMcpTransport()._resolve_endpoint("https://127.0.0.1/mcp")

    def test_registration_rejects_localhost_and_non_global_dns_before_persisting(self) -> None:
        def private_dns(host: str, port: int, **kwargs: Any) -> list[Any]:
            del host, kwargs
            return [(2, 1, 6, "", ("10.0.0.1", port))]

        transport = HttpExternalMcpTransport(dns_resolver=self.public_dns)
        broker = ExternalMcpBroker(self.service, transport=transport)
        for name, endpoint, origin in (
            ("localhost", "https://localhost/mcp", "https://localhost"),
            ("literal", "https://127.0.0.1/mcp", "https://127.0.0.1"),
        ):
            with self.assertRaisesRegex(ExternalMcpError, "blocked address"):
                broker.register(
                    tenant_id="acme",
                    actor="admin",
                    name=name,
                    endpoint=endpoint,
                    approved_tools=[TOOL],
                    approved_resources=[],
                    allowed_origins=[origin],
                    credential_ref=None,
                    reason="approved",
                )

        private_broker = ExternalMcpBroker(
            self.service,
            transport=HttpExternalMcpTransport(dns_resolver=private_dns),
        )
        with self.assertRaisesRegex(ExternalMcpError, "blocked address"):
            private_broker.register(
                tenant_id="acme",
                actor="admin",
                name="private",
                endpoint=ENDPOINT,
                approved_tools=[TOOL],
                approved_resources=[],
                allowed_origins=[ORIGIN],
                credential_ref=None,
                reason="approved",
            )
        with self.service.connect() as db:
            self.assertIsNone(
                db.execute(
                    "SELECT id FROM external_mcp_connections WHERE name = ?", ("private",)
                ).fetchone()
            )

    def test_registration_dns_resolution_is_bounded(self) -> None:
        def slow_dns(host: str, port: int, **kwargs: Any) -> list[Any]:
            del host, port, kwargs
            time.sleep(0.05)
            return []

        transport = HttpExternalMcpTransport(timeout_seconds=0.001, dns_resolver=slow_dns)
        with self.assertRaisesRegex(ExternalMcpError, "resolution timed out"):
            transport.validate_registration(ENDPOINT)

    def test_transport_bounds_timeout_and_response_size(self) -> None:
        async def slow_upstream(request: Any) -> Any:
            del request
            await asyncio.sleep(0.05)
            return upstream_httpx.Response(200)

        timeout_transport = HttpExternalMcpTransport(
            timeout_seconds=0.001,
            dns_resolver=self.public_dns,
            http_transport=upstream_httpx.MockTransport(slow_upstream),
        )
        with self.assertRaisesRegex(ExternalMcpError, "timed out"):
            timeout_transport.health(ENDPOINT, credential=None)

        def slow_dns(host: str, port: int, **kwargs: Any) -> list[Any]:
            time.sleep(0.05)
            return self.public_dns(host, port, **kwargs)

        dns_timeout_transport = HttpExternalMcpTransport(
            timeout_seconds=0.001,
            dns_resolver=slow_dns,
            http_transport=upstream_httpx.MockTransport(MockUpstream()),
        )
        with self.assertRaisesRegex(ExternalMcpError, "timed out"):
            dns_timeout_transport.health(ENDPOINT, credential=None)

        oversized = upstream_httpx.MockTransport(
            lambda request: upstream_httpx.Response(
                200,
                headers={"content-length": "33"},
                content=b"oversized",
            )
        )
        oversized_transport = HttpExternalMcpTransport(
            max_response_bytes=32,
            dns_resolver=self.public_dns,
            http_transport=oversized,
        )
        with self.assertRaisesRegex(ExternalMcpError, "exceeds the allowed size"):
            oversized_transport.health(ENDPOINT, credential=None)


class ExternalMcpPublicContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = FolioLattice(root / "folio.db", root / "blobs")
        self.broker = ExternalMcpBroker(
            self.service, credentials=FakeCredentials(), transport=FakeTransport()
        )
        self.server = build_mcp_server(
            self.service, tenant_id="acme", actor="admin", external_broker=self.broker
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def payload(response: Any) -> Any:
        structured = response.structured_content
        assert structured is not None
        return structured.get("result", structured) if isinstance(structured, dict) else structured

    async def test_admin_contract_and_non_admin_deny(self) -> None:
        async with Client(self.server) as client:
            created = await client.call_tool(
                "external_mcp_connection_register",
                {
                    "name": "calendar",
                    "endpoint": ENDPOINT,
                    "approved_tools": [TOOL],
                    "approved_resources": [RESOURCE],
                    "allowed_origins": [ORIGIN],
                    "credential_ref": SECRET_REF,
                },
            )
            self.assertFalse(created.is_error, created)
            record = self.payload(created)
            self.assertNotIn("credential_ref", record)
            connection_id = record["id"]
            listed = await client.call_tool("external_mcp_connection_list", {})
            self.assertFalse(listed.is_error, listed)
            listed_records = self.payload(listed)
            self.assertEqual(listed_records[0]["id"], connection_id)
            healthy = await client.call_tool(
                "external_mcp_connection_status", {"connection_id": connection_id, "probe": True}
            )
            self.assertFalse(healthy.is_error, healthy)
            self.assertEqual(self.payload(healthy)["health_status"], "healthy")
            allowed_tool = await client.call_tool(
                "external_mcp_tool_call",
                {
                    "connection_id": connection_id,
                    "tool_name": TOOL,
                    "arguments": {"limit": 5},
                },
            )
            self.assertFalse(allowed_tool.is_error, allowed_tool)
            self.assertEqual(self.payload(allowed_tool)["tool"], TOOL)
            allowed_resource = await client.call_tool(
                "external_mcp_resource_read",
                {"connection_id": connection_id, "resource_uri": RESOURCE},
            )
            self.assertFalse(allowed_resource.is_error, allowed_resource)
            self.assertEqual(self.payload(allowed_resource)["resource"], RESOURCE)
            denied = await client.call_tool(
                "external_mcp_tool_call",
                {
                    "connection_id": connection_id,
                    "tool_name": "calendar.events.delete",
                    "arguments": {},
                },
            )
            self.assertTrue(denied.is_error)
            self.assertIn("tool is not approved", denied.content[0].text)
            revoked = await client.call_tool(
                "external_mcp_connection_revoke",
                {"connection_id": connection_id, "reason": "retired"},
            )
            self.assertFalse(revoked.is_error, revoked)
            self.assertEqual(self.payload(revoked)["status"], "revoked")
            audit = await client.call_tool(
                "external_mcp_audit", {"connection_id": connection_id, "limit": 20}
            )
            self.assertFalse(audit.is_error, audit)
            audit_payload = self.payload(audit)
            self.assertNotIn(SECRET_REF, repr(audit_payload))
            self.assertNotIn(UPSTREAM_SECRET, repr(audit_payload))

        principal_token = set_request_principal(
            Principal("acme", "reader", "https://issuer.example", "reader", frozenset())
        )
        try:
            async with Client(build_mcp_server(self.service)) as client:
                denied = await client.call_tool("external_mcp_connection_list", {})
                self.assertTrue(denied.is_error)
                self.assertIn("operation not permitted", denied.content[0].text)
        finally:
            reset_request_principal(principal_token)

    async def test_cross_tenant_status_is_not_found(self) -> None:
        async with Client(self.server) as client:
            response = await client.call_tool(
                "external_mcp_connection_register",
                {
                    "name": "calendar",
                    "endpoint": ENDPOINT,
                    "approved_tools": [TOOL],
                    "approved_resources": [],
                    "allowed_origins": [ORIGIN],
                },
            )
            created = self.payload(response)
            connection_id = created["id"]
        principal_token = set_request_principal(
            Principal(
                "other",
                "other",
                "https://issuer.example",
                "other",
                frozenset({"tenant:admin"}),
            )
        )
        try:
            async with Client(build_mcp_server(self.service)) as client:
                denied = await client.call_tool(
                    "external_mcp_connection_status", {"connection_id": connection_id}
                )
                self.assertTrue(denied.is_error)
                self.assertIn("connection not found", denied.content[0].text)
        finally:
            reset_request_principal(principal_token)
