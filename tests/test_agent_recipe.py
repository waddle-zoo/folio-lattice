import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mcp import Client
from test_e2e import free_port, start_server, stop_server, wait_ready

from folio_lattice.agent_recipe import BUNDLE_SCHEMA, AgentRecipeError, run_recipe
from folio_lattice.mcp_protocol import build_mcp_server
from folio_lattice.public_mcp import HttpMcpClient
from folio_lattice.service import FolioLattice

RENDERER_CAPABILITY_SECRET = "agent-recipe-live-secret-012345678901234567890123"


class AgentRecipeTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, client: Client, tool: str, arguments: dict[str, Any]) -> Any:
        response = await client.call_tool(tool, arguments)
        self.assertFalse(response.is_error, response)
        structured = response.structured_content
        assert structured is not None
        return structured.get("result", structured)

    async def test_mcp_bundle_returns_pinned_assets_and_idempotent_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            server = build_mcp_server(service, tenant_id="recipe", actor="agent")
            arguments = {
                "bundle_id": "bundle-test",
                "render_base_url": "https://render.example",
                "title": "Agent graph",
                "css_body": b"body { color: red; }",
                "js_body": b"document.body.dataset.ready = 'yes';",
            }
            async with Client(server) as client:
                first = await run_recipe(client, **arguments)

                self.assertEqual(first.manifest["schema"], BUNDLE_SCHEMA)
                self.assertEqual(set(first.artifacts), {"html", "css", "js"})
                self.assertEqual(
                    first.artifact_ids,
                    {role: asset.artifact_id for role, asset in first.artifacts.items()},
                )
                self.assertEqual(
                    first.version_ids,
                    {role: asset.version_id for role, asset in first.artifacts.items()},
                )
                self.assertEqual(len(first.link_ids), 2)
                self.assertEqual(
                    first.link_ids,
                    [link["id"] for link in first.links],
                )
                self.assertIn(first.artifacts["html"].version_id, first.render_url)
                self.assertTrue(
                    any(
                        item["artifact_id"] == first.artifacts["html"].artifact_id
                        for item in first.searches["name"]
                    )
                )
                self.assertTrue(
                    any(
                        item["artifact_id"] == first.artifacts["html"].artifact_id
                        for item in first.searches["type"]
                    )
                )
                self.assertEqual(first.read["version"]["id"], first.version_ids["html"])
                self.assertEqual(first.versions[0]["id"], first.version_ids["html"])
                self.assertEqual(
                    {item["target_artifact_id"] for item in first.traversal},
                    {
                        first.artifacts["css"].artifact_id,
                        first.artifacts["js"].artifact_id,
                    },
                )

                html = await self.call(
                    client,
                    "artifact_read",
                    {"artifact_id": first.artifacts["html"].artifact_id},
                )
                self.assertIn(first.artifacts["css"].content_url, html["text"])
                self.assertIn(first.artifacts["js"].content_url, html["text"])
                traversal = await self.call(
                    client,
                    "graph_traverse",
                    {"start_artifact_id": first.artifacts["html"].artifact_id},
                )
                self.assertEqual(
                    {item["target_artifact_id"] for item in traversal},
                    {
                        first.artifacts["css"].artifact_id,
                        first.artifacts["js"].artifact_id,
                    },
                )

                second = await run_recipe(client, **arguments)

            self.assertEqual(first.artifact_ids, second.artifact_ids)
            self.assertEqual(first.version_ids, second.version_ids)
            self.assertEqual(first.link_ids, second.link_ids)
            self.assertEqual(len(service.list_artifacts("recipe", actor=None, limit=10)), 3)

    async def test_live_streamable_http_recipe_and_renderer_fail_closed(self) -> None:
        try:
            control_port, render_port = free_port(), free_port()
        except PermissionError as exc:
            self.skipTest(f"loopback sockets unavailable: {exc}")
        control_origin = f"http://127.0.0.1:{control_port}"
        render_origin = f"http://127.0.0.1:{render_port}"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {
                **os.environ,
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                "FOLIO_TENANT_ID": "recipe-live",
                "FOLIO_ACTOR": "agent",
                "FOLIO_CONTROL_ORIGIN": control_origin,
                "FOLIO_RENDER_ORIGIN": render_origin,
                "FOLIO_RENDERER_CAPABILITY_SECRET": RENDERER_CAPABILITY_SECRET,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            control = start_server(environment, "http", control_port)
            renderer = None
            try:
                try:
                    wait_ready(control_origin, control)
                    renderer = start_server(
                        {**environment, "FOLIO_MCP_URL": f"{control_origin}/mcp"},
                        "renderer",
                        render_port,
                    )
                    wait_ready(render_origin, renderer)
                except AssertionError as exc:
                    if "Operation not permitted" in str(exc):
                        self.skipTest(f"loopback server unavailable: {exc}")
                    raise

                result = await run_recipe(
                    HttpMcpClient(f"{control_origin}/mcp"),
                    bundle_id="live-bundle",
                    render_base_url=render_origin,
                    title="Live agent bundle",
                )
                html_id = result.artifact_ids["html"]
                self.assertTrue(
                    any(item["artifact_id"] == html_id for item in result.searches["name"])
                )
                self.assertTrue(
                    any(item["artifact_id"] == html_id for item in result.searches["type"])
                )
                self.assertEqual(result.read["version"]["id"], result.version_ids["html"])
                self.assertEqual(result.versions[0]["id"], result.version_ids["html"])
                self.assertEqual(
                    {item["target_artifact_id"] for item in result.traversal},
                    {result.artifact_ids["css"], result.artifact_ids["js"]},
                )

                render_request = urllib.request.Request(
                    result.render_url,
                    headers={"Sec-Fetch-Dest": "iframe"},
                )
                with urllib.request.urlopen(render_request, timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    rendered = response.read()
                self.assertIn(result.artifacts["css"].content_url.encode(), rendered)
                self.assertIn(result.artifacts["js"].content_url.encode(), rendered)

                mismatched_url = (
                    f"{render_origin}/content/{result.artifact_ids['css']}/"
                    f"{result.version_ids['js']}"
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(mismatched_url, timeout=10).read()
                self.assertIn(raised.exception.code, {400, 404})
            finally:
                if renderer is not None:
                    stop_server(renderer)
                stop_server(control)

    async def test_same_bundle_id_with_changed_asset_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            server = build_mcp_server(service, tenant_id="recipe", actor="agent")
            common = {
                "bundle_id": "same-bundle",
                "render_base_url": "https://render.example",
            }
            async with Client(server) as client:
                await run_recipe(client, css_body=b"body { color: red; }", **common)
                with self.assertRaisesRegex(AgentRecipeError, "css bundle ID"):
                    await run_recipe(client, css_body=b"body { color: blue; }", **common)

    async def test_bounds_reject_before_mcp_call(self) -> None:
        class NoCallClient:
            async def call_tool(self, name: str, arguments: dict[str, Any]) -> object:
                raise AssertionError(f"unexpected call: {name} {arguments}")

        with self.assertRaisesRegex(AgentRecipeError, "asset names must be unique"):
            await run_recipe(
                NoCallClient(),
                bundle_id="bounds",
                render_base_url="https://render.example",
                html_name="same.asset",
                css_name="same.asset",
            )


if __name__ == "__main__":
    unittest.main()
