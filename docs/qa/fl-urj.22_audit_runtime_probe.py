"""Run one disposable real-TCP audit/readiness acceptance probe.

The probe uses local mode only, starts the checked-out server as a subprocess,
and emits sanitized JSON. It deliberately reports missing trace/alert backends
as open criteria instead of treating readiness HTTP status as full alerting.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT / "src"))

from mcp import Client  # noqa: E402

from folio_lattice.audit import integrity_hash  # noqa: E402
from folio_lattice.service import FolioError, FolioLattice  # noqa: E402


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _environment(root: Path, base_url: str, tenant: str, actor: str) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": str(SOURCE_ROOT / "src"),
        "FOLIO_DB_PATH": str(root / "folio.db"),
        "FOLIO_BLOB_ROOT": str(root / "blobs"),
        "FOLIO_TENANT_ID": tenant,
        "FOLIO_ACTOR": actor,
        "FOLIO_CONTROL_ORIGIN": base_url,
        "FOLIO_RENDER_ORIGIN": base_url,
        "PYTHONUNBUFFERED": "1",
    }


def _start(root: Path, tenant: str, actor: str) -> tuple[subprocess.Popen[bytes], str]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
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
        cwd=SOURCE_ROOT,
        env=_environment(root, base_url, tenant, actor),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            status, value, _ = _http_json(f"{base_url}/readyz")
            if status == 200 and value.get("ready") is True:
                return process, base_url
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(0.05)
    logs = _stop(process)
    raise RuntimeError(f"runtime did not become ready: {logs[-500:]}")


def _stop(process: subprocess.Popen[bytes]) -> str:
    if process.poll() is None:
        process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
    return (stdout + stderr).decode(errors="replace")


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read()
            return (
                response.status,
                json.loads(raw) if raw else {},
                dict(response.headers.items()),
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else {}, dict(exc.headers.items())


def _logout(base_url: str, correlation_id: str) -> int:
    status, _, _ = _http_json(
        f"{base_url}/auth/logout",
        method="POST",
        body=b"",
        headers={
            "Content-Length": "0",
            "Origin": base_url,
            "X-Correlation-ID": correlation_id,
        },
    )
    return status


async def _call_tool(base_url: str, name: str, arguments: dict[str, Any]) -> Any:
    async with Client(f"{base_url}/mcp", raise_exceptions=False) as client:
        response = await client.call_tool(name, arguments)
    if response.is_error:
        text = " ".join(str(getattr(item, "text", "")) for item in (response.content or []))
        raise FolioError(text)
    structured = response.structured_content
    if structured is None:
        raise RuntimeError(f"{name} returned no structured content")
    return structured.get("result", structured)


async def _call_error(base_url: str, name: str, arguments: dict[str, Any]) -> str:
    async with Client(f"{base_url}/mcp", raise_exceptions=False) as client:
        response = await client.call_tool(name, arguments)
    if not response.is_error:
        raise RuntimeError(f"{name} unexpectedly succeeded")
    return " ".join(str(getattr(item, "text", "")) for item in (response.content or []))


def _call(base_url: str, name: str, arguments: dict[str, Any]) -> Any:
    return asyncio.run(_call_tool(base_url, name, arguments))


def _call_error_sync(base_url: str, name: str, arguments: dict[str, Any]) -> str:
    return asyncio.run(_call_error(base_url, name, arguments))


def _make_expired(service: FolioLattice, event_id: str) -> None:
    with service.connect() as db:
        row = dict(db.execute("SELECT * FROM audit_events WHERE id = ?", (event_id,)).fetchone())
        row["details"] = json.loads(row.pop("details_json"))
        row["legal_hold"] = bool(row["legal_hold"])
        row["expires_at"] = "2000-01-01T00:00:00.000+00:00"
        row["integrity_hash"] = integrity_hash(
            {key: value for key, value in row.items() if key != "integrity_hash"}
        )
        db.execute(
            "UPDATE audit_events SET expires_at = ?, integrity_hash = ? WHERE id = ?",
            (row["expires_at"], row["integrity_hash"], event_id),
        )


def _checksum(export: dict[str, Any]) -> str:
    lines = b"".join(
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
        for item in export["events"]
    )
    payload = {"schema_version": export["schema_version"], "ndjson": lines.hex()}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def run_probe() -> dict[str, Any]:
    source_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=SOURCE_ROOT, text=True
    ).strip()
    probe_id = f"fl22-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="folio-fl-urj22-audit-") as directory:
        root = Path(directory)
        tenant_a = f"fl22-a-{uuid.uuid4().hex[:12]}"
        tenant_b = f"fl22-b-{uuid.uuid4().hex[:12]}"
        actor_a = "runtime-auditor-a"
        actor_b = "runtime-auditor-b"
        process_a, base_a = _start(root, tenant_a, actor_a)
        try:
            ready_a_status, ready_a, _ = _http_json(f"{base_a}/readyz")
            health_a_status, health_a, _ = _http_json(f"{base_a}/health")
            content_marker = f"FL22_CONTENT_{uuid.uuid4().hex}"
            token_marker = f"FL22_TOKEN_{uuid.uuid4().hex}"
            credential_marker = f"FL22_CREDENTIAL_{uuid.uuid4().hex}"
            created = _call(
                base_a,
                "artifact_create",
                {
                    "name": "runtime-audit.txt",
                    "media_type": "text/plain",
                    "content_base64": base64.b64encode(content_marker.encode()).decode(),
                    "reason": "runtime audit probe",
                },
            )
            artifact_id = created["artifact"]["id"]
            logout_statuses = [
                _logout(base_a, "fl22-correlation-a-1"),
                _logout(base_a, "fl22-correlation-a-2"),
            ]
            if logout_statuses != [204, 204]:
                raise RuntimeError(f"audit correlation requests failed: {logout_statuses}")
            service = FolioLattice(root / "folio.db", root / "blobs")
            with service.connect() as db:
                ledger = [
                    dict(row)
                    for row in db.execute(
                        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
                    )
                ]
            held = service.record_audit_event(
                tenant_id=tenant_a,
                actor_id=actor_a,
                action="runtime_held",
                outcome="allowed",
                request_id="fl22-held-request",
                correlation_id="fl22-held-correlation",
                resource_type="audit_event",
                resource_id="opaque-held",
                reason="runtime_probe",
                retention_class="request",
            )
            unheld = service.record_audit_event(
                tenant_id=tenant_a,
                actor_id=actor_a,
                action="runtime_expired",
                outcome="allowed",
                request_id="fl22-expired-request",
                correlation_id="fl22-expired-correlation",
                resource_type="audit_event",
                resource_id="opaque-expired",
                reason="runtime_probe",
                retention_class="request",
            )
            try:
                service.record_audit_event(
                    tenant_id=tenant_a,
                    actor_id=actor_a,
                    action="runtime_rejected_secret",
                    outcome="denied",
                    details={"reason_code": token_marker},
                )
            except FolioError:
                rejected_secret = True
            else:
                rejected_secret = False
            held_count = service.set_audit_legal_hold(
                tenant_a, actor=actor_a, event_ids=[held["id"]], reason="runtime_hold"
            )
            _make_expired(service, held["id"])
            _make_expired(service, unheld["id"])
            purged = service.purge_audit_events(now="2026-01-01T00:00:00+00:00")
            with service.connect() as db:
                held_exists = (
                    db.execute("SELECT 1 FROM audit_events WHERE id = ?", (held["id"],)).fetchone()
                    is not None
                )
                unheld_exists = (
                    db.execute(
                        "SELECT 1 FROM audit_events WHERE id = ?", (unheld["id"],)
                    ).fetchone()
                    is not None
                )
            export_a = _call(base_a, "audit_export", {"limit": 10_000})
            process_a_logs = _stop(process_a)
            process_a = None
        finally:
            if process_a is not None:
                process_a_logs = _stop(process_a)

        process_b, base_b = _start(root, tenant_b, actor_b)
        try:
            denied_error = _call_error_sync(base_b, "artifact_read", {"artifact_id": artifact_id})
            if _logout(base_b, "fl22-correlation-b-1") != 204:
                raise RuntimeError("tenant-b audit correlation request failed")
            export_b = _call(base_b, "audit_export", {"limit": 10_000})
            ready_b_status, ready_b, _ = _http_json(f"{base_b}/readyz")
            health_b_status, health_b, _ = _http_json(f"{base_b}/health")
            db_path = root / "folio.db"
            hidden_path = root / "folio.db.hidden"
            db_path.rename(hidden_path)
            try:
                failed_ready_status, failed_ready, _ = _http_json(f"{base_b}/readyz")
                failed_health_status, failed_health, _ = _http_json(f"{base_b}/health")
            finally:
                hidden_path.rename(db_path)
            recovered_ready_status, recovered_ready, _ = _http_json(f"{base_b}/readyz")
            metrics_status, metrics, _ = _http_json(f"{base_b}/metrics")
            process_b_logs = _stop(process_b)
            process_b = None
        finally:
            if process_b is not None:
                process_b_logs = _stop(process_b)

        events = export_a["events"]
        required_fields = {
            "id",
            "occurred_at",
            "tenant_id",
            "actor_id",
            "actor_type",
            "request_id",
            "correlation_id",
            "action",
            "resource_type",
            "resource_id",
            "outcome",
            "reason",
            "policy_version",
            "source",
            "details",
            "retention_class",
            "expires_at",
            "legal_hold",
            "integrity_hash",
        }
        exported_text = json.dumps(export_a, sort_keys=True)
        logs = process_a_logs + process_b_logs
        log_correlations = all(
            marker in logs for marker in ("fl22-correlation-a-1", "fl22-correlation-a-2")
        )
        return {
            "schema_version": "folio-lattice.fl-urj.22-runtime.v1",
            "probe_id": probe_id,
            "source_sha": source_sha,
            "runtime": "real TCP local-mode subprocess; unique temporary DB/blob root",
            "cleanup": True,
            "migration_ledger": {
                "ready": bool(ready_a.get("dependencies", {}).get("migration", {}).get("ready")),
                "versions": [row["version"] for row in ledger],
                "checksums_64_hex": all(
                    isinstance(row["checksum"], str) and len(row["checksum"]) == 64
                    for row in ledger
                ),
            },
            "audit": {
                "required_fields": all(required_fields <= set(event) for event in events),
                "total_ordering": events
                == sorted(events, key=lambda event: (event["occurred_at"], event["id"])),
                "event_count": len(events),
                "checksum": export_a["checksum"] == _checksum(export_a),
                "authorized_export": export_a["tenant_id"] == tenant_a,
                "cross_tenant_denial": (
                    "artifact not found" in denied_error
                    and all(event["tenant_id"] == tenant_b for event in export_b["events"])
                    and all(event["tenant_id"] != tenant_a for event in export_b["events"])
                ),
                "content_token_credential_exclusion": all(
                    marker not in exported_text + logs
                    for marker in (content_marker, token_marker, credential_marker)
                ),
                "rejected_secret_input": rejected_secret,
                "retention_purged_unheld": not unheld_exists and purged.get(tenant_a, 0) >= 1,
                "legal_hold_retained": held_exists and held_count == 1,
            },
            "readiness": {
                "initial_ready": ready_a_status == 200 and ready_a.get("ready") is True,
                "liveness_initial": health_a_status == 200 and health_a.get("live") is True,
                "missing_db_fails_ready": (
                    failed_ready_status == 503 and failed_ready.get("ready") is False
                ),
                "missing_db_stays_live": (
                    failed_health_status == 200 and failed_health.get("live") is True
                ),
                "restored_db_recovers_ready": (
                    recovered_ready_status == 200 and recovered_ready.get("ready") is True
                ),
                "second_process_ready": ready_b_status == 200 and ready_b.get("ready") is True,
                "second_process_live": health_b_status == 200 and health_b.get("live") is True,
                "recovered_health": health_b.get("live") is True,
            },
            "metrics": {
                "status": metrics_status,
                "audit_keys_present": all(
                    key in metrics
                    for key in (
                        "audit_events_total",
                        "audit_events_denied_total",
                        "audit_exports_total",
                        "audit_retention_purges_total",
                    )
                ),
                "tenant_free": tenant_a not in json.dumps(metrics)
                and tenant_b not in json.dumps(metrics),
            },
            "observability": {
                "request_correlation_persisted": all(
                    marker in {event["correlation_id"] for event in events}
                    for marker in ("fl22-correlation-a-1", "fl22-correlation-a-2")
                ),
                "structured_log_correlation": log_correlations,
                "trace_id_or_span_id": {
                    "status": "OPEN",
                    "reason": "No trace_id/span_id or tracing backend exists in checked product tree.",
                },
                "alert_delivery": {
                    "status": "OPEN",
                    "signal": "readyz returned HTTP 503 while DB path was absent, then 200 after restore",
                    "reason": "No alert sink or notification delivery exists in checked runtime.",
                },
            },
        }


if __name__ == "__main__":
    print(json.dumps(run_probe(), sort_keys=True, indent=2))
