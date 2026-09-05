import base64
import tempfile
import unittest
from pathlib import Path

from folio_lattice.mcp_protocol import McpProtocol
from folio_lattice.service import FolioLattice


class McpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.protocol = McpProtocol(FolioLattice(root / "folio.db", root / "blobs"))

    def tearDown(self):
        self.temp.cleanup()

    def call(self, name, arguments, request_id=1):
        response = self.protocol.handle(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        self.assertNotIn("error", response)
        return response["result"]["structuredContent"]

    def test_initialize_list_and_provider_neutral_tool_flow(self):
        initialized = self.protocol.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "folio-lattice")
        tools = self.protocol.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        self.assertIn("artifact_search", {tool["name"] for tool in tools["result"]["tools"]})
        created = self.call(
            "artifact_create",
            {
                "tenant_id": "acme",
                "name": "note.md",
                "media_type": "text/markdown",
                "content_base64": base64.b64encode(b"hello graph").decode(),
                "actor": "client",
                "reason": "test",
            },
        )
        self.assertIn("version", created)
        read = self.call(
            "artifact_read", {"tenant_id": "acme", "artifact_id": created["artifact"]["id"]}
        )
        self.assertEqual(read["text"], "hello graph")

    def test_transport_notifications_resources_and_errors(self):
        self.assertIsNone(
            self.protocol.handle(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
            )
        )
        resources = self.protocol.handle(
            {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}}
        )
        self.assertEqual(resources["result"]["resources"], [])

        unknown_method = self.protocol.handle(
            {"jsonrpc": "2.0", "id": 4, "method": "not-a-method", "params": {}}
        )
        self.assertEqual(unknown_method["error"]["code"], -32601)

        unknown_tool = self.protocol.handle(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "not-a-tool", "arguments": {}},
            }
        )
        self.assertEqual(unknown_tool["error"]["code"], -32602)

        malformed = self.protocol.handle(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "artifact_create", "arguments": {}},
            }
        )
        self.assertEqual(malformed["error"]["code"], -32602)

    def test_provider_neutral_write_search_graph_and_versions(self):
        first = self.call(
            "artifact_create",
            {
                "tenant_id": "acme",
                "name": "source.md",
                "content_base64": base64.b64encode(b"source graph").decode(),
            },
        )
        second = self.call(
            "artifact_create",
            {
                "tenant_id": "acme",
                "name": "target.md",
                "content_base64": base64.b64encode(b"target node").decode(),
            },
        )
        artifact_id = first["artifact"]["id"]
        target_id = second["artifact"]["id"]
        version_id = first["version"]["id"]
        updated = self.call(
            "artifact_write",
            {
                "tenant_id": "acme",
                "artifact_id": artifact_id,
                "parent_version_id": version_id,
                "content_base64": base64.b64encode(b"updated graph").decode(),
            },
        )
        self.assertEqual(updated["parent_version_id"], version_id)
        self.assertTrue(self.call("artifact_search", {"tenant_id": "acme", "query": "updated"}))
        self.assertTrue(self.call("artifact_grep", {"tenant_id": "acme", "pattern": r"updated"}))
        edge = self.call(
            "graph_link",
            {
                "tenant_id": "acme",
                "source_artifact_id": artifact_id,
                "target_artifact_id": target_id,
                "edge_type": "references",
            },
        )
        self.assertEqual(edge["target_artifact_id"], target_id)
        self.assertTrue(
            self.call(
                "graph_traverse",
                {"tenant_id": "acme", "start_artifact_id": artifact_id},
            )
        )
        self.assertEqual(
            len(self.call("artifact_versions", {"tenant_id": "acme", "artifact_id": artifact_id})),
            2,
        )


if __name__ == "__main__":
    unittest.main()
