import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from mcp import Client

from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
from folio_lattice.external_mcp import ExternalMcpBroker, HttpExternalMcpTransport
from folio_lattice.mcp_protocol import build_mcp_server
from folio_lattice.service import FolioError, FolioLattice

ENDPOINT = "https://calendar.example/mcp"
ORIGIN = "https://calendar.example"
TOOL = "calendar.events.list"
RESOURCE = "calendar://events/today"
SECRET_REF = "secret://acme/calendar"
UPSTREAM_SECRET = "upstream-secret-must-not-escape"


class FakeCredentials:
    def issue(self, **kwargs: str) -> str:
        self.last_request = kwargs
        return UPSTREAM_SECRET


class FakeTransport:
    def __init__(self) -> None:
        self.last_credential: str | None = None
        self.echo_credential = False

    def health(self, endpoint: str, *, credential: str | None) -> str:
        self.last_credential = credential
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
        if self.echo_credential:
            return {"credential": credential}
        return {"endpoint": endpoint, "tool": tool_name, "arguments": arguments}

    def read_resource(self, endpoint: str, resource_uri: str, *, credential: str | None) -> Any:
        self.last_credential = credential
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
            value = await HttpExternalMcpTransport()._request(
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

    def test_cross_tenant_connection_is_not_resolvable(self) -> None:
        record = self.register(tenant_id="acme")
        self.assertEqual(self.service.list_external_connections("other", actor="other"), [])
        with self.assertRaisesRegex(FolioError, "connection not found"):
            self.service.external_connection_status("other", record["id"], actor="other")
        with self.assertRaisesRegex(FolioError, "connection not found"):
            self.service.revoke_external_connection(
                "other", record["id"], actor="other", reason="attack"
            )


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
