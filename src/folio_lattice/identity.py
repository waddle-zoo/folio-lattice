from __future__ import annotations

import hmac
import json
import math
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

import jwt

from .auth import OIDC_ALGORITHM, AuthenticationError, JwksClient, JwksError

MAX_IDENTITY_URL_LENGTH = 4 * 1024
MAX_IDENTITY_TOKEN_LENGTH = 128 * 1024
DEFAULT_IDENTITY_TIMEOUT_SECONDS = 5.0
DEFAULT_IDENTITY_MAX_RESPONSE_BYTES = 1 * 1024 * 1024
MAX_IDENTITY_TIMEOUT_SECONDS = 30
MAX_IDENTITY_RESPONSE_BYTES = 4 * 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler).open


@dataclass(frozen=True, slots=True)
class IdentityClaims:
    """Claims verified at the external identity boundary."""

    issuer: str
    subject: str


class HostedIdentityAdapter(Protocol):
    """Provider-neutral browser authorization-code adapter."""

    def ready(self) -> bool: ...

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
        return_to: str,
    ) -> str: ...

    def complete_callback(
        self,
        *,
        code: str,
        nonce: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> IdentityClaims: ...


@dataclass(frozen=True, slots=True)
class HostedIdentityConfig:
    issuer: str
    jwks_url: str
    authorization_endpoint: str
    token_endpoint: str
    client_id: str
    redirect_uri: str
    timeout_seconds: float = DEFAULT_IDENTITY_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_IDENTITY_MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        _https_url("issuer", self.issuer)
        _https_url("JWKS URL", self.jwks_url)
        _https_url("authorization endpoint", self.authorization_endpoint)
        _https_url("token endpoint", self.token_endpoint)
        _https_callback_uri(self.redirect_uri)
        _client_id(self.client_id)
        if (
            not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > MAX_IDENTITY_TIMEOUT_SECONDS
        ):
            raise ValueError("identity timeout must be between 0 and 30 seconds")
        if not 1 <= self.max_response_bytes <= MAX_IDENTITY_RESPONSE_BYTES:
            raise ValueError("identity response limit is invalid")


class HttpHostedIdentityAdapter:
    """OIDC authorization-code adapter with an exact, HTTPS-only trust config."""

    def __init__(
        self,
        config: HostedIdentityConfig,
        *,
        opener: Callable[..., Any] = _NO_REDIRECT_OPENER,
        now: Callable[[], float] = time.time,
        clock_skew_seconds: float = 0,
        jwks: JwksClient | None = None,
    ) -> None:
        if not math.isfinite(clock_skew_seconds) or clock_skew_seconds < 0:
            raise ValueError("identity clock skew must not be negative")
        self.config = config
        self._opener = opener
        self._now = now
        self._clock_skew_seconds = clock_skew_seconds
        self._jwks = jwks or JwksClient(
            config.jwks_url,
            timeout_seconds=config.timeout_seconds,
            cache_seconds=300,
            opener=opener,
        )
        self._ready = False

    def warm_up(self) -> None:
        self._jwks.refresh()
        self._ready = True

    def ready(self) -> bool:
        return self._ready

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
        return_to: str,
    ) -> str:
        del return_to
        if redirect_uri != self.config.redirect_uri:
            raise AuthenticationError("redirect URI does not match identity configuration")
        values = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": "openid",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.config.authorization_endpoint}?{values}"

    def complete_callback(
        self,
        *,
        code: str,
        nonce: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> IdentityClaims:
        if redirect_uri != self.config.redirect_uri:
            raise AuthenticationError("redirect URI does not match identity configuration")
        payload = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "client_id": self.config.client_id,
                "code_verifier": code_verifier,
            }
        ).encode("ascii")
        request = urllib.request.Request(
            self.config.token_endpoint,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.config.timeout_seconds) as response:
                body = response.read(self.config.max_response_bytes + 1)
        except (OSError, TimeoutError) as exc:
            raise AuthenticationError("identity token exchange failed") from exc
        if len(body) > self.config.max_response_bytes:
            raise AuthenticationError("identity token response is too large")
        try:
            token_response = json.loads(body)
        except (TypeError, ValueError) as exc:
            raise AuthenticationError("identity token response is invalid") from exc
        if not isinstance(token_response, dict):
            raise AuthenticationError("identity token response is invalid")
        id_token = token_response.get("id_token")
        if (
            not isinstance(id_token, str)
            or not id_token
            or len(id_token) > MAX_IDENTITY_TOKEN_LENGTH
        ):
            raise AuthenticationError("identity token response is invalid")
        return self._verify_id_token(id_token, nonce)

    def _verify_id_token(self, token: str, nonce: str) -> IdentityClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.exceptions.PyJWTError as exc:
            raise AuthenticationError("identity token is invalid") from exc
        if header.get("alg") != OIDC_ALGORITHM:
            raise AuthenticationError("identity token is invalid")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthenticationError("identity token is invalid")
        try:
            claims = jwt.decode(
                token,
                self._jwks.key(kid),
                algorithms=[OIDC_ALGORITHM],
                options={
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                    "verify_iss": False,
                    "verify_aud": False,
                    "verify_sub": False,
                },
            )
        except (JwksError, jwt.exceptions.PyJWTError) as exc:
            raise AuthenticationError("identity token is invalid") from exc
        if not isinstance(claims, dict):
            raise AuthenticationError("identity token is invalid")
        if claims.get("iss") != self.config.issuer:
            raise AuthenticationError("identity token is invalid")
        if not _audience_matches(claims.get("aud"), self.config.client_id):
            raise AuthenticationError("identity token is invalid")
        if claims.get("azp") != self.config.client_id:
            raise AuthenticationError("identity token is invalid")
        token_nonce = claims.get("nonce")
        if (
            not isinstance(token_nonce, str)
            or not isinstance(nonce, str)
            or not hmac.compare_digest(token_nonce, nonce)
        ):
            raise AuthenticationError("identity token is invalid")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("identity token is invalid")
        now = self._now()
        expiration = _numeric_claim(claims, "exp", required=True)
        issued_at = _numeric_claim(claims, "iat", required=True)
        assert expiration is not None and issued_at is not None
        if expiration <= now - self._clock_skew_seconds:
            raise AuthenticationError("identity token is invalid")
        if issued_at > now + self._clock_skew_seconds:
            raise AuthenticationError("identity token is invalid")
        not_before = _numeric_claim(claims, "nbf")
        if not_before is not None and not_before > now + self._clock_skew_seconds:
            raise AuthenticationError("identity token is invalid")
        return IdentityClaims(issuer=self.config.issuer, subject=subject)


def _https_url(field: str, value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_IDENTITY_URL_LENGTH
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"{field} must be an exact HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field} must be an exact HTTPS URL")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} must be an exact HTTPS URL") from exc
    return value


def _https_callback_uri(value: str) -> str:
    _https_url("redirect URI", value)
    if urlsplit(value).path != "/auth/callback":
        raise ValueError("redirect URI must use the exact /auth/callback path")
    return value


def _client_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or value != value.strip()
        or any(character.isspace() or ord(character) < 0x20 for character in value)
    ):
        raise ValueError("client ID is invalid")
    return value


def _audience_matches(value: object, audience: str) -> bool:
    if isinstance(value, str):
        return value == audience
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) for item in value)
        and audience in value
    )


def _numeric_claim(claims: Mapping[str, Any], name: str, *, required: bool = False) -> float | None:
    value = claims.get(name)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AuthenticationError("identity token is invalid")
    return float(value)
