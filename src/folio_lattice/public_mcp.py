from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
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
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path != "/mcp":
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
        tool: str = "__ready__",
        arguments: Mapping[str, Any] | None = None,
    ) -> str:
        if not isinstance(principal, Principal):
            raise ValueError("a verified principal is required")
        binding = CapabilityBinding(tool, capability_arguments_digest(arguments or {}))
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_locked()
            self._tokens[self._digest(token)] = (
                time.monotonic() + self.ttl_seconds,
                principal,
                binding,
            )
        return token

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
    """Verify short-lived renderer capabilities across process boundaries."""

    def __init__(self, endpoint: str, secret: str, *, ttl_seconds: float = 15.0) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path != "/mcp":
            raise ValueError("signed principal relay endpoint must be an HTTP /mcp URL")
        if not isinstance(secret, str) or len(secret) < 32:
            raise ValueError("signed principal relay secret must be at least 32 characters")
        if ttl_seconds <= 0 or ttl_seconds > 60:
            raise ValueError("signed principal relay TTL is outside the allowed bound")
        self.endpoint = endpoint
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def issue(
        self,
        principal: Principal,
        *,
        tool: str = "__ready__",
        arguments: Mapping[str, Any] | None = None,
    ) -> str:
        if not isinstance(principal, Principal):
            raise ValueError("a verified principal is required")
        now = int(time.time())
        payload = {
            "v": 1,
            "aud": self.endpoint,
            "iat": now,
            "exp": now + int(self.ttl_seconds),
            "tenant_id": principal.tenant_id,
            "actor_id": principal.actor_id,
            "issuer": principal.issuer,
            "subject": principal.subject,
            "scopes": sorted(principal.scopes),
            "tool": tool,
            "arguments_digest": capability_arguments_digest(arguments or {}),
        }
        encoded = self._encode(payload)
        signature = hmac.new(self.secret, encoded, hashlib.sha256).digest()
        return f"{encoded.decode('ascii')}.{self._b64(signature)}"

    def resolve(self, token: str) -> Principal | None:
        capability = self.resolve_capability(token)
        return capability.principal if capability is not None else None

    def resolve_capability(self, token: str) -> PrincipalCapability | None:
        if not isinstance(token, str) or len(token) > 4096:
            return None
        encoded, separator, signature = token.partition(".")
        if not separator or not encoded or not signature:
            return None
        try:
            signed = self._decode(signature)
            expected = hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(signed, expected):
                return None
            payload = json.loads(self._decode(encoded))
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        now = int(time.time())
        if (
            payload.get("v") != 1
            or payload.get("aud") != self.endpoint
            or not isinstance(payload.get("iat"), int)
            or not isinstance(payload.get("exp"), int)
            or payload["iat"] > now + 2
            or payload["exp"] < now
            or payload["exp"] - payload["iat"] > 60
        ):
            return None
        fields = ("tenant_id", "actor_id", "issuer", "subject", "tool", "arguments_digest")
        if any(not isinstance(payload.get(field), str) or not payload[field] for field in fields):
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
        # Signed capabilities expire quickly; there is no mutable revocation
        # state to share across the renderer and control processes.
        del token

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
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


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
        token = self.principal_relay.issue(principal, tool=tool, arguments=arguments)
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
        token = self.principal_relay.issue(principal)
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
