import asyncio
import base64
import hmac
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mcp import Client, StdioServerParameters

from folio_lattice.auth import Principal
from folio_lattice.public_mcp import HttpMcpClient, PublicMcpError, SignedPrincipalRelay
from folio_lattice.service import FolioLattice

RENDERER_CAPABILITY_SECRET = "test-renderer-capability-secret-012345678901234567890123"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_ready(base_url: str, process: subprocess.Popen[bytes]) -> dict[str, Any]:
    for _ in range(150):
        try:
            with urllib.request.urlopen(f"{base_url}/readyz", timeout=0.2) as response:
                value = json.loads(response.read())
                if value["ready"]:
                    return value
        except Exception:
            time.sleep(0.05)
    assert process.stderr is not None
    process.terminate()
    raise AssertionError(process.stderr.read().decode())


async def async_mcp_call(base_url: str, tool: str, arguments: dict[str, Any]) -> Any:
    async with Client(f"{base_url}/mcp", raise_exceptions=True) as client:
        response = await client.call_tool(tool, arguments)
    assert not response.is_error, response
    assert response.structured_content is not None
    return response.structured_content.get("result", response.structured_content)


def mcp_call(base_url: str, tool: str, arguments: dict[str, Any]) -> Any:
    return asyncio.run(async_mcp_call(base_url, tool, arguments))


async def async_mcp_error(base_url: str, tool: str, arguments: dict[str, Any]) -> str:
    async with Client(f"{base_url}/mcp") as client:
        response = await client.call_tool(tool, arguments)
    assert response.is_error
    return response.content[0].text


def ui_call(base_url: str, tool: str, arguments: dict[str, Any]) -> Any:
    return post_json(
        f"{base_url}/api/mcp",
        {"tool": tool, "arguments": arguments},
        origin=base_url,
    )


def post_json(url: str, payload: dict[str, Any], *, origin: str) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Origin": origin},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def start_server(environment: dict[str, str], transport: str, port: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "folio_lattice.server",
            "--transport",
            transport,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def stop_server(process: subprocess.Popen[bytes]) -> str:
    process.terminate()
    stdout, stderr = process.communicate(timeout=5)
    return (stdout + stderr).decode(errors="replace")


def iframe_get(url: str) -> Any:
    return urllib.request.urlopen(urllib.request.Request(url, headers={"Sec-Fetch-Dest": "iframe"}))


def create(base_url: str, name: str, content: bytes, media_type: str) -> dict[str, Any]:
    return mcp_call(
        base_url,
        "artifact_create",
        {
            "name": name,
            "media_type": media_type,
            "content_base64": base64.b64encode(content).decode(),
            "reason": "public E2E fixture",
        },
    )


class HttpE2ETests(unittest.TestCase):
    def test_public_contract_quickstart_regression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control_port, render_port = free_port(), free_port()
            control_origin = f"http://127.0.0.1:{control_port}"
            render_origin = f"http://127.0.0.1:{render_port}"
            environment = {
                **os.environ,
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                "FOLIO_TENANT_ID": "fl-urj-13-1-60c7099",
                "FOLIO_ACTOR": "public-contract-fixture",
                "FOLIO_CONTROL_ORIGIN": control_origin,
                "FOLIO_RENDER_ORIGIN": render_origin,
                "FOLIO_RENDERER_CAPABILITY_SECRET": RENDERER_CAPABILITY_SECRET,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            control = start_server(environment, "http", control_port)
            renderer: subprocess.Popen[bytes] | None = None
            try:
                wait_ready(control_origin, control)
                renderer = start_server(
                    {**environment, "FOLIO_MCP_URL": f"{control_origin}/mcp"},
                    "renderer",
                    render_port,
                )
                wait_ready(render_origin, renderer)
                result = subprocess.run(
                    [sys.executable, "tests/public_contract_quickstart.py"],
                    cwd=Path(__file__).parents[1],
                    env={
                        **environment,
                        "FOLIO_BASE_URL": control_origin,
                        "FOLIO_RENDER_URL": render_origin,
                    },
                    text=True,
                    capture_output=True,
                    timeout=45,
                    check=True,
                )
                evidence = json.loads(result.stdout)
                self.assertEqual(evidence["status"], "ok")
                self.assertEqual(evidence["artifact_count"], 12)
                self.assertEqual(evidence["linked_count"], 4)
                self.assertEqual(evidence["discovered_count"], 5)
                self.assertEqual(evidence["component_count"], 5)
                self.assertEqual(evidence["chunk_field"], "result.content")
                self.assertEqual(evidence["version_count"], 2)
                self.assertGreaterEqual(evidence["elapsed_ms"], 0)
            finally:
                if renderer is not None:
                    stop_server(renderer)
                logs = stop_server(control)
                self.assertIn('"decision":"allow"', logs)
                self.assertIn('"decision":"deny"', logs)

    def test_renderer_capability_isolated_across_actors_tenants_and_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control_port, render_port = free_port(), free_port()
            control_origin = f"http://127.0.0.1:{control_port}"
            render_origin = f"http://127.0.0.1:{render_port}"
            environment = {
                **os.environ,
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                "FOLIO_TENANT_ID": "capability-tenant",
                "FOLIO_ACTOR": "owner-actor",
                "FOLIO_CONTROL_ORIGIN": control_origin,
                "FOLIO_RENDER_ORIGIN": render_origin,
                "FOLIO_RENDERER_CAPABILITY_SECRET": RENDERER_CAPABILITY_SECRET,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            control = start_server(environment, "http", control_port)
            renderer: subprocess.Popen[bytes] | None = None
            renderer_logs = ""
            control_logs = ""
            replay_token: str | None = None
            expired_token: str | None = None
            try:
                wait_ready(control_origin, control)
                owner = create(control_origin, "owner.css", b"OWNER_MARKER", "text/css")
                service = FolioLattice(root / "folio.db", root / "blobs")
                private = service.create_artifact(
                    tenant_id="capability-tenant",
                    name="private.css",
                    data=b"PRIVATE_MARKER",
                    media_type="text/css",
                    actor="other-actor",
                )
                cross_tenant = service.create_artifact(
                    tenant_id="another-tenant",
                    name="cross.css",
                    data=b"CROSS_TENANT_MARKER",
                    media_type="text/css",
                    actor="owner-actor",
                )
                renderer = start_server(
                    {**environment, "FOLIO_MCP_URL": f"{control_origin}/mcp"},
                    "renderer",
                    render_port,
                )
                wait_ready(render_origin, renderer)

                def read_content(artifact: dict[str, Any]) -> bytes:
                    url = (
                        f"{render_origin}/content/{artifact['artifact']['id']}"
                        f"/{artifact['version']['id']}"
                    )
                    with urllib.request.urlopen(
                        urllib.request.Request(url, headers={"Sec-Fetch-Dest": "style"}),
                        timeout=10,
                    ) as response:
                        return response.read()

                self.assertEqual(read_content(owner), b"OWNER_MARKER")
                for artifact, marker in (
                    (private, b"PRIVATE_MARKER"),
                    (cross_tenant, b"CROSS_TENANT_MARKER"),
                ):
                    url = (
                        f"{render_origin}/content/{artifact['artifact']['id']}"
                        f"/{artifact['version']['id']}"
                    )
                    with self.assertRaises(urllib.error.HTTPError) as denied:
                        urllib.request.urlopen(
                            urllib.request.Request(url, headers={"Sec-Fetch-Dest": "style"}),
                            timeout=10,
                        )
                    self.assertIn(denied.exception.code, {400, 401, 404})
                    self.assertNotIn(marker, denied.exception.read())

                mismatch_url = f"{render_origin}/content/{owner['artifact']['id']}/ver_mismatched"
                with self.assertRaises(urllib.error.HTTPError) as mismatch:
                    urllib.request.urlopen(
                        urllib.request.Request(mismatch_url, headers={"Sec-Fetch-Dest": "style"}),
                        timeout=10,
                    )
                self.assertIn(mismatch.exception.code, {400, 404})

                async def forged_call() -> None:
                    forged = HttpMcpClient(
                        f"{control_origin}/mcp",
                        principal_relay=SignedPrincipalRelay(
                            f"{control_origin}/mcp",
                            "wrong-renderer-secret-012345678901234567890123",
                        ),
                        principal=Principal(
                            tenant_id="capability-tenant",
                            actor_id="owner-actor",
                            issuer="forged",
                            subject="forged",
                            scopes=frozenset({"artifact:read"}),
                        ),
                    )
                    with self.assertRaises(PublicMcpError):
                        await forged.call("artifact_read", {"artifact_id": owner["artifact"]["id"]})

                asyncio.run(forged_call())

                shared = service.share_artifact(
                    tenant_id="capability-tenant",
                    artifact_id=owner["artifact"]["id"],
                    actor="owner-actor",
                    subject_actor_id="reader-actor",
                    reason="capability ACL regression",
                )
                reader_principal = Principal(
                    tenant_id="capability-tenant",
                    actor_id="reader-actor",
                    issuer="test-reader",
                    subject="test-reader",
                    scopes=frozenset({"artifact:read"}),
                )
                reader_relay = SignedPrincipalRelay(
                    f"{control_origin}/mcp", RENDERER_CAPABILITY_SECRET
                )
                reader = HttpMcpClient(
                    f"{control_origin}/mcp",
                    principal_relay=reader_relay,
                    principal=reader_principal,
                )
                read_arguments = {
                    "artifact_id": owner["artifact"]["id"],
                    "version_id": owner["version"]["id"],
                }
                replay_token = reader_relay.issue(
                    reader_principal, tool="artifact_read", arguments=read_arguments
                )

                async def replay_read() -> Any:
                    result = await reader._session_call(
                        replay_token,
                        lambda session: session.call_tool("artifact_read", read_arguments),
                    )
                    return reader._decode_result(result)

                self.assertEqual(
                    asyncio.run(replay_read())["version"]["id"], owner["version"]["id"]
                )
                service.revoke_share(
                    tenant_id="capability-tenant",
                    artifact_id=owner["artifact"]["id"],
                    actor="owner-actor",
                    grant_id=shared["id"],
                    reason="capability ACL regression revoke",
                )
                with self.assertRaises(PublicMcpError):
                    asyncio.run(replay_read())
                reader_relay.revoke(replay_token)
                self.assertIsNone(reader_relay.resolve_capability(replay_token))

                expiry_artifact = service.create_artifact(
                    tenant_id="capability-tenant",
                    name="reader-owned.css",
                    data=b"READER_AUTHORIZED_MARKER",
                    media_type="text/css",
                    actor="reader-actor",
                )
                expiry_arguments = {
                    "artifact_id": expiry_artifact["artifact"]["id"],
                    "version_id": expiry_artifact["version"]["id"],
                }
                expired_relay = SignedPrincipalRelay(
                    f"{control_origin}/mcp", RENDERER_CAPABILITY_SECRET
                )
                expired_token = expired_relay.issue(
                    reader_principal, tool="artifact_read", arguments=expiry_arguments
                )
                expired_encoded, separator, expired_signature = expired_token.partition(".")
                assert separator
                expired_payload = json.loads(expired_relay._decode(expired_encoded))
                expired_payload["iat"] = int(time.time()) - 120
                expired_payload["exp"] = int(time.time()) - 60
                expired_encoded = expired_relay._encode(expired_payload)
                expired_signature = expired_relay._b64(
                    hmac.new(
                        expired_relay._signing_key,
                        expired_encoded,
                        "sha256",
                    ).digest()
                )
                expired_token = f"{expired_encoded.decode('ascii')}.{expired_signature}"
                expired_reader = HttpMcpClient(
                    f"{control_origin}/mcp",
                    principal_relay=expired_relay,
                    principal=reader_principal,
                )
                fresh_read = asyncio.run(expired_reader.call("artifact_read", expiry_arguments))
                self.assertEqual(fresh_read["version"]["id"], expiry_artifact["version"]["id"])
                with patch.object(expired_relay, "issue", return_value=expired_token):
                    with self.assertRaises(PublicMcpError):
                        asyncio.run(expired_reader.call("artifact_read", expiry_arguments))
            finally:
                if renderer is not None:
                    renderer_logs = stop_server(renderer)
                control_logs = stop_server(control)
            for token in (replay_token, expired_token):
                if token is not None:
                    self.assertNotIn(token, renderer_logs)
                    self.assertNotIn(token, control_logs)

    def test_http_and_black_box_hyperset_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            port = free_port()
            base_url = f"http://127.0.0.1:{port}"
            environment = {
                **os.environ,
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                "FOLIO_TENANT_ID": "hyperset-test",
                "FOLIO_ACTOR": "hyperset",
                "FOLIO_CONTROL_ORIGIN": base_url,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            process = start_server(environment, "http", port)
            try:
                health = wait_ready(base_url, process)
                self.assertEqual(health["deployment_mode"], "local")
                self.assertEqual(health["authentication"], "none-local-development")
                result = subprocess.run(
                    [sys.executable, "tests/hyperset_consumer.py"],
                    cwd=Path(__file__).parents[1],
                    env={**environment, "FOLIO_BASE_URL": base_url},
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=True,
                )
                consumer = json.loads(result.stdout)
                self.assertEqual(consumer["tenant_id"], "hyperset-test")
                self.assertEqual(len(consumer["blob_hash"]), 64)
            finally:
                stop_server(process)

    def test_cross_tenant_read_search_and_traverse_fail_closed_over_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_port, second_port = free_port(), free_port()
            first_url = f"http://127.0.0.1:{first_port}"
            second_url = f"http://127.0.0.1:{second_port}"
            common = {
                **os.environ,
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            first = start_server(
                {**common, "FOLIO_TENANT_ID": "tenant-a", "FOLIO_CONTROL_ORIGIN": first_url},
                "http",
                first_port,
            )
            second: subprocess.Popen[bytes] | None = None
            try:
                wait_ready(first_url, first)
                source = create(first_url, "private.txt", b"tenantonlymarker", "text/plain")
                target = create(first_url, "target.txt", b"private target", "text/plain")
                source_read = mcp_call(
                    first_url, "artifact_read", {"artifact_id": source["artifact"]["id"]}
                )
                mcp_call(
                    first_url,
                    "graph_link",
                    {
                        "source_artifact_id": source["artifact"]["id"],
                        "target_artifact_id": target["artifact"]["id"],
                        "edge_type": "private-edge",
                    },
                )
                second = start_server(
                    {
                        **common,
                        "FOLIO_TENANT_ID": "tenant-b",
                        "FOLIO_CONTROL_ORIGIN": second_url,
                    },
                    "http",
                    second_port,
                )
                wait_ready(second_url, second)
                artifact_id = source["artifact"]["id"]
                self.assertIn(
                    "artifact not found",
                    asyncio.run(
                        async_mcp_error(second_url, "artifact_read", {"artifact_id": artifact_id})
                    ),
                )
                self.assertIn(
                    "chunk not found",
                    asyncio.run(
                        async_mcp_error(
                            second_url,
                            "artifact_read_chunk",
                            {"chunk_id": source_read["chunks"][0]["id"]},
                        )
                    ),
                )
                self.assertEqual(
                    mcp_call(second_url, "artifact_search", {"query": "tenantonlymarker"}), []
                )
                self.assertEqual(
                    mcp_call(second_url, "artifact_grep", {"pattern": "tenantonlymarker"}), []
                )
                self.assertIn(
                    "artifact not found",
                    asyncio.run(
                        async_mcp_error(
                            second_url,
                            "graph_traverse",
                            {"start_artifact_id": artifact_id},
                        )
                    ),
                )
            finally:
                if second is not None:
                    stop_server(second)
                stop_server(first)

    def test_hosted_mode_refuses_to_listen(self) -> None:
        port = free_port()
        process = start_server(
            {
                **os.environ,
                "FOLIO_DEPLOYMENT_MODE": "hosted",
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            },
            "http",
            port,
        )
        _, stderr = process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn(b"hosted mode requires an authentication adapter", stderr)

    def test_bridge_deadline_applies_to_real_mcp_process_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            port = free_port()
            base_url = f"http://127.0.0.1:{port}"
            process = start_server(
                {
                    **os.environ,
                    "FOLIO_DB_PATH": str(root / "folio.db"),
                    "FOLIO_BLOB_ROOT": str(root / "blobs"),
                    "FOLIO_CONTROL_ORIGIN": base_url,
                    "FOLIO_BRIDGE_TIMEOUT_SECONDS": "0.000001",
                    "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
                },
                "http",
                port,
            )
            try:
                wait_ready(base_url, process)
                with self.assertRaises(urllib.error.HTTPError) as timed_out:
                    post_json(
                        f"{base_url}/api/bridge",
                        {
                            "request_id": "deadline",
                            "artifact_id": "art_context",
                            "attachment": "folio-lattice",
                            "tool": "artifact_search",
                            "arguments": {"query": "anything"},
                        },
                        origin=base_url,
                    )
                self.assertEqual(timed_out.exception.code, 504)
                self.assertIn(b"timed out", timed_out.exception.read())
            finally:
                logs = stop_server(process)
                self.assertIn('"reason":"timeout"', logs)


class InspectionRendererE2ETests(unittest.TestCase):
    def test_full_core_loop_uses_public_mcp_ui_bridge_and_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control_port, render_port = free_port(), free_port()
            control_origin = f"http://127.0.0.1:{control_port}"
            render_origin = f"http://127.0.0.1:{render_port}"
            environment = {
                **os.environ,
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                "FOLIO_TENANT_ID": "ui-test",
                "FOLIO_ACTOR": "inspection-user",
                "FOLIO_CONTROL_ORIGIN": control_origin,
                "FOLIO_RENDER_ORIGIN": render_origin,
                "FOLIO_RENDERER_CAPABILITY_SECRET": RENDERER_CAPABILITY_SECRET,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            control = start_server(environment, "http", control_port)
            renderer: subprocess.Popen[bytes] | None = None
            try:
                wait_ready(control_origin, control)
                html_source = b"<h1>First</h1><script>document.body.dataset.ran='yes'</script>"
                html = ui_call(
                    control_origin,
                    "artifact_create",
                    {
                        "name": "page.html",
                        "media_type": "text/html",
                        "content_base64": base64.b64encode(html_source).decode(),
                        "reason": "UI E2E create",
                        "source_context": {"interface": "inspection-ui"},
                    },
                )
                target = create(
                    control_origin, "target.txt", b"graph searchable target", "text/plain"
                )
                css = create(
                    control_origin, "theme.css", b"body { color: rgb(1, 2, 3); }", "text/css"
                )
                javascript = create(
                    control_origin,
                    "app.js",
                    b"document.body.dataset.javascript = 'ran'",
                    "application/javascript",
                )
                binary = create(control_origin, "data.bin", b"\x00\x01", "application/octet-stream")
                artifact_id = html["artifact"]["id"]
                first_version = html["version"]["id"]

                read = ui_call(control_origin, "artifact_read", {"artifact_id": artifact_id})
                self.assertEqual(read["text"].encode(), html_source)
                self.assertEqual(read["artifact"]["name"], "page.html")
                chunk = ui_call(
                    control_origin, "artifact_read_chunk", {"chunk_id": read["chunks"][0]["id"]}
                )
                self.assertEqual(chunk["content"].encode(), html_source)
                self.assertTrue(ui_call(control_origin, "artifact_search", {"query": "searchable"}))
                self.assertTrue(
                    ui_call(control_origin, "artifact_grep", {"pattern": "graph searchable"})
                )
                ui_call(
                    control_origin,
                    "graph_link",
                    {
                        "source_artifact_id": artifact_id,
                        "target_artifact_id": target["artifact"]["id"],
                        "edge_type": "references",
                    },
                )
                graph = ui_call(
                    control_origin, "graph_traverse", {"start_artifact_id": artifact_id}
                )
                self.assertEqual(graph[0]["target_artifact_id"], target["artifact"]["id"])

                updated_text = "<h1>Second</h1><script>document.body.dataset.ran='yes'</script>"
                write_args = {
                    "artifact_id": artifact_id,
                    "parent_version_id": first_version,
                    "media_type": "text/html",
                    "reason": "browser edit",
                    "content_base64": base64.b64encode(updated_text.encode()).decode(),
                    "source_context": {"interface": "inspection-ui"},
                }
                updated = ui_call(control_origin, "artifact_write", write_args)
                self.assertEqual(updated["actor"], "inspection-user")
                self.assertEqual(
                    len(ui_call(control_origin, "artifact_versions", {"artifact_id": artifact_id})),
                    2,
                )
                with self.assertRaises(urllib.error.HTTPError) as stale:
                    ui_call(control_origin, "artifact_write", write_args)
                self.assertEqual(stale.exception.code, 409)
                self.assertEqual(
                    len(
                        mcp_call(control_origin, "artifact_versions", {"artifact_id": artifact_id})
                    ),
                    2,
                )

                bridge_base = {
                    "artifact_id": artifact_id,
                    "attachment": "folio-lattice",
                    "arguments": {"artifact_id": artifact_id},
                }
                bridged = post_json(
                    f"{control_origin}/api/bridge",
                    {**bridge_base, "request_id": "allow-1", "tool": "artifact_read"},
                    origin=control_origin,
                )
                self.assertEqual(bridged["result"]["text"], updated_text)
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    post_json(
                        f"{control_origin}/api/bridge",
                        {**bridge_base, "request_id": "deny-1", "tool": "artifact_write"},
                        origin=control_origin,
                    )
                self.assertEqual(denied.exception.code, 403)
                large = create(
                    control_origin,
                    "large.txt",
                    b"x" * (1024 * 1024),
                    "text/plain",
                )
                with self.assertRaises(urllib.error.HTTPError) as oversized_result:
                    post_json(
                        f"{control_origin}/api/bridge",
                        {
                            "request_id": "large-result",
                            "artifact_id": large["artifact"]["id"],
                            "attachment": "folio-lattice",
                            "tool": "artifact_read",
                            "arguments": {"artifact_id": large["artifact"]["id"]},
                        },
                        origin=control_origin,
                    )
                self.assertEqual(oversized_result.exception.code, 502)
                oversized_body = urllib.request.Request(
                    f"{control_origin}/api/bridge",
                    data=b"x" * (64 * 1024 + 1),
                    headers={"Content-Type": "application/json", "Origin": control_origin},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as body_error:
                    urllib.request.urlopen(oversized_body)
                self.assertEqual(body_error.exception.code, 413)

                render_environment = {
                    **environment,
                    "FOLIO_MCP_URL": f"{control_origin}/mcp",
                }
                renderer = start_server(render_environment, "renderer", render_port)
                wait_ready(render_origin, renderer)
                with urllib.request.urlopen(f"{control_origin}/inspect/{artifact_id}") as response:
                    page = response.read().decode()
                    self.assertIn('sandbox="allow-scripts"', page)
                    self.assertNotIn("allow-same-origin", page)
                    self.assertIn("Unauthenticated local development", page)
                with iframe_get(
                    f"{render_origin}/render/{artifact_id}?version_id={updated['id']}"
                ) as response:
                    self.assertEqual(response.read().decode(), updated_text)
                    policy = response.headers["Content-Security-Policy"]
                    self.assertIn("connect-src 'none'", policy)
                    self.assertIn(f"frame-ancestors {control_origin}", policy)
                with self.assertRaises(urllib.error.HTTPError) as top_level:
                    urllib.request.urlopen(
                        urllib.request.Request(
                            f"{render_origin}/render/{artifact_id}?version_id={updated['id']}",
                            headers={"Sec-Fetch-Dest": "document"},
                        )
                    )
                self.assertEqual(top_level.exception.code, 404)
                self.assertNotIn(updated_text, top_level.exception.read().decode())
                with iframe_get(
                    f"{render_origin}/render/{artifact_id}?version_id={first_version}"
                ) as response:
                    self.assertIn("<h1>First</h1>", response.read().decode())
                self._assert_wrapped_resource(render_origin, css, "link")
                self._assert_wrapped_resource(render_origin, javascript, "script")
                with self.assertRaises(urllib.error.HTTPError) as unsupported:
                    iframe_get(f"{render_origin}/render/{binary['artifact']['id']}")
                self.assertEqual(unsupported.exception.code, 415)
            finally:
                if renderer is not None:
                    stop_server(renderer)
                logs = stop_server(control)
                self.assertIn('"decision":"allow"', logs)
                self.assertIn('"decision":"deny"', logs)
                self.assertNotIn(updated_text, logs)

    def _assert_wrapped_resource(
        self, render_origin: str, created: dict[str, Any], element: str
    ) -> None:
        artifact_id = created["artifact"]["id"]
        version_id = created["version"]["id"]
        with iframe_get(f"{render_origin}/render/{artifact_id}") as response:
            self.assertIn(f"<{element}", response.read().decode())
        with urllib.request.urlopen(
            f"{render_origin}/content/{artifact_id}/{version_id}"
        ) as response:
            self.assertTrue(response.read())


class StdioE2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_mcp_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "folio_lattice.server", "--transport", "stdio"],
                env={
                    **os.environ,
                    "FOLIO_DB_PATH": str(root / "folio.db"),
                    "FOLIO_BLOB_ROOT": str(root / "blobs"),
                    "FOLIO_TENANT_ID": "stdio-test",
                    "FOLIO_ACTOR": "stdio-client",
                    "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
                },
            )
            async with Client(parameters, raise_exceptions=True) as client:
                created = await client.call_tool(
                    "artifact_create",
                    {
                        "name": "stdio.txt",
                        "media_type": "text/plain",
                        "content_base64": base64.b64encode(b"stdio graph content").decode(),
                    },
                )
                self.assertFalse(created.is_error)
                assert created.structured_content is not None
                self.assertEqual(created.structured_content["artifact"]["tenant_id"], "stdio-test")


if __name__ == "__main__":
    unittest.main()
