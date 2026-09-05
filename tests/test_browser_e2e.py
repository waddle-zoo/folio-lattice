import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from folio_lattice.service import FolioLattice


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def browser_path() -> str | None:
    configured = os.environ.get("FOLIO_BROWSER")
    candidates = [
        configured,
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            probe = subprocess.run(
                [candidate, "--version"], capture_output=True, timeout=5, check=False
            )
        except OSError:
            continue
        if probe.returncode == 0:
            return candidate
    return None


def wait_ready(origin: str, process: subprocess.Popen[bytes]) -> None:
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"{origin}/health", timeout=0.2) as response:
                if json.loads(response.read())["ready"]:
                    return
        except Exception:
            time.sleep(0.05)
    assert process.stderr is not None
    raise AssertionError(process.stderr.read().decode())


def dump_dom(browser: str, url: str, profile: Path) -> str:
    process = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-background-networking",
            "--no-first-run",
            f"--user-data-dir={profile}",
            "--virtual-time-budget=5000",
            "--dump-dom",
            url,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
    if "</html>" not in stdout:
        raise AssertionError(f"headless browser produced no DOM: {stderr[-1000:]}")
    return stdout


class BrowserSandboxE2ETests(unittest.TestCase):
    def test_scripts_render_but_network_storage_and_host_escape_fail(self) -> None:
        browser = browser_path()
        if browser is None:
            self.skipTest("set FOLIO_BROWSER to Chrome or Chromium for browser sandbox evidence")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            render_port = free_port()
            harness_port = free_port()
            render_origin = f"http://127.0.0.1:{render_port}"
            harness_origin = f"http://127.0.0.1:{harness_port}"

            javascript = service.create_artifact(
                tenant_id="browser-test",
                name="behavior.js",
                data=(
                    b"document.getElementById('output').textContent='JavaScript ran';"
                    b"document.body.dataset.javascript='ran'"
                ),
                media_type="application/javascript",
            )
            css = service.create_artifact(
                tenant_id="browser-test",
                name="behavior.css",
                data=b"body { color: rgb(1, 2, 3); }",
                media_type="text/css",
            )
            js_id = javascript["artifact"]["id"]
            js_version = javascript["version"]["id"]
            hostile_source = self._hostile_source(
                harness_origin, render_origin, js_id, js_version
            ).encode()
            hostile = service.create_artifact(
                tenant_id="browser-test",
                name="hostile.html",
                data=hostile_source,
                media_type="text/html",
            )
            hostile_url = f"{render_origin}/render/{hostile['artifact']['id']}"

            handler = self._handler(hostile_url)
            harness = ThreadingHTTPServer(("127.0.0.1", harness_port), handler)
            thread = threading.Thread(target=harness.serve_forever, daemon=True)
            thread.start()
            environment = {
                **os.environ,
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                "FOLIO_TENANT_ID": "browser-test",
                "FOLIO_CONTROL_ORIGIN": harness_origin,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            renderer = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "folio_lattice.server",
                    "--transport",
                    "renderer",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(render_port),
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                wait_ready(render_origin, renderer)
                hostile_dom = dump_dom(browser, f"{harness_origin}/harness", root / "chrome-1")
                match = re.search(r'<pre id="result">(.*?)</pre>', hostile_dom, re.DOTALL)
                self.assertIsNotNone(match, hostile_dom)
                assert match is not None
                result = json.loads(html.unescape(match.group(1)))
                self.assertEqual(
                    result,
                    {
                        "script_ran": True,
                        "host_dom_blocked": True,
                        "storage_blocked": True,
                        "cookie_blocked": True,
                        "popup_blocked": True,
                        "fetch_blocked": True,
                        "same_origin_fetch_blocked": True,
                        "xhr_blocked": True,
                        "websocket_blocked": True,
                    },
                )
                self.assertEqual(handler.leaks, [])

                javascript_dom = dump_dom(
                    browser, f"{render_origin}/render/{js_id}", root / "chrome-2"
                )
                self.assertIn("JavaScript ran", javascript_dom)
                self.assertIn('data-javascript="ran"', javascript_dom)

                css_dom = dump_dom(
                    browser,
                    f"{render_origin}/render/{css['artifact']['id']}",
                    root / "chrome-3",
                )
                self.assertIn('data-computed-color="rgb(1, 2, 3)"', css_dom)
            finally:
                renderer.terminate()
                renderer.wait(timeout=5)
                assert renderer.stdout is not None and renderer.stderr is not None
                renderer.stdout.close()
                renderer.stderr.close()
                harness.shutdown()
                harness.server_close()
                thread.join(timeout=5)

    @staticmethod
    def _hostile_source(
        harness_origin: str, render_origin: str, js_id: str, js_version: str
    ) -> str:
        return f"""<!doctype html><body><script>
(async () => {{
  const result = {{script_ran: true}};
  try {{ parent.document.body; result.host_dom_blocked = false; }}
  catch {{ result.host_dom_blocked = true; }}
  try {{ localStorage.setItem('secret', 'x'); result.storage_blocked = false; }}
  catch {{ result.storage_blocked = true; }}
  try {{ document.cookie = 'secret=x'; result.cookie_blocked = !document.cookie.includes('secret=x'); }}
  catch {{ result.cookie_blocked = true; }}
  try {{ const popup = open('about:blank'); result.popup_blocked = popup === null; popup?.close(); }}
  catch {{ result.popup_blocked = true; }}
  try {{ await fetch('{harness_origin}/leak'); result.fetch_blocked = false; }}
  catch {{ result.fetch_blocked = true; }}
  try {{ await fetch('{render_origin}/content/{js_id}/{js_version}'); result.same_origin_fetch_blocked = false; }}
  catch {{ result.same_origin_fetch_blocked = true; }}
  result.xhr_blocked = await new Promise((resolve) => {{
    try {{
      const xhr = new XMLHttpRequest();
      xhr.onload = () => resolve(false); xhr.onerror = () => resolve(true);
      xhr.open('GET', '{harness_origin}/leak'); xhr.send();
      setTimeout(() => resolve(xhr.readyState !== 4), 300);
    }} catch {{ resolve(true); }}
  }});
  result.websocket_blocked = await new Promise((resolve) => {{
    try {{
      const socket = new WebSocket('ws://127.0.0.1:{harness_origin.rsplit(":", 1)[1]}/leak');
      socket.onopen = () => resolve(false); socket.onerror = () => resolve(true);
      setTimeout(() => resolve(socket.readyState !== WebSocket.OPEN), 300);
    }} catch {{ resolve(true); }}
  }});
  try {{
    const form = document.createElement('form'); form.action = '{harness_origin}/leak';
    form.method = 'POST'; document.body.append(form); form.submit();
  }} catch {{}}
  try {{ top.location = '{harness_origin}/escaped'; }} catch {{}}
  parent.postMessage({{type: 'folio-sandbox-result', result}}, '*');
}})();
</script></body>"""

    @staticmethod
    def _handler(hostile_url: str) -> type[BaseHTTPRequestHandler]:
        page = f"""<!doctype html><body>
<pre id="result">waiting</pre>
<script>
addEventListener('message', (event) => {{
  const frame = document.getElementById('artifact');
  if (event.source !== frame.contentWindow || event.data?.type !== 'folio-sandbox-result') return;
  document.getElementById('result').textContent = JSON.stringify(event.data.result);
}});
</script>
<iframe id="artifact" sandbox="allow-scripts" src="{hostile_url}"></iframe>
</body>""".encode()

        class Handler(BaseHTTPRequestHandler):
            leaks: list[str] = []

            def do_GET(self) -> None:
                if self.path == "/harness":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return
                if self.path in {"/leak", "/escaped"}:
                    self.leaks.append(self.path)
                self.send_response(404)
                self.end_headers()

            def do_POST(self) -> None:
                self.leaks.append(self.path)
                self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        return Handler


if __name__ == "__main__":
    unittest.main()
