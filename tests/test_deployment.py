from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from starlette.responses import JSONResponse

from folio_lattice.backup_ops import (
    BackupOperationsConfig,
    BackupOperationsMonitor,
    BackupStoreStatus,
    KeyCustodyStatus,
    StoredBackup,
)
from folio_lattice.deployment import (
    DeploymentError,
    initialize_deployment_state,
    rollback_deployment,
    transactional_upgrade,
)
from folio_lattice.renderer import RendererApp
from folio_lattice.server import FolioHttpApp, Settings, _renderer_relay_audience
from folio_lattice.service import FolioLattice
from folio_lattice.tls import TlsCertificateMonitor


async def unused_app(scope, receive, send) -> None:
    await JSONResponse({"ok": True})(scope, receive, send)


class ReadyCaller:
    async def call(self, tool, arguments):
        raise AssertionError(f"unexpected renderer call: {tool} {arguments}")

    async def ready(self) -> bool:
        return True


class ReadyAuthenticator:
    def ready(self) -> bool:
        return True


class ReadyBackupKeyCustody:
    def readiness(self) -> KeyCustodyStatus:
        return KeyCustodyStatus(
            provider="external-kms",
            ready=True,
            hosted=True,
            active_versions={"backup": "v1", "recovery": "v1"},
            accepted_versions={"backup": ("v1",), "recovery": ("v1",)},
        )

    def manifest_auth_key(self) -> bytes:
        return b"manifest-key"

    def backup_key(self, key_ref: str) -> bytes:
        return key_ref.encode()

    def recovery_key(self, key_ref: str) -> bytes:
        return key_ref.encode()

    def key_version(self, purpose: str, key_ref: str) -> str:
        del purpose, key_ref
        return "v1"

    def manifest_key_version(self) -> str:
        return "v1"


class ReadyBackupStore:
    def __init__(self) -> None:
        self.status = BackupStoreStatus(
            provider="external-worm",
            ready=True,
            hosted=True,
            immutable=True,
            overwrite_protected=True,
            delete_protected=True,
        )

    def readiness(self) -> BackupStoreStatus:
        return self.status

    def latest(self) -> StoredBackup:
        return StoredBackup(
            backup_id="backup_0123456789abcdef0123456789abcdef",
            created_at=datetime.now(UTC) - timedelta(seconds=1),
            size_bytes=1,
            sha256="a" * 64,
            backup_key_version="v1",
            recovery_key_version="v1",
            retention_until=datetime.now(UTC) + timedelta(days=1),
        )

    def inspect(self, backup_id: str) -> StoredBackup:
        record = self.latest()
        if record.backup_id != backup_id:
            raise RuntimeError("unexpected backup")
        return record


async def call(app, path: str, *, scheme: str = "http") -> tuple[int, dict[str, str], bytes]:
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": scheme,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 2),
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    body = b"".join(message.get("body", b"") for message in sent)
    return start["status"], headers, body


def write_certificate(root: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = root / "cert.pem"
    key_path = root / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class DeploymentTests(unittest.TestCase):
    def test_hosted_compose_isolated_renderer_has_no_storage_volume(self) -> None:
        compose = (Path(__file__).parents[1] / "deploy/compose/hosted.yml").read_text()
        renderer = compose.split("  renderer:\n", 1)[1].split("\nnetworks:\n", 1)[0]
        folio = compose.split("  folio:\n", 1)[1].split("\n  renderer:\n", 1)[0]
        self.assertNotIn("\n    volumes:", renderer)
        self.assertIn("\n      - renderer\n", renderer)
        self.assertNotIn("\n    ports:", renderer)
        self.assertIn("\n      - control\n      - renderer\n", folio)
        self.assertIn("  renderer:\n    internal: true", compose)

    def test_hosted_profile_shares_one_renderer_relay_audience(self) -> None:
        compose = (Path(__file__).parents[1] / "deploy/compose/hosted.yml").read_text()
        renderer = compose.split("  renderer:\n", 1)[1].split("\nnetworks:\n", 1)[0]
        folio = compose.split("  folio:\n", 1)[1].split("\n  renderer:\n", 1)[0]
        audience = "FOLIO_RENDERER_RELAY_AUDIENCE: https://folio:8000/mcp"
        self.assertEqual(compose.count(audience), 2)
        self.assertIn(audience, folio)
        self.assertIn(audience, renderer)
        self.assertNotIn("FOLIO_MCP_URL:", folio)
        self.assertIn("FOLIO_MCP_URL: https://folio:8000/mcp", renderer)
        self.assertIn(
            "FOLIO_RENDER_ORIGIN: ${FOLIO_RENDER_ORIGIN:?set the hosted renderer origin}",
            renderer,
        )

        hosted_env = {
            "FOLIO_DEPLOYMENT_MODE": "hosted",
            "FOLIO_OIDC_ISSUER": "https://issuer.example",
            "FOLIO_OIDC_AUDIENCE": "folio-api",
            "FOLIO_OIDC_JWKS_URL": "https://issuer.example/jwks.json",
            "FOLIO_RENDERER_RELAY_AUDIENCE": "https://folio:8000/mcp",
        }
        with patch.dict(os.environ, hosted_env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.renderer_relay_audience, "https://folio:8000/mcp")
        with patch.dict(
            os.environ,
            {
                key: value
                for key, value in hosted_env.items()
                if key != "FOLIO_RENDERER_RELAY_AUDIENCE"
            },
            clear=True,
        ):
            settings = Settings.from_env()
            with self.assertRaisesRegex(ValueError, "FOLIO_RENDERER_RELAY_AUDIENCE"):
                _renderer_relay_audience(settings, "https://127.0.0.1:8000/mcp")

    def test_hostile_configuration_fails_closed(self) -> None:
        cases = (
            {"FOLIO_DB_PATH": ""},
            {"FOLIO_BLOB_ROOT": "\x1f"},
            {"FOLIO_BRIDGE_TIMEOUT_SECONDS": "nan"},
            {"FOLIO_BRIDGE_TIMEOUT_SECONDS": "inf"},
            {"FOLIO_SESSION_COOKIE_SECURE": "false"},
            {"FOLIO_SESSION_COOKIE_SECURE": "sometimes"},
            {"FOLIO_CONTROL_ORIGIN": "https://example.com\n.evil"},
            {"FOLIO_CONTROL_ORIGIN": "https://user@example.com"},
            {"FOLIO_MCP_URL": "https://example.com:bad/mcp"},
        )
        for values in cases:
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(ValueError):
                    Settings.from_env()

    def test_tls_configuration_requires_complete_https_posture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert = root / "cert.pem"
            key = root / "key.pem"
            cert.write_text("certificate", encoding="utf-8")
            key.write_text("key", encoding="utf-8")
            with patch.dict(os.environ, {"FOLIO_TLS_CERTFILE": str(cert)}, clear=True):
                with self.assertRaisesRegex(ValueError, "set together"):
                    Settings.from_env()
            with patch.dict(
                os.environ,
                {"FOLIO_TLS_CERTFILE": str(cert), "FOLIO_TLS_KEYFILE": str(key)},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "HTTPS control"):
                    Settings.from_env()
            with patch.dict(
                os.environ,
                {
                    "FOLIO_TLS_CERTFILE": str(cert),
                    "FOLIO_TLS_KEYFILE": str(key),
                    "FOLIO_CONTROL_ORIGIN": "https://control.example",
                    "FOLIO_RENDER_ORIGIN": "https://render.example",
                },
                clear=True,
            ):
                settings = Settings.from_env()
            self.assertEqual(settings.tls_certfile, str(cert))
            self.assertTrue(settings.session_cookie_secure)
            with patch.dict(
                os.environ,
                {
                    "FOLIO_TLS_CERTFILE": str(cert),
                    "FOLIO_TLS_KEYFILE": str(key),
                    "FOLIO_CONTROL_ORIGIN": "https://control.example",
                    "FOLIO_RENDER_ORIGIN": "https://render.example",
                    "FOLIO_MCP_URL": "http://folio.internal/mcp",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "HTTPS FOLIO_MCP_URL"):
                    Settings.from_env()

    def test_tls_certificate_monitor_emits_expiry_and_rotation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert, _ = write_certificate(root)
            now = datetime.now(UTC)
            monitor = TlsCertificateMonitor(cert, warning_seconds=2 * 24 * 60 * 60)
            expiring = monitor.status(now)
            self.assertTrue(expiring["ready"])
            self.assertIn("tls_certificate_expiring", expiring["alerts"])
            self.assertEqual(expiring["metrics"]["tls_certificate_rotation_total"], 0)

            rotated_root = root / "rotated"
            rotated_root.mkdir()
            rotated, _ = write_certificate(rotated_root)
            shutil.copyfile(rotated, cert)
            rotated_status = monitor.status(now)
            self.assertTrue(rotated_status["ready"])
            self.assertIn("tls_certificate_rotated", rotated_status["alerts"])
            self.assertEqual(rotated_status["metrics"]["tls_certificate_rotation_total"], 1)

            expired = monitor.status(now + timedelta(days=2))
            self.assertFalse(expired["ready"])
            self.assertIn("tls_certificate_expired", expired["alerts"])

    def test_tls_certificate_dependency_fails_readiness_when_expired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert, _ = write_certificate(root)
            service = FolioLattice(root / "folio.db", root / "blobs")
            app = FolioHttpApp(
                unused_app,
                service,
                unused_app,
                deployment_mode="local",
                tls_certificate=TlsCertificateMonitor(cert),
            )
            app.tls_certificate.status = lambda now=None: {
                "ready": False,
                "alerts": ["tls_certificate_expired"],
                "metrics": {"tls_certificate_ready": 0},
            }
            status, _, body = asyncio.run(call(app, "/readyz"))
            self.assertEqual(status, 503)
            self.assertFalse(json.loads(body)["dependencies"]["tls_certificate"]["ready"])

    def test_versioned_upgrade_verifies_state_and_rolls_back_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "folio.db"
            blobs = root / "blobs"
            service = FolioLattice(database, blobs)
            created = service.create_artifact(
                tenant_id="upgrade",
                name="rollback.txt",
                data=b"upgrade-safe content",
                media_type="text/plain",
            )
            state_path = root / "deployment.json"
            initialize_deployment_state(database, blobs, "v1", state_path=state_path)

            upgraded = transactional_upgrade(
                database,
                blobs,
                current_version="v1",
                target_version="v2",
                state_path=state_path,
                migrate=lambda _db, _blobs: None,
            )
            self.assertTrue(upgraded["before"]["ready"])
            self.assertTrue(upgraded["after"]["ready"])
            self.assertEqual(json.loads(state_path.read_text())["active_version"], "v2")

            snapshot_manifest = Path(upgraded["rollback_snapshot"]) / "manifest.json"
            manifest = json.loads(snapshot_manifest.read_text())
            snapshot_manifest.write_text(
                json.dumps(
                    {**manifest, "state": {**manifest["state"], "database_sha256": "0" * 64}}
                ),
                encoding="utf-8",
            )
            with self.assertRaises(DeploymentError):
                rollback_deployment(
                    database,
                    blobs,
                    snapshot_path=upgraded["rollback_snapshot"],
                    target_version="v1",
                    state_path=state_path,
                )
            self.assertEqual(json.loads(state_path.read_text())["active_version"], "v2")
            snapshot_manifest.write_text(json.dumps(manifest), encoding="utf-8")

            FolioLattice(database, blobs).write_version(
                tenant_id="upgrade",
                artifact_id=created["artifact"]["id"],
                data=b"post-upgrade content",
                media_type="text/plain",
                actor="dev",
                reason="post-upgrade write",
                source_context={},
                parent_version_id=created["version"]["id"],
            )

            rolled_back = rollback_deployment(
                database,
                blobs,
                snapshot_path=upgraded["rollback_snapshot"],
                target_version="v1",
                state_path=state_path,
            )
            self.assertEqual(rolled_back["to_version"], "v1")
            self.assertEqual(json.loads(state_path.read_text())["active_version"], "v1")
            self.assertFalse(database.with_name(f"{database.name}-wal").exists())
            self.assertFalse(database.with_name(f"{database.name}-shm").exists())
            self.assertEqual(list(root.glob(".folio-stage-*")), [])
            self.assertEqual(list(root.glob(".folio-old-*")), [])
            self.assertEqual(
                FolioLattice(database, blobs).read_artifact("upgrade", created["artifact"]["id"])[
                    "text"
                ],
                "upgrade-safe content",
            )

            def hostile_migration(db: str | Path, blob_root: str | Path) -> None:
                Path(blob_root, "unsafe-marker").write_text("partial", encoding="utf-8")
                raise RuntimeError("migration rejected")

            with self.assertRaises(DeploymentError):
                transactional_upgrade(
                    database,
                    blobs,
                    current_version="v1",
                    target_version="v3",
                    state_path=state_path,
                    migrate=hostile_migration,
                )
            self.assertFalse((blobs / "unsafe-marker").exists())
            self.assertEqual(json.loads(state_path.read_text())["active_version"], "v1")
            self.assertTrue(FolioLattice(database, blobs, read_only=True).readiness()["ready"])

    def test_readiness_separates_liveness_and_durable_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            app = FolioHttpApp(
                unused_app,
                service,
                unused_app,
                deployment_mode="local",
                hsts_max_age=123,
            )
            status, headers, body = asyncio.run(call(app, "/health"))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["live"], True)
            self.assertNotIn("ready", json.loads(body))
            self.assertNotIn("strict-transport-security", headers)

            status, headers, body = asyncio.run(call(app, "/readyz", scheme="https"))
            self.assertEqual(status, 200)
            readiness = json.loads(body)
            self.assertTrue(readiness["ready"])
            for dependency in (
                "database",
                "blob",
                "migration",
                "acl",
                "external_mcp",
                "config",
                "provider",
                "identity",
            ):
                self.assertTrue(readiness["dependencies"][dependency]["ready"])
            self.assertNotIn("backup_operations", readiness["dependencies"])
            self.assertEqual(headers["strict-transport-security"], "max-age=123; includeSubDomains")

            renderer = RendererApp(
                ReadyCaller(), control_origin="https://control.example", hsts_max_age=123
            )
            status, headers, _ = asyncio.run(call(renderer, "/readyz", scheme="https"))
            self.assertEqual(status, 200)
            self.assertEqual(headers["strict-transport-security"], "max-age=123; includeSubDomains")

            app.config_ready = False
            status, _, body = asyncio.run(call(app, "/readyz"))
            self.assertEqual(status, 503)
            self.assertFalse(json.loads(body)["dependencies"]["config"]["ready"])
            app.config_ready = True

            service.blob_root.rmdir()
            self.assertFalse(service.readiness()["dependencies"]["blob"]["ready"])
            service.blob_root.mkdir()
            service.db_path.unlink()
            self.assertFalse(service.readiness()["dependencies"]["database"]["ready"])

    def test_hosted_backup_operations_are_required_for_readiness_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            app = FolioHttpApp(
                unused_app,
                service,
                unused_app,
                deployment_mode="hosted",
                authenticator=ReadyAuthenticator(),
            )

            status, _, body = asyncio.run(call(app, "/readyz"))
            self.assertEqual(status, 503)
            readiness = json.loads(body)
            self.assertFalse(readiness["ready"])
            self.assertEqual(
                readiness["dependencies"]["backup_operations"],
                {
                    "ready": False,
                    "required": True,
                    "reason": "hosted backup operations monitor is not configured",
                },
            )

            status, _, body = asyncio.run(call(app, "/metrics"))
            self.assertEqual(status, 200)
            metrics = json.loads(body)
            self.assertEqual(metrics["backup_ready"], 0)
            self.assertEqual(metrics["backup_age_seconds"], -1)
            self.assertEqual(metrics["backup_store_ready"], 0)
            self.assertEqual(metrics["backup_key_custody_ready"], 0)

    def test_hosted_backup_monitor_can_make_readiness_ready_only_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            config = BackupOperationsConfig(
                schedule_interval_seconds=900,
                rpo_seconds=3600,
                max_backup_age_seconds=1800,
                key_adapter_id="external-kms",
                store_adapter_id="external-worm",
            )
            monitor = BackupOperationsMonitor(config, ReadyBackupKeyCustody(), ReadyBackupStore())
            app = FolioHttpApp(
                unused_app,
                service,
                unused_app,
                deployment_mode="hosted",
                authenticator=ReadyAuthenticator(),
                backup_operations=monitor,
            )
            status, _, body = asyncio.run(call(app, "/readyz"))
            self.assertEqual(status, 200)
            readiness = json.loads(body)
            self.assertTrue(readiness["ready"])
            self.assertTrue(readiness["dependencies"]["backup_operations"]["ready"])

            unavailable = BackupOperationsMonitor(
                config, ReadyBackupKeyCustody(), ReadyBackupStore()
            )
            unavailable.store.status = BackupStoreStatus(
                provider="external-worm",
                ready=False,
                hosted=True,
                immutable=True,
                overwrite_protected=True,
                delete_protected=True,
            )
            app.backup_operations = unavailable
            status, _, body = asyncio.run(call(app, "/readyz"))
            self.assertEqual(status, 503)
            self.assertFalse(json.loads(body)["ready"])

    def test_upgrade_is_repeatable_and_rollback_copy_preserves_blob(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = FolioLattice(root / "folio.db", root / "blobs")
            created = original.create_artifact(
                tenant_id="upgrade",
                name="rollback.txt",
                data=b"upgrade-safe content",
                media_type="text/plain",
            )
            rollback_root = root / "rollback"
            rollback_root.mkdir()
            with sqlite3.connect(root / "folio.db") as source_db:
                with sqlite3.connect(rollback_root / "folio.db") as rollback_db:
                    source_db.backup(rollback_db)
            shutil.copytree(root / "blobs", rollback_root / "blobs")

            restored = FolioLattice(rollback_root / "folio.db", rollback_root / "blobs")
            read = restored.read_artifact("upgrade", created["artifact"]["id"])
            self.assertEqual(read["version"]["blob_hash"], created["version"]["blob_hash"])
            self.assertTrue(
                FolioLattice(
                    rollback_root / "folio.db", rollback_root / "blobs", read_only=True
                ).readiness()["ready"]
            )
            self.assertEqual(read["text"], "upgrade-safe content")

    def test_real_tls_process_negotiates_hostname_validated_tls12_and_hsts(self) -> None:
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = int(listener.getsockname()[1])
        except PermissionError:
            self.skipTest("environment disallows loopback sockets")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert, key = write_certificate(root)
            env = os.environ.copy()
            env.update(
                {
                    "FOLIO_DEPLOYMENT_MODE": "local",
                    "FOLIO_DB_PATH": str(root / "folio.db"),
                    "FOLIO_BLOB_ROOT": str(root / "blobs"),
                    "FOLIO_CONTROL_ORIGIN": f"https://127.0.0.1:{port}",
                    "FOLIO_RENDER_ORIGIN": f"https://127.0.0.1:{port}",
                    "FOLIO_TLS_CERTFILE": str(cert),
                    "FOLIO_TLS_KEYFILE": str(key),
                    "FOLIO_HSTS_MAX_AGE": "60",
                }
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "folio_lattice.server",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=Path(__file__).parents[1],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                context = ssl.create_default_context(cafile=str(cert))
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                response = None
                for _ in range(100):
                    if process.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen(
                            f"https://127.0.0.1:{port}/readyz", context=context, timeout=0.2
                        ) as current:
                            response = current.read()
                            self.assertEqual(current.status, 200)
                            self.assertEqual(
                                current.headers["Strict-Transport-Security"],
                                "max-age=60; includeSubDomains",
                            )
                            break
                    except OSError:
                        time.sleep(0.05)
                if response is None:
                    logs = process.communicate(timeout=5)[1].decode()
                    self.fail(f"TLS product did not become ready: {logs}")
                self.assertTrue(json.loads(response)["ready"])
                with socket.create_connection(("127.0.0.1", port), timeout=2) as raw:
                    with context.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
                        self.assertIn(tls.version(), {"TLSv1.2", "TLSv1.3"})
                wrong_context = ssl.create_default_context(cafile=str(cert))
                with socket.create_connection(("127.0.0.1", port), timeout=2) as raw:
                    with self.assertRaises(ssl.CertificateError):
                        wrong_context.wrap_socket(raw, server_hostname="wrong.example")
            finally:
                process.terminate()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
