import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from mcp import Client, StdioServerParameters


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


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
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(f"{base_url}/health", timeout=0.2) as response:
                            health = json.loads(response.read())
                        if health["ready"]:
                            break
                    except Exception:
                        time.sleep(0.05)
                else:
                    self.fail(process.stderr.read().decode())

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
