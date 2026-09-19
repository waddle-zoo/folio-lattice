from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from mcp import Client

from .auth import (
    CapabilityBinding,
    Principal,
    capability_arguments_digest,
    get_request_principal,
)

PUBLIC_TOOLS = frozenset(
    {
        "artifact_create",
        "artifact_write",
        "artifact_list",
        "artifact_read",
        "artifact_read_chunk",
        "artifact_search",
        "artifact_grep",
        "graph_link",
        "graph_traverse",
        "graph_component",
        "artifact_versions",
        "artifact_share",
        "artifact_revoke",
        "artifact_acl",
    }
)

ADMIN_TOOLS = frozenset(
    {
        "external_mcp_connection_register",
        "external_mcp_connection_list",
        "external_mcp_connection_status",
        "external_mcp_connection_revoke",
        "external_mcp_audit",
        "audit_export",
    }
)


class PublicMcpError(Exception):
    """Bounded, client-visible failure from the public MCP boundary."""


INTERNAL_PRINCIPAL_HEADER = "x-folio-internal-principal"
CAPABILITY_PROTOCOL = "folio-renderer-capability/v1"
CAPABILITY_KEY_LABEL = CAPABILITY_PROTOCOL.encode("ascii")
IDENTITY_RELAY_TOOL = "__identity__"
IDENTITY_RELAY_DIGEST = hashlib.sha256(IDENTITY_RELAY_TOOL.encode("ascii")).hexdigest()
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class PrincipalCapability:
    principal: Principal
    binding: CapabilityBinding


class TrustedPrincipalRelay:
    """Issue short-lived opaque capabilities for same-process HTTP handoffs.

    The token carries no identity and is never accepted from bridge content.  It
    is usable only for the exact configured MCP endpoint and is revoked when the
    nested client closes its connection.
    """

    def __init__(self, endpoint: str, *, ttl_seconds: float = 15.0) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path != "/mcp"
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("trusted principal relay endpoint must be an HTTP /mcp URL")
        if ttl_seconds <= 0 or ttl_seconds > 60:
            raise ValueError("trusted principal relay TTL is outside the allowed bound")
        self.endpoint = endpoint
        self.ttl_seconds = ttl_seconds
        self._tokens: dict[str, tuple[float, Principal, CapabilityBinding]] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        principal: Principal,
        *,
        tool: str = IDENTITY_RELAY_TOOL,
        arguments: Mapping[str, Any] | None = None,
    ) -> str:
        if not isinstance(principal, Principal):
            raise ValueError("a verified principal is required")
        if tool == IDENTITY_RELAY_TOOL:
            if arguments:
                raise ValueError("identity relay cannot carry arguments")
            digest = IDENTITY_RELAY_DIGEST
        elif tool == "artifact_read":
            digest = capability_arguments_digest(arguments or {})
        else:
            raise ValueError("renderer capability only supports artifact_read")
        binding = CapabilityBinding(tool, digest)
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_locked()
            self._tokens[self._digest(token)] = (
                time.monotonic() + self.ttl_seconds,
                principal,
                binding,
            )
        return token

    def issue_identity(self, principal: Principal) -> str:
        return self.issue(principal, tool=IDENTITY_RELAY_TOOL)

    def resolve(self, token: str) -> Principal | None:
        capability = self.resolve_capability(token)
        return capability.principal if capability is not None else None

    def resolve_capability(self, token: str) -> PrincipalCapability | None:
        if not isinstance(token, str) or not token or len(token) > 256:
            return None
        with self._lock:
            self._purge_locked()
            entry = self._tokens.get(self._digest(token))
            if entry is None:
                return None
            return PrincipalCapability(entry[1], entry[2])

    def revoke(self, token: str) -> None:
        if not isinstance(token, str):
            return
        with self._lock:
            self._tokens.pop(self._digest(token), None)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()

    def _purge_locked(self) -> None:
        now = time.monotonic()
        for key, (expires_at, _, _) in tuple(self._tokens.items()):
            if expires_at <= now:
                self._tokens.pop(key, None)


class SignedPrincipalRelay:
    """Verify short-lived renderer capabilities across process boundaries.

    Signed capabilities are a local renderer handoff, not a hosted session
    replacement.  The renderer keeps a local revocation denylist and the MCP
    server re-checks tenant/actor ACLs on every read, so ACL revocation is
    immediate even while a captured token remains inside its short TTL.
    """

    def __init__(
        self,
        endpoint: str,
        secret: str,
        *,
        audience: str | None = None,
        ttl_seconds: float = 15.0,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path != "/mcp"
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("signed principal relay endpoint must be an HTTP /mcp URL")
        if audience is not None:
            try:
                audience_parsed = urlsplit(audience)
                _ = audience_parsed.port
            except (UnicodeError, ValueError) as exc:
                raise ValueError(
                    "signed principal relay audience must be an exact HTTPS /mcp URL"
                ) from exc
            if (
                audience_parsed.scheme != "https"
                or not audience_parsed.netloc
                or not audience_parsed.hostname
                or audience_parsed.path != "/mcp"
                or audience_parsed.query
                or audience_parsed.fragment
                or audience_parsed.username is not None
                or audience_parsed.password is not None
            ):
                raise ValueError("signed principal relay audience must be an exact HTTPS /mcp URL")
        if not isinstance(secret, str) or len(secret) < 32:
            raise ValueError("signed principal relay secret must be at least 32 characters")
        if ttl_seconds <= 0 or ttl_seconds > 60:
            raise ValueError("signed principal relay TTL is outside the allowed bound")
        self.endpoint = endpoint
        self.audience = endpoint if audience is None else audience
        self._signing_key = hmac.new(
            secret.encode("utf-8"), CAPABILITY_KEY_LABEL, hashlib.sha256
        ).digest()
        self.ttl_seconds = ttl_seconds
        self._revoked: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        principal: Principal,
        *,
        tool: str = IDENTITY_RELAY_TOOL,
        arguments: Mapping[str, Any] | None = None,
    ) -> str:
        if not isinstance(principal, Principal):
            raise ValueError("a verified principal is required")
        if tool == IDENTITY_RELAY_TOOL:
            if arguments:
                raise ValueError("identity relay cannot carry arguments")
            digest = IDENTITY_RELAY_DIGEST
        elif tool == "artifact_read":
            digest = capability_arguments_digest(arguments or {})
        else:
            raise ValueError("renderer capability only supports artifact_read")
        now = int(time.time())
        payload = {
            "protocol": CAPABILITY_PROTOCOL,
            "v": 1,
            "aud": self.audience,
            "iat": now,
            "exp": now + int(self.ttl_seconds),
            "jti": secrets.token_urlsafe(16),
            "tenant_id": principal.tenant_id,
            "actor_id": principal.actor_id,
            "issuer": principal.issuer,
            "subject": principal.subject,
            "scopes": sorted(principal.scopes),
            "tool": tool,
            "arguments_digest": digest,
        }
        encoded = self._encode(payload)
        signature = hmac.new(self._signing_key, encoded, hashlib.sha256).digest()
        return f"{encoded.decode('ascii')}.{self._b64(signature)}"

    def issue_identity(self, principal: Principal) -> str:
        return self.issue(principal, tool=IDENTITY_RELAY_TOOL)

    def resolve(self, token: str) -> Principal | None:
        capability = self.resolve_capability(token)
        return capability.principal if capability is not None else None

    def resolve_capability(self, token: str) -> PrincipalCapability | None:
        if not isinstance(token, str) or len(token) > 4096 or token.count(".") != 1:
            return None
        token_digest = hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()
        with self._lock:
            self._purge_revoked_locked()
            if token_digest in self._revoked:
                return None
        encoded, separator, signature = token.partition(".")
        if not separator or not encoded or not signature:
            return None
        try:
            signed = self._decode(signature)
            expected = hmac.new(self._signing_key, encoded.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(signed, expected):
                return None
            payload = json.loads(self._decode(encoded))
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        now = int(time.time())
        if (
            payload.get("protocol") != CAPABILITY_PROTOCOL
            or payload.get("v") != 1
            or payload.get("aud") != self.audience
            or not isinstance(payload.get("iat"), int)
            or not isinstance(payload.get("exp"), int)
            or payload["iat"] > now + 2
            or payload["exp"] < now
            or payload["exp"] - payload["iat"] > 60
        ):
            return None
        fields = (
            "tenant_id",
            "actor_id",
            "issuer",
            "subject",
            "tool",
            "arguments_digest",
            "jti",
        )
        if any(not isinstance(payload.get(field), str) or not payload[field] for field in fields):
            return None
        if payload["tool"] not in {"artifact_read", IDENTITY_RELAY_TOOL}:
            return None
        if (
            payload["tool"] == IDENTITY_RELAY_TOOL
            and payload["arguments_digest"] != IDENTITY_RELAY_DIGEST
        ):
            return None
        scopes = payload.get("scopes")
        if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
            return None
        principal = Principal(
            tenant_id=payload["tenant_id"],
            actor_id=payload["actor_id"],
            issuer=payload["issuer"],
            subject=payload["subject"],
            scopes=frozenset(scopes),
        )
        return PrincipalCapability(
            principal,
            CapabilityBinding(payload["tool"], payload["arguments_digest"]),
        )

    def revoke(self, token: str) -> None:
        """Prevent local reuse; remote replay remains bounded by TTL and ACL."""

        if not isinstance(token, str) or not token or len(token) > 4096:
            return
        digest = hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()
        with self._lock:
            self._purge_revoked_locked()
            self._revoked[digest] = time.time() + 60

    def _purge_revoked_locked(self) -> None:
        now = time.time()
        for digest, expires_at in tuple(self._revoked.items()):
            if expires_at <= now:
                self._revoked.pop(digest, None)

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @classmethod
    def _encode(cls, payload: dict[str, Any]) -> bytes:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    @staticmethod
    def _decode(value: str) -> bytes:
        if not value or len(value) > 4096:
            raise ValueError("capability segment is too large")
        if not _BASE64URL_RE.fullmatch(value) or len(value) % 4 == 1:
            raise ValueError("capability segment is not canonical base64url")
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        if SignedPrincipalRelay._b64(decoded) != value:
            raise ValueError("capability segment is not canonical base64url")
        return decoded


class ToolCaller(Protocol):
    async def call(self, tool: str, arguments: Mapping[str, Any]) -> Any: ...

    async def ready(self) -> bool: ...


class HttpMcpClient:
    """Small official-SDK adapter for one configured Streamable HTTP server."""

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 5,
        max_result_bytes: int = 13 * 1024 * 1024,
        principal_relay: TrustedPrincipalRelay | SignedPrincipalRelay | None = None,
        principal: Principal | None = None,
    ):
        if timeout_seconds <= 0 or max_result_bytes < 1:
            raise ValueError("MCP client limits must be positive")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.max_result_bytes = max_result_bytes
        if principal_relay is not None and principal_relay.endpoint != endpoint:
            raise ValueError("trusted principal relay is bound to another MCP endpoint")
        if principal is not None and principal_relay is None:
            raise ValueError("a fixed principal requires a trusted relay")
        self.principal_relay = principal_relay
        self.principal = principal

    async def call(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        if tool not in PUBLIC_TOOLS:
            raise PublicMcpError("tool is not part of the Folio MCP contract")
        principal = get_request_principal() or self.principal
        if self.principal_relay is not None and principal is not None:
            return await self._call_with_relay(tool, arguments, principal)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with Client(
                    self.endpoint,
                    raise_exceptions=False,
                    read_timeout_seconds=self.timeout_seconds,
                ) as client:
                    result = await client.call_tool(tool, dict(arguments))
        except TimeoutError:
            raise PublicMcpError("MCP call timed out") from None
        except PublicMcpError:
            raise
        except Exception:
            raise PublicMcpError("MCP service unavailable") from None

        if result.is_error:
            messages = [item.text for item in result.content if item.type == "text"]
            message = " ".join(messages).strip() or "MCP tool failed"
            raise PublicMcpError(message[:500])
        value = result.structured_content
        if value is None:
            raise PublicMcpError("MCP tool returned no structured result")
        try:
            encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError):
            raise PublicMcpError("MCP tool returned an invalid structured result") from None
        if len(encoded) > self.max_result_bytes:
            raise PublicMcpError("MCP result exceeds the allowed size")
        if isinstance(value, dict) and set(value) == {"result"}:
            return value["result"]
        return value

    async def ready(self) -> bool:
        principal = get_request_principal() or self.principal
        if self.principal_relay is not None and principal is not None:
            try:
                await self._ready_with_relay(principal)
                return True
            except Exception:
                return False
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with Client(
                    self.endpoint,
                    raise_exceptions=False,
                    read_timeout_seconds=self.timeout_seconds,
                ) as client:
                    tools = await client.list_tools()
            return "artifact_read" in {tool.name for tool in tools.tools}
        except Exception:
            return False

    async def _call_with_relay(
        self, tool: str, arguments: Mapping[str, Any], principal: Principal
    ) -> Any:
        assert self.principal_relay is not None
        if tool == "artifact_read":
            token = self.principal_relay.issue(principal, tool=tool, arguments=arguments)
        else:
            token = self.principal_relay.issue_identity(principal)
        try:
            result = await self._session_call(
                token,
                lambda session: session.call_tool(tool, dict(arguments)),
            )
        except TimeoutError:
            raise PublicMcpError("MCP call timed out") from None
        except PublicMcpError:
            raise
        except Exception:
            raise PublicMcpError("MCP service unavailable") from None
        finally:
            self.principal_relay.revoke(token)
        return self._decode_result(result)

    async def _ready_with_relay(self, principal: Principal) -> None:
        assert self.principal_relay is not None
        token = self.principal_relay.issue_identity(principal)
        try:
            result = await self._session_call(token, lambda session: session.list_tools())
            if "artifact_read" not in {tool.name for tool in result.tools}:
                raise PublicMcpError("MCP service is not ready")
        finally:
            self.principal_relay.revoke(token)

    async def _session_call(self, token: str, operation: Any) -> Any:
        """Run one MCP operation with only the server-owned relay header."""

        try:
            import importlib

            try:
                httpx = importlib.import_module("httpx2")
            except ImportError:
                httpx = importlib.import_module("httpx")
            from mcp.client.session import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except Exception:
            raise PublicMcpError("MCP service unavailable") from None
        async with asyncio.timeout(self.timeout_seconds):
            async with httpx.AsyncClient(
                headers={INTERNAL_PRINCIPAL_HEADER: token},
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as http_client:
                async with streamable_http_client(
                    self.endpoint, http_client=http_client
                ) as streams:
                    async with ClientSession(
                        streams[0], streams[1], read_timeout_seconds=self.timeout_seconds
                    ) as session:
                        await session.initialize()
                        return await operation(session)

    def _decode_result(self, result: Any) -> Any:
        if result.is_error:
            messages = [item.text for item in result.content if item.type == "text"]
            message = " ".join(messages).strip() or "MCP tool failed"
            raise PublicMcpError(message[:500])
        value = result.structured_content
        if value is None:
            raise PublicMcpError("MCP tool returned no structured result")
        try:
            encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError):
            raise PublicMcpError("MCP tool returned an invalid structured result") from None
        if len(encoded) > self.max_result_bytes:
            raise PublicMcpError("MCP result exceeds the allowed size")
        if isinstance(value, dict) and set(value) == {"result"}:
            return value["result"]
        return value


class AdminMcpClient:
    """Call tenant-admin tools on the authenticated server context."""

    def __init__(self, server: Any, *, timeout_seconds: float = 5):
        if timeout_seconds <= 0:
            raise ValueError("MCP client timeout must be positive")
        self.server = server
        self.timeout_seconds = timeout_seconds

    async def call(self, tool: str, arguments: Mapping[str, Any]) -> Any:
        if tool not in ADMIN_TOOLS:
            raise PublicMcpError("tool is not part of the admin MCP contract")
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with Client(self.server, raise_exceptions=False) as client:
                    result = await client.call_tool(tool, dict(arguments))
        except TimeoutError:
            raise PublicMcpError("admin MCP call timed out") from None
        except PublicMcpError:
            raise
        except Exception:
            raise PublicMcpError("admin MCP service unavailable") from None
        if result.is_error:
            messages = [item.text for item in result.content if item.type == "text"]
            raise PublicMcpError(" ".join(messages).strip()[:500] or "Admin MCP tool failed")
        value = result.structured_content
        if value is None:
            raise PublicMcpError("admin MCP tool returned no structured result")
        if isinstance(value, dict) and set(value) == {"result"}:
            return value["result"]
        return value

    async def ready(self) -> bool:
        return True
