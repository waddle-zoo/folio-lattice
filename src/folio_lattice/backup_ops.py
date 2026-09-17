"""Provider-neutral hosted backup operations contracts.

The local :mod:`folio_lattice.backup` module creates and verifies an encrypted
snapshot. This module is the deliberately smaller deployment seam around it:
durable key custody, an immutable backup store, and the scheduler/RPO signal.
It contains no cloud SDK and no local fallback. A deployment must inject
adapters that can prove their external posture; otherwise readiness is false.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .backup import BackupError, VersionedBackupKeyProvider

_ADAPTER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LOCAL_ADAPTER_MARKERS = {"file", "filesystem", "in-memory", "inmemory", "local", "memory"}


class BackupOperationsError(BackupError):
    """A hosted backup contract is unavailable or fails closed."""


@dataclass(frozen=True)
class KeyCustodyStatus:
    """Redacted health and rotation state returned by an external key adapter."""

    provider: str
    ready: bool
    hosted: bool
    active_versions: Mapping[str, str]
    accepted_versions: Mapping[str, tuple[str, ...]]
    rotation_id: str | None = None
    detail: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "provider": (
                self.provider
                if isinstance(self.provider, str) and _ADAPTER_ID.fullmatch(self.provider)
                else "invalid"
            ),
            "ready": self.ready,
            "hosted": self.hosted,
            "active_versions": {
                purpose: version
                for purpose, version in self.active_versions.items()
                if purpose in {"backup", "recovery"}
                and isinstance(version, str)
                and _VERSION.fullmatch(version)
            },
            "accepted_versions": {
                purpose: [
                    version
                    for version in versions
                    if isinstance(version, str) and _VERSION.fullmatch(version)
                ]
                for purpose, versions in self.accepted_versions.items()
                if purpose in {"backup", "recovery"}
            },
        }


class DurableBackupKeyCustody(VersionedBackupKeyProvider, Protocol):
    """External key custody adapter; key bytes never belong in this contract."""

    def readiness(self) -> KeyCustodyStatus: ...


@dataclass(frozen=True)
class BackupStoreStatus:
    """Redacted posture returned by an immutable/WORM-capable store adapter."""

    provider: str
    ready: bool
    hosted: bool
    immutable: bool
    overwrite_protected: bool
    delete_protected: bool
    detail: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "provider": (
                self.provider
                if isinstance(self.provider, str) and _ADAPTER_ID.fullmatch(self.provider)
                else "invalid"
            ),
            "ready": self.ready,
            "hosted": self.hosted,
            "immutable": self.immutable,
            "overwrite_protected": self.overwrite_protected,
            "delete_protected": self.delete_protected,
        }


@dataclass(frozen=True)
class StoredBackup:
    """Provider-observed metadata for one immutable backup object.

    ``inspect`` must perform an existence and integrity check in the provider,
    not merely return a cached upload result. ``delete`` and overwrite methods
    are intentionally absent from :class:`ImmutableBackupStore`: retention
    controls belong to the external WORM policy, not application code.
    """

    backup_id: str
    created_at: datetime
    size_bytes: int
    sha256: str
    backup_key_version: str
    recovery_key_version: str
    retention_until: datetime
    exists: bool = True
    integrity_verified: bool = True
    immutable: bool = True
    overwrite_protected: bool = True
    delete_protected: bool = True

    def public(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "backup_key_version": self.backup_key_version,
            "recovery_key_version": self.recovery_key_version,
            "retention_until": self.retention_until.astimezone(UTC).isoformat(),
            "exists": self.exists,
            "integrity_verified": self.integrity_verified,
            "immutable": self.immutable,
            "overwrite_protected": self.overwrite_protected,
            "delete_protected": self.delete_protected,
        }


class ImmutableBackupStore(Protocol):
    """External store adapter with provider-side existence/integrity checks."""

    def readiness(self) -> BackupStoreStatus: ...

    def latest(self) -> StoredBackup | None: ...

    def inspect(self, backup_id: str) -> StoredBackup: ...


@dataclass(frozen=True)
class BackupOperationsConfig:
    """Explicit hosted schedule and adapter identity configuration."""

    schedule_interval_seconds: int
    rpo_seconds: int
    max_backup_age_seconds: int
    key_adapter_id: str
    store_adapter_id: str
    expected_backup_key_version: str | None = None
    expected_recovery_key_version: str | None = None
    require_hosted: bool = True

    def validate(self) -> None:
        for name, value in (
            ("schedule_interval_seconds", self.schedule_interval_seconds),
            ("rpo_seconds", self.rpo_seconds),
            ("max_backup_age_seconds", self.max_backup_age_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise BackupOperationsError(f"{name} must be a positive integer")
        if self.max_backup_age_seconds > self.rpo_seconds:
            raise BackupOperationsError("max_backup_age_seconds must not exceed rpo_seconds")
        _adapter_id(self.key_adapter_id, "key adapter")
        _adapter_id(self.store_adapter_id, "backup store adapter")
        if self.expected_backup_key_version is not None:
            _version(self.expected_backup_key_version, "backup key version")
        if self.expected_recovery_key_version is not None:
            _version(self.expected_recovery_key_version, "recovery key version")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> BackupOperationsConfig:
        """Load required hosted settings without selecting a vendor adapter."""

        env = os.environ if environ is None else environ

        def required(name: str) -> str:
            value = env.get(name)
            if value is None or not value.strip():
                raise BackupOperationsError(f"{name} is required for hosted backup readiness")
            return value.strip()

        def positive(name: str) -> int:
            value = required(name)
            try:
                parsed = int(value)
            except ValueError as exc:
                raise BackupOperationsError(f"{name} must be a positive integer") from exc
            if parsed < 1:
                raise BackupOperationsError(f"{name} must be a positive integer")
            return parsed

        result = cls(
            schedule_interval_seconds=positive("FOLIO_BACKUP_INTERVAL_SECONDS"),
            rpo_seconds=positive("FOLIO_BACKUP_RPO_SECONDS"),
            max_backup_age_seconds=positive("FOLIO_BACKUP_MAX_AGE_SECONDS"),
            key_adapter_id=required("FOLIO_BACKUP_KEY_ADAPTER"),
            store_adapter_id=required("FOLIO_BACKUP_STORE_ADAPTER"),
            expected_backup_key_version=env.get("FOLIO_BACKUP_KEY_VERSION"),
            expected_recovery_key_version=env.get("FOLIO_RECOVERY_KEY_VERSION"),
        )
        result.validate()
        return result


class UnavailableKeyCustody:
    """Explicit blocked seam used when no external key adapter is wired."""

    def manifest_auth_key(self) -> bytes:
        raise BackupOperationsError("external key custody is unavailable")

    def backup_key(self, key_ref: str) -> bytes:
        del key_ref
        raise BackupOperationsError("external key custody is unavailable")

    def recovery_key(self, key_ref: str) -> bytes:
        del key_ref
        raise BackupOperationsError("external key custody is unavailable")

    def key_version(self, purpose: str, key_ref: str) -> str:
        del purpose, key_ref
        raise BackupOperationsError("external key custody is unavailable")

    def manifest_key_version(self) -> str:
        raise BackupOperationsError("external key custody is unavailable")

    def readiness(self) -> KeyCustodyStatus:
        return KeyCustodyStatus(
            provider="unconfigured",
            ready=False,
            hosted=False,
            active_versions={},
            accepted_versions={},
            detail="external key custody adapter is not configured",
        )


class UnavailableBackupStore:
    """Explicit blocked seam used when no immutable store adapter is wired."""

    def readiness(self) -> BackupStoreStatus:
        return BackupStoreStatus(
            provider="unconfigured",
            ready=False,
            hosted=False,
            immutable=False,
            overwrite_protected=False,
            delete_protected=False,
            detail="immutable hosted backup store adapter is not configured",
        )

    def latest(self) -> StoredBackup | None:
        raise BackupOperationsError("immutable hosted backup store is unavailable")

    def inspect(self, backup_id: str) -> StoredBackup:
        del backup_id
        raise BackupOperationsError("immutable hosted backup store is unavailable")


def _adapter_id(value: object, field: str) -> str:
    if not isinstance(value, str) or _ADAPTER_ID.fullmatch(value) is None:
        raise BackupOperationsError(f"{field} is invalid")
    if value.casefold() in _LOCAL_ADAPTER_MARKERS or any(
        marker in value.casefold() for marker in ("local", "memory", "filesystem")
    ):
        raise BackupOperationsError(f"{field} must identify external hosted custody")
    return value


def _version(value: object, field: str) -> str:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise BackupOperationsError(f"{field} is invalid")
    return value


def _utc(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise BackupOperationsError(f"{field} is invalid") from exc
    else:
        raise BackupOperationsError(f"{field} is invalid")
    if parsed.tzinfo is None:
        raise BackupOperationsError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _coerce_key_status(raw: object) -> KeyCustodyStatus:
    if isinstance(raw, KeyCustodyStatus):
        source: Mapping[str, object] = {
            "provider": raw.provider,
            "ready": raw.ready,
            "hosted": raw.hosted,
            "active_versions": raw.active_versions,
            "accepted_versions": raw.accepted_versions,
            "rotation_id": raw.rotation_id,
            "detail": raw.detail,
        }
    elif isinstance(raw, Mapping):
        source = raw
    else:
        raise BackupOperationsError("key custody readiness is unavailable")
    active = source.get("active_versions")
    accepted = source.get("accepted_versions")
    if not isinstance(active, Mapping) or not isinstance(accepted, Mapping):
        raise BackupOperationsError("key custody rotation state is unavailable")
    normalized_accepted: dict[str, tuple[str, ...]] = {}
    for purpose, versions in accepted.items():
        if not isinstance(purpose, str) or not isinstance(versions, (list, tuple, set)):
            raise BackupOperationsError("key custody rotation state is invalid")
        normalized_accepted[purpose] = tuple(
            _version(item, "accepted key version") for item in versions
        )
    rotation_id = source.get("rotation_id")
    if not isinstance(rotation_id, str):
        rotation_id = None
    detail = source.get("detail")
    if not isinstance(detail, str):
        detail = None
    return KeyCustodyStatus(
        provider=str(source.get("provider", "")),
        ready=source.get("ready") is True,
        hosted=source.get("hosted") is True,
        active_versions={
            str(purpose): _version(version, "active key version")
            for purpose, version in active.items()
        },
        accepted_versions=normalized_accepted,
        rotation_id=rotation_id,
        detail=detail,
    )


def _coerce_store_status(raw: object) -> BackupStoreStatus:
    if isinstance(raw, BackupStoreStatus):
        source: Mapping[str, object] = {
            "provider": raw.provider,
            "ready": raw.ready,
            "hosted": raw.hosted,
            "immutable": raw.immutable,
            "overwrite_protected": raw.overwrite_protected,
            "delete_protected": raw.delete_protected,
            "detail": raw.detail,
        }
    elif isinstance(raw, Mapping):
        source = raw
    else:
        raise BackupOperationsError("backup store readiness is unavailable")
    detail = source.get("detail")
    if not isinstance(detail, str):
        detail = None
    return BackupStoreStatus(
        provider=str(source.get("provider", "")),
        ready=source.get("ready") is True,
        hosted=source.get("hosted") is True,
        immutable=source.get("immutable") is True,
        overwrite_protected=source.get("overwrite_protected") is True,
        delete_protected=source.get("delete_protected") is True,
        detail=detail,
    )


def _valid_record(record: object) -> StoredBackup:
    if not isinstance(record, StoredBackup):
        raise BackupOperationsError("backup store returned invalid metadata")
    if (
        re.fullmatch(r"backup_[0-9a-f]{32}", record.backup_id) is None
        or not isinstance(record.size_bytes, int)
        or record.size_bytes < 1
        or _SHA256.fullmatch(record.sha256) is None
        or not record.exists
        or not record.integrity_verified
        or not record.immutable
        or not record.overwrite_protected
        or not record.delete_protected
    ):
        raise BackupOperationsError("backup store object failed existence/integrity/WORM checks")
    _version(record.backup_key_version, "backup key version")
    _version(record.recovery_key_version, "recovery key version")
    _utc(record.created_at, "backup created_at")
    if _utc(record.retention_until, "backup retention_until") <= _utc(
        record.created_at, "backup created_at"
    ):
        raise BackupOperationsError("backup retention_until is invalid")
    return record


class BackupOperationsMonitor:
    """Evaluate external custody/store posture and the latest backup age."""

    def __init__(
        self,
        config: BackupOperationsConfig,
        key_custody: DurableBackupKeyCustody,
        store: ImmutableBackupStore,
    ) -> None:
        config.validate()
        self.config = config
        self.key_custody = key_custody
        self.store = store

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        checked_at = _utc(now or datetime.now(UTC), "status time")
        alerts: list[str] = []
        key_status = self._key_status(alerts)
        store_status = self._store_status(alerts)

        latest: StoredBackup | None = None
        if store_status.ready and store_status.hosted:
            try:
                candidate = self.store.latest()
                if candidate is None:
                    alerts.append("backup_missing")
                else:
                    inspected = self.store.inspect(candidate.backup_id)
                    if inspected.backup_id != candidate.backup_id:
                        raise BackupOperationsError(
                            "latest backup identity changed during inspection"
                        )
                    latest = _valid_record(inspected)
            except Exception as exc:
                if "backup_missing" not in alerts:
                    alerts.append("backup_store_check_failed")
                if isinstance(exc, BackupOperationsError) and str(exc):
                    alerts.append("backup_integrity_failed")
        else:
            alerts.append("backup_store_unavailable")

        age_seconds: float | None = None
        if latest is not None:
            created_at = _utc(latest.created_at, "backup created_at")
            age_seconds = (checked_at - created_at).total_seconds()
            if age_seconds < 0:
                alerts.append("backup_timestamp_invalid")
                age_seconds = None
            elif age_seconds > self.config.max_backup_age_seconds:
                alerts.append("backup_stale")
            if age_seconds is not None and age_seconds > self.config.rpo_seconds:
                alerts.append("backup_rpo_exceeded")
            if age_seconds is not None and age_seconds > self.config.schedule_interval_seconds:
                alerts.append("backup_due")
            if _utc(latest.retention_until, "backup retention_until") <= checked_at:
                alerts.append("backup_retention_expired")
            self._check_rotation_versions(latest, key_status, alerts)

        # Preserve order while preventing repeated alerts from adapter failures.
        alerts = list(dict.fromkeys(alerts))
        ready = not alerts
        metrics: dict[str, int | float] = {
            "backup_ready": int(ready),
            "backup_age_seconds": age_seconds if age_seconds is not None else -1,
            "backup_rpo_seconds": self.config.rpo_seconds,
            "backup_max_age_seconds": self.config.max_backup_age_seconds,
            "backup_schedule_interval_seconds": self.config.schedule_interval_seconds,
            "backup_stale": int("backup_stale" in alerts),
            "backup_rpo_exceeded": int("backup_rpo_exceeded" in alerts),
            "backup_store_ready": int(store_status.ready and store_status.hosted),
            "backup_key_custody_ready": int(key_status.ready and key_status.hosted),
        }
        return {
            "status": "ok" if ready else "not_ready",
            "ready": ready,
            "checked_at": checked_at.isoformat(),
            "alerts": alerts,
            "dependencies": {
                "key_custody": key_status.public(),
                "backup_store": store_status.public(),
                "scheduler": {
                    "ready": True,
                    "interval_seconds": self.config.schedule_interval_seconds,
                    "rpo_seconds": self.config.rpo_seconds,
                    "max_backup_age_seconds": self.config.max_backup_age_seconds,
                },
                "latest_backup": latest.public() if latest is not None else {"ready": False},
            },
            "metrics": metrics,
        }

    def metrics(self, now: datetime | None = None) -> dict[str, int | float]:
        """Return only bounded, secret-free metric values."""

        return self.status(now)["metrics"]

    def _key_status(self, alerts: list[str]) -> KeyCustodyStatus:
        try:
            raw = self.key_custody.readiness()
            status = _coerce_key_status(raw)
        except Exception:
            alerts.append("key_custody_unavailable")
            return KeyCustodyStatus(
                "unavailable", False, False, {}, {}, detail="adapter unavailable"
            )
        if status.provider != self.config.key_adapter_id:
            alerts.append("key_custody_adapter_mismatch")
        if self.config.require_hosted and not status.hosted:
            alerts.append("key_custody_not_hosted")
        if not status.ready:
            alerts.append("key_custody_not_ready")
        for purpose in ("backup", "recovery"):
            active = status.active_versions.get(purpose)
            accepted = status.accepted_versions.get(purpose, ())
            if active is None or active not in accepted:
                alerts.append(f"{purpose}_key_rotation_state_invalid")
        if (
            self.config.expected_backup_key_version is not None
            and status.active_versions.get("backup") != self.config.expected_backup_key_version
        ):
            alerts.append("backup_key_active_version_mismatch")
        if (
            self.config.expected_recovery_key_version is not None
            and status.active_versions.get("recovery") != self.config.expected_recovery_key_version
        ):
            alerts.append("recovery_key_active_version_mismatch")
        return status

    def _store_status(self, alerts: list[str]) -> BackupStoreStatus:
        try:
            raw = self.store.readiness()
            status = _coerce_store_status(raw)
        except Exception:
            alerts.append("backup_store_unavailable")
            return BackupStoreStatus("unavailable", False, False, False, False, False)
        if status.provider != self.config.store_adapter_id:
            alerts.append("backup_store_adapter_mismatch")
        if self.config.require_hosted and not status.hosted:
            alerts.append("backup_store_not_hosted")
        if not status.ready:
            alerts.append("backup_store_not_ready")
        if not status.immutable:
            alerts.append("backup_store_not_immutable")
        if not status.overwrite_protected:
            alerts.append("backup_store_overwrite_allowed")
        if not status.delete_protected:
            alerts.append("backup_store_delete_allowed")
        return status

    def _check_rotation_versions(
        self,
        record: StoredBackup,
        status: KeyCustodyStatus,
        alerts: list[str],
    ) -> None:
        for purpose, version in (
            ("backup", record.backup_key_version),
            ("recovery", record.recovery_key_version),
        ):
            accepted = status.accepted_versions.get(purpose, ())
            if version not in accepted:
                alerts.append(f"{purpose}_key_version_mismatch")
