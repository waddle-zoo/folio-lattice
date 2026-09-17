import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp import Client

from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
from folio_lattice.external_mcp import ExternalMcpBroker
from folio_lattice.mcp_protocol import build_mcp_server
from folio_lattice.service import FolioError, FolioLattice
from folio_lattice.slack import ApprovedSlackConsumer, AuthenticatedSlackEdge

ENDPOINT = "https://slack.fixture.example/mcp"
ORIGIN = "https://slack.fixture.example"
SECRET = "slack-fixture-secret"
START = "2026-01-01T00:00:00Z"
END = "2026-01-02T00:00:00Z"


class FakeCredentials:
    def __init__(self, secret: str = SECRET) -> None:
        self.secret = secret

    def issue(self, **kwargs: str) -> str:
        self.last_request = kwargs
        return self.secret


class SeededSlackTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response_override: Any | None = None
        self.echo_credential = False

    def validate_registration(self, endpoint: str) -> None:
        if endpoint != ENDPOINT:
            raise AssertionError(f"unexpected endpoint: {endpoint}")

    def health(self, endpoint: str, *, credential: str | None) -> str:
        assert endpoint == ENDPOINT
        assert isinstance(credential, str) and credential
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
        assert isinstance(credential, str) and credential
        self.calls.append({"tool": tool_name, "arguments": dict(arguments)})
        if tool_name != "slack.search":
            raise AssertionError(f"unapproved tool reached fixture: {tool_name}")
        if self.echo_credential:
            return {
                "messages": [
                    {
                        "id": "secret",
                        "channel": "#deployments",
                        "timestamp": "2026-01-01T12:00:00Z",
                        "text": credential,
                    }
                ]
            }
        if self.response_override is not None:
            return self.response_override
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
        self.consumer = ApprovedSlackConsumer(self.service, self.broker)
        self.edge = AuthenticatedSlackEdge(self.consumer)
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
                    "policy": {
                        "slack": {
                            "channels": ["#deployments"],
                            "max_time_range_seconds": 86_400,
                        }
                    },
                    "credential_ref": "secret://tenant-a/slack",
                },
            )
        self.assertFalse(response.is_error, response)
        record = self.payload(response)
        self.assertNotIn("secret://tenant-a/slack", repr(record))
        self.connection_id = record["id"]
        return self.connection_id

    def _member_search(self, **kwargs: Any) -> dict[str, Any]:
        token = set_request_principal(
            Principal(
                "tenant-a",
                "member-a",
                "https://issuer.example",
                "member-a",
                frozenset({"artifact:search"}),
            )
        )
        try:
            return self.edge.search(connection_id=kwargs.pop("connection_id"), **kwargs)
        finally:
            reset_request_principal(token)

    def _member_save(self, **kwargs: Any) -> dict[str, Any]:
        token = set_request_principal(
            Principal(
                "tenant-a",
                "member-a",
                "https://issuer.example",
                "member-a",
                frozenset({"artifact:search", "artifact:write"}),
            )
        )
        try:
            return self.edge.save(connection_id=kwargs.pop("connection_id"), **kwargs)
        finally:
            reset_request_principal(token)

    async def test_core_mcp_contract_has_no_provider_specific_tools(self) -> None:
        async with Client(self.admin_server) as client:
            listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        self.assertNotIn("slack_search", names)
        self.assertNotIn("slack_save", names)
        self.assertIn("external_mcp_tool_call", names)
        self.assertIn("artifact_create", names)
        with self.assertRaisesRegex(FolioError, "operation not permitted"):
            self.edge.search(
                connection_id="not-authenticated",
                channel="#deployments",
                start_time=START,
                end_time=END,
                query="renderer",
            )

    async def test_unknown_policy_keys_fail_closed_without_public_disclosure(self) -> None:
        with self.assertRaisesRegex(FolioError, "policy is invalid"):
            self.service.register_external_connection(
                tenant_id="tenant-a",
                actor="tenant-admin",
                name="unknown-policy",
                endpoint=ENDPOINT,
                approved_tools=["slack.search"],
                approved_resources=[],
                allowed_origins=[ORIGIN],
                policy={"unknown": {"secret": "policy-secret"}},
            )

        with self.assertRaisesRegex(FolioError, "policy is invalid"):
            ApprovedSlackConsumer._policy(
                {
                    "policy": {
                        "slack": {
                            "channels": ["#deployments"],
                            "max_time_range_seconds": 86_400,
                            "secret": "nested-policy-secret",
                        }
                    }
                }
            )
        with self.assertRaisesRegex(FolioError, "policy is invalid"):
            ApprovedSlackConsumer._policy(
                {
                    "policy": {
                        "slack": {
                            "channels": ["#deployments"],
                            "max_time_range_seconds": 86_400,
                        },
                        "unknown": "top-level-policy-secret",
                    }
                }
            )
        public = FolioLattice._external_public(
            {
                "id": "connection",
                "credential_ref": "secret://tenant-a/slack",
                "future_secret": "public-secret",
                "policy_json": '{"slack":{"channels":["#deployments"],"max_time_range_seconds":86400,"secret":"policy-secret"},"unknown":"generic-secret"}',
            }
        )
        self.assertNotIn("public-secret", repr(public))
        self.assertNotIn("policy-secret", repr(public))
        self.assertNotIn("generic-secret", repr(public))
        self.assertNotIn("credential_ref", public)

    async def test_approved_search_save_provenance_and_revoke(self) -> None:
        connection_id = await self._register()
        arguments = {
            "channel": "#deployments",
            "start_time": START,
            "end_time": END,
            "query": "renderer",
            "limit": 10,
        }
        result = self._member_search(connection_id=connection_id, **arguments)
        self.assertEqual(result["provider"], "slack")
        self.assertEqual(result["connection_id"], connection_id)
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["messages"][0]["id"], "msg-1")
        self.assertNotIn(SECRET, repr(result))

        saved_result = self._member_save(
            connection_id=connection_id,
            name="renderer-search.md",
            **arguments,
        )
        source = saved_result["version"]["source_context"]
        self.assertEqual(source["provider"], "slack")
        self.assertEqual(source["connection_id"], connection_id)
        self.assertEqual(source["channel"], "#deployments")
        self.assertEqual(source["query_hash"], hashlib.sha256(b"renderer").hexdigest())
        self.assertEqual(source["message_ids"], ["msg-1"])
        self.assertEqual(
            source,
            {
                "interface": "approved-slack-search",
                "provider": "slack",
                "connection_id": connection_id,
                "channel": "#deployments",
                "start_time": START,
                "end_time": END,
                "query_hash": hashlib.sha256(b"renderer").hexdigest(),
                "policy_version": "external-mcp-v1",
                "message_ids": ["msg-1"],
            },
        )
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
        with self.assertRaisesRegex(FolioError, "revoked"):
            self._member_search(connection_id=connection_id, **arguments)
        self.assertEqual(len(self.transport.calls), calls_before)
        self.assertTrue(all(call["tool"] == "slack.search" for call in self.transport.calls))

    async def test_tenant_isolation_and_bounds_fail_closed(self) -> None:
        connection_id = await self._register()
        with self.assertRaisesRegex(FolioError, "connection not found"):
            token = set_request_principal(
                Principal(
                    "tenant-b",
                    "member-b",
                    "https://issuer.example",
                    "member-b",
                    frozenset({"artifact:search"}),
                )
            )
            try:
                self.edge.search(
                    connection_id=connection_id,
                    channel="#deployments",
                    start_time=START,
                    end_time=END,
                    query="renderer",
                )
            finally:
                reset_request_principal(token)

        with self.assertRaisesRegex(FolioError, "approved maximum"):
            self._member_search(
                connection_id=connection_id,
                channel="#deployments",
                start_time=START,
                end_time="2026-02-15T00:00:00Z",
                query="renderer",
            )

        with self.assertRaisesRegex(FolioError, "channel is invalid"):
            self._member_search(
                connection_id=connection_id,
                channel="#deploy ments",
                start_time=START,
                end_time=END,
                query="renderer",
            )
        self.assertEqual(self.transport.calls, [])

    async def test_channel_and_time_policy_fail_before_transport(self) -> None:
        connection_id = await self._register()
        with self.assertRaisesRegex(FolioError, "channel is not approved"):
            self._member_search(
                connection_id=connection_id,
                channel="#private",
                start_time=START,
                end_time=END,
                query="renderer",
            )
        with self.assertRaisesRegex(FolioError, "approved maximum"):
            self._member_search(
                connection_id=connection_id,
                channel="#deployments",
                start_time=START,
                end_time="2026-01-03T00:00:00Z",
                query="renderer",
            )
        self.assertEqual(self.transport.calls, [])

    async def test_save_requires_search_and_write_authority(self) -> None:
        connection_id = await self._register()
        arguments = {
            "connection_id": connection_id,
            "name": "unauthorized.md",
            "channel": "#deployments",
            "start_time": START,
            "end_time": END,
            "query": "renderer",
        }
        for scopes in ({"artifact:write"}, {"artifact:search"}):
            token = set_request_principal(
                Principal(
                    "tenant-a",
                    "member-a",
                    "https://issuer.example",
                    "member-a",
                    frozenset(scopes),
                )
            )
            try:
                with self.assertRaisesRegex(FolioError, "operation not permitted"):
                    self.edge.save(**arguments)
            finally:
                reset_request_principal(token)
        self.assertEqual(self.transport.calls, [])

    async def test_empty_results_are_a_valid_bounded_search(self) -> None:
        connection_id = await self._register()
        self.transport.response_override = {"messages": []}
        searched = self._member_search(
            connection_id=connection_id,
            channel="#deployments",
            start_time=START,
            end_time=END,
            query="no-match",
            limit=1,
        )
        self.assertEqual(searched["messages"], [])
        self.assertEqual(searched["match_count"], 0)

    async def test_upstream_limit_malformed_and_oversize_fail_before_save(self) -> None:
        connection_id = await self._register()
        arguments = {
            "name": "bounded.md",
            "channel": "#deployments",
            "start_time": START,
            "end_time": END,
            "query": "renderer",
            "limit": 1,
        }
        self.transport.response_override = {
            "messages": [
                {
                    "id": "msg-1",
                    "channel": "#deployments",
                    "timestamp": "2026-01-01T12:00:00Z",
                    "text": "first",
                },
                {
                    "id": "msg-2",
                    "channel": "#deployments",
                    "timestamp": "2026-01-01T12:01:00Z",
                    "text": "second",
                },
            ]
        }
        before = self.service.list_artifacts("tenant-a", actor="member-a")
        with self.assertRaisesRegex(FolioError, "more messages than requested"):
            self._member_save(connection_id=connection_id, **arguments)
        self.assertEqual(self.service.list_artifacts("tenant-a", actor="member-a"), before)

        self.transport.response_override = {"messages": [{"id": "broken"}]}
        with self.assertRaisesRegex(FolioError, "Slack message channel is invalid"):
            self._member_save(connection_id=connection_id, **arguments)
        self.assertEqual(self.service.list_artifacts("tenant-a", actor="member-a"), before)

        self.transport.response_override = {
            "messages": [
                {
                    "id": "huge",
                    "channel": "#deployments",
                    "timestamp": "2026-01-01T12:00:00Z",
                    "text": "x" * (1024 * 1024),
                }
            ]
        }
        with self.assertRaisesRegex(FolioError, "exceeds the allowed size"):
            self._member_save(connection_id=connection_id, **arguments)
        self.assertEqual(self.service.list_artifacts("tenant-a", actor="member-a"), before)
        audit = self.broker.audit(
            tenant_id="tenant-a", actor="admin", connection_id=connection_id, limit=20
        )
        self.assertIn(
            {"outcome": "failed", "reason": "slack_result_over_limit"},
            [{"outcome": row["outcome"], "reason": row["reason"]} for row in audit],
        )
        self.assertIn(
            {"outcome": "failed", "reason": "slack_result_invalid"},
            [{"outcome": row["outcome"], "reason": row["reason"]} for row in audit],
        )
        self.assertNotIn("renderer", repr(audit))

    async def test_upstream_credential_echo_is_rejected_before_save(self) -> None:
        connection_id = await self._register()
        self.transport.echo_credential = True
        arguments = {
            "connection_id": connection_id,
            "name": "secret.md",
            "channel": "#deployments",
            "start_time": START,
            "end_time": END,
            "query": "secret",
        }
        before = self.service.list_artifacts("tenant-a", actor="member-a")
        with self.assertRaisesRegex(FolioError, "credential material") as raised:
            self._member_save(**arguments)
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertEqual(self.service.list_artifacts("tenant-a", actor="member-a"), before)

    async def test_credential_variants_never_reach_artifact_or_exception(self) -> None:
        connection_id = await self._register()
        self.transport.echo_credential = True
        before = self.service.list_artifacts("tenant-a", actor="member-a")
        for secret in ("sécret", 'sec"ret', "sec\\ret", "sec\nret"):
            self.broker.credentials = FakeCredentials(secret)
            with self.subTest(secret=repr(secret)):
                with self.assertRaisesRegex(FolioError, "credential material") as raised:
                    self._member_save(
                        connection_id=connection_id,
                        name="variant.md",
                        channel="#deployments",
                        start_time=START,
                        end_time=END,
                        query="secret",
                    )
                self.assertNotIn(secret, str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertEqual(self.service.list_artifacts("tenant-a", actor="member-a"), before)
