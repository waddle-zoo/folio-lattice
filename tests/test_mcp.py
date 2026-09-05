import base64
import json
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
        response = self.protocol.handle({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        self.assertNotIn("error", response)
        return response["result"]["structuredContent"]

    def test_initialize_list_and_provider_neutral_tool_flow(self):
        initialized = self.protocol.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "folio-lattice")
        tools = self.protocol.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertIn("artifact_search", {tool["name"] for tool in tools["result"]["tools"]})
        created = self.call("artifact_create", {"tenant_id": "acme", "name": "note.md", "media_type": "text/markdown", "content_base64": base64.b64encode(b"hello graph").decode(), "actor": "client", "reason": "test"})
        self.assertIn("version", created)
        read = self.call("artifact_read", {"tenant_id": "acme", "artifact_id": created["artifact"]["id"]})
        self.assertEqual(read["text"], "hello graph")


if __name__ == "__main__":
    unittest.main()
