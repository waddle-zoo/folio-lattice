import base64
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

from mcp import Client, StdioServerParameters

from folio_lattice.service import FolioLattice


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_ready(base_url: str, process: subprocess.Popen[bytes]) -> None:
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=0.2) as response:
                if json.loads(response.read())["ready"]:
                    return
        except Exception:
            time.sleep(0.05)
    assert process.stderr is not None
    raise AssertionError(process.stderr.read().decode())


class HttpE2ETests(unittest.TestCase):
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
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "folio_lattice.server",
                    "--transport",
                    "http",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                wait_ready(base_url, process)

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
                process.terminate()
                process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()


class InspectionRendererE2ETests(unittest.TestCase):
    def test_read_edit_version_graph_and_render_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            html = service.create_artifact(
                tenant_id="ui-test",
                name="page.html",
                data=b"<h1>First</h1><script>document.body.dataset.ran='yes'</script>",
                media_type="text/html",
                actor="seed",
                reason="fixture",
            )
            target = service.create_artifact(
                tenant_id="ui-test", name="target.txt", data=b"graph target"
            )
            css = service.create_artifact(
                tenant_id="ui-test",
                name="theme.css",
                data=b"body { color: rgb(1, 2, 3); }",
                media_type="text/css",
            )
            javascript = service.create_artifact(
                tenant_id="ui-test",
                name="app.js",
                data=b"document.body.dataset.javascript = 'ran'",
                media_type="application/javascript",
            )
            binary = service.create_artifact(
                tenant_id="ui-test",
                name="data.bin",
                data=b"\x00\x01",
                media_type="application/octet-stream",
            )
            service.link("ui-test", html["artifact"]["id"], target["artifact"]["id"], "references")

            control_port = free_port()
            render_port = free_port()
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
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            control = self._start(environment, "http", control_port)
            renderer = self._start(environment, "renderer", render_port)
            try:
                wait_ready(control_origin, control)
                wait_ready(render_origin, renderer)
                artifact_id = html["artifact"]["id"]
                first_version = html["version"]["id"]

                with urllib.request.urlopen(f"{control_origin}/inspect/{artifact_id}") as response:
                    page = response.read().decode()
                    self.assertIn('sandbox="allow-scripts"', page)
                    self.assertNotIn("allow-same-origin", page)
                    self.assertIn(render_origin, response.headers["Content-Security-Policy"])

                state = self._json(f"{control_origin}/api/artifacts/{artifact_id}")
                self.assertEqual(
                    state["read"]["text"],
                    "<h1>First</h1><script>document.body.dataset.ran='yes'</script>",
                )
                self.assertEqual(len(state["versions"]), 1)
                self.assertEqual(state["graph"][0]["target_artifact_id"], target["artifact"]["id"])

                updated_text = "<h1>Second</h1><script>document.body.dataset.ran='yes'</script>"
                updated = self._json(
                    f"{control_origin}/api/artifacts/{artifact_id}/versions",
                    {
                        "parent_version_id": first_version,
                        "media_type": "text/html",
                        "reason": "browser edit",
                        "text": updated_text,
                    },
                )
                self.assertEqual(updated["parent_version_id"], first_version)
                self.assertEqual(updated["actor"], "inspection-user")
                self.assertEqual(updated["source_context"], {"interface": "inspection-ui"})

                state = self._json(f"{control_origin}/api/artifacts/{artifact_id}")
                self.assertEqual(state["read"]["text"], updated_text)
                self.assertEqual(len(state["versions"]), 2)
                self.assertEqual(state["versions"][1]["id"], updated["id"])

                with self.assertRaises(urllib.error.HTTPError) as stale:
                    self._json(
                        f"{control_origin}/api/artifacts/{artifact_id}/versions",
                        {
                            "parent_version_id": first_version,
                            "media_type": "text/html",
                            "reason": "stale edit",
                            "text": "must not persist",
                        },
                    )
                self.assertEqual(stale.exception.code, 409)
                self.assertEqual(
                    len(self._json(f"{control_origin}/api/artifacts/{artifact_id}")["versions"]),
                    2,
                )

                with urllib.request.urlopen(
                    f"{render_origin}/render/{artifact_id}?version_id={updated['id']}"
                ) as response:
                    self.assertEqual(response.read().decode(), updated_text)
                    policy = response.headers["Content-Security-Policy"]
                    self.assertIn("sandbox allow-scripts", policy)
                    self.assertIn("connect-src 'none'", policy)
                    self.assertIn(f"frame-ancestors {control_origin}", policy)
                    self.assertNotIn("allow-same-origin", policy)
                    self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

                with urllib.request.urlopen(
                    f"{render_origin}/render/{artifact_id}?version_id={first_version}"
                ) as response:
                    self.assertIn("<h1>First</h1>", response.read().decode())

                self._assert_wrapped_resource(render_origin, css, "link")
                self._assert_wrapped_resource(render_origin, javascript, "script")

                with self.assertRaises(urllib.error.HTTPError) as unsupported:
                    urllib.request.urlopen(f"{render_origin}/render/{binary['artifact']['id']}")
                self.assertEqual(unsupported.exception.code, 415)
            finally:
                for process in (renderer, control):
                    process.terminate()
                    process.wait(timeout=5)
                    assert process.stdout is not None and process.stderr is not None
                    process.stdout.close()
                    process.stderr.close()

    @staticmethod
    def _start(environment: dict[str, str], transport: str, port: int) -> subprocess.Popen[bytes]:
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

    @staticmethod
    def _json(url: str, payload: dict[str, str] | None = None) -> dict[str, object]:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method="POST" if data else "GET",
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def _assert_wrapped_resource(
        self, render_origin: str, created: dict[str, object], element: str
    ) -> None:
        artifact = created["artifact"]
        version = created["version"]
        assert isinstance(artifact, dict) and isinstance(version, dict)
        artifact_id = artifact["id"]
        version_id = version["id"]
        with urllib.request.urlopen(f"{render_origin}/render/{artifact_id}") as response:
            wrapper = response.read().decode()
        self.assertIn(f"<{element}", wrapper)
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
