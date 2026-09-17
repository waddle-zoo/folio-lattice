from __future__ import annotations

import asyncio
import base64
import hmac
import io
import json
import logging
import os
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from mcp import Client

from folio_lattice.auth import (
    Principal,
    reset_request_capability,
    reset_request_principal,
    set_request_capability,
    set_request_principal,
)
from folio_lattice.bridge import AttachedMcpBridge, BridgeRequestError, validate_bridge_request
from folio_lattice.inspection import InspectionApp, ui_html
from folio_lattice.mcp_protocol import build_mcp_server
from folio_lattice.public_mcp import (
    AdminMcpClient,
    HttpMcpClient,
    PublicMcpError,
    SignedPrincipalRelay,
)
from folio_lattice.renderer import RendererApp, versioned_content_url
from folio_lattice.server import Settings, _optional_mcp_url_env, _origin_env
from folio_lattice.service import FolioLattice

CONTROL_ORIGIN = "http://127.0.0.1:8000"
RENDER_ORIGIN = "http://127.0.0.1:8001"


class LocalMcpCaller:
    def __init__(self, server: Any):
        self.server = server

    async def call(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        async with Client(self.server) as client:
            response = await client.call_tool(tool, dict(arguments))
        if response.is_error:
            raise PublicMcpError(response.content[0].text)
        value = response.structured_content
        assert value is not None
        return value.get("result", value)

    async def ready(self) -> bool:
        return True


class FixedCaller:
    def __init__(self, value: Any, *, delay: float = 0):
        self.value = value
        self.delay = delay

    async def call(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        await asyncio.sleep(self.delay)
        return self.value

    async def ready(self) -> bool:
        return True


class FakeSdkClient:
    def __init__(
        self,
        result: Any = None,
        *,
        tools: list[str] | None = None,
        delay: float = 0,
        failure: Exception | None = None,
    ):
        self.result = result
        self.tools = tools or []
        self.delay = delay
        self.failure = failure

    async def __aenter__(self) -> FakeSdkClient:
        if self.failure is not None and self.delay == 0:
            raise self.failure
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def call_tool(self, *args: object, **kwargs: object) -> Any:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure is not None:
            raise self.failure
        return self.result

    async def list_tools(self) -> Any:
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in self.tools])


async def call(
    app: Any,
    method: str,
    path: str,
    *,
    body: bytes = b"",
    content_type: str | None = None,
    origin: str | None = None,
    query: str = "",
    fetch_dest: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request = {"type": "http.request", "body": body, "more_body": False}
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return request

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = []
    if content_type is not None:
        headers.append((b"content-type", content_type.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if fetch_dest is not None:
        headers.append((b"sec-fetch-dest", fetch_dest.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 2),
    }
    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return start["status"], response_headers, response_body


def request(tool: str, arguments: dict[str, Any]) -> bytes:
    return json.dumps({"tool": tool, "arguments": arguments}).encode()


class WebAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = FolioLattice(root / "folio.db", root / "blobs")
        self.html = self.service.create_artifact(
            tenant_id="web", name="page.html", data=b"<h1>Page</h1>", media_type="text/html"
        )
        self.target = self.service.create_artifact(
            tenant_id="web", name="target.txt", data=b"target marker", media_type="text/plain"
        )
        self.css = self.service.create_artifact(
            tenant_id="web", name="style.css", data=b"body{color:red}", media_type="text/css"
        )
        self.javascript = self.service.create_artifact(
            tenant_id="web",
            name="app.js",
            data=b"document.body.dataset.ran='yes'",
            media_type="application/javascript",
        )
        self.binary = self.service.create_artifact(
            tenant_id="web", name="blob.bin", data=b"\x00", media_type="application/octet-stream"
        )
        self.service.link(
            "web", self.html["artifact"]["id"], self.target["artifact"]["id"], "references"
        )
        server = build_mcp_server(self.service, tenant_id="web", actor="web-user")
        self.caller = LocalMcpCaller(server)
        self.admin_caller = AdminMcpClient(server)
        self.inspection = InspectionApp(
            self.caller,
            control_origin=CONTROL_ORIGIN,
            render_origin=RENDER_ORIGIN,
            max_request_bytes=1000,
            admin_caller=self.admin_caller,
        )
        self.renderer = RendererApp(self.caller, control_origin=CONTROL_ORIGIN)

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_static_ui_is_bounded_accessible_and_strictly_sandboxed(self) -> None:
        for path, expected in (
            ("/", b"Choose a graph"),
            (f"/artifacts/{self.html['artifact']['id']}", b'id="back-to-library"'),
            (f"/workspace/{self.html['artifact']['id']}", b'id="artifact-tree"'),
            (f"/standalone/{self.html['artifact']['id']}", b'id="human-preview"'),
            (f"/inspect/{self.html['artifact']['id']}", b'sandbox="allow-scripts"'),
            ("/ui.css", b"focus-visible"),
            ("/ui.js", b"event.origin !== 'null' || event.source !== frame.contentWindow"),
        ):
            status, headers, body = await call(self.inspection, "GET", path)
            self.assertEqual(status, 200)
            self.assertIn(expected, body)
            self.assertEqual(headers["cache-control"], "no-store")
        standalone = (
            await call(self.inspection, "GET", f"/standalone/{self.html['artifact']['id']}")
        )[2]
        self.assertIn(b'id="standalone-back"', standalone)
        settings = (await call(self.inspection, "GET", "/settings/connections"))[2]
        self.assertIn(b"Approved connections", settings)
        self.assertNotIn(b"credential_ref", settings)
        script = (await call(self.inspection, "GET", "/connections.js"))[2]
        self.assertIn(b"/api/admin/mcp", script)
        self.assertNotIn(b"credential_ref", script)
        _, headers, page = await call(self.inspection, "GET", "/")
        self.assertIn(f"frame-src {RENDER_ORIGIN}", headers["content-security-policy"])
        self.assertNotIn(b"allow-same-origin", page)
        for marker in (
            b'role="status"',
            b'role="alert"',
            b'aria-busy="false"',
            b"New graph or artifact",
        ):
            self.assertIn(marker, page)
        for forbidden in (
            b"Unauthenticated local development",
            b'id="artifact-id"',
            b'id="sharing"',
            b'id="connection-admin"',
            b'id="revoke-access"',
            b'id="bridge-status"',
            b'id="fullscreen-preview"',
        ):
            self.assertNotIn(forbidden, page)

    async def test_connections_admin_uses_real_tools_and_keeps_states_secret_free(self) -> None:
        arguments = {
            "name": "Calendar approval",
            "endpoint": "https://example.com/mcp",
            "approved_tools": ["calendar.events.list"],
            "approved_resources": [],
            "allowed_origins": ["https://example.com"],
            "reason": "web test approval",
        }
        with patch("folio_lattice.external_mcp.HttpExternalMcpTransport.validate_registration"):
            status, _, body = await call(
                self.inspection,
                "POST",
                "/api/admin/mcp",
                body=request("external_mcp_connection_register", arguments),
                content_type="application/json",
                origin=CONTROL_ORIGIN,
            )
        self.assertEqual(status, 200)
        registered = json.loads(body)
        connection_id = registered["id"]
        self.assertEqual(registered["status"], "active")
        self.assertFalse(registered["credential_configured"])
        self.assertNotIn("credential_ref", registered)

        status, _, body = await call(
            self.inspection,
            "POST",
            "/api/admin/mcp",
            body=request("external_mcp_connection_status", {"connection_id": connection_id}),
            content_type="application/json",
            origin=CONTROL_ORIGIN,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "active")

        status, _, body = await call(
            self.inspection,
            "POST",
            "/api/admin/mcp",
            body=request(
                "external_mcp_connection_revoke",
                {"connection_id": connection_id, "reason": "web test revoke"},
            ),
            content_type="application/json",
            origin=CONTROL_ORIGIN,
        )
        self.assertEqual(status, 200)
        revoked = json.loads(body)
        self.assertEqual(revoked["status"], "revoked")
        self.assertNotIn("credential_ref", revoked)

    async def test_connections_admin_denies_authenticated_non_admin(self) -> None:
        token = set_request_principal(
            Principal(
                tenant_id="web",
                actor_id="reader",
                issuer="https://issuer.example",
                subject="reader-subject",
                scopes=frozenset({"artifact:read"}),
            )
        )
        try:
            status, _, body = await call(
                self.inspection,
                "POST",
                "/api/admin/mcp",
                body=request("external_mcp_connection_list", {"limit": 10}),
                content_type="application/json",
                origin=CONTROL_ORIGIN,
            )
        finally:
            reset_request_principal(token)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["code"], "permission_denied")
        self.assertFalse(json.loads(body)["reauthenticate"])

    async def test_sign_in_shell_is_safe_and_does_not_offer_local_fake_auth(self) -> None:
        status, headers, page = await call(
            self.inspection,
            "GET",
            "/sign-in",
            query="return_to=%2Fworkspace%2Fart_123",
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn(b"Sign in to continue.", page)
        self.assertIn(b"local workspace is running without sign-in", page)
        self.assertIn(b'data-return-to="/workspace/art_123"', page)
        self.assertIn(b'<script src="/ui.js"></script>', page)
        _, _, script = await call(self.inspection, "GET", "/ui.js")
        self.assertIn(b"/v1/me", script)
        self.assertIn(b"/auth/logout", script)
        self.assertNotIn(b"/auth/start", page)

        hosted = ui_html(
            RENDER_ORIGIN,
            auth_state="hosted",
            sign_in=True,
            return_to="/workspace/art_123",
        )
        self.assertIn('href="/auth/start?return_to=%2Fworkspace%2Fart_123"', hosted)
        self.assertIn('data-return-to="/workspace/art_123"', hosted)
        self.assertIn('id="auth-session"', hosted)
        self.assertNotIn("/auth/start?return_to=https", hosted)
        self.assertNotIn("document.cookie", hosted)

        unsafe = ui_html(
            RENDER_ORIGIN, auth_state="hosted", sign_in=True, return_to="https://evil.example"
        )
        self.assertIn('data-return-to="/"', unsafe)
        self.assertNotIn("evil.example", unsafe)

    async def test_ui_contract_has_nontechnical_orientation_and_safe_status_copy(self) -> None:
        status, _, page = await call(self.inspection, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<h1", page)
        self.assertIn(b"Choose a graph", page)
        self.assertIn(b"New graph or artifact", page)
        self.assertIn(b"Search documents and files", page)
        self.assertIn(b'id="search-type-filter"', page)
        self.assertIn(b"All file types", page)
        self.assertIn(b"GRAPH PICKER", page)
        for narration in (
            b"Search across the text you have indexed",
            b"Grep checks for an exact substring",
            b"One place for the things your team knows",
            b"A calm place to create",
        ):
            self.assertNotIn(narration, page)

        status, _, script = await call(self.inspection, "GET", "/ui.js")
        self.assertEqual(status, 200)
        self.assertIn(b"Version 1 saved", script)
        self.assertIn(b"did not run", script)
        self.assertIn(b"artifact_name", script)
        self.assertIn(b"graphPathLabel", script)
        self.assertIn(b"dataset.graphPath", script)
        self.assertIn(b"has_readable_neighbors", script)
        self.assertNotIn(b"edge_count", script)
        self.assertNotIn(b"graph_edges", script)
        self.assertNotIn(b"Graph: Graph path:", script)
        self.assertIn(b"dataset.artifactId", script)
        self.assertIn(b"mediaTypeLabel", script)
        self.assertIn(b"renderLibrarySearch", script)
        self.assertIn(b"result-group-heading", script)
        self.assertIn(b"request count", script)

        status, _, css = await call(self.inspection, "GET", "/ui.css")
        self.assertEqual(status, 200)
        self.assertIn(b"prefers-color-scheme: dark", css)
        self.assertIn(b"#error", css)

    async def test_ui_contract_covers_core_loop_and_omits_debug_output(self) -> None:
        status, _, page = await call(self.inspection, "GET", "/")
        self.assertEqual(status, 200)
        for marker in (
            b'id="graph-artifacts"',
            b'id="create"',
            b'id="create-file"',
            b"Choose a graph",
            b'id="find"',
            b'aria-live="polite"',
        ):
            self.assertIn(marker, page)
        for forbidden in (
            b"Open by identifier",
            b'id="open"',
            b'id="artifact-id"',
            b'id="artifact-details"',
            b'id="graph"',
            b'id="sharing"',
            b'id="connection-admin"',
            b"Approved connection",
            b'id="revoke-access"',
            b'id="bridge-status"',
            b'id="fullscreen-preview"',
            b'id="content"',
            b'id="chunks"',
            b'id="versions"',
            b'id="create-media"',
            b'id="create-reason"',
        ):
            self.assertNotIn(forbidden, page)
        for forbidden in (b"Traceback", b"content_base64", b"source_context", b"sqlite"):
            self.assertNotIn(forbidden, page)

        debug_page = (
            await call(self.inspection, "GET", f"/inspect/{self.html['artifact']['id']}")
        )[2]
        for marker in (
            b'id="open"',
            b'id="artifact-id"',
            b'id="artifact-details"',
            b'id="graph"',
            b'id="sharing"',
            b'id="bridge-status"',
            b'id="fullscreen-preview"',
        ):
            self.assertIn(marker, debug_page)
        human_page = (
            await call(self.inspection, "GET", f"/artifacts/{self.html['artifact']['id']}")
        )[2]
        for marker in (
            b'id="back-to-library"',
            b'id="human-title"',
            b'id="human-viewer"',
            b'id="human-document"',
            b'id="human-preview"',
            b'<main id="main" class="page" tabindex="-1"',
        ):
            self.assertIn(marker, human_page)
        for forbidden in (
            b'id="workspace"',
            b'id="artifact-id"',
            b"People with access",
            b"Readable document",
            b"Artifact preview",
            b"Update document",
            b'id="graph-context"',
            b'id="human-edit"',
            b'id="content"',
            b'id="chunks"',
            b'id="versions"',
            b'id="bridge-status"',
            b'id="fullscreen-preview"',
        ):
            self.assertNotIn(forbidden, human_page)
        self.assertIn(b'sandbox="allow-scripts"', human_page)

        workspace_page = (
            await call(self.inspection, "GET", f"/workspace/{self.html['artifact']['id']}")
        )[2]
        for marker in (
            b'id="artifact-tree-panel"',
            b'id="read-mode"',
            b'id="graph-mode"',
            b'id="workspace-share"',
            b'id="new-note"',
            b'id="new-note-entry"',
            b'id="human-edit"',
            b'id="graph-context"',
            b'id="revoke-access"',
            b"Approved person identifier",
            b'<main id="main" class="page" tabindex="-1"',
        ):
            self.assertIn(marker, workspace_page)
        self.assertNotIn(b'id="human-viewer"', workspace_page)

        standalone_page = (
            await call(self.inspection, "GET", f"/standalone/{self.html['artifact']['id']}")
        )[2]
        self.assertIn(b'class="human-route standalone-route"', standalone_page)
        self.assertIn(b'id="human-preview"', standalone_page)
        self.assertNotIn(b'class="topbar"', standalone_page)
        self.assertNotIn(b"Folio Lattice", standalone_page)

        status, _, script = await call(self.inspection, "GET", "/ui.js")
        self.assertEqual(status, 200)
        for marker in (
            b"location.assign",
            b"artifact_list",
            b"loadLibrary",
            b"artifact_read_chunk",
            b"artifact_write",
            b"artifact_versions",
            b"artifact_share",
            b"artifact_revoke",
            b"artifact_acl",
            b"renderAccess",
            b"Access update incomplete",
            b"graph_link",
            b"graph note create",
            b"markdownNoteName",
            b"graph_traverse",
            b"renderArtifactTree",
            b"artifactLabelMap",
            b"stableArtifactLabel",
            b"syncFindPanel",
            b"revokeTrigger",
            b"setWorkspaceMode",
            b"workspacePath",
            b"/render/",
            b"/api/bridge",
            b"activeRequests",
        ):
            self.assertIn(marker, script)
        for forbidden in (b"Traceback", b"console.log", b"document.cookie"):
            self.assertNotIn(forbidden, script)
        self.assertNotIn(b"artifactNameCounts", script)

    async def test_library_returns_named_recent_artifacts(self) -> None:
        status, _, body = await call(
            self.inspection,
            "POST",
            "/api/mcp",
            body=request("artifact_list", {"limit": 20}),
            content_type="application/json",
            origin=CONTROL_ORIGIN,
        )
        self.assertEqual(status, 200)
        names = {item["name"] for item in json.loads(body)}
        self.assertIn("page.html", names)

        page = (await call(self.inspection, "GET", "/"))[2]
        self.assertIn(b"Choose a graph", page)
        viewer = (await call(self.inspection, "GET", f"/inspect/{self.html['artifact']['id']}"))[2]
        self.assertIn(b"Back to library", viewer)

    async def test_ui_renders_auth_context_without_local_warning_in_hosted_state(self) -> None:
        page = ui_html(
            RENDER_ORIGIN,
            auth_state="authenticated",
            organization="Acme & Sons",
            actor='Ada "A" Lovelace',
            debug=True,
        )
        self.assertIn('data-auth-state="authenticated"', page)
        self.assertIn("Organization: Acme &amp; Sons", page)
        self.assertIn("Actor: Ada &quot;A&quot; Lovelace", page)
        self.assertNotIn("Unauthenticated local development", page)
        self.assertIn('href="/sign-in?return_to=%2F"', page)
        self.assertNotIn("tenant_id", page)
        self.assertNotIn("access_token", page)

    async def test_gateway_reads_writes_and_reports_stale_conflict(self) -> None:
        artifact_id = self.html["artifact"]["id"]
        version_id = self.html["version"]["id"]
        status, _, body = await call(
            self.inspection,
            "POST",
            "/api/mcp",
            body=request("artifact_read", {"artifact_id": artifact_id}),
            content_type="application/json",
            origin=CONTROL_ORIGIN,
        )
        self.assertEqual(status, 200)
        read = json.loads(body)
        self.assertEqual(read["text"], "<h1>Page</h1>")
        self.assertEqual(read["artifact"]["id"], artifact_id)
        self.assertTrue(read["chunks"])

        arguments = {
            "artifact_id": artifact_id,
            "parent_version_id": version_id,
            "content_base64": base64.b64encode(b"<h1>Edited</h1>").decode(),
            "media_type": "text/html",
            "reason": "unit edit",
            "source_context": {"interface": "inspection-ui"},
        }
        status, _, body = await call(
            self.inspection,
            "POST",
            "/api/mcp",
            body=request("artifact_write", arguments),
            content_type="application/json; charset=utf-8",
            origin=CONTROL_ORIGIN,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["actor"], "web-user")
        status, _, _ = await call(
            self.inspection,
            "POST",
            "/api/mcp",
            body=request("artifact_write", arguments),
            content_type="application/json",
            origin=CONTROL_ORIGIN,
        )
        self.assertEqual(status, 409)
        self.assertEqual(len(self.service.versions("web", artifact_id)), 2)

    async def test_gateway_and_bridge_fail_closed_and_audit_without_content(self) -> None:
        artifact_id = self.html["artifact"]["id"]
        for body, content_type, origin, expected in (
            (b"{}", "application/json", CONTROL_ORIGIN, 400),
            (b"not-json", "application/json", CONTROL_ORIGIN, 400),
            (b"[]", "application/json", CONTROL_ORIGIN, 400),
            (b"x" * 1001, "application/json", CONTROL_ORIGIN, 413),
            (request("artifact_read", {"artifact_id": artifact_id}), None, CONTROL_ORIGIN, 400),
            (request("artifact_read", {"artifact_id": artifact_id}), "application/json", None, 403),
            (request("secret_tool", {}), "application/json", CONTROL_ORIGIN, 400),
        ):
            status, _, _ = await call(
                self.inspection,
                "POST",
                "/api/mcp",
                body=body,
                content_type=content_type,
                origin=origin,
            )
            self.assertEqual(status, expected)

        stream = io.StringIO()
        logger = logging.Logger("bridge-test")
        logger.addHandler(logging.StreamHandler(stream))
        bridge = AttachedMcpBridge(self.caller, logger=logger)
        app = InspectionApp(
            self.caller,
            control_origin=CONTROL_ORIGIN,
            render_origin=RENDER_ORIGIN,
            max_request_bytes=1000,
            bridge=bridge,
        )
        allowed = {
            "request_id": "req-1",
            "artifact_id": artifact_id,
            "attachment": "folio-lattice",
            "tool": "artifact_search",
            "arguments": {"query": "Page", "limit": 10},
        }
        status, _, body = await call(
            app,
            "POST",
            "/api/bridge",
            body=json.dumps(allowed).encode(),
            content_type="application/json",
            origin=CONTROL_ORIGIN,
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["result"])

        denied = {**allowed, "request_id": "req-2", "tool": "artifact_write", "arguments": {}}
        status, _, _ = await call(
            app,
            "POST",
            "/api/bridge",
            body=json.dumps(denied).encode(),
            content_type="application/json",
            origin=CONTROL_ORIGIN,
        )
        self.assertEqual(status, 403)
        log = stream.getvalue()
        self.assertIn('"decision":"allow"', log)
        self.assertIn('"decision":"deny"', log)
        self.assertNotIn("target marker", log)

        malformed = {**allowed, "tenant_id": "other"}
        status, _, _ = await call(
            app,
            "POST",
            "/api/bridge",
            body=json.dumps(malformed).encode(),
            content_type="application/json",
            origin=CONTROL_ORIGIN,
        )
        self.assertEqual(status, 400)
        status, _, _ = await call(
            app,
            "POST",
            "/api/bridge",
            body=json.dumps(allowed).encode(),
            content_type="application/json",
            origin="http://127.0.0.1.evil:8000",
        )
        self.assertEqual(status, 403)

    async def test_bridge_bounds_slow_and_oversized_results(self) -> None:
        base = {
            "request_id": "r",
            "artifact_id": "art_x",
            "attachment": "folio-lattice",
            "tool": "artifact_search",
            "arguments": {"query": "x"},
        }
        request_value = validate_bridge_request(base)
        with self.assertRaisesRegex(PublicMcpError, "timed out"):
            await AttachedMcpBridge(FixedCaller([], delay=0.05), timeout_seconds=0.001).call(
                request_value
            )
        with self.assertRaisesRegex(PublicMcpError, "size limit"):
            await AttachedMcpBridge(
                FixedCaller([{"artifact_id": "art_x", "snippet": "x" * 100}]),
                max_result_bytes=10,
            ).call(request_value)
        oversized = {**base, "arguments": {"query": "x" * (32 * 1024)}}
        with self.assertRaisesRegex(Exception, "too large"):
            validate_bridge_request(oversized)

    async def test_public_bridge_binds_reads_and_traversal_to_attached_artifact(self) -> None:
        attached_id = self.html["artifact"]["id"]
        other_id = self.target["artifact"]["id"]
        with self.assertRaisesRegex(BridgeRequestError, "attached artifact"):
            await AttachedMcpBridge(self.caller).call(
                {
                    "request_id": "raw-read-other",
                    "artifact_id": attached_id,
                    "attachment": "folio-lattice",
                    "tool": "artifact_read",
                    "arguments": {"artifact_id": other_id},
                }
            )
        for request_id, tool, arguments in (
            ("read-other", "artifact_read", {"artifact_id": other_id}),
            ("traverse-other", "graph_traverse", {"start_artifact_id": other_id}),
        ):
            payload = {
                "request_id": request_id,
                "artifact_id": attached_id,
                "attachment": "folio-lattice",
                "tool": tool,
                "arguments": arguments,
            }
            status, _, body = await call(
                self.inspection,
                "POST",
                "/api/bridge",
                body=json.dumps(payload).encode(),
                content_type="application/json",
                origin=CONTROL_ORIGIN,
            )
            self.assertEqual(status, 400)
            self.assertNotIn(other_id.encode(), body)
            self.assertNotIn(b"target marker", body)

        for request_id, tool, arguments in (
            ("read-attached", "artifact_read", {"artifact_id": attached_id}),
            ("traverse-attached", "graph_traverse", {"start_artifact_id": attached_id}),
        ):
            payload = {
                "request_id": request_id,
                "artifact_id": attached_id,
                "attachment": "folio-lattice",
                "tool": tool,
                "arguments": arguments,
            }
            status, _, _ = await call(
                self.inspection,
                "POST",
                "/api/bridge",
                body=json.dumps(payload).encode(),
                content_type="application/json",
                origin=CONTROL_ORIGIN,
            )
            self.assertEqual(status, 200)

        search_with_selector = {
            "request_id": "search-selector",
            "artifact_id": attached_id,
            "attachment": "folio-lattice",
            "tool": "artifact_search",
            "arguments": {"query": "target marker", "artifact_id": other_id},
        }
        status, _, body = await call(
            self.inspection,
            "POST",
            "/api/bridge",
            body=json.dumps(search_with_selector).encode(),
            content_type="application/json",
            origin=CONTROL_ORIGIN,
        )
        self.assertEqual(status, 400)
        self.assertNotIn(other_id.encode(), body)

        cross_artifact_search = {
            "request_id": "search-other",
            "artifact_id": attached_id,
            "attachment": "folio-lattice",
            "tool": "artifact_search",
            "arguments": {"query": "target marker"},
        }
        status, _, body = await call(
            self.inspection,
            "POST",
            "/api/bridge",
            body=json.dumps(cross_artifact_search).encode(),
            content_type="application/json",
            origin=CONTROL_ORIGIN,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"], [])
        self.assertNotIn(b"target marker", body)

    async def test_http_mcp_adapter_bounds_errors_and_readiness(self) -> None:
        successful = SimpleNamespace(
            is_error=False,
            content=[],
            structured_content={"result": [{"id": "one"}]},
        )
        with patch("folio_lattice.public_mcp.Client", return_value=FakeSdkClient(successful)):
            caller = HttpMcpClient("http://127.0.0.1:1/mcp")
            self.assertEqual(
                await caller.call("artifact_search", {"query": "one"}), [{"id": "one"}]
            )

        tool_error = SimpleNamespace(
            is_error=True,
            content=[SimpleNamespace(type="text", text="bounded tool failure")],
            structured_content=None,
        )
        with patch("folio_lattice.public_mcp.Client", return_value=FakeSdkClient(tool_error)):
            with self.assertRaisesRegex(PublicMcpError, "bounded tool failure"):
                await caller.call("artifact_read", {"artifact_id": "art_x"})

        no_result = SimpleNamespace(is_error=False, content=[], structured_content=None)
        with patch("folio_lattice.public_mcp.Client", return_value=FakeSdkClient(no_result)):
            with self.assertRaisesRegex(PublicMcpError, "no structured result"):
                await caller.call("artifact_search", {"query": "one"})

        invalid_result = SimpleNamespace(
            is_error=False, content=[], structured_content={"bad": {1, 2}}
        )
        with patch("folio_lattice.public_mcp.Client", return_value=FakeSdkClient(invalid_result)):
            with self.assertRaisesRegex(PublicMcpError, "invalid structured result"):
                await caller.call("artifact_search", {"query": "one"})

        large_result = SimpleNamespace(
            is_error=False, content=[], structured_content={"value": "x" * 100}
        )
        with patch("folio_lattice.public_mcp.Client", return_value=FakeSdkClient(large_result)):
            with self.assertRaisesRegex(PublicMcpError, "allowed size"):
                await HttpMcpClient("http://127.0.0.1:1/mcp", max_result_bytes=10).call(
                    "artifact_search", {"query": "one"}
                )

        with patch(
            "folio_lattice.public_mcp.Client",
            return_value=FakeSdkClient(successful, delay=0.05),
        ):
            with self.assertRaisesRegex(PublicMcpError, "timed out"):
                await HttpMcpClient("http://127.0.0.1:1/mcp", timeout_seconds=0.001).call(
                    "artifact_search", {"query": "one"}
                )

        with patch(
            "folio_lattice.public_mcp.Client",
            return_value=FakeSdkClient(failure=OSError("private detail")),
        ):
            with self.assertRaisesRegex(PublicMcpError, "unavailable") as unavailable:
                await caller.call("artifact_search", {"query": "one"})
            self.assertNotIn("private detail", str(unavailable.exception))
            self.assertFalse(await caller.ready())

        with patch(
            "folio_lattice.public_mcp.Client",
            return_value=FakeSdkClient(tools=["artifact_read"]),
        ):
            self.assertTrue(await caller.ready())
        with self.assertRaisesRegex(PublicMcpError, "not part"):
            await caller.call("unknown", {})
        with self.assertRaisesRegex(ValueError, "positive"):
            HttpMcpClient("http://127.0.0.1:1/mcp", timeout_seconds=0)

    async def test_renderer_serves_only_safe_web_types_through_caller(self) -> None:
        html_id = self.html["artifact"]["id"]
        status, headers, body = await call(
            self.renderer, "GET", f"/render/{html_id}", fetch_dest="iframe"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<h1>Page</h1>")
        self.assertIn("sandbox allow-scripts", headers["content-security-policy"])
        self.assertIn("connect-src 'none'", headers["content-security-policy"])

        for created, marker in ((self.css, b"<link"), (self.javascript, b"<script")):
            artifact_id = created["artifact"]["id"]
            version_id = created["version"]["id"]
            status, _, wrapper = await call(
                self.renderer, "GET", f"/render/{artifact_id}", fetch_dest="iframe"
            )
            self.assertEqual(status, 200)
            self.assertIn(marker, wrapper)
            status, _, content = await call(
                self.renderer, "GET", f"/content/{artifact_id}/{version_id}"
            )
            self.assertEqual(status, 200)
            self.assertTrue(content)

        status, _, _ = await call(
            self.renderer,
            "GET",
            f"/render/{self.binary['artifact']['id']}",
            fetch_dest="iframe",
        )
        self.assertEqual(status, 415)
        status, _, _ = await call(
            self.renderer, "GET", f"/content/{html_id}/{self.html['version']['id']}"
        )
        self.assertEqual(status, 400)
        status, _, body = await call(self.renderer, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ready"])
        status, headers, _ = await call(self.renderer, "POST", f"/render/{html_id}")
        self.assertEqual(status, 405)
        self.assertIn("sandbox allow-scripts", headers["content-security-policy"])

    async def test_version_pinned_linked_assets_are_acl_and_version_bound(self) -> None:
        css_id = self.css["artifact"]["id"]
        css_version = self.css["version"]["id"]
        javascript_id = self.javascript["artifact"]["id"]
        javascript_version = self.javascript["version"]["id"]
        css_url = versioned_content_url(css_id, css_version)
        javascript_url = versioned_content_url(javascript_id, javascript_version)
        html_source = (
            "<!doctype html><html><head>"
            f'<link rel="stylesheet" href="{css_url}"></head><body>'
            '<main id="asset-recipe">Linked assets</main>'
            f'<script src="{javascript_url}"></script></body></html>'
        ).encode()
        linked = self.service.create_artifact(
            tenant_id="web",
            name="linked-site.html",
            data=html_source,
            media_type="text/html",
            actor="dev",
        )
        self.service.link("web", linked["artifact"]["id"], self.css["artifact"]["id"], "references")
        self.service.link(
            "web", linked["artifact"]["id"], self.javascript["artifact"]["id"], "references"
        )
        status, _, rendered = await call(
            self.renderer,
            "GET",
            f"/render/{linked['artifact']['id']}",
            query=f"version_id={linked['version']['id']}",
            fetch_dest="iframe",
        )
        self.assertEqual(status, 200)
        self.assertEqual(rendered, html_source)
        self.assertIn(css_url.encode(), rendered)
        self.assertIn(javascript_url.encode(), rendered)

        for artifact_id, version_id, expected in (
            (css_id, css_version, b"body{color:red}"),
            (javascript_id, javascript_version, b"document.body.dataset.ran='yes'"),
        ):
            status, _, content = await call(
                self.renderer, "GET", versioned_content_url(artifact_id, version_id)
            )
            self.assertEqual(status, 200)
            self.assertEqual(content, expected)

        css_updated = self.service.write_version(
            tenant_id="web",
            artifact_id=css_id,
            data=b"body{color:blue}",
            media_type="text/css",
            actor="dev",
            reason="asset version regression",
            source_context={},
            parent_version_id=css_version,
        )
        status, _, old_content = await call(
            self.renderer, "GET", versioned_content_url(css_id, css_version)
        )
        self.assertEqual(status, 200)
        self.assertEqual(old_content, b"body{color:red}")
        status, _, new_content = await call(
            self.renderer, "GET", versioned_content_url(css_id, css_updated["id"])
        )
        self.assertEqual(status, 200)
        self.assertEqual(new_content, b"body{color:blue}")

        # A version belongs to one artifact.  The renderer must not turn a
        # mismatched immutable ID pair into a readable resource.
        status, _, body = await call(
            self.renderer, "GET", versioned_content_url(css_id, javascript_version)
        )
        self.assertIn(status, {400, 404})
        self.assertNotIn(b"body{color:red}", body)

        # The MCP caller supplies tenant and ACL enforcement; the renderer
        # never reads another tenant just because an ID was placed in a URL.
        other = self.service.create_artifact(
            tenant_id="other",
            name="private.css",
            data=b"body{color:lime}",
            media_type="text/css",
            actor="other-user",
        )
        status, _, body = await call(
            self.renderer,
            "GET",
            versioned_content_url(other["artifact"]["id"], other["version"]["id"]),
        )
        self.assertIn(status, {400, 404})
        self.assertNotIn(b"color:lime", body)

        status, _, body = await call(self.renderer, "GET", "/content/art_missing/ver_missing")
        self.assertIn(status, {400, 404})
        self.assertNotIn(b"private.css", body)

        private = self.service.create_artifact(
            tenant_id="web",
            name="unshared.css",
            data=b"body{color:orange}",
            media_type="text/css",
            actor="owner-only",
        )
        token = set_request_principal(
            Principal(
                tenant_id="web",
                actor_id="web-user",
                issuer="https://issuer.example",
                subject="web-user-subject",
                scopes=frozenset({"artifact:read"}),
            )
        )
        try:
            status, _, body = await call(
                self.renderer,
                "GET",
                versioned_content_url(private["artifact"]["id"], private["version"]["id"]),
            )
        finally:
            reset_request_principal(token)
        self.assertIn(status, {400, 404})
        self.assertNotIn(b"color:orange", body)

    async def test_signed_renderer_capability_is_scoped_and_expires(self) -> None:
        relay = SignedPrincipalRelay(
            "http://folio.internal:8000/mcp",
            "renderer-capability-secret-012345678901234567890123456789",
            ttl_seconds=1,
        )
        principal = Principal(
            tenant_id="web",
            actor_id="web-user",
            issuer="folio-local-renderer",
            subject="web-user",
            scopes=frozenset({"artifact:read"}),
        )
        arguments = {"artifact_id": "art_private", "version_id": "ver_immutable"}
        token = relay.issue(principal, tool="artifact_read", arguments=arguments)
        capability = relay.resolve_capability(token)
        self.assertIsNotNone(capability)
        assert capability is not None
        self.assertEqual(capability.principal.tenant_id, "web")
        self.assertEqual(capability.principal.actor_id, "web-user")
        self.assertEqual(capability.binding.tool, "artifact_read")
        self.assertNotEqual(
            capability.binding.arguments_digest,
            relay.issue(principal, tool="artifact_read", arguments={"artifact_id": "other"}),
        )
        encoded, separator, signature = token.partition(".")
        self.assertTrue(separator)
        self.assertEqual(relay._b64(relay._decode(encoded)), encoded)
        self.assertEqual(relay._b64(relay._decode(signature)), signature)
        for malformed in (
            f"{encoded}=.{signature}",
            f"{encoded}. {signature}",
            f"{encoded}.{signature}/",
            f"{encoded}\n.{signature}",
            f"{encoded}.{signature}=",
        ):
            self.assertIsNone(relay.resolve_capability(malformed))
        forged = f"{token[:-1]}{'A' if token[-1] != 'A' else 'B'}"
        self.assertIsNone(relay.resolve_capability(forged))
        other_audience = SignedPrincipalRelay(
            "http://other.internal:8000/mcp",
            "renderer-capability-secret-012345678901234567890123456789",
        )
        self.assertIsNone(relay.resolve_capability(other_audience.issue_identity(principal)))
        wrong_key = SignedPrincipalRelay(
            "http://folio.internal:8000/mcp",
            "different-renderer-secret-012345678901234567890123456789",
        )
        self.assertIsNone(relay.resolve_capability(wrong_key.issue_identity(principal)))
        payload = json.loads(relay._decode(encoded))
        payload["v"] = 2
        wrong_version_encoded = relay._encode(payload)
        wrong_version_signature = relay._b64(
            hmac.new(relay._signing_key, wrong_version_encoded, "sha256").digest()
        )
        self.assertIsNone(
            relay.resolve_capability(
                f"{wrong_version_encoded.decode('ascii')}.{wrong_version_signature}"
            )
        )
        for invalid_endpoint in (
            "http://folio.internal:8000/mcp?token=secret",
            "http://user:password@folio.internal:8000/mcp",
            "http://folio.internal:8000/mcp#fragment",
        ):
            with self.assertRaises(ValueError):
                SignedPrincipalRelay(
                    invalid_endpoint,
                    "renderer-capability-secret-012345678901234567890123456789",
                )
        relay.revoke(token)
        self.assertIsNone(relay.resolve_capability(token))
        fresh = relay.issue(principal, tool="artifact_read", arguments=arguments)
        capability = relay.resolve_capability(fresh)
        self.assertIsNotNone(capability)
        await asyncio.sleep(2.05)
        self.assertIsNone(relay.resolve_capability(fresh))

    async def test_renderer_capability_allows_only_exact_artifact_read(self) -> None:
        readable = self.service.create_artifact(
            tenant_id="web",
            name="capability.css",
            data=b"body{color:purple}",
            media_type="text/css",
            actor="web-user",
        )
        artifact_id = readable["artifact"]["id"]
        version_id = readable["version"]["id"]
        arguments = {"artifact_id": artifact_id, "version_id": version_id}
        relay = SignedPrincipalRelay(
            CONTROL_ORIGIN + "/mcp",
            "renderer-capability-secret-012345678901234567890123456789",
        )
        principal = Principal(
            tenant_id="web",
            actor_id="web-user",
            issuer="folio-local-renderer",
            subject="web-user",
            scopes=frozenset({"artifact:read", "artifact:search"}),
        )
        capability = relay.resolve_capability(
            relay.issue(principal, tool="artifact_read", arguments=arguments)
        )
        assert capability is not None
        principal_token = set_request_principal(principal)
        capability_token = set_request_capability(capability.binding)
        try:
            caller = LocalMcpCaller(
                build_mcp_server(self.service, tenant_id="web", actor="web-user")
            )
            read = await caller.call("artifact_read", arguments)
            self.assertEqual(read["version"]["id"], version_id)
            with self.assertRaises(PublicMcpError):
                await caller.call("artifact_search", {"query": "body"})
            with self.assertRaises(PublicMcpError):
                await caller.call("artifact_read", {"artifact_id": artifact_id})
        finally:
            reset_request_capability(capability_token)
            reset_request_principal(principal_token)

    async def test_renderer_denies_top_level_render_navigation_but_allows_iframe(self) -> None:
        html_id = self.html["artifact"]["id"]
        status, headers, body = await call(
            self.renderer, "GET", f"/render/{html_id}", fetch_dest="document"
        )
        self.assertEqual(status, 404)
        self.assertNotIn(b"<h1>Page</h1>", body)
        self.assertIn("sandbox allow-scripts", headers["content-security-policy"])

        status, _, body = await call(self.renderer, "GET", f"/render/{html_id}")
        self.assertEqual(status, 404)
        self.assertNotIn(b"<h1>Page</h1>", body)

        status, _, body = await call(
            self.renderer, "GET", f"/render/{html_id}", fetch_dest="iframe"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<h1>Page</h1>")

    def test_configuration_is_canonical_and_hosted_mode_fails_closed(self) -> None:
        with patch.dict(os.environ, {"TEST_ORIGIN": "https://Example.COM:8443/"}):
            self.assertEqual(
                _origin_env("TEST_ORIGIN", "http://unused"), "https://example.com:8443"
            )
        for value in (
            "https://user@example.com",
            "https://example.com/path",
            "https://example.com 'none'",
            "http://:8000",
            "file:///tmp/render",
            "https://example.com:99999",
        ):
            with patch.dict(os.environ, {"TEST_ORIGIN": value}):
                with self.assertRaisesRegex(ValueError, "HTTP origin"):
                    _origin_env("TEST_ORIGIN", "http://unused")
        with patch.dict(os.environ, {"FOLIO_MCP_URL": "https://user@example.com/mcp"}):
            with self.assertRaisesRegex(ValueError, "without credentials"):
                _optional_mcp_url_env("FOLIO_MCP_URL")
        with patch.dict(os.environ, {"FOLIO_DEPLOYMENT_MODE": "hosted"}):
            with self.assertRaisesRegex(ValueError, "authentication adapter"):
                Settings.from_env()

        bearer_config = {
            "FOLIO_DEPLOYMENT_MODE": "hosted",
            "FOLIO_OIDC_ISSUER": "https://issuer.example",
            "FOLIO_OIDC_AUDIENCE": "folio-api",
            "FOLIO_OIDC_JWKS_URL": "https://issuer.example/jwks.json",
        }
        hostile_values = (
            ("FOLIO_OIDC_ISSUER", "http://issuer.example"),
            ("FOLIO_OIDC_ISSUER", "https://user:pass@issuer.example"),
            ("FOLIO_OIDC_JWKS_URL", "http://issuer.example/jwks.json"),
            ("FOLIO_OIDC_JWKS_URL", "https://issuer.example/jwks.json?x=1"),
            ("FOLIO_OIDC_AUDIENCE", "folio api"),
            ("FOLIO_OIDC_ALGORITHM", "HS256"),
            ("FOLIO_OIDC_JWKS_TIMEOUT_SECONDS", "0"),
            ("FOLIO_OIDC_JWKS_CACHE_SECONDS", "nan"),
            ("FOLIO_OIDC_CLOCK_SKEW_SECONDS", "-1"),
        )
        for name, value in hostile_values:
            with self.subTest(name=name, value=value):
                with patch.dict(os.environ, {**bearer_config, name: value}, clear=True):
                    with self.assertRaisesRegex(ValueError, "authentication adapter"):
                        Settings.from_env()

        browser_config = {
            "FOLIO_DEPLOYMENT_MODE": "hosted",
            "FOLIO_OIDC_ISSUER": "https://issuer.example",
            "FOLIO_OIDC_AUDIENCE": "folio-api",
            "FOLIO_OIDC_JWKS_URL": "https://issuer.example/jwks.json",
            "FOLIO_OIDC_AUTHORIZATION_ENDPOINT": "https://issuer.example/authorize",
            "FOLIO_OIDC_TOKEN_ENDPOINT": "https://issuer.example/token",
            "FOLIO_OIDC_CLIENT_ID": "folio-browser",
            "FOLIO_OIDC_REDIRECT_URI": "https://folio.example/auth/callback",
        }
        bearer_only_config = {
            key: value
            for key, value in browser_config.items()
            if "AUTHORIZATION_ENDPOINT" not in key
            and "TOKEN_ENDPOINT" not in key
            and "CLIENT_ID" not in key
            and "REDIRECT_URI" not in key
        }
        with patch.dict(os.environ, bearer_only_config, clear=True):
            self.assertFalse(Settings.from_env().browser_auth_configured)
        with patch.dict(os.environ, browser_config, clear=True):
            settings = Settings.from_env()
        self.assertTrue(settings.browser_auth_configured)
        self.assertEqual(settings.auth_client_id, "folio-browser")
        self.assertEqual(settings.auth_redirect_uri, "https://folio.example/auth/callback")

        incomplete_config = {
            key: value
            for key, value in browser_config.items()
            if key != "FOLIO_OIDC_TOKEN_ENDPOINT"
        }
        with patch.dict(os.environ, incomplete_config, clear=True):
            with self.assertRaisesRegex(ValueError, "authentication adapter"):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
