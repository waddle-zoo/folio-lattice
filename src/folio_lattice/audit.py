from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

AUDIT_SCHEMA_VERSION = "audit-v1"
AUDIT_MAX_EVENTS_PER_EXPORT = 10_000
AUDIT_MAX_EXPORT_BYTES = 1 * 1024 * 1024
AUDIT_EXPORT_TTL_SECONDS = 24 * 60 * 60
AUDIT_RETENTION_DAYS = {
    "security": 365,
    "request": 90,
    "debug": 30,
}
AUDIT_DETAIL_KEYS = frozenset(
    {
        "status_code",
        "reason_code",
        "policy_version",
        "source",
        "connection_id",
        "tool_name",
        "resource_type",
        "resource_id",
        "limit_name",
        "retry_after",
        "transport",
    }
)
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]{1,255}$")


class AuditValidationError(ValueError):
    """A client supplied audit field is outside the bounded safe contract."""


def validate_token(field: str, value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value) or len(value) > 255:
        raise AuditValidationError(f"{field} is invalid")
    if value and (_SAFE_TOKEN.fullmatch(value) is None or value != value.strip() or "://" in value):
        raise AuditValidationError(f"{field} is invalid")
    return value


def safe_details(details: Mapping[str, Any] | None) -> dict[str, str | int | float | bool]:
    if details is None:
        return {}
    if not isinstance(details, Mapping) or len(details) > len(AUDIT_DETAIL_KEYS):
        raise AuditValidationError("audit details are invalid")
    safe: dict[str, str | int | float | bool] = {}
    for key, value in details.items():
        if key not in AUDIT_DETAIL_KEYS:
            raise AuditValidationError("audit details contain a restricted field")
        if isinstance(value, bool):
            safe[key] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            if abs(value) > 1_000_000_000:
                raise AuditValidationError("audit detail is out of bounds")
            safe[key] = value
        elif isinstance(value, float):
            if value != value or value in {float("inf"), float("-inf")} or abs(value) > 1e12:
                raise AuditValidationError("audit detail is out of bounds")
            safe[key] = value
        elif isinstance(value, str):
            if (
                len(value) > 255
                or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
                or "://" in value
                or any(
                    marker in value.casefold()
                    for marker in ("bearer", "token", "secret", "credential", "password")
                )
            ):
                raise AuditValidationError("audit detail is out of bounds")
            safe[key] = value
        else:
            raise AuditValidationError("audit details must contain scalar values")
    return safe


def expiry_for(retention_class: str, *, now: datetime | None = None) -> str:
    days = AUDIT_RETENTION_DAYS.get(retention_class)
    if days is None:
        raise AuditValidationError("audit retention class is invalid")
    current = now or datetime.now(UTC)
    return (current + timedelta(days=days)).isoformat(timespec="milliseconds")


def canonical_event(event: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(event), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def integrity_hash(event: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_event(event)).hexdigest()


def export_line(event: Mapping[str, Any]) -> bytes:
    return canonical_event(event) + b"\n"
