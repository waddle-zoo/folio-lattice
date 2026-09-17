"""Certificate expiry and rotation evidence for the HTTP runtime."""

from __future__ import annotations

import hashlib
import ssl
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CERT_TIME_FORMAT = "%b %d %H:%M:%S %Y %Z"
DEFAULT_CERTIFICATE_WARNING_SECONDS = 30 * 24 * 60 * 60


class TlsCertificateMonitor:
    """Expose bounded certificate posture without exposing certificate data."""

    def __init__(
        self,
        certificate_path: str | Path,
        *,
        warning_seconds: int = DEFAULT_CERTIFICATE_WARNING_SECONDS,
    ) -> None:
        if warning_seconds < 1:
            raise ValueError("certificate warning window must be positive")
        self.certificate_path = Path(certificate_path)
        self.warning_seconds = warning_seconds
        self._fingerprint: str | None = None
        self._rotation_count = 0

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        checked_at = now or datetime.now(UTC)
        if checked_at.tzinfo is None:
            raise ValueError("certificate status time must include a timezone")
        checked_at = checked_at.astimezone(UTC)
        try:
            pem = self.certificate_path.read_text(encoding="ascii")
            decode_cert = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
            if not callable(decode_cert):
                raise ssl.SSLError("certificate decoder is unavailable")
            certificate = decode_cert(str(self.certificate_path))
            not_after = datetime.strptime(certificate["notAfter"], _CERT_TIME_FORMAT).replace(
                tzinfo=UTC
            )
            fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
        except (KeyError, OSError, TypeError, ValueError, ssl.SSLError):
            return {
                "status": "not_ready",
                "ready": False,
                "checked_at": checked_at.isoformat(),
                "alerts": ["tls_certificate_unavailable"],
                "metrics": {
                    "tls_certificate_ready": 0,
                    "tls_certificate_expiry_seconds": -1,
                    "tls_certificate_rotation_total": self._rotation_count,
                },
            }

        alerts: list[str] = []
        if self._fingerprint is not None and self._fingerprint != fingerprint:
            self._rotation_count += 1
            alerts.append("tls_certificate_rotated")
        self._fingerprint = fingerprint
        expiry_seconds = (not_after - checked_at).total_seconds()
        if expiry_seconds <= 0:
            alerts.append("tls_certificate_expired")
        elif expiry_seconds <= self.warning_seconds:
            alerts.append("tls_certificate_expiring")
        ready = "tls_certificate_expired" not in alerts
        return {
            "status": "ok" if ready else "not_ready",
            "ready": ready,
            "checked_at": checked_at.isoformat(),
            "not_after": not_after.isoformat(),
            "fingerprint_sha256": fingerprint,
            "alerts": alerts,
            "metrics": {
                "tls_certificate_ready": int(ready),
                "tls_certificate_expiry_seconds": expiry_seconds,
                "tls_certificate_rotation_total": self._rotation_count,
            },
        }

    def metrics(self, now: datetime | None = None) -> dict[str, int | float]:
        return self.status(now)["metrics"]
