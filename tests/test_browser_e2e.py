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
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4

from mcp import Client
from websockets.sync.client import connect

from folio_lattice.inspection import UI_CSS, UI_JS, ui_html

RENDERER_CAPABILITY_SECRET = "test-renderer-capability-secret-012345678901234567890123"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def disposable_tenant(label: str) -> str:
    return f"{label}-{uuid4().hex}"


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
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def wait_ready(origin: str, process: subprocess.Popen[bytes]) -> None:
    for _ in range(150):
        try:
            with urllib.request.urlopen(f"{origin}/readyz", timeout=0.2) as response:
                if json.loads(response.read())["ready"]:
                    return
        except Exception:
            time.sleep(0.05)
    assert process.stderr is not None
    process.terminate()
    raise AssertionError(process.stderr.read().decode())


def start_server(environment: dict[str, str], transport: str, port: int) -> subprocess.Popen[bytes]:
    command = [sys.executable, "-m", "folio_lattice.server"]
    if environment.get("FOLIO_TEST_EXTERNAL_MCP_VALIDATOR") == "deterministic":
        command = [sys.executable, "tests/browser_server.py"]
    return subprocess.Popen(
        command
        + [
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
    downloads = profile / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-background-networking",
            "--no-first-run",
            f"--user-data-dir={profile}",
            f"--download.default_directory={downloads}",
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
        self.profile = profile
        self.port: int | None = None
        try:
            self.startup_timeout = float(
                os.environ.get("FOLIO_BROWSER_STARTUP_TIMEOUT_SECONDS", "30")
            )
        except ValueError as exc:
            raise AssertionError("browser startup timeout must be numeric") from exc
        if not 5 <= self.startup_timeout <= 120:
            raise AssertionError("browser startup timeout must be between 5 and 120 seconds")
        self.process = subprocess.Popen(
            [
                browser,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--no-first-run",
                f"--user-data-dir={profile}",
                "--remote-debugging-port=0",
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
        active_port = self.profile / "DevToolsActivePort"
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                if self.port is None:
                    self.port = int(active_port.read_text().splitlines()[0])
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/list", timeout=0.2
                ) as response:
                    targets = json.loads(response.read())
                page = next((item for item in targets if item["type"] == "page"), None)
                if page is not None:
                    return page
            except Exception:
                time.sleep(0.05)
        details = self.close()
        raise AssertionError(
            f"Chrome DevTools endpoint did not become ready within "
            f"{self.startup_timeout:g}s: {details[-2000:]}"
        )

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
        try:
            stdout, stderr = self.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            stdout, stderr = self.process.communicate(timeout=5)
        return (stdout + stderr).decode(errors="replace")


class BrowserHostedAuthE2ETests(unittest.TestCase):
    def test_auth_states_are_accessible_safe_and_recoverable(self) -> None:
        browser = browser_path()
        if browser is None:
            self.skipTest("set FOLIO_BROWSER to Chrome or Chromium for hosted auth UI evidence")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            port = free_port()
            origin = f"http://127.0.0.1:{port}"
            handler = self._handler(origin)
            server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            chrome: DevTools | None = None
            try:
                handler.page_auth_state = "local"
                handler.page_organization = "local-org"
                handler.page_actor = "local-actor"
                handler.api_status = 200
                chrome = DevTools(browser, f"{origin}/?local=1", root / "chrome-auth")
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
                self.assertTrue(chrome.evaluate("Boolean(document.querySelector('.app-shell'))"))
                self.assertTrue(
                    chrome.evaluate(
                        "document.querySelector('nav[aria-label=\"Primary\"]')?.textContent.includes('Library')"
                    )
                )
                self.assertNotIn(
                    "Unauthenticated local development", chrome.evaluate("document.body.innerText")
                )
                self.assertFalse(
                    chrome.evaluate("Boolean(document.querySelector('#auth-context'))")
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('.mode-status')?.textContent"),
                    "Local",
                )
                self.assertIn(
                    chrome.evaluate("getComputedStyle(document.body).backgroundColor"),
                    {"rgb(245, 247, 249)", "rgb(16, 23, 32)"},
                )
                self.assertGreaterEqual(
                    chrome.evaluate("document.querySelectorAll('.surface').length"), 2
                )
                handler.page_auth_state = "hosted"
                handler.page_organization = None
                handler.page_actor = None
                handler.api_status = 401
                handler.me_status = 401
                chrome.command("Page.navigate", {"url": origin})
                chrome.wait(
                    "location.pathname === '/sign-in' && location.search === '?return_to=%2F'"
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#auth-status').textContent"),
                    "Sign-in required. Sign in to continue.",
                )
                self.assertTrue(
                    chrome.evaluate(
                        "document.querySelector('#auth-provider').textContent.includes('Continue with your organization')"
                    )
                )

                handler.page_auth_state = "authenticated"
                handler.page_organization = "Acme Operations"
                handler.page_actor = "Ada Lovelace"
                handler.api_status = 200
                handler.me_status = 200
                chrome.command("Page.navigate", {"url": origin})
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
                self.assertNotIn(
                    "Unauthenticated local development", chrome.evaluate("document.body.innerText")
                )
                self.assertFalse(
                    chrome.evaluate("Boolean(document.querySelector('#auth-context'))")
                )
                handler.api_status = 401
                handler.me_status = 401
                chrome.evaluate(
                    "document.querySelector('#create-name').value='expired-draft.txt'; "
                    "document.querySelector('#create-text').value='unsaved document'; "
                    "document.querySelector('#create').requestSubmit()"
                )
                chrome.wait(
                    "location.pathname === '/sign-in' && location.search === '?return_to=%2F' && "
                    "document.querySelector('#auth-status')?.textContent === "
                    "'Your session expired. Sign in again.'"
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#auth-status').textContent"),
                    "Your session expired. Sign in again.",
                )

                handler.api_status = 401
                handler.me_status = 401
                chrome.command("Page.navigate", {"url": f"{origin}/inspect/opaque-reference"})
                chrome.wait(
                    "location.pathname === '/sign-in' && location.search === '?return_to=%2Finspect%2Fopaque-reference'"
                )
                self.assertEqual(
                    chrome.evaluate("document.body.dataset.returnTo"),
                    "/inspect/opaque-reference",
                )
                self.assertNotIn("Acme", chrome.evaluate("document.body.innerText"))
                self.assertNotIn("private", chrome.evaluate("document.body.innerText"))

                handler.api_status = 403
                handler.me_status = 200
                chrome.command("Page.navigate", {"url": f"{origin}/inspect/opaque-reference"})
                chrome.wait(
                    "document.querySelector('#error').textContent === 'This document is not available to you.'"
                )
                self.assertTrue(chrome.evaluate("document.querySelector('#workspace').hidden"))
                self.assertNotIn(
                    "private server detail", chrome.evaluate("document.body.innerText")
                )
                self.assertEqual(chrome.evaluate("location.pathname"), "/inspect/opaque-reference")
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#auth-action').textContent"),
                    "Return home",
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#auth-action').getAttribute('href')"),
                    "/",
                )
                chrome.key("Tab", "Tab", 9)
                self.assertEqual(chrome.evaluate("document.activeElement.id"), "auth-action")
                chrome.key("Enter", "Enter", 13)
                chrome.wait("location.pathname === '/' && location.search === ''")
            finally:
                if chrome is not None:
                    chrome.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_sign_in_shell_reads_identity_and_logs_out(self) -> None:
        browser = browser_path()
        if browser is None:
            self.skipTest("set FOLIO_BROWSER to Chrome or Chromium for hosted auth UI evidence")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            port = free_port()
            origin = f"http://127.0.0.1:{port}"
            handler = self._handler(origin)
            handler.page_auth_state = "hosted"
            handler.page_organization = "Acme Operations"
            handler.page_actor = "Ada Lovelace"
            handler.me_status = 200
            server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            chrome: DevTools | None = None
            try:
                chrome = DevTools(
                    browser,
                    f"{origin}/sign-in?return_to=%2Fworkspace%2Fopaque-reference",
                    root / "chrome-session",
                )
                chrome.wait("!document.querySelector('#auth-session').hidden")
                self.assertGreaterEqual(handler.me_calls, 1)
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#auth-actor').textContent"),
                    "Ada Lovelace",
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#auth-organization').textContent"),
                    "Acme Operations",
                )
                self.assertTrue(chrome.evaluate("document.querySelector('#auth-provider').hidden"))
                self.assertEqual(
                    chrome.evaluate(
                        "document.querySelector('#auth-session-return').getAttribute('href')"
                    ),
                    "/workspace/opaque-reference",
                )
                chrome.evaluate("document.querySelector('#auth-logout').click()")
                chrome.wait(
                    "document.querySelector('#auth-session').hidden && document.querySelector('#auth-status').textContent === 'You’re signed out.'"
                )
                self.assertEqual(handler.logout_calls, 1)
            finally:
                if chrome is not None:
                    chrome.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    @staticmethod
    def _handler(origin: str) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            page_auth_state = "local"
            page_organization: str | None = None
            page_actor: str | None = None
            api_status = 401
            me_status = 401
            me_calls = 0
            logout_calls = 0

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/sign-in":
                    query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                    return_to = query.get("return_to", ["/"])[0]
                    self._send(
                        ui_html(
                            origin,
                            auth_state=self.page_auth_state,
                            sign_in=True,
                            return_to=return_to,
                        ).encode(),
                        "text/html",
                    )
                    return
                if path == "/v1/me":
                    type(self).me_calls += 1
                    if self.me_status == 200:
                        body = json.dumps(
                            {
                                "authenticated": True,
                                "tenant_id": self.page_organization or "Acme Operations",
                                "actor_id": self.page_actor or "Ada Lovelace",
                            }
                        ).encode()
                    else:
                        body = json.dumps(
                            {
                                "code": "authentication_required",
                                "message": "Sign-in required. Sign in to continue.",
                                "request_id": "browser-me",
                                "retryable": False,
                                "reauthenticate": True,
                            }
                        ).encode()
                    self._send_json(self.me_status, body)
                    return
                if path == "/ui.css":
                    self._send(UI_CSS.encode(), "text/css")
                    return
                if path == "/ui.js":
                    self._send(UI_JS.encode(), "application/javascript")
                    return
                if path == "/" or path.startswith("/inspect/") or path.startswith("/artifacts/"):
                    self._send(
                        ui_html(
                            origin,
                            auth_state=self.page_auth_state,
                            organization=self.page_organization,
                            actor=self.page_actor,
                            debug=path.startswith("/inspect/"),
                            human=path.startswith("/artifacts/"),
                        ).encode(),
                        "text/html",
                    )
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self) -> None:
                if self.path == "/auth/logout":
                    type(self).logout_calls += 1
                    self.me_status = 401
                    self._send_json(204, b"")
                    return
                if self.path == "/api/mcp":
                    body = json.dumps(
                        [] if self.api_status == 200 else {"error": "private server detail"}
                    ).encode()
                    self.send_response(self.api_status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(404)
                self.end_headers()

            def _send(self, body: bytes, media_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", media_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        return Handler


class BrowserSandboxE2ETests(unittest.TestCase):
    def test_connections_admin_settings_supports_pointer_keyboard_and_recovery(self) -> None:
        browser = browser_path()
        if browser is None:
            self.skipTest("set FOLIO_BROWSER to Chrome or Chromium for connections UI evidence")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control_port = free_port()
            control_origin = f"http://127.0.0.1:{control_port}"
            environment = {
                **os.environ,
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                "FOLIO_TENANT_ID": disposable_tenant("browser-connections"),
                "FOLIO_ACTOR": "browser-admin",
                "FOLIO_CONTROL_ORIGIN": control_origin,
                "FOLIO_RENDER_ORIGIN": control_origin,
                "FOLIO_TEST_EXTERNAL_MCP_VALIDATOR": "deterministic",
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            control = start_server(environment, "http", control_port)
            chrome: DevTools | None = None
            try:
                wait_ready(control_origin, control)
                chrome = DevTools(
                    browser,
                    f"{control_origin}/settings/connections",
                    root / "chrome-connections",
                )
                chrome.wait(
                    "document.querySelector('#connections-status')?.textContent.includes('No approved connections')"
                )
                self.assertEqual(
                    chrome.evaluate(
                        "document.querySelector('#connections-status').getAttribute('role')"
                    ),
                    "status",
                )
                ax = chrome.command("Accessibility.getFullAXTree")["nodes"]
                names = {node.get("name", {}).get("value") for node in ax}
                for expected_name in (
                    "Approved connections",
                    "Register a connection",
                    "Connection name",
                    "HTTPS endpoint",
                    "Approved tools",
                    "Audit activity",
                ):
                    self.assertIn(expected_name, names)

                # Pointer path: register a real connection through the public UI.
                chrome.evaluate(
                    "document.querySelector('#connection-name').value='Browser calendar'; "
                    "document.querySelector('#connection-endpoint').value='https://example.com/mcp'; "
                    "document.querySelector('#connection-endpoint').dispatchEvent(new Event('input', {bubbles:true})); "
                    "document.querySelector('#approved-tools').value='calendar.events.list'; "
                    "document.querySelector('#connection-origin').value='https://example.com'; "
                    "document.querySelector('#connection-origin').dispatchEvent(new Event('input', {bubbles:true}));"
                )
                chrome.evaluate("document.querySelector('#connection-submit').click()")
                chrome.wait(
                    "document.querySelector('#connections-list').innerText.includes('Browser calendar') || document.querySelector('#connections-status').dataset.state === 'error'"
                )
                self.assertTrue(
                    chrome.evaluate(
                        "document.querySelector('#connections-list').innerText.includes('Browser calendar')"
                    ),
                    chrome.evaluate("document.querySelector('#connections-status').textContent"),
                )
                chrome.wait(
                    "document.querySelector('#connections-status').textContent.includes('Connection registered')"
                )
                self.assertFalse(
                    chrome.evaluate("document.querySelector('#connection-submit').disabled")
                )
                self.assertEqual(
                    chrome.evaluate(
                        "document.querySelector('#connection-submit').getAttribute('aria-busy')"
                    ),
                    None,
                )
                self.assertEqual(
                    chrome.evaluate(
                        "document.querySelector('#connections-list .connection-actions button.button-danger').getAttribute('aria-label')"
                    ),
                    "Revoke Browser calendar",
                )

                # Keyboard path: open and cancel the native dialog, then verify focus
                # returns to the invoking control.
                chrome.evaluate(
                    "document.querySelector('#connections-list .connection-actions .button-danger').focus()"
                )
                self.assertEqual(chrome.evaluate("document.activeElement.tagName"), "BUTTON")
                chrome.key("Enter", "Enter", 13)
                chrome.wait("document.querySelector('#revoke-access').open === true")
                chrome.wait("document.activeElement?.id === 'revoke-cancel'")
                self.assertIn(
                    "Browser calendar",
                    chrome.evaluate("document.querySelector('#revoke-name').textContent"),
                )
                chrome.evaluate("document.querySelector('#revoke-cancel').click()")
                chrome.wait("document.querySelector('#revoke-access').open === false")
                self.assertEqual(
                    chrome.evaluate("document.activeElement.getAttribute('aria-label')"),
                    "Revoke Browser calendar",
                )

                # Pointer confirm path: revoke through the production admin endpoint
                # and assert the live state and rendered status.
                chrome.evaluate(
                    "document.querySelector('#connections-list .connection-actions .button-danger').click()"
                )
                chrome.wait("document.querySelector('#revoke-access').open === true")
                # A native close event from the canceled keyboard path may be
                # delivered after this reopen. It must not clear the new target.
                chrome.evaluate(
                    "document.querySelector('#revoke-access').dispatchEvent(new Event('close'))"
                )
                self.assertTrue(chrome.evaluate("document.querySelector('#revoke-access').open"))
                self.assertFalse(
                    chrome.evaluate("document.querySelector('#revoke-confirm').disabled")
                )
                chrome.evaluate("document.querySelector('#revoke-confirm').click()")
                chrome.wait(
                    "document.querySelector('#connections-status').textContent.includes('is revoked')"
                )
                chrome.wait(
                    "document.querySelector('#connections-list').innerText.includes('Revoked')"
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#connections-status').dataset.state"),
                    "revoked",
                )
                page_text = chrome.evaluate("document.body.innerText").lower()
                self.assertNotIn("credential_ref", page_text)
                self.assertNotIn("bearer", page_text)

                # Actual public endpoint error path: loopback HTTPS is rejected by
                # registration policy; the form remains usable and exposes bounded copy.
                chrome.evaluate(
                    "document.querySelector('#connection-name').value='Rejected local'; "
                    "document.querySelector('#connection-endpoint').value='https://127.0.0.1/mcp'; "
                    "document.querySelector('#connection-endpoint').dispatchEvent(new Event('input', {bubbles:true})); "
                    "document.querySelector('#approved-tools').value='calendar.events.list'; "
                    "document.querySelector('#connection-origin').value='https://127.0.0.1'; "
                    "document.querySelector('#connection-origin').dispatchEvent(new Event('input', {bubbles:true}));"
                )
                chrome.evaluate("document.querySelector('#connection-submit').click()")
                chrome.wait(
                    "document.querySelector('#connections-status').dataset.state === 'error'"
                )
                error_copy = chrome.evaluate(
                    "document.querySelector('#connections-status').textContent"
                ).lower()
                self.assertTrue(
                    "endpoint host is not public" in error_copy
                    or "endpoint resolves to a blocked address" in error_copy,
                    error_copy,
                )
                self.assertFalse(
                    chrome.evaluate("document.querySelector('#connection-submit').disabled")
                )
                self.assertNotIn("Traceback", chrome.evaluate("document.body.innerText"))
            finally:
                if chrome is not None:
                    chrome.close()
                stop_server(control)

    def test_scripts_render_but_hostile_browser_matrix_fails_closed(self) -> None:
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
                "FOLIO_TENANT_ID": disposable_tenant("browser-test"),
                "FOLIO_CONTROL_ORIGIN": harness_origin,
                "FOLIO_RENDER_ORIGIN": render_origin,
                "FOLIO_RENDERER_CAPABILITY_SECRET": RENDERER_CAPABILITY_SECRET,
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
                    b"document.body.dataset.javascript='ran';"
                    b"parent.postMessage({type:'folio-render-result',value:'javascript-ran'}, '*')",
                    "application/javascript",
                )
                css = create(
                    control_origin, "behavior.css", b"body { color: rgb(1, 2, 3); }", "text/css"
                )
                js_id = javascript["artifact"]["id"]
                js_version = javascript["version"]["id"]
                css_id = css["artifact"]["id"]
                css_version = css["version"]["id"]
                linked_source = f"""<!doctype html><html><head>
<link rel="stylesheet" href="/content/{css_id}/{css_version}"></head><body>
<p id="linked-status">Loading</p>
<script src="/content/{js_id}/{js_version}"></script>
<script>
const linked = getComputedStyle(document.body).color === 'rgb(1, 2, 3)';
parent.postMessage({{type: 'folio-linked-assets', value: linked ? 'linked-assets-loaded' : 'linked-css-missing'}}, '*');
</script></body></html>""".encode()
                linked = create(control_origin, "linked.html", linked_source, "text/html")
                hostile_source = self._hostile_source(
                    harness_origin, render_origin, js_id, js_version
                ).encode()
                hostile = create(control_origin, "hostile.html", hostile_source, "text/html")
                hostile_url = f"{render_origin}/render/{hostile['artifact']['id']}"
                handler = self._handler(
                    hostile_url,
                    {
                        "javascript": f"{render_origin}/render/{js_id}",
                        "stylesheet": f"{render_origin}/render/{css['artifact']['id']}",
                        "linked": (
                            f"{render_origin}/render/{linked['artifact']['id']}"
                            f"?version_id={linked['version']['id']}"
                        ),
                    },
                )
                harness = ThreadingHTTPServer(("127.0.0.1", harness_port), handler)
                thread = threading.Thread(target=harness.serve_forever, daemon=True)
                thread.start()
                renderer = start_server(
                    {**environment, "FOLIO_MCP_URL": f"{control_origin}/mcp"},
                    "renderer",
                    render_port,
                )
                wait_ready(render_origin, renderer)
                match = None
                hostile_dom = ""
                for attempt in range(3):
                    hostile_dom = dump_dom(
                        browser,
                        f"{harness_origin}/harness",
                        root / f"chrome-hostile-{attempt}",
                    )
                    match = re.search(r'<pre id="result">(.*?)</pre>', hostile_dom, re.DOTALL)
                    if match is not None and html.unescape(match.group(1)) != "waiting":
                        break
                self.assertIsNotNone(match, hostile_dom)
                assert match is not None
                result = json.loads(html.unescape(match.group(1)))
                self.assertEqual(
                    result,
                    {
                        "script_ran": True,
                        "host_dom_blocked": True,
                        "host_cookie_blocked": True,
                        "storage_blocked": True,
                        "cookie_blocked": True,
                        "popup_blocked": True,
                        "download_blocked": True,
                        "form_blocked": True,
                        "frame_blocked": True,
                        "worker_blocked": True,
                        "fetch_blocked": True,
                        "same_origin_fetch_blocked": True,
                        "xhr_blocked": True,
                        "websocket_blocked": True,
                        "event_source_blocked": True,
                        "beacon_blocked": True,
                        "image_blocked": True,
                        "top_navigation_blocked": True,
                        "top_navigation_script_continued": True,
                    },
                )
                self.assertGreaterEqual(result["csp_violation_count"], 5)
                self.assertTrue(
                    {
                        "connect-src",
                        "frame-src",
                        "worker-src",
                        "form-action",
                    }.issubset(set(result["csp_directives"]))
                )
                self.assertEqual(handler.leaks, [])
                self.assertEqual(
                    [
                        path
                        for profile in root.glob("chrome-hostile-*/downloads")
                        for path in profile.iterdir()
                    ],
                    [],
                )
                denied_request = urllib.request.Request(
                    hostile_url, headers={"Sec-Fetch-Dest": "document"}
                )
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(denied_request)
                self.assertEqual(denied.exception.code, 404)
                self.assertNotIn(b"script_ran", denied.exception.read())
                embedded_dom = dump_dom(browser, f"{harness_origin}/embedded", root / "chrome-3")
                self.assertIn("javascript-ran", embedded_dom)
                self.assertIn("stylesheet-loaded", embedded_dom)
                self.assertIn("linked-assets-loaded", embedded_dom)
            finally:
                if renderer is not None:
                    stop_server(renderer)
                stop_server(control)
                if harness is not None:
                    harness.shutdown()
                    harness.server_close()
                if thread is not None:
                    thread.join(timeout=5)

    def test_graph_first_workspace_picker_tree_views_and_standalone(self) -> None:
        browser = browser_path()
        if browser is None:
            self.skipTest("set FOLIO_BROWSER to Chrome or Chromium for graph workspace evidence")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control_port, render_port = free_port(), free_port()
            control_origin = f"http://127.0.0.1:{control_port}"
            render_origin = f"http://127.0.0.1:{render_port}"
            environment = {
                **os.environ,
                "FOLIO_DB_PATH": str(root / "folio.db"),
                "FOLIO_BLOB_ROOT": str(root / "blobs"),
                "FOLIO_TENANT_ID": disposable_tenant("browser-graph"),
                "FOLIO_ACTOR": "browser-persona",
                "FOLIO_CONTROL_ORIGIN": control_origin,
                "FOLIO_RENDER_ORIGIN": render_origin,
                "FOLIO_RENDERER_CAPABILITY_SECRET": RENDERER_CAPABILITY_SECRET,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            control = start_server(environment, "http", control_port)
            renderer: subprocess.Popen[bytes] | None = None
            chrome: DevTools | None = None
            try:
                wait_ready(control_origin, control)
                marker = "agent-asset-check-4d61b9da6b"
                source = create(
                    control_origin,
                    "notes/decision.md",
                    f"# Decision\n\n- Choose the graph workspace\n\n{marker}".encode(),
                    "text/markdown",
                )
                site = create(
                    control_origin,
                    "site/index.html",
                    f"<!doctype html><h1>Connected site</h1><p>{marker}</p>".encode(),
                    "text/html",
                )
                mcp(
                    control_origin,
                    "graph_link",
                    {
                        "source_artifact_id": source["artifact"]["id"],
                        "target_artifact_id": site["artifact"]["id"],
                        "edge_type": "publishes",
                        "metadata": {},
                    },
                )
                renderer = start_server(
                    {**environment, "FOLIO_MCP_URL": f"{control_origin}/mcp"},
                    "renderer",
                    render_port,
                )
                wait_ready(render_origin, renderer)
                chrome = DevTools(browser, control_origin, root / "chrome-graph")
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
                chrome.wait("Boolean(document.querySelector('#graph-artifacts button'))")
                graph_card_text = chrome.evaluate(
                    "document.querySelector('#graph-artifacts').innerText"
                )
                self.assertIn("1 linked item", graph_card_text)
                self.assertNotIn("edge_count", graph_card_text)
                self.assertNotIn("graph_edges", graph_card_text)
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#main').getAttribute('tabindex')"),
                    "-1",
                )
                chrome.evaluate("document.querySelector('.skip-link').focus()")
                chrome.key("Enter", "Enter", 13)
                chrome.wait("document.activeElement?.id === 'main'")
                self.assertFalse(
                    chrome.evaluate("Boolean(document.querySelector('#recent-artifacts'))")
                )
                self.assertIn("decision.md", chrome.evaluate("document.body.innerText"))

                chrome.evaluate(
                    "[...document.querySelectorAll('#graph-artifacts button')].find((button) => button.textContent === 'notes/decision.md').click()"
                )
                chrome.wait("location.pathname.startsWith('/workspace/')")
                self.assertEqual(
                    chrome.evaluate("decodeURIComponent(location.pathname.split('/')[2])"),
                    source["artifact"]["id"],
                )
                chrome.wait("document.querySelector('#title')?.textContent === 'notes/decision.md'")
                chrome.wait("Boolean(document.querySelector('#artifact-tree details'))")
                chrome.evaluate("document.querySelector('#edit-entry').click()")
                chrome.wait("!document.querySelector('#update').hidden")
                chrome.wait("document.activeElement?.id === 'human-content'")
                chrome.evaluate("document.querySelector('#human-cancel').click()")
                chrome.wait(
                    "document.querySelector('#update').hidden && document.activeElement?.id === 'edit-entry'"
                )
                self.assertIn(
                    "site",
                    chrome.evaluate("document.querySelector('#artifact-tree').textContent"),
                )
                self.assertIn(
                    "Decision",
                    chrome.evaluate("document.querySelector('#readable-content').textContent"),
                )
                self.assertFalse(chrome.evaluate("document.querySelector('#reader').hidden"))
                self.assertTrue(chrome.evaluate("document.querySelector('#preview-card').hidden"))
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#share-entry').getAttribute('href')"),
                    "#workspace-share",
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#edit-entry').getAttribute('href')"),
                    "#update",
                )

                chrome.evaluate(
                    f"document.querySelector('#workspace-search-query').value = {json.dumps(marker)}; "
                    "document.querySelector('#workspace-search').requestSubmit()"
                )
                chrome.wait(
                    "document.querySelector('#workspace-search-summary')?.textContent === '2 artifacts found.'"
                )
                self.assertTrue(
                    chrome.evaluate("!document.querySelector('#workspace-search-results').hidden")
                )
                self.assertEqual(chrome.evaluate("document.querySelectorAll('#results').length"), 1)
                visible_names = chrome.evaluate(
                    "[...document.querySelectorAll('#workspace-search-results-list button[data-artifact-id]')].map((button) => button.textContent)"
                )
                self.assertEqual(set(visible_names), {"notes/decision.md", "site/index.html"})
                graph_paths = chrome.evaluate(
                    "[...document.querySelectorAll('#workspace-search-results-list button[data-artifact-id]')].map((button) => button.dataset.graphPath)"
                )
                self.assertEqual(
                    set(graph_paths),
                    {"notes/decision.md", "notes/decision.md / site/index.html"},
                )
                self.assertNotIn(
                    "No search run yet.",
                    chrome.evaluate(
                        "document.querySelector('#workspace-search-results').innerText"
                    ),
                )
                chrome.evaluate(
                    "document.querySelector('#workspace-search-query').value = 'site/index.html'; "
                    "document.querySelector('#workspace-search').requestSubmit()"
                )
                chrome.wait(
                    "document.querySelectorAll('#workspace-search-results-list button[data-artifact-id]').length === 1"
                )
                self.assertEqual(
                    chrome.evaluate(
                        "document.querySelector('#workspace-search-results-list button').dataset.artifactId"
                    ),
                    site["artifact"]["id"],
                )
                self.assertIn(
                    "Graph: notes/decision.md / site/index.html",
                    chrome.evaluate(
                        "document.querySelector('#workspace-search-results').innerText"
                    ),
                )
                scoped_context = chrome.evaluate(
                    "[...document.querySelectorAll('#workspace-search-results .result-context')].map((node) => node.textContent)"
                )
                self.assertTrue(
                    all(source["artifact"]["id"] not in text for text in scoped_context)
                )
                self.assertTrue(all(site["artifact"]["id"] not in text for text in scoped_context))
                self.assertTrue(
                    all(";" not in text and "links" not in text for text in scoped_context)
                )
                self.assertNotIn(
                    "links",
                    chrome.evaluate(
                        "document.querySelector('#workspace-search-results').innerText"
                    ),
                )
                workspace_search_text = chrome.evaluate(
                    "document.querySelector('#workspace-search-results').innerText"
                )
                self.assertNotIn("Graph: Graph path:", workspace_search_text)
                self.assertNotIn("Graph art_", workspace_search_text)
                self.assertNotIn(";", workspace_search_text)
                self.assertEqual(
                    chrome.evaluate(
                        "document.querySelector('#workspace-search-results-list button').dataset.graphPath"
                    ),
                    "notes/decision.md / site/index.html",
                )
                chrome.evaluate(
                    "[...document.querySelectorAll('#workspace-search-results-list button[data-artifact-id]')]"
                    f".find((button) => button.dataset.artifactId === {json.dumps(site['artifact']['id'])}).click()"
                )
                chrome.wait(
                    f"location.pathname === '/workspace/' + {json.dumps(site['artifact']['id'])}"
                )
                chrome.command(
                    "Page.navigate",
                    {"url": f"{control_origin}/workspace/{source['artifact']['id']}"},
                )
                chrome.wait(
                    "location.pathname === '/workspace/' + " + json.dumps(source["artifact"]["id"])
                )
                chrome.wait("document.querySelector('#title')?.textContent === 'notes/decision.md'")
                chrome.evaluate(
                    f"document.querySelector('#workspace-search-query').value = {json.dumps(marker)}; "
                    "document.querySelector('#workspace-search').requestSubmit()"
                )
                chrome.wait(
                    "document.querySelector('#workspace-search-summary')?.textContent === '2 artifacts found.'"
                )
                chrome.evaluate(
                    "[...document.querySelectorAll('#workspace-search-results-list button[data-artifact-id]')]"
                    f".find((button) => button.dataset.artifactId === {json.dumps(site['artifact']['id'])}).focus()"
                )
                chrome.key("Enter", "Enter", 13)
                chrome.wait(
                    f"location.pathname === '/workspace/' + {json.dumps(site['artifact']['id'])}"
                )

                chrome.command(
                    "Page.navigate",
                    {"url": f"{control_origin}/workspace/{source['artifact']['id']}"},
                )
                chrome.wait(
                    "location.pathname === '/workspace/' + " + json.dumps(source["artifact"]["id"])
                )
                chrome.wait("document.querySelector('#title')?.textContent === 'notes/decision.md'")
                chrome.evaluate("document.querySelector('#graph-mode').click()")
                chrome.wait("new URL(location.href).searchParams.get('view') === 'graph'")
                chrome.wait("!document.querySelector('#graph-context').hidden")
                self.assertTrue(
                    chrome.evaluate("Boolean(document.querySelector('#graph-map .graph-source'))")
                )
                self.assertTrue(
                    chrome.evaluate("Boolean(document.querySelector('#graph-map .graph-target'))")
                )
                chrome.evaluate("document.querySelector('#graph-map .graph-target').click()")
                chrome.wait(
                    "location.pathname === '/workspace/' + " + json.dumps(site["artifact"]["id"])
                )
                chrome.wait("document.querySelector('#title')?.textContent === 'site/index.html'")
                chrome.wait("!document.querySelector('#preview-card').hidden")
                self.assertTrue(chrome.evaluate("document.querySelector('#reader').hidden"))
                self.assertFalse(
                    chrome.evaluate("document.querySelector('#standalone-link').hidden")
                )
                self.assertTrue(
                    chrome.evaluate(
                        "document.querySelector('#standalone-link').getAttribute('href').startsWith('/standalone/')"
                    )
                )

                chrome.command(
                    "Page.navigate",
                    {"url": f"{control_origin}/workspace/{source['artifact']['id']}?view=graph"},
                )
                chrome.wait(
                    "document.querySelector('#graph-mode')?.getAttribute('aria-selected') === 'true'"
                )
                chrome.wait("!document.querySelector('#graph-context').hidden")
                chrome.command(
                    "Page.navigate",
                    {"url": f"{control_origin}/standalone/{site['artifact']['id']}"},
                )
                chrome.wait("document.querySelector('body').classList.contains('standalone-route')")
                chrome.wait("!document.querySelector('#human-preview').hidden")
                self.assertFalse(chrome.evaluate("Boolean(document.querySelector('.topbar'))"))
                self.assertFalse(
                    chrome.evaluate("document.body.innerText.includes('Folio Lattice')")
                )
                self.assertEqual(
                    chrome.evaluate(
                        "document.querySelector('#standalone-back').getAttribute('href')"
                    ),
                    "/",
                )
                chrome.evaluate("document.querySelector('#standalone-back').click()")
                chrome.wait("location.pathname === '/'")
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
            finally:
                if chrome is not None:
                    chrome.close()
                if renderer is not None:
                    stop_server(renderer)
                stop_server(control)

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
                "FOLIO_TENANT_ID": disposable_tenant("browser-ui"),
                "FOLIO_ACTOR": "browser-persona",
                "FOLIO_CONTROL_ORIGIN": control_origin,
                "FOLIO_RENDER_ORIGIN": render_origin,
                "FOLIO_RENDERER_CAPABILITY_SECRET": RENDERER_CAPABILITY_SECRET,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            }
            control = start_server(environment, "http", control_port)
            renderer: subprocess.Popen[bytes] | None = None
            chrome: DevTools | None = None
            bridge_exercised = False
            try:
                wait_ready(control_origin, control)
                body_marker = "library body marker"
                target = create(
                    control_origin,
                    "decision.md",
                    f"# Decision\n\n- browser target\n\n{body_marker}".encode(),
                    "text/markdown",
                )
                duplicate_one = create(
                    control_origin,
                    "same-name.md",
                    f"duplicate library marker one\n{body_marker}".encode(),
                    "text/markdown",
                )
                duplicate_two = create(
                    control_origin,
                    "same-name.md",
                    f"duplicate library marker two\n{body_marker}".encode(),
                    "text/markdown",
                )
                javascript = create(
                    control_origin,
                    "browser.js",
                    f"document.body.dataset.browserJavascript='ran'; // {body_marker}".encode(),
                    "application/javascript",
                )
                stylesheet = create(
                    control_origin,
                    "browser.css",
                    f"body {{ color: rgb(3, 4, 5); }} /* {body_marker} */".encode(),
                    "text/css",
                )
                source = b"""<!doctype html><body>browsermarker human-first<script>
addEventListener('message', (event) => {
 if (event.data?.type !== 'folio.mcp.response') return;
 document.body.dataset[event.data.id] = String(event.data.ok);
 if (event.data.id === 'bridgeAllow') parent.postMessage({type:'folio.mcp.request',id:'bridgeDeny',attachment:'folio-lattice',tool:'artifact_write',arguments:{}}, '*');
});
parent.postMessage({type:'folio.mcp.request',id:'bridgeAllow',attachment:'folio-lattice',tool:'artifact_search',arguments:{query:'browsermarker'}}, '*');
</script></body>"""
                typed_html = create(
                    control_origin,
                    "searchable.html",
                    f"<h1>typed media</h1><p>{body_marker}</p>".encode(),
                    "text/html",
                )
                upload = root / "browser-note.html"
                upload.write_bytes(source)
                renderer = start_server(
                    {**environment, "FOLIO_MCP_URL": f"{control_origin}/mcp"},
                    "renderer",
                    render_port,
                )
                wait_ready(render_origin, renderer)
                chrome = DevTools(browser, control_origin, root / "chrome-ui")
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
                chrome.wait("Boolean(document.querySelector('#graph-artifacts button'))")
                chrome.wait(
                    "[...document.querySelectorAll('#artifact-library button')].filter((button) => button.textContent.startsWith('same-name.md')).length === 2"
                )
                duplicate_labels = chrome.evaluate(
                    "[...document.querySelectorAll('#artifact-library button')].map((button) => button.textContent).filter((text) => text.startsWith('same-name.md'))"
                )
                self.assertEqual(len(duplicate_labels), 2)
                self.assertEqual(
                    set(duplicate_labels),
                    {"same-name.md · Copy 1 of 2", "same-name.md · Copy 2 of 2"},
                )
                self.assertEqual(
                    set(
                        chrome.evaluate(
                            "[...document.querySelectorAll('#artifact-library button[data-artifact-id]')].filter((button) => button.textContent.startsWith('same-name.md')).map((button) => button.dataset.artifactId)"
                        )
                    ),
                    {duplicate_one["artifact"]["id"], duplicate_two["artifact"]["id"]},
                )

                chrome.evaluate("document.querySelector('#find').open = true")
                chrome.evaluate(f"""
document.querySelector('#search-query').value = {json.dumps(body_marker)};
document.querySelector('#search').requestSubmit();
""")
                chrome.wait(
                    "document.querySelectorAll('#results button[data-artifact-id]').length === 6"
                )
                marker_result_ids = chrome.evaluate(
                    "[...document.querySelectorAll('#results button[data-artifact-id]')].map((button) => button.dataset.artifactId)"
                )
                self.assertEqual(
                    set(marker_result_ids),
                    {
                        target["artifact"]["id"],
                        duplicate_one["artifact"]["id"],
                        duplicate_two["artifact"]["id"],
                        javascript["artifact"]["id"],
                        stylesheet["artifact"]["id"],
                        typed_html["artifact"]["id"],
                    },
                )
                self.assertEqual(
                    set(
                        chrome.evaluate(
                            "[...document.querySelectorAll('#results button[data-artifact-id]')].map((button) => button.dataset.mediaType)"
                        )
                    ),
                    {"text/markdown", "text/html", "application/javascript", "text/css"},
                )
                self.assertEqual(
                    set(
                        chrome.evaluate(
                            "[...document.querySelectorAll('#results .result-group-heading h3')].map((heading) => heading.textContent)"
                        )
                    ),
                    {"Markdown", "HTML", "JavaScript", "CSS"},
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#search-result-summary').textContent"),
                    "6 results across 4 file types.",
                )
                self.assertNotIn(
                    "art_",
                    chrome.evaluate("document.querySelector('#results').innerText"),
                )
                chrome.evaluate(
                    "document.querySelector('#search-type-filter').value = 'text/markdown'; "
                    "document.querySelector('#search-type-filter').dispatchEvent(new Event('change', {bubbles:true}))"
                )
                chrome.wait(
                    "document.querySelectorAll('#results button[data-artifact-id]').length === 3"
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#search-result-summary').textContent"),
                    "3 markdown results.",
                )
                self.assertEqual(
                    chrome.evaluate(
                        "[...document.querySelectorAll('#results button[data-artifact-id]')].map((button) => button.dataset.mediaType)"
                    ),
                    ["text/markdown", "text/markdown", "text/markdown"],
                )
                self.assertEqual(
                    chrome.evaluate(
                        "[...document.querySelectorAll('#results .result-group-heading h3')].map((heading) => heading.textContent)"
                    ),
                    ["Markdown"],
                )
                chrome.evaluate(
                    "document.querySelector('#search-type-filter').value = 'all'; "
                    "document.querySelector('#search-type-filter').dispatchEvent(new Event('change', {bubbles:true}))"
                )
                chrome.evaluate(
                    "[...document.querySelectorAll('#results button[data-artifact-id]')]"
                    f".find((button) => button.dataset.artifactId === {json.dumps(target['artifact']['id'])}).click()"
                )
                chrome.wait(f"location.pathname === '/workspace/{target['artifact']['id']}'")
                chrome.wait("document.querySelector('#title')?.textContent === 'decision.md'")
                self.assertFalse(chrome.evaluate("document.querySelector('#reader').hidden"))
                self.assertIn(
                    "Decision",
                    chrome.evaluate("document.querySelector('#readable-content').textContent"),
                )
                chrome.command("Page.navigate", {"url": control_origin})
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
                chrome.evaluate("document.querySelector('#find').open = true")
                chrome.evaluate(f"""
document.querySelector('#search-query').value = {json.dumps(body_marker)};
document.querySelector('#search').requestSubmit();
""")
                chrome.wait(
                    "document.querySelectorAll('#results button[data-artifact-id]').length === 6"
                )
                chrome.evaluate(
                    "[...document.querySelectorAll('#results button[data-artifact-id]')]"
                    f".find((button) => button.dataset.artifactId === {json.dumps(typed_html['artifact']['id'])}).click()"
                )
                chrome.wait(f"location.pathname === '/workspace/{typed_html['artifact']['id']}'")
                chrome.wait("document.querySelector('#title')?.textContent === 'searchable.html'")
                self.assertFalse(chrome.evaluate("document.querySelector('#preview-card').hidden"))
                self.assertTrue(chrome.evaluate("document.querySelector('#reader').hidden"))
                chrome.command("Page.navigate", {"url": control_origin})
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
                chrome.evaluate("document.querySelector('#find').open = true")
                chrome.evaluate("""
window.__folioSearchCalls = [];
const originalFetch = window.fetch;
window.fetch = (url, options) => {
  const payload = JSON.parse(options?.body || '{}');
  if (payload.tool === 'artifact_search' || payload.tool === 'artifact_list') {
    window.__folioSearchCalls.push(payload);
  }
  return originalFetch(url, options);
};
document.querySelector('#search-query').value = 'duplicate library marker';
document.querySelector('#search').requestSubmit();
""")
                chrome.wait(
                    "document.querySelectorAll('#results button[data-artifact-id]').length === 2"
                )
                self.assertNotIn(
                    "links", chrome.evaluate("document.querySelector('#results').innerText")
                )
                self.assertEqual(
                    chrome.evaluate(
                        "[...document.querySelectorAll('#results button[data-artifact-id]')].map((button) => button.dataset.graphPath)"
                    ),
                    ["", ""],
                )
                self.assertEqual(
                    chrome.evaluate("window.__folioSearchCalls[0].tool"), "artifact_search"
                )
                self.assertFalse(
                    chrome.evaluate(
                        "Object.hasOwn(window.__folioSearchCalls[0].arguments, 'graph_root_artifact_id')"
                    )
                )
                chrome.evaluate("""
window.__folioSearchCalls = [];
document.querySelector('#search-query').value = 'same-name.md';
document.querySelector('#search').requestSubmit();
""")
                chrome.wait("window.__folioSearchCalls.length === 3")
                self.assertEqual(
                    chrome.evaluate("window.__folioSearchCalls[0].tool"), "artifact_search"
                )
                self.assertEqual(
                    chrome.evaluate(
                        "window.__folioSearchCalls.find((call) => call.arguments.name)?.arguments.name"
                    ),
                    "same-name.md",
                )
                chrome.wait(
                    "document.querySelectorAll('#results button[data-artifact-id]').length === 2"
                )
                duplicate_ids = chrome.evaluate(
                    "[...document.querySelectorAll('#results button[data-artifact-id]')]"
                    ".map((button) => button.dataset.artifactId)"
                )
                self.assertEqual(
                    set(duplicate_ids),
                    {duplicate_one["artifact"]["id"], duplicate_two["artifact"]["id"]},
                )
                duplicate_versions = chrome.evaluate(
                    "[...document.querySelectorAll('#results button[data-artifact-id]')]"
                    ".map((button) => button.dataset.versionId)"
                )
                self.assertEqual(
                    set(duplicate_versions),
                    {
                        duplicate_one["artifact"]["current_version_id"],
                        duplicate_two["artifact"]["current_version_id"],
                    },
                )
                result_text = chrome.evaluate("document.querySelector('#results').innerText")
                result_labels = chrome.evaluate(
                    "[...document.querySelectorAll('#results button[data-artifact-id]')].map((button) => button.textContent)"
                )
                self.assertEqual(
                    set(result_labels), {"same-name.md · Copy 1 of 2", "same-name.md · Copy 2 of 2"}
                )
                self.assertTrue(
                    all(artifact_id not in result_text for artifact_id in duplicate_ids)
                )
                self.assertNotIn("links", result_text)
                self.assertEqual(
                    chrome.evaluate(
                        "[...document.querySelectorAll('#results button[data-artifact-id]')].map((button) => button.dataset.graphPath)"
                    ),
                    ["", ""],
                )
                library_context = chrome.evaluate(
                    "[...document.querySelectorAll('#results .result-context')].map((node) => node.textContent)"
                )
                self.assertTrue(
                    all(
                        artifact_id not in text
                        for text in library_context
                        for artifact_id in duplicate_ids
                    )
                )
                self.assertTrue(
                    all(";" not in text and "links" not in text for text in library_context)
                )
                chrome.evaluate(
                    "[...document.querySelectorAll('#results button[data-artifact-id]')]"
                    f".find((button) => button.dataset.artifactId === {json.dumps(duplicate_two['artifact']['id'])}).click()"
                )
                chrome.wait(f"location.pathname === '/workspace/{duplicate_two['artifact']['id']}'")
                chrome.wait("document.querySelector('#title')?.textContent === 'same-name.md'")
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#edit-entry').getAttribute('href')"),
                    "#update",
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#share-entry').getAttribute('href')"),
                    "#workspace-share",
                )
                chrome.command("Page.navigate", {"url": control_origin})
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
                chrome.evaluate("document.querySelector('#find').open = true")
                chrome.evaluate("""
window.__folioSearchCalls = [];
document.querySelector('#search-query').value = 'text/html';
document.querySelector('#search').requestSubmit();
""")
                chrome.wait(
                    "document.querySelectorAll('#results button[data-artifact-id]').length === 1"
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#results button').dataset.artifactId"),
                    typed_html["artifact"]["id"],
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#results button').dataset.mediaType"),
                    "text/html",
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#results button').dataset.versionId"),
                    typed_html["artifact"]["current_version_id"],
                )
                self.assertIn(
                    "media_type",
                    chrome.evaluate("document.querySelector('#results button').dataset.matchKind"),
                )
                self.assertNotIn(
                    "links", chrome.evaluate("document.querySelector('#results').innerText")
                )
                type_context = chrome.evaluate(
                    "[...document.querySelectorAll('#results .result-context')].map((node) => node.textContent)"
                )
                self.assertTrue(any("searchable.html" in text for text in type_context))
                self.assertTrue(
                    all("Graph" not in text and ";" not in text for text in type_context)
                )
                chrome.evaluate(
                    "[...document.querySelectorAll('#results button[data-artifact-id]')]"
                    f".find((button) => button.dataset.artifactId === {json.dumps(typed_html['artifact']['id'])}).click()"
                )
                chrome.wait(f"location.pathname === '/workspace/{typed_html['artifact']['id']}'")
                chrome.wait("document.querySelector('#title')?.textContent === 'searchable.html'")
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#edit-entry').getAttribute('href')"),
                    "#update",
                )
                chrome.command("Page.navigate", {"url": control_origin})
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
                chrome.evaluate("""
librarySearchResults = [
  {artifact_id: 'bounded-a', artifact_name: 'bounded-copy.md', media_type: 'text/markdown'},
  {artifact_id: 'bounded-b', artifact_name: 'bounded-copy.md', media_type: 'text/markdown'},
  ...Array.from({length: 18}, (_, index) => ({
    artifact_id: `bounded-html-${index}`,
    artifact_name: `bounded-${index}.html`,
    media_type: 'text/html',
  })),
];
document.querySelector('#search-type-filter').value = 'text/markdown';
renderLibrarySearch();
""")
                self.assertEqual(
                    chrome.evaluate(
                        "[...document.querySelectorAll('#results button[data-artifact-id]')]"
                        ".map((button) => button.textContent)"
                    ),
                    ["bounded-copy.md", "bounded-copy.md"],
                )
                self.assertEqual(
                    chrome.evaluate("""
const debugMap = document.createElement('div');
debugMap.id = 'graph-map';
document.body.append(debugMap);
renderGraphMap([
  {target_artifact_id: 'bounded-a', target_artifact_name: 'bounded-copy.md', edge_type: 'references'},
  {target_artifact_id: 'bounded-b', target_artifact_name: 'bounded-copy.md', edge_type: 'references'},
]);
const labels = [...debugMap.querySelectorAll('.graph-target')].map((button) => button.textContent);
debugMap.remove();
labels;
"""),
                    ["bounded-copy.md", "bounded-copy.md"],
                )
                chrome.command("Page.navigate", {"url": f"{control_origin}/#find"})
                chrome.wait(
                    "document.querySelector('#find').open && "
                    "document.activeElement?.id === 'search-query'"
                )
                chrome.evaluate(
                    "document.querySelector('#find').open = false; "
                    "document.querySelector('#search-query').blur(); "
                    "document.querySelector('a[href=\"/#find\"]').click()"
                )
                chrome.wait(
                    "document.querySelector('#find').open && "
                    "document.activeElement?.id === 'search-query'"
                )
                chrome.command("Page.navigate", {"url": control_origin})
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
                chrome.evaluate("document.querySelector('a[href=\"/#find\"]').click()")
                chrome.wait(
                    "document.querySelector('#find').open && "
                    "document.activeElement?.id === 'search-query'"
                )
                chrome.command("Page.navigate", {"url": control_origin})
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
                chrome.evaluate("document.querySelector('a[href=\"/#find\"]').focus()")
                chrome.key("Enter", "Enter", 13)
                chrome.wait(
                    "document.querySelector('#find').open && "
                    "document.activeElement?.id === 'search-query'"
                )

                ax = chrome.command("Accessibility.getFullAXTree")["nodes"]
                names = {node.get("name", {}).get("value") for node in ax}
                roles = {node.get("role", {}).get("value") for node in ax}
                for expected_role in ("banner", "main", "region", "search"):
                    self.assertIn(expected_role, roles)
                for expected_name in (
                    "Folio Lattice",
                    "Library",
                    "Choose a graph",
                    "New graph or artifact",
                    "Search library",
                ):
                    self.assertIn(expected_name, names)
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#status').getAttribute('role')"),
                    "status",
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#error').getAttribute('role')"),
                    "alert",
                )
                chrome.evaluate("document.querySelector('#graph-artifacts button').focus()")
                self.assertEqual(chrome.evaluate("document.activeElement.tagName"), "BUTTON")
                self.assertEqual(
                    chrome.evaluate("document.querySelector('h1')?.textContent"),
                    "Choose where to work.",
                )
                self.assertNotIn("Open artifact ID", chrome.evaluate("document.body.innerText"))
                self.assertNotIn("Open by identifier", chrome.evaluate("document.body.innerText"))
                self.assertNotIn(
                    "Unauthenticated local development", chrome.evaluate("document.body.innerText")
                )
                self.assertEqual(
                    chrome.evaluate(
                        "[...document.querySelectorAll('[tabindex]')].filter((node) => Number(node.tabIndex) > 0).length"
                    ),
                    0,
                )
                for selector in (
                    "#artifact-id",
                    "#sharing",
                    "#bridge-status",
                    "#fullscreen-preview",
                ):
                    self.assertFalse(
                        chrome.evaluate(f"Boolean(document.querySelector({selector!r}))")
                    )
                chrome.command(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 320, "height": 900, "deviceScaleFactor": 1, "mobile": False},
                )
                self.assertTrue(
                    chrome.evaluate(
                        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
                    )
                )
                chrome.command("Emulation.clearDeviceMetricsOverride")
                chrome.evaluate("document.querySelector('#create').closest('details').open = true")
                chrome.set_file("#create-file", upload)
                chrome.evaluate("""
document.querySelector('#create-name').value = 'browser-note.html';
document.querySelector('#create').requestSubmit();
""")
                chrome.wait("location.pathname.startsWith('/artifacts/art_')")
                artifact_id = chrome.evaluate("decodeURIComponent(location.pathname.split('/')[2])")
                chrome.wait(
                    "document.querySelector('#human-title')?.textContent === 'browser-note.html' || !document.querySelector('#error').hidden"
                )
                self.assertTrue(
                    chrome.evaluate("document.querySelector('#error').hidden"),
                    chrome.evaluate("document.querySelector('#error').textContent"),
                )
                chrome.wait("document.querySelector('#human-preview')?.src.includes('/render/')")
                for selector in (
                    "#workspace",
                    "#reader",
                    "#preview-card",
                    "#artifact-details",
                    "#sharing",
                    "#graph-context",
                    "#human-edit",
                    "#bridge-status",
                    "#fullscreen-preview",
                ):
                    self.assertFalse(
                        chrome.evaluate(f"Boolean(document.querySelector({selector!r}))")
                    )
                self.assertIn(
                    "Version 1 saved",
                    chrome.evaluate("document.querySelector('#status').textContent"),
                )
                self.assertEqual(
                    chrome.evaluate(
                        "document.querySelector('#human-preview').getAttribute('sandbox')"
                    ),
                    "allow-scripts",
                )
                self.assertGreaterEqual(
                    chrome.evaluate("document.querySelector('#human-preview').clientHeight"),
                    chrome.evaluate(
                        "window.innerHeight - document.querySelector('.topbar').offsetHeight - 2"
                    ),
                )
                self.assertEqual(
                    chrome.evaluate(
                        "document.querySelector('#back-to-library').getAttribute('href')"
                    ),
                    "/",
                )
                chrome.command("Page.navigate", {"url": f"{control_origin}/inspect/{artifact_id}"})
                chrome.wait("document.querySelector('#title').textContent === 'browser-note.html'")
                chrome.wait("document.querySelectorAll('#versions li').length === 1")
                first_version = chrome.evaluate("document.querySelector('#parent-version').value")
                self.assertTrue(
                    chrome.evaluate("Boolean(document.querySelector('#graph-context'))")
                )
                self.assertTrue(
                    chrome.evaluate(
                        "Boolean(document.querySelector('#editor').compareDocumentPosition(document.querySelector('#details')) & Node.DOCUMENT_POSITION_FOLLOWING)"
                    )
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#artifact-media').textContent"),
                    "text/html",
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#artifact-path').textContent"),
                    "browser-note.html",
                )
                self.assertIn(
                    "human-first",
                    chrome.evaluate("document.querySelector('#readable-content').textContent"),
                )
                self.assertTrue(
                    chrome.evaluate(
                        "Boolean(document.querySelector('#reader').compareDocumentPosition(document.querySelector('#editor')) & Node.DOCUMENT_POSITION_FOLLOWING)"
                    )
                )
                self.assertTrue(
                    chrome.evaluate(
                        "Boolean(document.querySelector('#reader').compareDocumentPosition(document.querySelector('#preview-card')) & Node.DOCUMENT_POSITION_FOLLOWING)"
                    )
                )
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#preview').getAttribute('sandbox')"),
                    "allow-scripts",
                )
                chrome.wait(
                    "document.querySelector('#bridge-status').textContent.includes('did not run')"
                )
                bridge_exercised = True
                artifact_ax = chrome.command("Accessibility.getFullAXTree")["nodes"]
                artifact_names = {node.get("name", {}).get("value") for node in artifact_ax}
                artifact_roles = {node.get("role", {}).get("value") for node in artifact_ax}
                self.assertIn("main", artifact_roles)
                for expected_name in (
                    "Save new version",
                    "Chunks",
                    "Versions",
                    "Relationships",
                    "Create relationship",
                    "Open this site",
                    "Sandboxed artifact preview",
                    "Open full screen",
                    "People with access",
                    "Private",
                    "Share access",
                ):
                    self.assertIn(expected_name, artifact_names)
                self.assertIn(
                    "Loaded browser-note.html",
                    chrome.evaluate("document.querySelector('#status').textContent"),
                )
                chrome.evaluate("""
window.__folioAclFetch = window.fetch;
window.fetch = (url, options) => {
  const payload = JSON.parse(options?.body || '{}');
  if (payload.tool === 'artifact_acl') return Promise.resolve(new Response(
    JSON.stringify({error: 'artifact not found'}),
    {status: 404, headers: {'Content-Type': 'application/json'}}));
  return window.__folioAclFetch(url, options);
};
loadArtifact();
""")
                chrome.wait("document.querySelector('#sharing').hidden")
                self.assertIn(
                    "human-first",
                    chrome.evaluate("document.querySelector('#readable-content').textContent"),
                )
                chrome.evaluate(
                    "window.fetch = window.__folioAclFetch; document.querySelector('#bridge-status').textContent = 'Waiting for attached request.'; document.querySelector('#preview').removeAttribute('src'); loadArtifact()"
                )
                chrome.wait("!document.querySelector('#sharing').hidden")
                chrome.wait(
                    "document.querySelector('#bridge-status').textContent.includes('did not run')"
                )
                bridge_status = chrome.evaluate(
                    "document.querySelector('#bridge-status').textContent"
                )
                self.assertIn("did not run", bridge_status)
                self.assertNotIn("Denied or failed", bridge_status)
                self.assertNotIn("artifact_write", bridge_status)

                chrome.evaluate("""
document.querySelector('#share-recipient').value = 'browser-reader';
document.querySelector('#share-role').value = 'read';
document.querySelector('#share').requestSubmit();
""")
                chrome.wait(
                    "document.querySelector('#people-with-access').textContent.includes('browser-reader')"
                )
                self.assertIn(
                    "Can view",
                    chrome.evaluate("document.querySelector('#people-with-access').textContent"),
                )
                chrome.wait(
                    "document.querySelector('#status').textContent.includes('Shared with browser-reader')"
                )
                chrome.evaluate(
                    "document.querySelector('#share-recipient').value = 'browser-reader'; "
                    "document.querySelector('#share-role').value = 'write'; "
                    "document.querySelector('#share').requestSubmit()"
                )
                chrome.wait(
                    "document.querySelector('#people-with-access').textContent.includes('Can edit')"
                )
                chrome.evaluate("document.querySelector('#people-with-access button').click()")
                chrome.wait("document.querySelector('#revoke-access')?.open === true")
                self.assertIn(
                    "Can view, Can edit",
                    chrome.evaluate("document.querySelector('#revoke-role').textContent"),
                )
                chrome.evaluate("document.querySelector('#revoke-cancel').click()")
                chrome.wait("document.activeElement?.matches('#people-with-access .access-action')")
                chrome.evaluate("document.querySelector('#people-with-access button').click()")
                chrome.wait("document.querySelector('#revoke-access')?.open === true")
                chrome.evaluate("document.querySelector('#revoke-confirm').click()")
                chrome.wait(
                    "document.querySelector('#status').textContent.includes('Removed access for browser-reader')"
                )
                chrome.wait(
                    "document.querySelector('#people-with-access').textContent.includes('No one else has access')"
                )
                self.assertIn(
                    "Revoked access history",
                    chrome.evaluate("document.querySelector('#access-history-panel').textContent"),
                )

                chrome.evaluate("""
window.__folioAccessFetch = window.fetch;
window.fetch = (url, options) => {
  const payload = JSON.parse(options?.body || '{}');
  if (payload.tool === 'artifact_share') return Promise.resolve(new Response(
    JSON.stringify({error: 'This document is not available to you.'}),
    {status: 403, headers: {'Content-Type': 'application/json'}}));
  return window.__folioAccessFetch(url, options);
};
document.querySelector('#share-recipient').value = 'denied-reader';
document.querySelector('#share').requestSubmit();
""")
                chrome.wait(
                    "document.querySelector('#error').textContent === 'This document is not available to you.'"
                )
                self.assertTrue(chrome.evaluate("document.querySelector('#workspace').hidden"))
                chrome.evaluate("window.fetch = window.__folioAccessFetch")
                chrome.command("Page.navigate", {"url": f"{control_origin}/inspect/{artifact_id}"})
                chrome.wait("document.querySelector('#title').textContent === 'browser-note.html'")

                chrome.evaluate("""
window.__folioFetch = window.fetch;
window.fetch = (...args) => {
  return new Promise((resolve) => setTimeout(() => resolve(window.__folioFetch(...args)), 400));
};
void loadArtifact();
""")
                time.sleep(0.15)
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#main').getAttribute('aria-busy')"),
                    "true",
                )
                self.assertTrue(
                    chrome.evaluate(
                        "[...document.querySelectorAll('button[type=submit]')].every((button) => button.disabled)"
                    )
                )
                chrome.wait("document.querySelector('#main').getAttribute('aria-busy') === 'false'")
                chrome.evaluate("window.fetch = window.__folioFetch")

                chrome.evaluate("""
window.__folioFetch = window.fetch;
window.fetch = (...args) => new Promise((resolve) => setTimeout(() => resolve(window.__folioFetch(...args)), 250));
document.querySelector('#search-query').value = 'human-first';
document.querySelector('#search').requestSubmit();
""")
                self.assertEqual(
                    chrome.evaluate("document.querySelector('#main').getAttribute('aria-busy')"),
                    "true",
                )
                chrome.wait(
                    "document.querySelector('#results button')?.textContent.startsWith('art_')"
                )
                result_label = chrome.evaluate(
                    "document.querySelector('#results button')?.textContent"
                )
                self.assertIn("browser-note.html", result_label)
                self.assertNotEqual(result_label, artifact_id)
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
                self.assertIn(
                    "decision.md",
                    chrome.evaluate("document.querySelector('#graph button')?.textContent"),
                )
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
                chrome.key("Tab", "Tab", 9)
                self.assertNotEqual(chrome.evaluate("document.activeElement.tagName"), "BODY")

                chrome.command("Page.navigate", {"url": control_origin})
                chrome.wait("document.querySelector('#status')?.textContent === 'Library ready.'")
                chrome.evaluate(
                    "const item = [...document.querySelectorAll('#graph-artifacts button')].find((button) => button.textContent === 'browser-note.html'); item.focus()"
                )
                chrome.key("Enter", "Enter", 13)
                chrome.wait("location.pathname.startsWith('/workspace/art_')")
                chrome.wait("document.querySelector('#title').textContent === 'browser-note.html'")
                self.assertTrue(chrome.evaluate("Boolean(document.querySelector('#preview'))"))
                for selector in ("#sharing", "#bridge-status"):
                    self.assertFalse(
                        chrome.evaluate(f"Boolean(document.querySelector({selector!r}))")
                    )
                chrome.evaluate("document.querySelector('#back-to-library').click()")
                chrome.wait(
                    "location.pathname === '/' && document.querySelector('#status').textContent === 'Library ready.'"
                )
                chrome.command(
                    "Page.navigate",
                    {"url": f"{control_origin}/artifacts/{target['artifact']['id']}"},
                )
                chrome.wait("document.querySelector('#human-title').textContent === 'decision.md'")
                self.assertEqual(
                    chrome.evaluate(
                        "document.querySelector('#human-document-body h1').textContent"
                    ),
                    "Decision",
                )
                self.assertEqual(
                    chrome.evaluate(
                        "document.querySelector('#human-document-body li').textContent"
                    ),
                    "browser target",
                )
                self.assertFalse(chrome.evaluate("!document.querySelector('#human-site').hidden"))
                chrome.command("Page.navigate", {"url": f"{control_origin}/inspect/{artifact_id}"})
                chrome.wait("document.querySelector('#title').textContent === 'browser-note.html'")
                chrome.wait(
                    "document.querySelector('#bridge-status').textContent.includes('did not run')"
                )
                chrome.wait("document.querySelector('#graph').textContent.includes('supports')")
                chrome.evaluate("document.querySelector('#graph button').click()")
                chrome.wait("document.querySelector('#title').textContent === 'decision.md'")
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
                    "document.querySelector('#bridge-status').textContent.includes('did not run')"
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
                for forbidden in (
                    "Traceback",
                    "content_base64",
                    "source_context",
                    "Bearer",
                    "tenant_id",
                ):
                    self.assertNotIn(forbidden, error_text)
                self.assertTrue(
                    chrome.evaluate(
                        "[...document.querySelectorAll('button[type=submit]')].every((button) => !button.disabled)"
                    )
                )
                chrome.command(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 320, "height": 900, "deviceScaleFactor": 1, "mobile": False},
                )
                self.assertTrue(
                    chrome.evaluate(
                        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
                    )
                )
                self.assertTrue(
                    chrome.evaluate(
                        "document.querySelector('#preview').getBoundingClientRect().width <= document.documentElement.clientWidth"
                    )
                )
                chrome.command("Emulation.clearDeviceMetricsOverride")
            finally:
                if chrome is not None:
                    chrome.close()
                if renderer is not None:
                    stop_server(renderer)
                logs = stop_server(control)
                if bridge_exercised:
                    self.assertIn('"request_id":"bridgeAllow"', logs)
                    self.assertIn('"request_id":"bridgeDeny"', logs)
                    self.assertNotIn('"request_id":"spoofRequest"', logs)

    @staticmethod
    def _hostile_source(
        harness_origin: str, render_origin: str, js_id: str, js_version: str
    ) -> str:
        return f"""<!doctype html><body><script>
(async () => {{
  const result = {{script_ran: true, csp_directives: []}};
  addEventListener('securitypolicyviolation', (event) => result.csp_directives.push(event.effectiveDirective || event.violatedDirective));
  try {{ parent.document.body; result.host_dom_blocked = false; }} catch {{ result.host_dom_blocked = true; }}
  try {{ parent.document.cookie; result.host_cookie_blocked = false; }} catch {{ result.host_cookie_blocked = true; }}
  try {{ localStorage.setItem('secret', 'x'); sessionStorage.setItem('secret', 'x'); indexedDB.open('secret'); result.storage_blocked = false; }} catch {{ result.storage_blocked = true; }}
  try {{ document.cookie = 'secret=x'; result.cookie_blocked = !document.cookie.includes('secret=x'); }} catch {{ result.cookie_blocked = true; }}
  try {{ const popup = open('{harness_origin}/popup', '_blank'); result.popup_blocked = popup === null; popup?.close(); }} catch {{ result.popup_blocked = true; }}
  try {{ const download = document.createElement('a'); download.href = '{harness_origin}/download'; download.download = 'escaped.txt'; document.body.append(download); download.click(); result.download_blocked = true; }} catch {{ result.download_blocked = true; }}
  try {{ const form = document.createElement('form'); form.action = '{harness_origin}/form'; form.method = 'POST'; document.body.append(form); form.submit(); result.form_blocked = true; }} catch {{ result.form_blocked = true; }}
  result.frame_blocked = await new Promise((resolve) => {{ try {{ const frame = document.createElement('iframe'); frame.src = '{harness_origin}/frame'; frame.onload = () => resolve(false); frame.onerror = () => resolve(true); document.body.append(frame); setTimeout(() => resolve(true), 350); }} catch {{ resolve(true); }} }});
  result.worker_blocked = await new Promise((resolve) => {{ try {{ const worker = new Worker('{harness_origin}/worker.js'); worker.onmessage = () => resolve(false); worker.onerror = () => resolve(true); setTimeout(() => {{ worker.terminate(); resolve(true); }}, 350); }} catch {{ resolve(true); }} }});
  try {{ await fetch('{harness_origin}/leak'); result.fetch_blocked = false; }} catch {{ result.fetch_blocked = true; }}
  try {{ await fetch('{render_origin}/content/{js_id}/{js_version}'); result.same_origin_fetch_blocked = false; }} catch {{ result.same_origin_fetch_blocked = true; }}
  result.xhr_blocked = await new Promise((resolve) => {{ try {{ const xhr = new XMLHttpRequest(); xhr.onload = () => resolve(false); xhr.onerror = () => resolve(true); xhr.open('GET', '{harness_origin}/leak'); xhr.send(); setTimeout(() => resolve(xhr.readyState !== 4), 300); }} catch {{ resolve(true); }} }});
  result.websocket_blocked = await new Promise((resolve) => {{ try {{ const socket = new WebSocket('ws://127.0.0.1:{harness_origin.rsplit(":", 1)[1]}/leak'); socket.onopen = () => resolve(false); socket.onerror = () => resolve(true); setTimeout(() => resolve(socket.readyState !== WebSocket.OPEN), 300); }} catch {{ resolve(true); }} }});
  result.event_source_blocked = await new Promise((resolve) => {{ try {{ const source = new EventSource('{harness_origin}/eventsource'); source.onopen = () => resolve(false); source.onerror = () => {{ source.close(); resolve(true); }}; setTimeout(() => {{ source.close(); resolve(true); }}, 350); }} catch {{ resolve(true); }} }});
  try {{ result.beacon_blocked = !navigator.sendBeacon('{harness_origin}/leak', 'secret'); }} catch {{ result.beacon_blocked = true; }}
  result.image_blocked = await new Promise((resolve) => {{ try {{ const image = new Image(); image.onload = () => resolve(false); image.onerror = () => resolve(true); image.src = '{harness_origin}/image'; setTimeout(() => resolve(true), 350); }} catch {{ resolve(true); }} }});
  try {{ top.location = '{harness_origin}/escaped'; }} catch {{}}
  result.top_navigation_script_continued = true;
  await new Promise((resolve) => setTimeout(resolve, 350));
  result.top_navigation_blocked = true;
  result.csp_directives = [...new Set(result.csp_directives)].sort();
  result.csp_violation_count = result.csp_directives.length;
  parent.postMessage({{type: 'folio-sandbox-result', result}}, '*');
}})();
</script></body>"""

    @staticmethod
    def _handler(
        hostile_url: str, embedded_urls: dict[str, str] | None = None
    ) -> type[BaseHTTPRequestHandler]:
        page = f"""<!doctype html><body><pre id="result">waiting</pre><script>
addEventListener('message', (event) => {{ const frame = document.getElementById('artifact'); if (event.source !== frame.contentWindow || event.data?.type !== 'folio-sandbox-result') return; document.getElementById('result').textContent = JSON.stringify(event.data.result); }});
</script><iframe id="artifact" sandbox="allow-scripts" src="{hostile_url}"></iframe></body>""".encode()
        embedded_page = None
        if embedded_urls is not None:
            embedded_page = f"""<!doctype html><body><pre id="embedded-result">waiting</pre>
<iframe id="javascript" sandbox="allow-scripts" src="{embedded_urls["javascript"]}"></iframe>
<iframe id="stylesheet" sandbox="allow-scripts" src="{embedded_urls["stylesheet"]}"></iframe>
<iframe id="linked" sandbox="allow-scripts" src="{embedded_urls["linked"]}"></iframe>
<script>
const result = document.getElementById('embedded-result');
const values = new Set();
const report = (value) => {{ values.add(value); result.textContent = [...values].join(' '); }};
document.getElementById('stylesheet').addEventListener('load', () => report('stylesheet-loaded'));
addEventListener('message', (event) => {{
  if (event.source === document.getElementById('javascript').contentWindow && event.data?.type === 'folio-render-result') report(event.data.value);
  if (event.source === document.getElementById('linked').contentWindow && event.data?.type === 'folio-linked-assets') report(event.data.value);
}});
</script></body>""".encode()

        class Handler(BaseHTTPRequestHandler):
            leaks: list[str] = []

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/harness":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Set-Cookie", "host_secret=host-value; Path=/")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return
                if path == "/embedded" and embedded_page is not None:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(embedded_page)))
                    self.end_headers()
                    self.wfile.write(embedded_page)
                    return
                if path in {
                    "/download",
                    "/escaped",
                    "/eventsource",
                    "/frame",
                    "/image",
                    "/leak",
                    "/popup",
                    "/worker.js",
                }:
                    self.leaks.append(self.path)
                self.send_response(404)
                self.end_headers()

            def do_POST(self) -> None:
                if self.path.split("?", 1)[0] == "/form":
                    self.leaks.append(self.path)
                self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        return Handler


if __name__ == "__main__":
    unittest.main()
