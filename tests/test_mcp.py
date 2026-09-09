import base64
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp import Client

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


if __name__ == "__main__":
    unittest.main()
