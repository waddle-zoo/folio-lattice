import base64
import json
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


class HttpE2ETests(unittest.TestCase):
    def test_http_mcp_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            port = 18765
            environment = {
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                **__import__("os").environ,
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
                for _ in range(50):
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2)
                        break
                    except Exception:
                        time.sleep(0.05)
                else:
                    self.fail(process.stderr.read().decode())

                def call(request):
                    payload = json.dumps(request).encode()
                    request_obj = urllib.request.Request(
                        f"http://127.0.0.1:{port}/mcp",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(request_obj, timeout=2) as response:
                        return json.loads(response.read())

                created = call(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "artifact_create",
                            "arguments": {
                                "tenant_id": "acme",
                                "name": "e2e.txt",
                                "media_type": "text/plain",
                                "content_base64": base64.b64encode(b"e2e graph content").decode(),
                            },
                        },
                    }
                )
                artifact_id = created["result"]["structuredContent"]["artifact"]["id"]
                searched = call(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "artifact_search",
                            "arguments": {"tenant_id": "acme", "query": "graph"},
                        },
                    }
                )
                self.assertEqual(
                    searched["result"]["structuredContent"][0]["artifact_id"], artifact_id
                )
            finally:
                process.terminate()
                process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()

    def test_stdio_mcp_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                **__import__("os").environ,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            process = subprocess.Popen(
                [sys.executable, "-m", "folio_lattice.server", "--transport", "stdio"],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:

                def call(request):
                    assert process.stdin and process.stdout
                    process.stdin.write(json.dumps(request) + "\n")
                    process.stdin.flush()
                    return json.loads(process.stdout.readline())

                initialized = call(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
                )
                self.assertEqual(initialized["result"]["serverInfo"]["name"], "folio-lattice")
                created = call(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "artifact_create",
                            "arguments": {
                                "tenant_id": "acme",
                                "name": "stdio.txt",
                                "media_type": "text/plain",
                                "content_base64": base64.b64encode(b"stdio graph content").decode(),
                            },
                        },
                    }
                )
                self.assertIn("version", created["result"]["structuredContent"])
            finally:
                process.terminate()
                process.wait(timeout=5)
                if process.stdin:
                    process.stdin.close()
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
