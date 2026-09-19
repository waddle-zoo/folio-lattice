from __future__ import annotations

import hashlib
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mcp import Client

from folio_lattice.audit import (
    AUDIT_SCHEMA_VERSION,
    integrity_hash,
    legacy_span_id,
    legacy_trace_id,
)
from folio_lattice.auth import Principal, reset_request_principal, set_request_principal
from folio_lattice.mcp_protocol import build_mcp_server
from folio_lattice.ops import main as ops_main
from folio_lattice.server import FolioHttpApp
from folio_lattice.service import SCHEMA_MIGRATIONS, FolioError, FolioLattice


async def unused_app(scope: Any, receive: Any, send: Any) -> None:
    del scope, receive, send


async def call_http(
    app: FolioHttpApp,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
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
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [
                (key.lower().encode("ascii"), value.encode("ascii"))
                for key, value in (headers or {}).items()
            ],
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
        self.root = Path(self.temp.name)
        self.service = FolioLattice(self.root / "folio.db", self.root / "blobs")

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
            rows = db.execute(
                "SELECT * FROM audit_events WHERE tenant_id IN ('tenant-a', 'tenant-b')"
            ).fetchall()
            for row in rows:
                event = self.service._audit_row(row)
                event["expires_at"] = "2000-01-01T00:00:00.000+00:00"
                event["integrity_hash"] = integrity_hash(
                    {key: value for key, value in event.items() if key != "integrity_hash"}
                )
                db.execute(
                    "UPDATE audit_events SET expires_at = ?, integrity_hash = ? WHERE id = ?",
                    (event["expires_at"], event["integrity_hash"], event["id"]),
                )
        self.assertEqual(
            self.service.set_audit_legal_hold(
                "tenant-a", actor="auditor", event_ids=[first["id"]], reason="investigation"
            ),
            1,
        )
        purged = self.service.purge_audit_events(now="2026-01-01T00:00:00+00:00")
        self.assertEqual(purged.get("tenant-a"), 2)
        self.assertEqual(purged.get("tenant-b"), 1)
        remaining_ids = {
            item["id"] for item in self.service.list_audit_events("tenant-a", actor="auditor")
        }
        self.assertIn(first["id"], remaining_ids)

        with self.service.connect() as db:
            tenant_b_remaining = db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE tenant_id = 'tenant-b'"
            ).fetchone()[0]
        self.assertEqual(tenant_b_remaining, 1)

    def test_fresh_audit_schema_persists_trace_fields_and_exports_them(self) -> None:
        event = self.service.record_audit_event(
            tenant_id="tenant-a", actor_id="auditor", action="fresh", outcome="allowed"
        )
        with self.service.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(audit_events)")}
        self.assertIn("trace_id", columns)
        self.assertIn("span_id", columns)
        self.assertRegex(event["trace_id"], r"^[0-9a-f]{32}$")
        self.assertRegex(event["span_id"], r"^[0-9a-f]{16}$")
        export = self.service.export_audit_events("tenant-a", actor="auditor")
        self.assertEqual(export["schema_version"], AUDIT_SCHEMA_VERSION)
        exported = next(item for item in export["events"] if item["id"] == event["id"])
        self.assertEqual(exported["trace_id"], event["trace_id"])
        self.assertEqual(exported["span_id"], event["span_id"])

    def test_trace_schema_migration_backfills_and_rehashes_legacy_rows(self) -> None:
        legacy = self.service.record_audit_event(
            tenant_id="tenant-a",
            actor_id="auditor",
            action="legacy",
            outcome="allowed",
            details={"status_code": 200},
        )
        with self.service.connect() as db:
            row = dict(
                db.execute("SELECT * FROM audit_events WHERE id = ?", (legacy["id"],)).fetchone()
            )
            row["details"] = json.loads(row.pop("details_json"))
            row.pop("trace_id")
            row.pop("span_id")
            row["legal_hold"] = bool(row["legal_hold"])
            row["integrity_hash"] = integrity_hash(row)
            db.execute("ALTER TABLE audit_events DROP COLUMN trace_id")
            db.execute("ALTER TABLE audit_events DROP COLUMN span_id")
            db.execute(
                "UPDATE audit_events SET integrity_hash = ? WHERE id = ?",
                (row["integrity_hash"], legacy["id"]),
            )
            db.execute("DELETE FROM schema_migrations WHERE version = 7")

        self.service.initialize()
        migrated = next(
            item
            for item in self.service.list_audit_events("tenant-a", actor="auditor")
            if item["id"] == legacy["id"]
        )
        self.assertEqual(migrated["trace_id"], legacy_trace_id(legacy["id"]))
        self.assertEqual(migrated["span_id"], legacy_span_id(legacy["id"]))
        self.assertNotIn("trace_id", migrated["details"])
        self.assertNotIn("span_id", migrated["details"])
        self.assertEqual(
            migrated["integrity_hash"],
            integrity_hash(
                {key: value for key, value in migrated.items() if key != "integrity_hash"}
            ),
        )
        export = self.service.export_audit_events("tenant-a", actor="auditor")
        exported = next(item for item in export["events"] if item["id"] == legacy["id"])
        self.assertEqual(exported["trace_id"], migrated["trace_id"])
        self.assertEqual(exported["span_id"], migrated["span_id"])
        with self.service.connect() as db:
            ledger = db.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
        self.assertEqual(ledger[-1]["version"], 7)
        self.assertEqual(ledger[-1]["name"], "audit_trace_context")
        before_repeat = (migrated["trace_id"], migrated["span_id"], migrated["integrity_hash"])
        self.service.initialize()
        repeated = next(
            item
            for item in self.service.list_audit_events("tenant-a", actor="auditor")
            if item["id"] == legacy["id"]
        )
        self.assertEqual(
            (repeated["trace_id"], repeated["span_id"], repeated["integrity_hash"]),
            before_repeat,
        )
        self.assertEqual(
            self.service._expected_migration_rows()[-1][2],
            FolioLattice._migration_checksum(
                7, "audit_trace_context", "explicit trace/span IDs and legacy audit hash backfill"
            ),
        )

    def test_trace_migration_rolls_back_when_interrupted(self) -> None:
        legacy = self.service.record_audit_event(
            tenant_id="tenant-a", actor_id="auditor", action="legacy", outcome="allowed"
        )
        with self.service.connect() as db:
            db.execute("ALTER TABLE audit_events DROP COLUMN trace_id")
            db.execute("ALTER TABLE audit_events DROP COLUMN span_id")
            db.execute("DELETE FROM schema_migrations WHERE version = 7")

        with patch.object(
            FolioLattice,
            "_migrate_audit_trace_context",
            side_effect=RuntimeError("simulated interrupted migration"),
        ):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.service.initialize()

        with self.service.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(audit_events)")}
            self.assertNotIn("trace_id", columns)
            self.assertNotIn("span_id", columns)
            self.assertIsNone(
                db.execute("SELECT 1 FROM schema_migrations WHERE version = 7").fetchone()
            )

        self.service.initialize()
        migrated = next(
            item
            for item in self.service.list_audit_events("tenant-a", actor="auditor")
            if item["id"] == legacy["id"]
        )
        self.assertEqual(migrated["trace_id"], legacy_trace_id(legacy["id"]))
        self.assertEqual(migrated["span_id"], legacy_span_id(legacy["id"]))
        self.assertEqual(
            migrated["integrity_hash"],
            integrity_hash(
                {key: value for key, value in migrated.items() if key != "integrity_hash"}
            ),
        )

    def test_integrity_windows_pagination_size_and_atomicity(self) -> None:
        first = self.service.record_audit_event(
            tenant_id="tenant-a",
            actor_id="auditor",
            action="fixture",
            outcome="allowed",
            request_id="req-window",
            details={"status_code": 200},
        )
        self.service.record_audit_event(
            tenant_id="tenant-a", actor_id="auditor", action="second", outcome="allowed"
        )
        with self.assertRaisesRegex(FolioError, "timezone"):
            self.service.list_audit_events(
                "tenant-a", actor="auditor", from_time="2026-01-01T00:00:00"
            )
        with self.assertRaisesRegex(FolioError, "inverted"):
            self.service.list_audit_events(
                "tenant-a",
                actor="auditor",
                from_time="2026-01-02T00:00:00+00:00",
                to_time="2026-01-01T00:00:00+00:00",
            )

        page = self.service.export_audit_events("tenant-a", actor="auditor", limit=1)
        self.assertEqual(page["event_count"], 1)
        self.assertIsNotNone(page["next_cursor"])
        continuation = self.service.export_audit_events(
            "tenant-a", actor="auditor", limit=1, cursor=page["next_cursor"]
        )
        self.assertGreaterEqual(continuation["event_count"], 1)

        long_value = "x" * 255
        for index in range(1_400):
            self.service.record_audit_event(
                tenant_id="tenant-a",
                actor_id="auditor",
                action=f"bulk_{index}",
                outcome="allowed",
                reason=long_value,
                resource_type="artifact",
                resource_id=long_value,
                details={"reason_code": long_value},
            )
        bounded = self.service.export_audit_events("tenant-a", actor="auditor", limit=50_000)
        ndjson = b"".join(
            json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
            + b"\n"
            for item in bounded["events"]
        )
        self.assertLessEqual(len(ndjson), 1 * 1024 * 1024)
        self.assertIsNotNone(bounded["next_cursor"])
        self.assertLessEqual(
            self.service.export_audit_events("tenant-a", actor="auditor", limit=50_000)[
                "event_count"
            ],
            10_000,
        )

        with self.service.connect() as db:
            db.execute(
                "UPDATE audit_events SET details_json = '{\"status_code\":500}' WHERE id = ?",
                (first["id"],),
            )
        with self.assertRaisesRegex(FolioError, "integrity"):
            self.service.list_audit_events("tenant-a", actor="auditor")
        with self.assertRaisesRegex(FolioError, "integrity"):
            self.service.export_audit_events("tenant-a", actor="auditor")

        clean = FolioLattice(self.root / "clean.db", self.root / "clean-blobs")
        clean.record_audit_event(
            tenant_id="tenant-a", actor_id="auditor", action="atomic", outcome="allowed"
        )

        original_insert = FolioLattice._insert_audit_event

        def fail_export(db: Any, event: dict[str, Any]) -> None:
            if event["action"] == "audit_export":
                raise FolioError("injected audit write failure")
            original_insert(db, event)

        with patch.object(FolioLattice, "_insert_audit_event", side_effect=fail_export):
            with self.assertRaisesRegex(FolioError, "injected"):
                clean.export_audit_events("tenant-a", actor="auditor")
        with clean.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_exports").fetchone()[0], 0)

    def test_retention_hook_command_and_existing_schema_readiness(self) -> None:
        event = self.service.record_audit_event(
            tenant_id="tenant-a", actor_id="auditor", action="expired", outcome="allowed"
        )
        with self.service.connect() as db:
            expired = dict(
                db.execute("SELECT * FROM audit_events WHERE id = ?", (event["id"],)).fetchone()
            )
            expired["details"] = json.loads(expired.pop("details_json"))
            expired["legal_hold"] = bool(expired["legal_hold"])
            expired["expires_at"] = "2000-01-01T00:00:00.000+00:00"
            expired["integrity_hash"] = integrity_hash(
                {key: value for key, value in expired.items() if key != "integrity_hash"}
            )
            db.execute(
                "UPDATE audit_events SET expires_at = ?, integrity_hash = ? WHERE id = ?",
                (expired["expires_at"], expired["integrity_hash"], event["id"]),
            )
            db.execute("DROP TABLE audit_exports")
            db.execute("DROP TABLE schema_migrations")
        self.service.initialize()
        self.assertTrue(self.service.readiness()["dependencies"]["audit"]["ready"])
        with self.service.connect() as db:
            ledger = db.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
        self.assertEqual(
            [(row["version"], row["name"]) for row in ledger],
            [(version, name) for version, name, _ in SCHEMA_MIGRATIONS],
        )
        self.assertEqual(
            ops_main(
                [
                    "audit",
                    "purge",
                    "--before",
                    "2026-01-01T00:00:00+00:00",
                    "--db",
                    str(self.root / "folio.db"),
                    "--blobs",
                    str(self.root / "blobs"),
                ]
            ),
            0,
        )
        retained = self.service.list_audit_events("tenant-a", actor="auditor")
        self.assertNotIn(event["id"], {item["id"] for item in retained})
        self.assertTrue(any(item["action"] == "audit_retention_purge" for item in retained))

    def test_schema_migration_ledger_is_numbered_and_fail_closed(self) -> None:
        with self.service.connect() as db:
            ledger = db.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
        self.assertEqual(
            [(row["version"], row["name"]) for row in ledger],
            [(version, name) for version, name, _ in SCHEMA_MIGRATIONS],
        )
        self.assertTrue(all(len(row["checksum"]) == 64 for row in ledger))
        self.assertTrue(self.service.readiness()["dependencies"]["migration"]["ready"])

        with self.service.connect() as db:
            db.execute(
                "UPDATE schema_migrations SET checksum = ? WHERE version = 1",
                ("0" * 64,),
            )
        self.assertFalse(self.service.readiness()["dependencies"]["migration"]["ready"])
        with self.assertRaisesRegex(FolioError, "schema migration ledger checksum mismatch"):
            self.service.initialize()

    def test_legal_hold_and_purge_events_are_atomic(self) -> None:
        held = self.service.record_audit_event(
            tenant_id="tenant-a", actor_id="auditor", action="held", outcome="allowed"
        )
        original_insert = FolioLattice._insert_audit_event

        def fail_hold(db: Any, event: dict[str, Any]) -> None:
            if event["action"] == "audit_legal_hold":
                raise FolioError("injected legal-hold write failure")
            original_insert(db, event)

        with patch.object(FolioLattice, "_insert_audit_event", side_effect=fail_hold):
            with self.assertRaisesRegex(FolioError, "legal-hold"):
                self.service.set_audit_legal_hold(
                    "tenant-a", actor="auditor", event_ids=[held["id"]], reason="investigation"
                )
        with self.service.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT legal_hold FROM audit_events WHERE id = ?", (held["id"],)
                ).fetchone()[0],
                0,
            )

        expired = self.service.record_audit_event(
            tenant_id="tenant-a", actor_id="auditor", action="expired", outcome="allowed"
        )
        with self.service.connect() as db:
            row = dict(
                db.execute("SELECT * FROM audit_events WHERE id = ?", (expired["id"],)).fetchone()
            )
            row["details"] = json.loads(row.pop("details_json"))
            row["legal_hold"] = bool(row["legal_hold"])
            row["expires_at"] = "2000-01-01T00:00:00.000+00:00"
            row["integrity_hash"] = integrity_hash(
                {key: value for key, value in row.items() if key != "integrity_hash"}
            )
            db.execute(
                "UPDATE audit_events SET expires_at = ?, integrity_hash = ? WHERE id = ?",
                (row["expires_at"], row["integrity_hash"], expired["id"]),
            )

        def fail_purge(db: Any, event: dict[str, Any]) -> None:
            if event["action"] == "audit_retention_purge":
                raise FolioError("injected purge write failure")
            original_insert(db, event)

        with patch.object(FolioLattice, "_insert_audit_event", side_effect=fail_purge):
            with self.assertRaisesRegex(FolioError, "purge"):
                self.service.purge_audit_events(now="2026-01-01T00:00:00+00:00")
        with self.service.connect() as db:
            self.assertIsNotNone(
                db.execute("SELECT 1 FROM audit_events WHERE id = ?", (expired["id"],)).fetchone()
            )

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

    async def test_http_audit_correlates_trace_span_and_readiness_transitions(self) -> None:
        log_stream = io.StringIO()
        audit_logger = logging.Logger("audit-observability-test")
        audit_logger.addHandler(logging.StreamHandler(log_stream))
        app = FolioHttpApp(
            unused_app,
            self.service,
            unused_app,
            deployment_mode="local",
            control_origin="http://testserver",
            audit_logger=audit_logger,
            local_tenant_id="acme",
            local_actor_id="auditor",
        )

        with patch.object(
            self.service,
            "readiness",
            side_effect=[
                {"status": "ok", "ready": True, "dependencies": {"database": {"ready": True}}},
                {
                    "status": "not_ready",
                    "ready": False,
                    "dependencies": {"database": {"ready": False}},
                },
                {"status": "ok", "ready": True, "dependencies": {"database": {"ready": True}}},
            ],
        ):
            self.assertEqual((await call_http(app, "/readyz"))[0], 200)
            self.assertEqual((await call_http(app, "/readyz"))[0], 503)
            self.assertEqual((await call_http(app, "/readyz"))[0], 200)

        status, _, _ = await call_http(
            app,
            "/auth/logout",
            method="POST",
            headers={"Origin": "http://testserver", "X-Correlation-ID": "corr-http"},
        )
        self.assertEqual(status, 204)
        status, _, body = await call_http(app, "/metrics")
        self.assertEqual(status, 200)
        metrics = json.loads(body)
        self.assertEqual(metrics["readiness_failures_total"], 1)
        self.assertEqual(metrics["readiness_recoveries_total"], 1)

        event = next(
            event
            for event in self.service.list_audit_events("acme", actor="auditor")
            if event["action"] == "logout_succeeded"
        )
        self.assertRegex(event["trace_id"], r"^[0-9a-f]{32}$")
        self.assertRegex(event["span_id"], r"^[0-9a-f]{16}$")
        logs = log_stream.getvalue()
        self.assertIn('"event":"readiness_transition"', logs)
        self.assertIn('"transition":"failed"', logs)
        self.assertIn('"transition":"recovered"', logs)
        self.assertIn(event["trace_id"], logs)
        self.assertIn(event["span_id"], logs)


if __name__ == "__main__":
    unittest.main()
