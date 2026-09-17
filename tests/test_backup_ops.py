from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from folio_lattice.backup_ops import (
    BackupOperationsConfig,
    BackupOperationsError,
    BackupOperationsMonitor,
    BackupStoreStatus,
    KeyCustodyStatus,
    StoredBackup,
    UnavailableBackupStore,
    UnavailableKeyCustody,
)


class ExternalKeyCustody:
    def __init__(self, status: KeyCustodyStatus | None = None) -> None:
        self.status = status or KeyCustodyStatus(
            provider="external-kms",
            ready=True,
            hosted=True,
            active_versions={"backup": "v2", "recovery": "v2"},
            accepted_versions={"backup": ("v1", "v2"), "recovery": ("v1", "v2")},
            rotation_id="rotation-2",
        )

    def readiness(self) -> KeyCustodyStatus:
        return self.status


class ExternalWormStore:
    def __init__(self, record: StoredBackup | None) -> None:
        self.record = record
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

    def latest(self) -> StoredBackup | None:
        return self.record

    def inspect(self, backup_id: str) -> StoredBackup:
        if self.record is None or self.record.backup_id != backup_id:
            raise BackupOperationsError("backup object is missing")
        return self.record


class BackupOperationsTests(unittest.TestCase):
    checked_at = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)

    def config(self) -> BackupOperationsConfig:
        return BackupOperationsConfig(
            schedule_interval_seconds=900,
            rpo_seconds=3600,
            max_backup_age_seconds=1800,
            key_adapter_id="external-kms",
            store_adapter_id="external-worm",
        )

    def record(self, *, age_seconds: int = 30, backup_version: str = "v2") -> StoredBackup:
        return StoredBackup(
            backup_id="backup_0123456789abcdef0123456789abcdef",
            created_at=self.checked_at - timedelta(seconds=age_seconds),
            size_bytes=128,
            sha256="a" * 64,
            backup_key_version=backup_version,
            recovery_key_version="v2",
            retention_until=self.checked_at + timedelta(days=30),
        )

    def test_ready_status_has_rpo_age_and_alertable_metrics(self) -> None:
        monitor = BackupOperationsMonitor(
            self.config(), ExternalKeyCustody(), ExternalWormStore(self.record())
        )
        status = monitor.status(self.checked_at)
        self.assertTrue(status["ready"])
        self.assertEqual(status["alerts"], [])
        self.assertEqual(status["metrics"]["backup_age_seconds"], 30)
        self.assertEqual(status["metrics"]["backup_rpo_seconds"], 3600)
        self.assertEqual(status["metrics"]["backup_ready"], 1)

    def test_unavailable_custody_and_store_fail_closed(self) -> None:
        monitor = BackupOperationsMonitor(
            self.config(), UnavailableKeyCustody(), UnavailableBackupStore()
        )
        status = monitor.status(self.checked_at)
        self.assertFalse(status["ready"])
        self.assertIn("key_custody_not_ready", status["alerts"])
        self.assertIn("backup_store_not_ready", status["alerts"])
        self.assertEqual(status["metrics"]["backup_key_custody_ready"], 0)
        self.assertEqual(status["metrics"]["backup_store_ready"], 0)

    def test_missing_and_stale_backups_fail_rpo_readiness(self) -> None:
        missing = BackupOperationsMonitor(
            self.config(), ExternalKeyCustody(), ExternalWormStore(None)
        ).status(self.checked_at)
        self.assertFalse(missing["ready"])
        self.assertIn("backup_missing", missing["alerts"])

        stale = BackupOperationsMonitor(
            self.config(), ExternalKeyCustody(), ExternalWormStore(self.record(age_seconds=4000))
        ).status(self.checked_at)
        self.assertFalse(stale["ready"])
        self.assertIn("backup_stale", stale["alerts"])
        self.assertIn("backup_rpo_exceeded", stale["alerts"])
        self.assertEqual(stale["metrics"]["backup_rpo_exceeded"], 1)

    def test_rotation_version_mismatch_fails_closed(self) -> None:
        status = BackupOperationsMonitor(
            self.config(), ExternalKeyCustody(), ExternalWormStore(self.record(backup_version="v0"))
        ).status(self.checked_at)
        self.assertFalse(status["ready"])
        self.assertIn("backup_key_version_mismatch", status["alerts"])

    def test_store_without_worm_protection_is_not_acceptable(self) -> None:
        store = ExternalWormStore(self.record())
        store.status = BackupStoreStatus(
            provider="external-worm",
            ready=True,
            hosted=True,
            immutable=True,
            overwrite_protected=False,
            delete_protected=False,
        )
        status = BackupOperationsMonitor(self.config(), ExternalKeyCustody(), store).status(
            self.checked_at
        )
        self.assertFalse(status["ready"])
        self.assertIn("backup_store_overwrite_allowed", status["alerts"])
        self.assertIn("backup_store_delete_allowed", status["alerts"])

    def test_store_missing_or_tampered_metadata_fails_integrity_checks(self) -> None:
        missing = self.record()
        missing = StoredBackup(
            **{**missing.__dict__, "exists": False},
        )
        store = ExternalWormStore(missing)
        status = BackupOperationsMonitor(self.config(), ExternalKeyCustody(), store).status(
            self.checked_at
        )
        self.assertFalse(status["ready"])
        self.assertIn("backup_integrity_failed", status["alerts"])

        tampered = self.record()
        tampered = StoredBackup(
            **{**tampered.__dict__, "integrity_verified": False},
        )
        status = BackupOperationsMonitor(
            self.config(), ExternalKeyCustody(), ExternalWormStore(tampered)
        ).status(self.checked_at)
        self.assertFalse(status["ready"])
        self.assertIn("backup_integrity_failed", status["alerts"])

    def test_status_does_not_emit_adapter_details_or_secret_like_provider_fields(self) -> None:
        key = ExternalKeyCustody(
            KeyCustodyStatus(
                provider="external-kms",
                ready=True,
                hosted=True,
                active_versions={"backup": "v2", "recovery": "v2"},
                accepted_versions={"backup": ("v2",), "recovery": ("v2",)},
                detail="secret-token=do-not-emit",
            )
        )
        store = ExternalWormStore(self.record())
        store.status = BackupStoreStatus(
            provider="external-worm",
            ready=True,
            hosted=True,
            immutable=True,
            overwrite_protected=True,
            delete_protected=True,
            detail="authorization=do-not-emit",
        )
        status = BackupOperationsMonitor(self.config(), key, store).status(self.checked_at)
        evidence = repr(status)
        self.assertNotIn("secret-token", evidence)
        self.assertNotIn("authorization=", evidence)

    def test_malformed_adapter_readiness_is_fail_closed_without_raw_error(self) -> None:
        class MalformedCustody(ExternalKeyCustody):
            def readiness(self) -> KeyCustodyStatus:
                return KeyCustodyStatus(
                    provider="external-kms",
                    ready=True,
                    hosted=True,
                    active_versions=[],  # type: ignore[arg-type]
                    accepted_versions={"backup": ("v2",), "recovery": ("v2",)},
                )

        status = BackupOperationsMonitor(
            self.config(), MalformedCustody(), ExternalWormStore(self.record())
        ).status(self.checked_at)
        self.assertFalse(status["ready"])
        self.assertIn("key_custody_unavailable", status["alerts"])

    def test_config_requires_external_adapters_and_explicit_schedule(self) -> None:
        values = {
            "FOLIO_BACKUP_INTERVAL_SECONDS": "900",
            "FOLIO_BACKUP_RPO_SECONDS": "3600",
            "FOLIO_BACKUP_MAX_AGE_SECONDS": "1800",
            "FOLIO_BACKUP_KEY_ADAPTER": "in-memory",
            "FOLIO_BACKUP_STORE_ADAPTER": "external-worm",
        }
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(BackupOperationsError, "external hosted"):
                BackupOperationsConfig.from_env()

        values["FOLIO_BACKUP_KEY_ADAPTER"] = "external-kms"
        values["FOLIO_BACKUP_RPO_SECONDS"] = ""
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesRegex(BackupOperationsError, "RPO"):
                BackupOperationsConfig.from_env()


if __name__ == "__main__":
    unittest.main()
