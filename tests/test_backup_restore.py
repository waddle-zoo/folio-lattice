from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from folio_lattice.backup import (
    BackupError,
    InMemoryBackupKeyProvider,
    create_backup,
    migration_check,
    restore_backup,
    verify_backup,
)
from folio_lattice.service import FolioLattice


class BackupRestoreTests(unittest.TestCase):
    def key_provider(self) -> InMemoryBackupKeyProvider:
        return InMemoryBackupKeyProvider(
            manifest_key=b"m" * 32,
            backup_keys={"backup:v1": b"b" * 32},
            recovery_keys={"recovery:v1": b"r" * 32},
        )

    def create_fixture_backup(self, db_path: Path, blob_root: Path, backup_path: Path) -> dict:
        return create_backup(
            db_path,
            blob_root,
            backup_path,
            key_provider=self.key_provider(),
            backup_key_ref="backup:v1",
            recovery_key_ref="recovery:v1",
        )

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
            manifest = self.create_fixture_backup(db_path, blob_root, backup_path)
            backup_seconds = time.monotonic() - started
            self.assertLess(backup_seconds, 15)
            self.assertEqual(manifest["backup_schema_version"], "folio-backup-v2")
            self.assertEqual(manifest["blobs"]["count"], 3)
            verified = verify_backup(
                backup_path,
                key_provider=self.key_provider(),
                expected_tenant_scope={"tenant-a", "tenant-b"},
            )
            self.assertTrue(verified["verified"])
            self.assertIn("audit_events", verified["database"]["table_counts"])
            self.assertEqual(verified["consistency_set"]["acl"], "included in metadata.sqlite")
            with self.assertRaisesRegex(BackupError, "recovery-key identity"):
                verify_backup(
                    backup_path,
                    key_provider=self.key_provider(),
                    expected_key_id="recovery:production",
                )
            with self.assertRaisesRegex(BackupError, "tenant scope"):
                restore_backup(
                    backup_path,
                    root / "wrong-tenant" / "folio.db",
                    root / "wrong-tenant" / "blobs",
                    key_provider=self.key_provider(),
                    expected_tenant_scope={"tenant-a"},
                )

            recovered_db = root / "recovered" / "folio.db"
            recovered_blobs = root / "recovered" / "blobs"
            started = time.monotonic()
            result = restore_backup(
                backup_path,
                recovered_db,
                recovered_blobs,
                key_provider=self.key_provider(),
                expected_tenant_scope={"tenant-a", "tenant-b"},
            )
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
            service.create_artifact(
                tenant_id="tenant-a", name="one.txt", data=b"one", actor="owner-a"
            )
            backup_path = root / "backup"
            self.create_fixture_backup(root / "folio.db", root / "blobs", backup_path)
            target_db = root / "restored.db"
            target_db.write_text("must not overwrite", encoding="utf-8")
            with self.assertRaisesRegex(BackupError, "targets must not"):
                restore_backup(
                    backup_path,
                    target_db,
                    root / "restored-blobs",
                    key_provider=self.key_provider(),
                )
            payload = backup_path / "payload.bin"
            payload_bytes = bytearray(payload.read_bytes())
            payload_bytes[20] ^= 0x01
            payload.write_bytes(payload_bytes)
            with self.assertRaises(BackupError):
                verify_backup(backup_path, key_provider=self.key_provider())

    def test_restore_readiness_failure_rolls_back_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            service.create_artifact(
                tenant_id="tenant-a", name="one.txt", data=b"one", actor="owner-a"
            )
            backup_path = root / "backup"
            self.create_fixture_backup(root / "folio.db", root / "blobs", backup_path)
            target_db = root / "recovered" / "folio.db"
            target_blobs = root / "recovered" / "blobs"
            with patch.object(FolioLattice, "readiness", return_value={"ready": False}):
                with self.assertRaisesRegex(BackupError, "readiness"):
                    restore_backup(
                        backup_path,
                        target_db,
                        target_blobs,
                        key_provider=self.key_provider(),
                    )
            self.assertFalse(target_db.exists())
            self.assertFalse(target_blobs.exists())

    def test_restore_second_placement_failure_rolls_back_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            service.create_artifact(
                tenant_id="tenant-a", name="one.txt", data=b"one", actor="owner-a"
            )
            backup_path = root / "backup"
            self.create_fixture_backup(root / "folio.db", root / "blobs", backup_path)
            target_db = root / "recovered" / "folio.db"
            target_blobs = root / "recovered" / "blobs"
            original_replace = os.replace
            calls = 0

            def fail_blob_placement(
                source: str | bytes | os.PathLike[str], destination: str | bytes | os.PathLike[str]
            ) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected blob placement failure")
                original_replace(source, destination)

            with patch("folio_lattice.backup.os.replace", side_effect=fail_blob_placement):
                with self.assertRaisesRegex(BackupError, "restore failed"):
                    restore_backup(
                        backup_path,
                        target_db,
                        target_blobs,
                        key_provider=self.key_provider(),
                    )
            self.assertEqual(calls, 2)
            self.assertFalse(target_db.exists())
            self.assertFalse(target_blobs.exists())

    def test_backup_failure_removes_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            created = service.create_artifact(
                tenant_id="tenant-a", name="one.txt", data=b"one", actor="owner-a"
            )
            source_blob = (
                root
                / "blobs"
                / created["version"]["blob_hash"][:2]
                / created["version"]["blob_hash"]
            )
            source_blob.unlink()
            backup_path = root / "backup"
            with self.assertRaisesRegex(BackupError, "missing or unsafe"):
                self.create_fixture_backup(root / "folio.db", root / "blobs", backup_path)
            self.assertFalse((backup_path / "metadata.sqlite").exists())
            self.assertFalse((backup_path / "manifest.json").exists())
            self.assertFalse((backup_path / "blobs").exists())

    def test_authenticated_backup_rejects_tamper_wrong_scope_replay_and_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = FolioLattice(root / "folio.db", root / "blobs")
            service.create_artifact(
                tenant_id="tenant-a", name="one.txt", data=b"one", actor="owner-a"
            )
            backup_path = root / "backup"
            self.create_fixture_backup(root / "folio.db", root / "blobs", backup_path)
            manifest_text = (backup_path / "manifest.json").read_text(encoding="utf-8")
            self.assertNotIn((b"m" * 32).hex(), manifest_text)
            self.assertNotIn((b"b" * 32).hex(), manifest_text)
            self.assertNotIn((b"r" * 32).hex(), manifest_text)

            def copied(name: str) -> Path:
                target = root / name
                shutil.copytree(backup_path, target)
                return target

            tampered_key = copied("tampered-key")
            changed = json.loads((tampered_key / "manifest.json").read_text())
            changed["keys"]["recovery_key_ref"] = "recovery:v2"
            (tampered_key / "manifest.json").write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(BackupError, "authentication"):
                verify_backup(tampered_key, key_provider=self.key_provider())

            wrong_key_provider = InMemoryBackupKeyProvider(
                manifest_key=b"m" * 32,
                backup_keys={"backup:v1": b"b" * 32},
                recovery_keys={"recovery:v1": b"x" * 32},
            )
            with self.assertRaisesRegex(BackupError, "could not decrypt"):
                verify_backup(backup_path, key_provider=wrong_key_provider)
            with self.assertRaisesRegex(BackupError, "tenant scope"):
                verify_backup(
                    backup_path,
                    key_provider=self.key_provider(),
                    expected_tenant_scope={"tenant-b"},
                )
            with self.assertRaisesRegex(BackupError, "identity"):
                verify_backup(
                    backup_path,
                    key_provider=self.key_provider(),
                    expected_backup_id="backup_00000000000000000000000000000000",
                )

            traversal = copied("traversal")
            traversal_manifest = json.loads((traversal / "manifest.json").read_text())
            traversal_manifest["payload"]["path"] = "../payload.bin"
            (traversal / "manifest.json").write_text(
                json.dumps(traversal_manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(BackupError, "authentication"):
                verify_backup(traversal, key_provider=self.key_provider())

            extra = copied("extra")
            (extra / "extra-file").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(BackupError, "top-level"):
                verify_backup(extra, key_provider=self.key_provider())

            truncated = copied("truncated")
            truncated_payload = truncated / "payload.bin"
            truncated_payload.write_bytes(truncated_payload.read_bytes()[:-1])
            with self.assertRaisesRegex(BackupError, "authentication|truncated"):
                verify_backup(truncated, key_provider=self.key_provider())

            legacy = copied("legacy")
            legacy_manifest = json.loads((legacy / "manifest.json").read_text())
            legacy_manifest["backup_schema_version"] = "folio-backup-v1"
            (legacy / "manifest.json").write_text(json.dumps(legacy_manifest), encoding="utf-8")
            with self.assertRaisesRegex(BackupError, "legacy"):
                verify_backup(legacy, key_provider=self.key_provider())

            tag = copied("tag")
            tag_payload = tag / "payload.bin"
            tag_bytes = bytearray(tag_payload.read_bytes())
            tag_bytes[-1] ^= 0x01
            tag_payload.write_bytes(tag_bytes)
            with self.assertRaisesRegex(BackupError, "authentication"):
                verify_backup(tag, key_provider=self.key_provider())


if __name__ == "__main__":
    unittest.main()
