import base64
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp import Client

from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
from folio_lattice.mcp_protocol import build_mcp_server
from folio_lattice.service import FolioLattice


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
            self.assertEqual(set(list_tool.input_schema.get("properties", {})), {"limit"})
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
            read = await self.call(
                client, "artifact_read", {"artifact_id": created["artifact"]["id"]}
            )
            self.assertEqual(read["text"], "hello graph")

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
