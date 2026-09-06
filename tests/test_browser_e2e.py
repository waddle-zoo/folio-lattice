import asyncio
import base64
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
from typing import Any

from mcp import Client
from websockets.sync.client import connect


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
    for _ in range(150):
        try:
            with urllib.request.urlopen(f"{origin}/health", timeout=0.2) as response:
                if json.loads(response.read())["ready"]:
                    return
        except Exception:
            time.sleep(0.05)
    assert process.stderr is not None
    process.terminate()
    raise AssertionError(process.stderr.read().decode())


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


async def async_mcp(base_url: str, tool: str, arguments: dict[str, Any]) -> Any:
    async with Client(f"{base_url}/mcp", raise_exceptions=True) as client:
        response = await client.call_tool(tool, arguments)
    assert not response.is_error, response
    assert response.structured_content is not None
    return response.structured_content.get("result", response.structured_content)


def mcp(base_url: str, tool: str, arguments: dict[str, Any]) -> Any:
    return asyncio.run(async_mcp(base_url, tool, arguments))


def create(base_url: str, name: str, source: bytes, media_type: str) -> dict[str, Any]:
    return mcp(
        base_url,
        "artifact_create",
        {
            "name": name,
            "media_type": media_type,
            "content_base64": base64.b64encode(source).decode(),
            "reason": "browser fixture",
        },
    )


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


class DevTools:
    def __init__(self, browser: str, url: str, profile: Path):
        self.port = free_port()
        self.process = subprocess.Popen(
            [
                browser,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-background-networking",
                "--no-first-run",
                f"--user-data-dir={profile}",
                f"--remote-debugging-port={self.port}",
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        target = self._target()
        self.socket = connect(target["webSocketDebuggerUrl"], open_timeout=5)
        self.identifier = 0
        self.command("Page.enable")
        self.command("Runtime.enable")
        self.command("DOM.enable")
        self.command("Accessibility.enable")
        self.command("Page.bringToFront")
        self.command("Emulation.setFocusEmulationEnabled", {"enabled": True})

    def _target(self) -> dict[str, Any]:
        for _ in range(150):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/list", timeout=0.2
                ) as response:
                    targets = json.loads(response.read())
                page = next((item for item in targets if item["type"] == "page"), None)
                if page is not None:
                    return page
            except Exception:
                time.sleep(0.05)
        self.close()
        raise AssertionError("Chrome DevTools endpoint did not become ready")

    def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.identifier += 1
        identifier = self.identifier
        self.socket.send(json.dumps({"id": identifier, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.socket.recv(timeout=10))
            if message.get("id") != identifier:
                continue
            if "error" in message:
                raise AssertionError(message["error"])
            return message.get("result", {})

    def evaluate(self, expression: str, *, context_id: int | None = None) -> Any:
        params: dict[str, Any] = {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        }
        if context_id is not None:
            params["contextId"] = context_id
        result = self.command("Runtime.evaluate", params)
        if "exceptionDetails" in result:
            raise AssertionError(result["exceptionDetails"])
        return result["result"].get("value")

    def wait(self, expression: str, timeout: float = 10, *, context_id: int | None = None) -> Any:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = self.evaluate(expression, context_id=context_id)
                if last:
                    return last
            except (AssertionError, KeyError):
                pass
            time.sleep(0.05)
        raise AssertionError(f"browser condition did not become true: {expression}; last={last!r}")

    def set_file(self, selector: str, path: Path) -> None:
        root = self.command("DOM.getDocument")["root"]["nodeId"]
        node = self.command("DOM.querySelector", {"nodeId": root, "selector": selector})["nodeId"]
        self.command("DOM.setFileInputFiles", {"nodeId": node, "files": [str(path)]})

    def key(self, key: str, code: str, virtual: int) -> None:
        for kind in ("keyDown", "keyUp"):
            params: dict[str, Any] = {
                "type": kind,
                "key": key,
                "code": code,
                "windowsVirtualKeyCode": virtual,
                "nativeVirtualKeyCode": virtual,
            }
            if kind == "keyDown" and key == "Enter":
                params.update({"text": "\r", "unmodifiedText": "\r"})
            self.command(
                "Input.dispatchKeyEvent",
                params,
            )

    def close(self) -> str:
        if hasattr(self, "socket"):
            self.socket.close()
        if self.process.poll() is None:
            self.process.terminate()
        stdout, stderr = self.process.communicate(timeout=5)
        return (stdout + stderr).decode(errors="replace")


class BrowserSandboxE2ETests(unittest.TestCase):
    def test_scripts_render_but_network_storage_and_host_escape_fail(self) -> None:
        browser = browser_path()
        if browser is None:
            self.skipTest("set FOLIO_BROWSER to Chrome or Chromium for browser sandbox evidence")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control_port, render_port, harness_port = free_port(), free_port(), free_port()
            control_origin = f"http://127.0.0.1:{control_port}"
            render_origin = f"http://127.0.0.1:{render_port}"
            harness_origin = f"http://127.0.0.1:{harness_port}"
            environment = {
                **os.environ,
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                "FOLIO_TENANT_ID": "browser-test",
                "FOLIO_CONTROL_ORIGIN": harness_origin,
                "FOLIO_RENDER_ORIGIN": render_origin,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            control = start_server(environment, "http", control_port)
            renderer: subprocess.Popen[bytes] | None = None
            harness: ThreadingHTTPServer | None = None
            thread: threading.Thread | None = None
            try:
                wait_ready(control_origin, control)
                javascript = create(
                    control_origin,
                    "behavior.js",
                    b"document.getElementById('output').textContent='JavaScript ran';"
                    b"document.body.dataset.javascript='ran'",
                    "application/javascript",
                )
                css = create(
                    control_origin, "behavior.css", b"body { color: rgb(1, 2, 3); }", "text/css"
                )
                js_id = javascript["artifact"]["id"]
                js_version = javascript["version"]["id"]
                hostile_source = self._hostile_source(
                    harness_origin, render_origin, js_id, js_version
                ).encode()
                hostile = create(control_origin, "hostile.html", hostile_source, "text/html")
                hostile_url = f"{render_origin}/render/{hostile['artifact']['id']}"
                handler = self._handler(hostile_url)
                harness = ThreadingHTTPServer(("127.0.0.1", harness_port), handler)
                thread = threading.Thread(target=harness.serve_forever, daemon=True)
                thread.start()
                renderer = start_server(
                    {**environment, "FOLIO_MCP_URL": f"{control_origin}/mcp"},
                    "renderer",
                    render_port,
                )
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
                css_dom = dump_dom(
                    browser, f"{render_origin}/render/{css['artifact']['id']}", root / "chrome-3"
                )
                self.assertIn('data-computed-color="rgb(1, 2, 3)"', css_dom)
            finally:
                if renderer is not None:
                    stop_server(renderer)
                stop_server(control)
                if harness is not None:
                    harness.shutdown()
                    harness.server_close()
                if thread is not None:
                    thread.join(timeout=5)

    def test_real_ui_upload_search_grep_chunk_graph_versions_conflict_and_bridge(self) -> None:
        browser = browser_path()
        if browser is None:
            self.skipTest("set FOLIO_BROWSER to Chrome or Chromium for browser UI evidence")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control_port, render_port = free_port(), free_port()
            control_origin = f"http://127.0.0.1:{control_port}"
            render_origin = f"http://127.0.0.1:{render_port}"
            environment = {
                **os.environ,
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                "FOLIO_TENANT_ID": "browser-ui",
                "FOLIO_ACTOR": "browser-persona",
                "FOLIO_CONTROL_ORIGIN": control_origin,
                "FOLIO_RENDER_ORIGIN": render_origin,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            control = start_server(environment, "http", control_port)
            renderer: subprocess.Popen[bytes] | None = None
            chrome: DevTools | None = None
            try:
                wait_ready(control_origin, control)
                target = create(control_origin, "decision.txt", b"browser target", "text/plain")
                javascript = create(
                    control_origin,
                    "browser.js",
                    b"document.body.dataset.browserJavascript='ran'",
                    "application/javascript",
                )
                stylesheet = create(
                    control_origin,
                    "browser.css",
                    b"body { color: rgb(3, 4, 5); }",
                    "text/css",
                )
                source = b"""<!doctype html><body>browsermarker<script>
addEventListener('message', (event) => {
 if (event.data?.type !== 'folio.mcp.response') return;
 document.body.dataset[event.data.id] = String(event.data.ok);
 if (event.data.id === 'bridgeAllow') parent.postMessage({type:'folio.mcp.request',id:'bridgeDeny',attachment:'folio-lattice',tool:'artifact_write',arguments:{}}, '*');
});
parent.postMessage({type:'folio.mcp.request',id:'bridgeAllow',attachment:'folio-lattice',tool:'artifact_search',arguments:{query:'browsermarker'}}, '*');
</script></body>"""
                upload = root / "browser-note.html"
                upload.write_bytes(source)
                renderer = start_server(
                    {**environment, "FOLIO_MCP_URL": f"{control_origin}/mcp"},
                    "renderer",
                    render_port,
                )
                wait_ready(render_origin, renderer)
                chrome = DevTools(browser, control_origin, root / "chrome-ui")
                chrome.wait(
                    "document.querySelector('#status')?.textContent.includes('Create or upload')"
                )

                ax = chrome.command("Accessibility.getFullAXTree")["nodes"]
                names = {node.get("name", {}).get("value") for node in ax}
                self.assertIn("Create first version", names)
                self.assertIn("Indexed content search", names)
                chrome.set_file("#create-file", upload)
                chrome.evaluate("""
document.querySelector('#create-name').value = 'browser-note.html';
document.querySelector('#create-media').value = 'text/html';
document.querySelector('#create-reason').value = 'Morgan upload';
document.querySelector('#create').requestSubmit();
""")
                chrome.wait("location.pathname.startsWith('/inspect/art_')")
                artifact_id = chrome.evaluate("decodeURIComponent(location.pathname.split('/')[2])")
                chrome.wait("document.querySelector('#title')?.textContent === 'browser-note.html'")
                first_version = chrome.evaluate("document.querySelector('#parent-version').value")
                chrome.wait(
                    "document.querySelector('#bridge-status').textContent.includes('Denied')"
                )

                chrome.evaluate("""
window.__folioFetch = window.fetch;
window.fetch = (...args) => new Promise((resolve) => setTimeout(() => resolve(window.__folioFetch(...args)), 250));
document.querySelector('#search-query').value = 'browsermarker';
document.querySelector('#search').requestSubmit();
""")
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#main').getAttribute('aria-busy')"),
                    "true",
                )
                chrome.wait(
                    "document.querySelector('#results button')?.textContent.startsWith('art_')"
                )
                chrome.evaluate("window.fetch = window.__folioFetch")
                chrome.evaluate(
                    "document.querySelector('#search-query').value='definitelynomatches'; document.querySelector('#search').requestSubmit()"
                )
                chrome.wait("document.querySelector('#results').textContent.includes('No results')")
                chrome.evaluate(
                    "document.querySelector('#grep-pattern').value='browsermarker'; document.querySelector('#grep').requestSubmit()"
                )
                chrome.wait("document.querySelector('#status').textContent === '1 result.'")
                chrome.evaluate("document.querySelector('#chunks button').click()")
                chrome.wait(
                    "document.querySelector('#chunk-content').textContent.includes('browsermarker')"
                )

                chrome.evaluate(
                    f"document.querySelector('#target-id').value={json.dumps(target['artifact']['id'])}; document.querySelector('#edge-type').value='supports'; document.querySelector('#link').requestSubmit()"
                )
                chrome.wait("document.querySelector('#graph').textContent.includes('supports')")
                chrome.evaluate(
                    "document.querySelector('#content').value += '\\nupdated'; document.querySelector('#reason').value='Morgan edit'; document.querySelector('#edit').requestSubmit()"
                )
                chrome.wait("document.querySelectorAll('#versions li').length === 2")
                second_version = chrome.evaluate("document.querySelector('#parent-version').value")
                self.assertNotEqual(first_version, second_version)
                chrome.evaluate("document.querySelector('#versions button').click()")
                chrome.wait(
                    f"document.querySelector('#parent-version').value === {json.dumps(first_version)}"
                )
                self.assertNotIn(
                    "updated", chrome.evaluate("document.querySelector('#content').value")
                )
                chrome.command("Page.navigate", {"url": f"{control_origin}/inspect/{artifact_id}"})
                chrome.wait(
                    f"document.querySelector('#parent-version').value === {json.dumps(second_version)}"
                )

                mcp(
                    control_origin,
                    "artifact_write",
                    {
                        "artifact_id": artifact_id,
                        "parent_version_id": second_version,
                        "media_type": "text/html",
                        "reason": "concurrent MCP edit",
                        "content_base64": base64.b64encode(source + b"\nexternal").decode(),
                    },
                )
                chrome.evaluate(
                    "document.querySelector('#content').value += '\\nunsaved'; document.querySelector('#edit').requestSubmit()"
                )
                chrome.wait(
                    "document.querySelector('#error').textContent.includes('newer version')"
                )
                self.assertEqual(chrome.evaluate("document.activeElement.id"), "error")
                self.assertIn(
                    "unsaved", chrome.evaluate("document.querySelector('#content').value")
                )

                chrome.command("Page.navigate", {"url": control_origin})
                chrome.wait(
                    "document.querySelector('#status')?.textContent.includes('Create or upload')"
                )
                chrome.evaluate(
                    f"document.querySelector('#artifact-id').focus(); document.querySelector('#artifact-id').value={json.dumps(artifact_id)}"
                )
                chrome.key("Enter", "Enter", 13)
                chrome.wait("location.pathname.startsWith('/inspect/art_')")
                chrome.wait("document.querySelector('#title').textContent === 'browser-note.html'")
                chrome.wait(
                    "document.querySelector('#bridge-status').textContent.includes('Denied')"
                )
                chrome.wait("document.querySelector('#graph').textContent.includes('supports')")
                chrome.evaluate("document.querySelector('#graph button').click()")
                chrome.wait("document.querySelector('#title').textContent === 'decision.txt'")
                chrome.command("Page.navigate", {"url": f"{control_origin}/inspect/{artifact_id}"})
                chrome.wait("document.querySelector('#title').textContent === 'browser-note.html'")

                for preview_artifact in (javascript, stylesheet):
                    preview_id = preview_artifact["artifact"]["id"]
                    chrome.command(
                        "Page.navigate", {"url": f"{control_origin}/inspect/{preview_id}"}
                    )
                    chrome.wait(
                        f"document.querySelector('#preview').src.includes({json.dumps('/render/' + preview_id)})"
                    )
                chrome.command("Page.navigate", {"url": f"{control_origin}/inspect/{artifact_id}"})
                chrome.wait(
                    "document.querySelector('#bridge-status').textContent.includes('Denied')"
                )

                spoof = create(
                    control_origin,
                    "spoof.html",
                    b"<script>parent.postMessage({type:'folio.mcp.request',id:'spoofRequest',attachment:'folio-lattice',tool:'artifact_search',arguments:{query:'browsermarker'}},'*')</script>",
                    "text/html",
                )
                before = chrome.evaluate("document.querySelector('#bridge-status').textContent")
                chrome.evaluate(
                    f"const f=document.createElement('iframe'); f.sandbox='allow-scripts'; f.src={json.dumps(render_origin + '/render/' + spoof['artifact']['id'])}; document.body.append(f)"
                )
                time.sleep(0.5)
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#bridge-status').textContent"), before
                )
                chrome.command("Page.navigate", {"url": f"{control_origin}/inspect/art_missing"})
                chrome.wait("document.querySelector('#error').textContent.includes('not found')")
                error_text = chrome.evaluate("document.querySelector('#error').textContent")
                self.assertNotIn(str(root), error_text)
                self.assertNotIn("Traceback", error_text)
            finally:
                if chrome is not None:
                    chrome.close()
                if renderer is not None:
                    stop_server(renderer)
                logs = stop_server(control)
                self.assertIn('"request_id":"bridgeAllow"', logs)
                self.assertIn('"request_id":"bridgeDeny"', logs)
                self.assertNotIn('"request_id":"spoofRequest"', logs)

    @staticmethod
    def _hostile_source(
        harness_origin: str, render_origin: str, js_id: str, js_version: str
    ) -> str:
        return f"""<!doctype html><body><script>
(async () => {{
  const result = {{script_ran: true}};
  try {{ parent.document.body; result.host_dom_blocked = false; }} catch {{ result.host_dom_blocked = true; }}
  try {{ localStorage.setItem('secret', 'x'); result.storage_blocked = false; }} catch {{ result.storage_blocked = true; }}
  try {{ document.cookie = 'secret=x'; result.cookie_blocked = !document.cookie.includes('secret=x'); }} catch {{ result.cookie_blocked = true; }}
  try {{ const popup = open('about:blank'); result.popup_blocked = popup === null; popup?.close(); }} catch {{ result.popup_blocked = true; }}
  try {{ await fetch('{harness_origin}/leak'); result.fetch_blocked = false; }} catch {{ result.fetch_blocked = true; }}
  try {{ await fetch('{render_origin}/content/{js_id}/{js_version}'); result.same_origin_fetch_blocked = false; }} catch {{ result.same_origin_fetch_blocked = true; }}
  result.xhr_blocked = await new Promise((resolve) => {{ try {{ const xhr = new XMLHttpRequest(); xhr.onload = () => resolve(false); xhr.onerror = () => resolve(true); xhr.open('GET', '{harness_origin}/leak'); xhr.send(); setTimeout(() => resolve(xhr.readyState !== 4), 300); }} catch {{ resolve(true); }} }});
  result.websocket_blocked = await new Promise((resolve) => {{ try {{ const socket = new WebSocket('ws://127.0.0.1:{harness_origin.rsplit(":", 1)[1]}/leak'); socket.onopen = () => resolve(false); socket.onerror = () => resolve(true); setTimeout(() => resolve(socket.readyState !== WebSocket.OPEN), 300); }} catch {{ resolve(true); }} }});
  try {{ const form = document.createElement('form'); form.action = '{harness_origin}/leak'; form.method = 'POST'; document.body.append(form); form.submit(); }} catch {{}}
  try {{ top.location = '{harness_origin}/escaped'; }} catch {{}}
  parent.postMessage({{type: 'folio-sandbox-result', result}}, '*');
}})();
</script></body>"""

    @staticmethod
    def _handler(hostile_url: str) -> type[BaseHTTPRequestHandler]:
        page = f"""<!doctype html><body><pre id="result">waiting</pre><script>
addEventListener('message', (event) => {{ const frame = document.getElementById('artifact'); if (event.source !== frame.contentWindow || event.data?.type !== 'folio-sandbox-result') return; document.getElementById('result').textContent = JSON.stringify(event.data.result); }});
</script><iframe id="artifact" sandbox="allow-scripts" src="{hostile_url}"></iframe></body>""".encode()

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
