import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from folio_lattice.inspection import InspectionApp
from folio_lattice.renderer import RendererApp
from folio_lattice.server import _origin_env
from folio_lattice.service import FolioLattice


async def call(
    app: Any,
    method: str,
    path: str,
    *,
    body: bytes = b"",
    content_type: str | None = None,
    query: str = "",
) -> tuple[int, dict[str, str], bytes]:
    request = {"type": "http.request", "body": body, "more_body": False}
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return request

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [] if content_type is None else [(b"content-type", content_type.encode())]
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


class WebAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = FolioLattice(root / "folio.db", root / "blobs")
        self.html = self.service.create_artifact(
            tenant_id="web",
            name="page.html",
            data=b"<h1>Page</h1>",
            media_type="text/html",
        )
        self.target = self.service.create_artifact(
            tenant_id="web", name="target.txt", data=b"target"
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
            tenant_id="web",
            name="blob.bin",
            data=b"\x00",
            media_type="application/octet-stream",
        )
        self.service.link(
            "web", self.html["artifact"]["id"], self.target["artifact"]["id"], "references"
        )
        self.inspection = InspectionApp(
            self.service,
            tenant_id="web",
            actor="web-user",
            render_origin="http://127.0.0.1:8001",
            max_request_bytes=1000,
        )
        self.reader = FolioLattice(root / "folio.db", root / "blobs", read_only=True)
        self.renderer = RendererApp(
            self.reader, tenant_id="web", control_origin="http://127.0.0.1:8000"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_inspection_read_and_edit(self) -> None:
        artifact_id = self.html["artifact"]["id"]
        version_id = self.html["version"]["id"]
        for path, expected in (
            ("/", b"Folio Lattice inspection"),
            (f"/inspect/{artifact_id}", b'sandbox="allow-scripts"'),
            ("/ui.css", b"color-scheme"),
            ("/ui.js", b"loadArtifact"),
        ):
            status, _, body = await call(self.inspection, "GET", path)
            self.assertEqual(status, 200)
            self.assertIn(expected, body)

        status, _, body = await call(self.inspection, "GET", f"/api/artifacts/{artifact_id}")
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertEqual(state["read"]["text"], "<h1>Page</h1>")
        self.assertEqual(state["graph"][0]["target_artifact_id"], self.target["artifact"]["id"])

        payload = json.dumps(
            {
                "text": "<h1>Edited</h1>",
                "parent_version_id": version_id,
                "media_type": "text/html",
                "reason": "unit edit",
            }
        ).encode()
        status, _, body = await call(
            self.inspection,
            "POST",
            f"/api/artifacts/{artifact_id}/versions",
            body=payload,
            content_type="application/json; charset=utf-8",
        )
        self.assertEqual(status, 201)
        written = json.loads(body)
        self.assertEqual(written["parent_version_id"], version_id)
        self.assertEqual(written["actor"], "web-user")
        self.assertEqual(len(self.service.versions("web", artifact_id)), 2)

        status, _, _ = await call(
            self.inspection,
            "POST",
            f"/api/artifacts/{artifact_id}/versions",
            body=payload,
            content_type="application/json",
        )
        self.assertEqual(status, 409)

    async def test_inspection_rejects_bad_requests(self) -> None:
        artifact_id = self.html["artifact"]["id"]
        endpoint = f"/api/artifacts/{artifact_id}/versions"
        cases = [
            (b"{}", None, 400),
            (b"not-json", "application/json", 400),
            (b"[]", "application/json", 400),
            (b"x" * 1001, "application/json", 413),
        ]
        for body, content_type, expected in cases:
            status, _, _ = await call(
                self.inspection,
                "POST",
                endpoint,
                body=body,
                content_type=content_type,
            )
            self.assertEqual(status, expected)

        status, _, _ = await call(self.inspection, "GET", "/api/artifacts/art_missing")
        self.assertEqual(status, 404)
        status, _, _ = await call(self.inspection, "GET", "/missing")
        self.assertEqual(status, 404)

        binary_id = self.binary["artifact"]["id"]
        binary_payload = json.dumps(
            {
                "text": "replacement",
                "parent_version_id": self.binary["version"]["id"],
                "media_type": "text/plain",
                "reason": "must fail",
            }
        ).encode()
        status, _, _ = await call(
            self.inspection,
            "POST",
            f"/api/artifacts/{binary_id}/versions",
            body=binary_payload,
            content_type="application/json",
        )
        self.assertEqual(status, 400)

    async def test_renderer_serves_only_safe_web_types(self) -> None:
        html_id = self.html["artifact"]["id"]
        status, headers, body = await call(self.renderer, "GET", f"/render/{html_id}")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<h1>Page</h1>")
        self.assertIn("sandbox allow-scripts", headers["content-security-policy"])
        self.assertIn("connect-src 'none'", headers["content-security-policy"])

        for created, marker in ((self.css, b"<link"), (self.javascript, b"<script")):
            artifact_id = created["artifact"]["id"]
            version_id = created["version"]["id"]
            status, _, wrapper = await call(self.renderer, "GET", f"/render/{artifact_id}")
            self.assertEqual(status, 200)
            self.assertIn(marker, wrapper)
            status, _, content = await call(
                self.renderer, "GET", f"/content/{artifact_id}/{version_id}"
            )
            self.assertEqual(status, 200)
            self.assertTrue(content)

        status, _, _ = await call(self.renderer, "GET", f"/render/{self.binary['artifact']['id']}")
        self.assertEqual(status, 415)
        status, _, _ = await call(
            self.renderer,
            "GET",
            f"/content/{html_id}/{self.html['version']['id']}",
        )
        self.assertEqual(status, 400)

    async def test_renderer_health_and_closed_routes(self) -> None:
        with self.assertRaisesRegex(ValueError, "read-only"):
            RendererApp(self.service, tenant_id="web", control_origin="http://127.0.0.1:8000")
        status, _, body = await call(self.renderer, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ready"])
        for method, path, expected in (
            ("POST", "/render/x", 405),
            ("GET", "/render/", 404),
            ("GET", "/content/incomplete", 404),
            ("GET", "/missing", 404),
        ):
            status, _, _ = await call(self.renderer, method, path)
            self.assertEqual(status, expected)

    def test_origins_are_canonical_and_cannot_inject_policy(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
