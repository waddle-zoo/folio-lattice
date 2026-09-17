import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp import Client

from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
from folio_lattice.external_mcp import ExternalMcpBroker
from folio_lattice.mcp_protocol import build_mcp_server
from folio_lattice.service import FolioLattice

ENDPOINT = "https://slack.fixture.example/mcp"
ORIGIN = "https://slack.fixture.example"
SECRET = "slack-fixture-secret"
START = "2026-01-01T00:00:00Z"
END = "2026-01-02T00:00:00Z"


class FakeCredentials:
    def issue(self, **kwargs: str) -> str:
        self.last_request = kwargs
        return SECRET


class SeededSlackTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def validate_registration(self, endpoint: str) -> None:
        if endpoint != ENDPOINT:
            raise AssertionError(f"unexpected endpoint: {endpoint}")

    def health(self, endpoint: str, *, credential: str | None) -> str:
        assert endpoint == ENDPOINT
        assert credential == SECRET
        return "healthy"

    def call_tool(
        self,
        endpoint: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        credential: str | None,
    ) -> Any:
        assert endpoint == ENDPOINT
        assert credential == SECRET
        self.calls.append({"tool": tool_name, "arguments": dict(arguments)})
        if tool_name != "slack.search":
            raise AssertionError(f"unapproved tool reached fixture: {tool_name}")
        return {
            "messages": [
                {
                    "id": "msg-1",
                    "channel": arguments["channel"],
                    "timestamp": "2026-01-01T12:00:00Z",
                    "text": "deploy the renderer after the canary",
                },
                {
                    "id": "msg-1",
                    "channel": arguments["channel"],
                    "timestamp": "2026-01-01T12:00:00Z",
                    "text": "duplicate is deduplicated",
                },
            ],
        }

    def read_resource(self, endpoint: str, resource_uri: str, *, credential: str | None) -> Any:
        raise AssertionError(f"resource proxy reached fixture: {resource_uri}")


class ApprovedSlackContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = FolioLattice(root / "folio.db", root / "blobs")
        self.transport = SeededSlackTransport()
        self.broker = ExternalMcpBroker(
            self.service,
            credentials=FakeCredentials(),
            transport=self.transport,
        )
        self.admin_server = build_mcp_server(
            self.service,
            tenant_id="tenant-a",
            actor="tenant-admin",
            external_broker=self.broker,
        )
        self.connection_id: str | None = None

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def payload(response: Any) -> Any:
        value = response.structured_content
        assert value is not None
        return value.get("result", value) if isinstance(value, dict) else value

    async def _register(self) -> str:
        async with Client(self.admin_server) as client:
            response = await client.call_tool(
                "external_mcp_connection_register",
                {
                    "name": "approved-slack",
                    "endpoint": ENDPOINT,
                    "approved_tools": ["slack.search"],
                    "approved_resources": [],
                    "allowed_origins": [ORIGIN],
                    "credential_ref": "secret://tenant-a/slack",
                },
            )
        self.assertFalse(response.is_error, response)
        record = self.payload(response)
        self.assertNotIn("secret://tenant-a/slack", repr(record))
        self.connection_id = record["id"]
        return self.connection_id

    async def _member_call(self, tool: str, arguments: dict[str, Any]) -> Any:
        token = set_request_principal(
            Principal(
                "tenant-a",
                "member-a",
                "https://issuer.example",
                "member-a",
                frozenset({"artifact:read", "artifact:search", "artifact:write"}),
            )
        )
        try:
            async with Client(
                build_mcp_server(self.service, external_broker=self.broker)
            ) as client:
                return await client.call_tool(tool, arguments)
        finally:
            reset_request_principal(token)

    async def test_approved_search_save_provenance_and_revoke(self) -> None:
        connection_id = await self._register()
        arguments = {
            "connection_id": connection_id,
            "channel": "#deployments",
            "start_time": START,
            "end_time": END,
            "query": "renderer",
            "limit": 10,
        }
        searched = await self._member_call("slack_search", arguments)
        self.assertFalse(searched.is_error, searched)
        result = self.payload(searched)
        self.assertEqual(result["provider"], "slack")
        self.assertEqual(result["connection_id"], connection_id)
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["messages"][0]["id"], "msg-1")
        self.assertNotIn(SECRET, repr(result))

        saved = await self._member_call(
            "slack_save",
            {**arguments, "name": "renderer-search.md"},
        )
        self.assertFalse(saved.is_error, saved)
        saved_result = self.payload(saved)
        source = saved_result["version"]["source_context"]
        self.assertEqual(source["provider"], "slack")
        self.assertEqual(source["connection_id"], connection_id)
        self.assertEqual(source["channel"], "#deployments")
        self.assertEqual(source["query"], "renderer")
        self.assertEqual(source["message_ids"], ["msg-1"])
        self.assertEqual(saved_result["version"]["actor"], "member-a")
        self.assertNotIn(SECRET, repr(saved_result))

        token = set_request_principal(
            Principal(
                "tenant-a",
                "member-a",
                "https://issuer.example",
                "member-a",
                frozenset({"artifact:read", "artifact:search", "artifact:write"}),
            )
        )
        try:
            async with Client(
                build_mcp_server(self.service, external_broker=self.broker)
            ) as client:
                denied_admin = await client.call_tool("external_mcp_connection_list", {})
                self.assertTrue(denied_admin.is_error)
                self.assertIn("operation not permitted", denied_admin.content[0].text)
        finally:
            reset_request_principal(token)

        async with Client(self.admin_server) as client:
            revoked = await client.call_tool(
                "external_mcp_connection_revoke",
                {"connection_id": connection_id, "reason": "fixture cleanup"},
            )
        self.assertFalse(revoked.is_error, revoked)
        calls_before = len(self.transport.calls)
        revoked_search = await self._member_call("slack_search", arguments)
        self.assertTrue(revoked_search.is_error)
        self.assertIn("revoked", revoked_search.content[0].text)
        self.assertEqual(len(self.transport.calls), calls_before)
        self.assertTrue(all(call["tool"] == "slack.search" for call in self.transport.calls))

    async def test_tenant_isolation_and_bounds_fail_closed(self) -> None:
        connection_id = await self._register()
        other_token = set_request_principal(
            Principal(
                "tenant-b",
                "member-b",
                "https://issuer.example",
                "member-b",
                frozenset({"artifact:search"}),
            )
        )
        try:
            async with Client(
                build_mcp_server(self.service, external_broker=self.broker)
            ) as client:
                cross_tenant = await client.call_tool(
                    "slack_search",
                    {
                        "connection_id": connection_id,
                        "channel": "#deployments",
                        "start_time": START,
                        "end_time": END,
                        "query": "renderer",
                    },
                )
                self.assertTrue(cross_tenant.is_error)
                self.assertIn("connection not found", cross_tenant.content[0].text)
        finally:
            reset_request_principal(other_token)

        overlong = await self._member_call(
            "slack_search",
            {
                "connection_id": connection_id,
                "channel": "#deployments",
                "start_time": START,
                "end_time": "2026-02-15T00:00:00Z",
                "query": "renderer",
            },
        )
        self.assertTrue(overlong.is_error)
        self.assertIn("31 days", overlong.content[0].text)

        invalid_channel = await self._member_call(
            "slack_search",
            {
                "connection_id": connection_id,
                "channel": "#deploy ments",
                "start_time": START,
                "end_time": END,
                "query": "renderer",
            },
        )
        self.assertTrue(invalid_channel.is_error)
        self.assertIn("channel is invalid", invalid_channel.content[0].text)
        self.assertEqual(self.transport.calls, [])
