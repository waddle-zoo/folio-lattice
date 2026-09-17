from __future__ import annotations

import base64
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from folio_lattice.backup import (
    BackupError,
    create_backup,
    migration_check,
    restore_backup,
    verify_backup,
)
from folio_lattice.service import FolioLattice


class BackupRestoreTests(unittest.TestCase):
    def test_consistency_set_restore_and_repeatable_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "live" / "folio.db"
            blob_root = root / "live" / "blobs"
            service = FolioLattice(db_path, blob_root)
            first = service.create_artifact(
                tenant_id="tenant-a",
                name="readme.md",
                data=b"hello from version one",
                media_type="text/markdown",
                actor="owner-a",
                source_context={"source": "fixture"},
            )
            second = service.create_artifact(
                tenant_id="tenant-a",
                name="graph.html",
                data=b"<html><body>graph</body></html>",
                media_type="text/html",
                actor="owner-a",
            )
            service.link(
                "tenant-a",
                first["artifact"]["id"],
                second["artifact"]["id"],
                "references",
                actor="owner-a",
            )
            service.share_artifact(
                "tenant-a",
                first["artifact"]["id"],
                actor="owner-a",
                subject_actor_id="reader-a",
            )
            updated = service.write_version(
                tenant_id="tenant-a",
                artifact_id=first["artifact"]["id"],
                data=b"hello from version two",
                media_type="text/markdown",
                actor="owner-a",
                reason="fixture update",
                source_context={"source": "fixture"},
                parent_version_id=first["version"]["id"],
            )
            service.record_audit_event(
                tenant_id="tenant-a",
                actor_id="owner-a",
                action="fixture_write",
                outcome="allowed",
                resource_type="artifact",
                resource_id=first["artifact"]["id"],
                details={"status_code": 200},
            )
            service.record_audit_event(
                tenant_id="tenant-b", actor_id="owner-b", action="fixture", outcome="allowed"
            )
            migration = migration_check(db_path, blob_root)
            self.assertTrue(migration["after"]["ready"])
            self.assertFalse(migration["destructive_down_migrations"])

            backup_path = root / "backup"
            started = time.monotonic()
            manifest = create_backup(db_path, blob_root, backup_path)
            backup_seconds = time.monotonic() - started
            self.assertLess(backup_seconds, 15)
            self.assertEqual(manifest["backup_schema_version"], "folio-backup-v1")
            self.assertEqual(manifest["blobs"]["count"], 3)
            verified = verify_backup(backup_path)
            self.assertTrue(verified["verified"])
            self.assertIn("audit_events", verified["database"]["table_counts"])
            self.assertEqual(verified["consistency_set"]["acl"], "included in metadata.sqlite")
            with self.assertRaisesRegex(BackupError, "encryption key identity"):
                verify_backup(backup_path, expected_key_id="kms://production")
            with self.assertRaisesRegex(BackupError, "tenant scope"):
                restore_backup(
                    backup_path,
                    root / "wrong-tenant" / "folio.db",
                    root / "wrong-tenant" / "blobs",
                    expected_tenant_scope={"tenant-a"},
                )

            recovered_db = root / "recovered" / "folio.db"
            recovered_blobs = root / "recovered" / "blobs"
            started = time.monotonic()
            result = restore_backup(backup_path, recovered_db, recovered_blobs)
            restore_seconds = time.monotonic() - started
            self.assertLess(restore_seconds, 60)
            self.assertEqual(result["status"], "restored")
            recovered = FolioLattice(recovered_db, recovered_blobs, read_only=True)
            self.assertEqual(recovered.readiness()["ready"], True)
            read = recovered.read_artifact(
                "tenant-a", first["artifact"]["id"], actor="owner-a", version_id=updated["id"]
            )
            self.assertEqual(base64.b64decode(read["content_base64"]), b"hello from version two")
            self.assertEqual(
                len(recovered.versions("tenant-a", first["artifact"]["id"], actor="owner-a")), 2
            )
            self.assertEqual(
                len(
                    recovered.graph_component("tenant-a", first["artifact"]["id"], actor="owner-a")
                ),
                2,
            )
            self.assertEqual(len(recovered.list_audit_events("tenant-a", actor="owner-a")), 1)

    def test_corruption_and_non_empty_restore_targets_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            created = service.create_artifact(
                tenant_id="tenant-a", name="one.txt", data=b"one", actor="owner-a"
            )
            backup_path = root / "backup"
            create_backup(root / "folio.db", root / "blobs", backup_path)
            target_db = root / "restored.db"
            target_db.write_text("must not overwrite", encoding="utf-8")
            with self.assertRaisesRegex(BackupError, "targets must not"):
                restore_backup(backup_path, target_db, root / "restored-blobs")
            blob = (
                backup_path
                / "blobs"
                / created["version"]["blob_hash"][:2]
                / created["version"]["blob_hash"]
            )
            blob.write_bytes(b"tampered")
            with self.assertRaises(BackupError):
                verify_backup(backup_path)

    def test_restore_readiness_failure_rolls_back_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            service.create_artifact(tenant_id="tenant-a", name="one.txt", data=b"one", actor="owner-a")
            backup_path = root / "backup"
            create_backup(root / "folio.db", root / "blobs", backup_path)
            target_db = root / "recovered" / "folio.db"
            target_blobs = root / "recovered" / "blobs"
            with patch.object(FolioLattice, "readiness", return_value={"ready": False}):
                with self.assertRaisesRegex(BackupError, "readiness"):
                    restore_backup(backup_path, target_db, target_blobs)
            self.assertFalse(target_db.exists())
            self.assertFalse(target_blobs.exists())


if __name__ == "__main__":
    unittest.main()
