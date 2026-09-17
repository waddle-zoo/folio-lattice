from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp import Client

from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
from folio_lattice.mcp_protocol import build_mcp_server
from folio_lattice.server import FolioHttpApp
from folio_lattice.service import FolioError, FolioLattice


async def unused_app(scope: Any, receive: Any, send: Any) -> None:
    del scope, receive, send


async def call_http(app: FolioHttpApp, path: str) -> tuple[int, dict[str, str], bytes]:
    messages = [
        {"type": "http.request", "body": b"", "more_body": False},
    ]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "http_version": "1.1",
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return (
        int(start["status"]),
        {key.decode(): value.decode() for key, value in start.get("headers", [])},
        body,
    )


class AuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = FolioLattice(root / "folio.db", root / "blobs")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_minimized_ordered_export_checksum_and_retention_hold(self) -> None:
        first = self.service.record_audit_event(
            tenant_id="tenant-a",
            actor_id="alice",
            action="acl_denied",
            outcome="denied",
            request_id="req-a",
            correlation_id="corr-a",
            resource_type="artifact",
            resource_id="art_opaque",
            reason="read_denied",
            details={"status_code": 403, "reason_code": "private"},
            retention_class="request",
        )
        self.service.record_audit_event(
            tenant_id="tenant-a",
            actor_id="alice",
            action="mcp_call",
            outcome="allowed",
            request_id="req-b",
            correlation_id="corr-a",
            resource_type="tool",
            resource_id="artifact_search",
            reason="completed",
            details={"status_code": 200, "transport": "stdio"},
        )
        self.service.record_audit_event(
            tenant_id="tenant-b",
            actor_id="bob",
            action="mcp_call",
            outcome="allowed",
            reason="completed",
        )
        with self.assertRaisesRegex(FolioError, "restricted field"):
            self.service.record_audit_event(
                tenant_id="tenant-a",
                actor_id="alice",
                action="bad",
                outcome="denied",
                details={"token": "bearer-secret"},
            )
        with self.assertRaisesRegex(FolioError, "audit detail is out of bounds"):
            self.service.record_audit_event(
                tenant_id="tenant-a",
                actor_id="alice",
                action="bad",
                outcome="denied",
                details={"resource_id": "https://private.example/raw"},
            )

        export = self.service.export_audit_events("tenant-a", actor="auditor", limit=10)
        self.assertEqual(export["event_count"], 2)
        self.assertEqual(
            export["events"],
            sorted(export["events"], key=lambda event: (event["occurred_at"], event["id"])),
        )
        self.assertNotIn("bearer-secret", json.dumps(export))
        lines = b"".join(
            json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
            + b"\n"
            for item in export["events"]
        )
        expected_checksum = hashlib.sha256(
            json.dumps(
                {"schema_version": export["schema_version"], "ndjson": lines.hex()},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self.assertEqual(export["checksum"], expected_checksum)
        stored_first = next(
            item
            for item in self.service.list_audit_events("tenant-a", actor="a")
            if item["id"] == first["id"]
        )
        self.assertEqual(first["integrity_hash"], stored_first["integrity_hash"])
        self.assertTrue(self.service.readiness()["dependencies"]["audit"]["ready"])

        with self.service.connect() as db:
            db.execute(
                "UPDATE audit_events SET expires_at = '2000-01-01T00:00:00+00:00' "
                "WHERE tenant_id = 'tenant-a'",
            )
        self.assertEqual(
            self.service.set_audit_legal_hold(
                "tenant-a", actor="auditor", event_ids=[first["id"]], reason="investigation"
            ),
            1,
        )
        purged = self.service.purge_audit_events(now="2026-01-01T00:00:00+00:00")
        self.assertEqual(purged.get("tenant-a"), 2)
        remaining_ids = {
            item["id"] for item in self.service.list_audit_events("tenant-a", actor="auditor")
        }
        self.assertIn(first["id"], remaining_ids)

    async def test_mcp_export_requires_admin_scope_and_metrics_are_bounded(self) -> None:
        self.service.record_audit_event(
            tenant_id="acme",
            actor_id="reader",
            action="rate_limited",
            outcome="denied",
            reason="limit",
        )
        server = build_mcp_server(self.service)
        token = set_request_principal(
            Principal(
                "acme",
                "reader",
                "https://issuer.example",
                "reader",
                frozenset({"artifact:read"}),
            )
        )
        try:
            async with Client(server) as client:
                denied = await client.call_tool("audit_export", {})
                self.assertTrue(denied.is_error)
                self.assertIn("operation not permitted", denied.content[0].text)
        finally:
            reset_request_principal(token)

        token = set_request_principal(
            Principal(
                "acme",
                "auditor",
                "https://issuer.example",
                "auditor",
                frozenset({"tenant:admin"}),
            )
        )
        try:
            async with Client(server) as client:
                response = await client.call_tool("audit_export", {"limit": 10})
                self.assertFalse(response.is_error, response)
                structured = response.structured_content
                assert structured is not None
                export = structured.get("result", structured)
                self.assertEqual(export["tenant_id"], "acme")
                self.assertEqual(export["event_count"], 1)
        finally:
            reset_request_principal(token)

        app = FolioHttpApp(unused_app, self.service, unused_app, deployment_mode="local")
        status, headers, body = await call_http(app, "/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(headers["cache-control"], "no-store")
        metrics = json.loads(body)
        self.assertGreaterEqual(metrics["audit_events_total"], 2)
        self.assertGreaterEqual(metrics["audit_exports_total"], 1)


if __name__ == "__main__":
    unittest.main()
