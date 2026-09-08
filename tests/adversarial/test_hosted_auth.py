"""Seven adversarial checks against the real hosted-auth test target."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TARGET = Path(__file__).resolve().parents[1] / "hosted_auth_target.py"
AUDIENCE = "https://folio-lattice.test/mcp"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=data, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, response.read(), dict(response.headers.items())
    except HTTPError as error:
        return error.code, error.read(), dict(error.headers.items())


class HostedTarget:
    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="folio-hosted-auth-test-")
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(TARGET),
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--state-dir",
                self.directory.name,
                "--audience",
                AUDIENCE,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            self.wait_ready()
        except Exception:
            self.stop()
            raise

    def wait_ready(self) -> None:
        for _ in range(100):
            if self.process.poll() is not None:
                details = self.process.stderr.read().decode(errors="replace")
                raise AssertionError(f"target exited before readiness: {details[-500:]}")
            try:
                status, body, _ = _json_request(f"{self.base_url}/health")
                metadata_status, metadata_body, _ = _json_request(
                    f"{self.base_url}/issuer/.well-known/openid-configuration"
                )
                jwks_status, jwks_body, _ = _json_request(f"{self.base_url}/issuer/jwks.json")
                if (
                    status == metadata_status == jwks_status == 200
                    and json.loads(body)["ready"]
                    and json.loads(metadata_body)["issuer"] == f"{self.base_url}/issuer"
                    and json.loads(jwks_body)["keys"]
                ):
                    return
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.05)
        raise AssertionError("hosted auth target did not become ready")

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.directory.cleanup()

    def token(self, profile: str, variant: str = "") -> str:
        query = urlencode({"profile": profile, **({"variant": variant} if variant else {})})
        status, body, _ = _json_request(f"{self.base_url}/issuer/token?{query}")
        assert status == 200, body
        return json.loads(body)["access_token"]

    def mcp_probe(
        self,
        token: str | None,
        *,
        endpoint: str = "/tenant-a/mcp",
        cookie: str | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if cookie is not None:
            headers["Cookie"] = cookie
        return _json_request(
            f"{self.base_url}{endpoint}",
            method="POST",
            payload={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "hosted-auth-adversarial", "version": "1"},
                },
            },
            headers=headers,
        )

    def tool_probe(
        self, token: str, tool: str = "artifact_read"
    ) -> tuple[int, bytes, dict[str, str]]:
        return _json_request(
            f"{self.base_url}/tenant-a/mcp",
            method="POST",
            payload={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": {"artifact_id": "not-visible"}},
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": "2025-06-18",
                "Authorization": f"Bearer {token}",
            },
        )


class HostedAuthAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = HostedTarget()

    def tearDown(self) -> None:
        self.target.stop()

    def assert_denied(self, result: tuple[int, bytes, dict[str, str]], token: str) -> None:
        status, body, _ = result
        self.assertIn(status, (401, 403), body)
        self.assertNotIn(token.encode(), body)
        self.assertNotIn(b"tenant-a", body)
        self.assertNotIn(b"tenant-b", body)
        self.assertNotIn(b"not-visible", body)

    def test_forged_signature_is_rejected(self) -> None:
        token = self.target.token("A_OWNER")
        header, payload, signature = token.split(".")
        forged = ".".join((header, payload, ("A" if signature[-1] != "A" else "B") + signature[1:]))
        self.assert_denied(self.target.mcp_probe(forged), forged)

    def test_expired_token_is_rejected(self) -> None:
        token = self.target.token("A_OWNER", "expired")
        self.assert_denied(self.target.mcp_probe(token), token)

    def test_wrong_issuer_and_audience_are_rejected(self) -> None:
        for variant in ("wrong-issuer", "wrong-audience"):
            with self.subTest(variant=variant):
                token = self.target.token("A_OWNER", variant)
                self.assert_denied(self.target.mcp_probe(token), token)

    def test_missing_scope_is_rejected_before_tool_dispatch(self) -> None:
        token = self.target.token("A_MEMBER", "missing-scope")
        self.assert_denied(self.target.tool_probe(token), token)

    def test_tenant_principal_cannot_cross_endpoint(self) -> None:
        token = self.target.token("B")
        self.assert_denied(self.target.mcp_probe(token), token)

    def test_revoked_existing_bearer_is_rejected(self) -> None:
        token = self.target.token("A_MEMBER")
        status, _, _ = self.target.mcp_probe(token)
        self.assertEqual(status, 200)
        status, body, _ = _json_request(
            f"{self.target.base_url}/__test/revoke",
            method="POST",
            payload={"subject": "a-member"},
        )
        self.assertEqual(status, 200, body)
        self.assert_denied(self.target.mcp_probe(token), token)

    def test_cookie_or_origin_cannot_create_mcp_session(self) -> None:
        self.assert_denied(
            self.target.mcp_probe(None, cookie="session=a-member"),
            "session=a-member",
        )


if __name__ == "__main__":
    unittest.main()
