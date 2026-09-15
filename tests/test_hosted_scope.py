import base64
import tempfile
import unittest
from pathlib import Path

from mcp import Client

from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
from folio_lattice.mcp_protocol import TOOL_SCOPES, build_mcp_server
from folio_lattice.service import FolioLattice


class HostedScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_enforces_least_privilege_scope_before_service_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            server = build_mcp_server(service)
            principal = Principal(
                "tenant-a", "actor-a", "https://issuer.example", "subject-a", frozenset()
            )
            token = set_request_principal(principal)
            try:
                async with Client(server, raise_exceptions=True) as client:
                    tools = await client.list_tools()
                    self.assertEqual({tool.name for tool in tools.tools}, set(TOOL_SCOPES))

                    denied = await client.call_tool(
                        "artifact_create",
                        {
                            "name": "blocked.txt",
                            "content_base64": base64.b64encode(b"blocked").decode(),
                        },
                    )
                    self.assertTrue(denied.is_error)
                    self.assertIn("operation not permitted", denied.content[0].text)
                    self.assertEqual(service.list_artifacts("tenant-a", actor=None), [])

                    reset_request_principal(token)
                    token = set_request_principal(
                        Principal(
                            "tenant-a",
                            "actor-a",
                            "https://issuer.example",
                            "subject-a",
                            frozenset({"artifact:write"}),
                        )
                    )
                    allowed = await client.call_tool(
                        "artifact_create",
                        {
                            "name": "allowed.txt",
                            "content_base64": base64.b64encode(b"allowed").decode(),
                        },
                    )
                    self.assertFalse(allowed.is_error, allowed)

                    read_denied = await client.call_tool("artifact_list", {})
                    self.assertTrue(read_denied.is_error)
                    self.assertIn("operation not permitted", read_denied.content[0].text)
            finally:
                reset_request_principal(token)
