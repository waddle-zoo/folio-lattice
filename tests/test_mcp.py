import base64
import binascii
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
from folio_lattice.mcp_protocol import _tool_errors, build_mcp_server
from folio_lattice.service import FolioError, FolioLattice


class McpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = FolioLattice(root / "folio.db", root / "blobs")
        self.server = build_mcp_server(self.service, tenant_id="acme", actor="hyperset")

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def call(self, client: Client, name: str, arguments: dict[str, Any]) -> Any:
        response = await client.call_tool(name, arguments)
        self.assertFalse(response.is_error, response)
        structured = response.structured_content
        assert structured is not None
        return structured.get("result", structured)

    async def test_official_protocol_schema_and_provider_neutral_flow(self) -> None:
        async with Client(self.server, raise_exceptions=True) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            self.assertIn("artifact_list", names)
            self.assertIn("artifact_search", names)
            self.assertIn("graph_component", names)
            list_tool = next(tool for tool in listed.tools if tool.name == "artifact_list")
            search_tool = next(tool for tool in listed.tools if tool.name == "artifact_search")
            self.assertEqual(
                set(list_tool.input_schema.get("properties", {})),
                {"limit", "name", "media_type", "cursor"},
            )
            self.assertIn(
                "media type", search_tool.description.lower() if search_tool.description else ""
            )
            self.assertIn("cursor", search_tool.input_schema.get("properties", {}))
            for tool in listed.tools:
                properties = tool.input_schema.get("properties", {})
                self.assertNotIn("tenant_id", properties)
                self.assertNotIn("actor", properties)

            created = await self.call(
                client,
                "artifact_create",
                {
                    "name": "note.md",
                    "media_type": "text/markdown",
                    "content_base64": base64.b64encode(b"hello graph").decode(),
                    "reason": "test",
                },
            )
            self.assertEqual(created["artifact"]["tenant_id"], "acme")
            self.assertEqual(created["version"]["actor"], "hyperset")
            library = await self.call(client, "artifact_list", {})
            self.assertEqual([item["name"] for item in library], ["note.md"])
            self.assertIs(library[0]["has_readable_neighbors"], False)
            self.assertNotIn("graph_edges", library[0])
            read = await self.call(
                client, "artifact_read", {"artifact_id": created["artifact"]["id"]}
            )
            self.assertEqual(read["text"], "hello graph")

            asset = await self.call(
                client,
                "artifact_create",
                {
                    "name": "agent-launch-board.html",
                    "media_type": "text/html",
                    "content_base64": base64.b64encode(b"launch controls").decode(),
                },
            )
            filename_search = await self.call(
                client, "artifact_search", {"query": "agent-launch-board.html"}
            )
            self.assertEqual(
                [item["artifact_id"] for item in filename_search], [asset["artifact"]["id"]]
            )
            self.assertEqual(filename_search[0]["match_kind"], "name")
            self.assertEqual(filename_search[0]["version_id"], asset["version"]["id"])
            self.assertTrue(
                {
                    "artifact_id",
                    "version_id",
                    "name",
                    "media_type",
                    "path",
                    "match_kind",
                    "match_kinds",
                    "snippet",
                    "score",
                    "graph_path",
                    "graph_context",
                }.issubset(filename_search[0])
            )
            self.assertNotIn("graph_edges", filename_search[0])
            self.assertNotIn("edge_count", filename_search[0]["graph_context"])
            self.assertEqual(
                await self.call(client, "artifact_grep", {"pattern": "agent-launch-board.html"}),
                [],
            )
            by_name = await self.call(client, "artifact_list", {"name": "agent-launch-board.html"})
            by_type = await self.call(client, "artifact_list", {"media_type": "text/html"})
            self.assertEqual([item["id"] for item in by_name], [asset["artifact"]["id"]])
            self.assertEqual([item["id"] for item in by_type], [asset["artifact"]["id"]])
            first_page = await self.call(client, "artifact_list", {"limit": 1})
            cursor = f"{first_page[-1]['updated_at']}|{first_page[-1]['id']}"
            second_page = await self.call(client, "artifact_list", {"limit": 1, "cursor": cursor})
            self.assertEqual(
                {item["id"] for item in first_page + second_page},
                {created["artifact"]["id"], asset["artifact"]["id"]},
            )

    async def test_write_search_graph_versions_and_fail_closed_tenant(self) -> None:
        async with Client(self.server, raise_exceptions=True) as client:
            first = await self.call(
                client,
                "artifact_create",
                {
                    "name": "source.md",
                    "content_base64": base64.b64encode(b"source graph").decode(),
                },
            )
            second = await self.call(
                client,
                "artifact_create",
                {
                    "name": "target.md",
                    "content_base64": base64.b64encode(b"target node").decode(),
                },
            )
            artifact_id = first["artifact"]["id"]
            target_id = second["artifact"]["id"]
            updated = await self.call(
                client,
                "artifact_write",
                {
                    "artifact_id": artifact_id,
                    "parent_version_id": first["version"]["id"],
                    "content_base64": base64.b64encode(b"updated graph").decode(),
                },
            )
            self.assertEqual(updated["parent_version_id"], first["version"]["id"])
            self.assertTrue(await self.call(client, "artifact_search", {"query": "updated"}))
            self.assertTrue(await self.call(client, "artifact_grep", {"pattern": "updated"}))
            edge = await self.call(
                client,
                "graph_link",
                {
                    "source_artifact_id": artifact_id,
                    "target_artifact_id": target_id,
                    "edge_type": "references",
                },
            )
            self.assertEqual(edge["target_artifact_id"], target_id)
            self.assertTrue(
                await self.call(client, "graph_traverse", {"start_artifact_id": artifact_id})
            )
            component = await self.call(
                client, "graph_component", {"start_artifact_id": artifact_id}
            )
            self.assertEqual({item["id"] for item in component}, {artifact_id, target_id})
            self.assertTrue(
                await self.call(
                    client,
                    "artifact_search",
                    {"query": "updated", "graph_root_artifact_id": artifact_id},
                )
            )
            scoped = await self.call(
                client,
                "artifact_search",
                {"query": "updated", "graph_root_artifact_id": artifact_id},
            )
            self.assertEqual(
                [node["artifact_id"] for node in scoped[0]["graph_path"]],
                [artifact_id],
            )
            self.assertNotIn("graph_edges", scoped[0])
            self.assertNotIn("edge_count", scoped[0]["graph_context"])
            missing_component = await client.call_tool(
                "graph_component", {"start_artifact_id": "art_missing"}
            )
            self.assertTrue(missing_component.is_error)
            self.assertIn("artifact not found", missing_component.content[0].text)
            missing_search = await client.call_tool(
                "artifact_search",
                {"query": "updated", "graph_root_artifact_id": "art_missing"},
            )
            self.assertTrue(missing_search.is_error)
            self.assertIn("artifact not found", missing_search.content[0].text)
            self.assertEqual(
                len(await self.call(client, "artifact_versions", {"artifact_id": artifact_id})),
                2,
            )

        other_server = build_mcp_server(self.service, tenant_id="other", actor="other-client")
        async with Client(other_server) as other_client:
            response = await other_client.call_tool("artifact_read", {"artifact_id": artifact_id})
            self.assertTrue(response.is_error)
            self.assertIn("artifact not found", response.content[0].text)

    async def test_invalid_base64_and_schema_fail_cleanly(self) -> None:
        async with Client(self.server) as client:
            invalid = await client.call_tool(
                "artifact_create", {"name": "bad.txt", "content_base64": "%%%"}
            )
            self.assertTrue(invalid.is_error)
            self.assertIn("valid base64", invalid.content[0].text)
            missing = await client.call_tool("artifact_create", {})
            self.assertTrue(missing.is_error)
            invalid_root = await client.call_tool(
                "artifact_search", {"query": "anything", "graph_root_artifact_id": ""}
            )
            self.assertTrue(invalid_root.is_error)

    def test_unexpected_tool_failure_has_no_secret_exception_chain(self) -> None:
        secret = "unexpected-tool-secret"

        def explode() -> None:
            raise RuntimeError(secret)

        with self.assertRaises(ToolError) as raised:
            _tool_errors(explode)
        self.assertEqual(str(raised.exception), "MCP tool failed safely")
        self.assertNotIn(secret, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_internal_folio_failure_is_static_and_chainless(self) -> None:
        secret = "db secret=internal-mcp-secret"

        def explode() -> None:
            raise FolioError(secret)

        with self.assertRaises(ToolError) as raised:
            _tool_errors(explode)
        self.assertEqual(str(raised.exception), "MCP tool failed safely")
        self.assertNotIn(secret, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_exception_text_is_never_echoed_at_the_mcp_boundary(self) -> None:
        secret = "PUBLICSECRET123"
        failures = (
            ValueError(secret),
            ToolError(secret),
            binascii.Error(secret),
            FolioError(secret),
            RuntimeError(secret),
        )
        for failure in failures:
            with self.subTest(exception=type(failure).__name__):

                def explode(failure: BaseException = failure) -> None:
                    raise failure

                with self.assertRaises(ToolError) as raised:
                    _tool_errors(explode)
                self.assertEqual(str(raised.exception), "MCP tool failed safely")
                self.assertNotIn(secret, str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)

    def test_public_error_mapping_is_exact_and_suffix_free(self) -> None:
        def parent_mismatch() -> None:
            raise FolioError("parent version mismatch; expected PUBLICSECRET123")

        with self.assertRaises(ToolError) as raised:
            _tool_errors(parent_mismatch)
        self.assertEqual(str(raised.exception), "parent version mismatch")
        self.assertNotIn("PUBLICSECRET123", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

        def invalid_from_time() -> None:
            raise FolioError("from_time is invalid")

        with self.assertRaises(ToolError) as raised:
            _tool_errors(invalid_from_time)
        self.assertEqual(str(raised.exception), "from_time is invalid")
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_public_folio_validation_contract_is_preserved(self) -> None:
        def missing() -> None:
            raise FolioError("artifact not found")

        with self.assertRaises(ToolError) as raised:
            _tool_errors(missing)
        self.assertEqual(str(raised.exception), "artifact not found")
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    async def test_graph_scoped_search_requires_graph_read_scope(self) -> None:
        server = build_mcp_server(self.service)
        token = set_request_principal(
            Principal(
                "acme",
                "reader",
                "https://issuer.example",
                "reader",
                frozenset({"artifact:search"}),
            )
        )
        try:
            async with Client(server) as client:
                denied = await client.call_tool(
                    "artifact_search",
                    {"query": "anything", "graph_root_artifact_id": "art_root"},
                )
                self.assertTrue(denied.is_error)
                self.assertIn("operation not permitted", denied.content[0].text)
        finally:
            reset_request_principal(token)


if __name__ == "__main__":
    unittest.main()
