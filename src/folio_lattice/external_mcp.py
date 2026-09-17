from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from typing import Any, Protocol

from .service import FolioError, FolioLattice

MAX_EXTERNAL_RESULT_BYTES = 1 * 1024 * 1024


class CredentialBroker(Protocol):
    """Issue one short-lived, audience-bound credential for one approved call."""

    def issue(
        self, *, tenant_id: str, connection_id: str, credential_ref: str, audience: str
    ) -> str: ...


class ExternalMcpTransport(Protocol):
    """Call an already-approved upstream without accepting caller destinations."""

    def health(self, endpoint: str, *, credential: str | None) -> str: ...

    def call_tool(
        self,
        endpoint: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        credential: str | None,
    ) -> Any: ...

    def read_resource(self, endpoint: str, resource_uri: str, *, credential: str | None) -> Any: ...


class ExternalMcpError(FolioError):
    """Safe public failure from the external MCP broker."""


class UnavailableCredentialBroker:
    """Fail closed until a deployment supplies its secret-manager adapter."""

    def issue(
        self, *, tenant_id: str, connection_id: str, credential_ref: str, audience: str
    ) -> str:
        del tenant_id, connection_id, credential_ref, audience
        raise ExternalMcpError("credential broker unavailable")


class UnavailableExternalMcpTransport:
    """Fail closed until a deployment supplies its bounded MCP transport adapter."""

    def health(self, endpoint: str, *, credential: str | None) -> str:
        del endpoint, credential
        raise ExternalMcpError("external MCP transport unavailable")

    def call_tool(
        self,
        endpoint: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        credential: str | None,
    ) -> Any:
        del endpoint, tool_name, arguments, credential
        raise ExternalMcpError("external MCP transport unavailable")

    def read_resource(self, endpoint: str, resource_uri: str, *, credential: str | None) -> Any:
        del endpoint, resource_uri, credential
        raise ExternalMcpError("external MCP transport unavailable")


class HttpExternalMcpTransport:
    """Use the pinned MCP SDK against one already-validated HTTPS endpoint."""

    def __init__(self, *, timeout_seconds: float = 10) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("external MCP transport timeout must be positive")
        self.timeout_seconds = timeout_seconds

    def health(self, endpoint: str, *, credential: str | None) -> str:
        return self._run(endpoint, credential, "health", None)

    def call_tool(
        self,
        endpoint: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        credential: str | None,
    ) -> Any:
        return self._run(endpoint, credential, "tool", (tool_name, dict(arguments)))

    def read_resource(self, endpoint: str, resource_uri: str, *, credential: str | None) -> Any:
        return self._run(endpoint, credential, "resource", resource_uri)

    def _run(
        self,
        endpoint: str,
        credential: str | None,
        operation: str,
        value: Any,
    ) -> Any:
        try:
            return asyncio.run(self._request(endpoint, credential, operation, value))
        except ExternalMcpError:
            raise
        except Exception as exc:
            raise ExternalMcpError("external MCP transport unavailable") from exc

    async def _request(
        self,
        endpoint: str,
        credential: str | None,
        operation: str,
        value: Any,
    ) -> Any:
        import httpx2
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {credential}"} if credential is not None else {}
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(endpoint, http_client=http_client) as streams:
                async with ClientSession(
                    *streams, read_timeout_seconds=self.timeout_seconds
                ) as session:
                    await session.initialize()
                    if operation == "health":
                        return "healthy"
                    result: Any
                    if operation == "tool":
                        assert isinstance(value, tuple)
                        result = await session.call_tool(value[0], value[1])
                    else:
                        result = await session.read_resource(value)
        if getattr(result, "is_error", False):
            raise ExternalMcpError("external MCP upstream call failed")
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            return structured
        return result.model_dump(mode="json")


class ExternalMcpBroker:
    """Enforce the registry before asking adapters to reach an upstream."""

    def __init__(
        self,
        service: FolioLattice,
        *,
        credentials: CredentialBroker | None = None,
        transport: ExternalMcpTransport | None = None,
    ):
        self.service = service
        self.credentials = credentials or UnavailableCredentialBroker()
        self.transport = transport or HttpExternalMcpTransport()

    def register(
        self,
        *,
        tenant_id: str,
        actor: str,
        name: str,
        endpoint: str,
        approved_tools: list[str],
        approved_resources: list[str],
        allowed_origins: list[str],
        credential_ref: str | None,
        reason: str,
    ) -> dict[str, Any]:
        return self.service.register_external_connection(
            tenant_id=tenant_id,
            actor=actor,
            name=name,
            endpoint=endpoint,
            approved_tools=approved_tools,
            approved_resources=approved_resources,
            allowed_origins=allowed_origins,
            credential_ref=credential_ref,
            reason=reason,
        )

    def list_connections(self, *, tenant_id: str, actor: str) -> list[dict[str, Any]]:
        return self.service.list_external_connections(tenant_id, actor=actor)

    def status(self, *, tenant_id: str, actor: str, connection_id: str) -> dict[str, Any]:
        return self.service.external_connection_status(tenant_id, connection_id, actor=actor)

    def health(
        self, *, tenant_id: str, actor: str, connection_id: str, probe: bool
    ) -> dict[str, Any]:
        connection = self.service.external_connection_for_broker(
            tenant_id, connection_id, actor=actor
        )
        if not probe:
            return self.service.external_connection_status(tenant_id, connection_id, actor=actor)
        if connection["status"] != "active":
            raise ExternalMcpError("external MCP connection is revoked")
        credential: str | None = None
        try:
            credential = self._credential(connection)
            health_status = self.transport.health(connection["endpoint"], credential=credential)
            if health_status not in {"healthy", "unhealthy"}:
                raise ExternalMcpError("external MCP health result is invalid")
        except ExternalMcpError:
            self.service.record_external_health(
                tenant_id, connection_id, actor=actor, status="unhealthy"
            )
            raise
        except Exception as exc:
            self.service.record_external_health(
                tenant_id, connection_id, actor=actor, status="unhealthy"
            )
            raise ExternalMcpError("external MCP health check failed") from exc
        finally:
            credential = None
        self.service.record_external_health(
            tenant_id, connection_id, actor=actor, status=health_status
        )
        return self.service.external_connection_status(tenant_id, connection_id, actor=actor)

    def revoke(
        self, *, tenant_id: str, actor: str, connection_id: str, reason: str
    ) -> dict[str, Any]:
        return self.service.revoke_external_connection(
            tenant_id, connection_id, actor=actor, reason=reason
        )

    def audit(
        self, *, tenant_id: str, actor: str, connection_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        return self.service.external_mcp_audit(
            tenant_id, actor=actor, connection_id=connection_id, limit=limit
        )

    def call_tool(
        self,
        *,
        tenant_id: str,
        actor: str,
        connection_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        connection = self.service.authorize_external_tool(
            tenant_id,
            connection_id,
            tool_name,
            actor=actor,
            arguments=arguments,
        )
        credential: str | None = None
        try:
            credential = self._credential(connection)
            result = self.transport.call_tool(
                connection["endpoint"],
                tool_name,
                arguments,
                credential=credential,
            )
            self._bounded_result(result, credential)
            self.service.record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="tool_call",
                outcome="allowed",
                tool_name=tool_name,
            )
            return result
        except ExternalMcpError:
            self.service.record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="tool_call",
                outcome="failed",
                tool_name=tool_name,
            )
            raise
        except Exception as exc:
            self.service.record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="tool_call",
                outcome="failed",
                tool_name=tool_name,
            )
            raise ExternalMcpError("external MCP tool call failed") from exc
        finally:
            credential = None

    def read_resource(
        self,
        *,
        tenant_id: str,
        actor: str,
        connection_id: str,
        resource_uri: str,
    ) -> Any:
        connection = self.service.authorize_external_resource(
            tenant_id, connection_id, resource_uri, actor=actor
        )
        credential: str | None = None
        try:
            credential = self._credential(connection)
            result = self.transport.read_resource(
                connection["endpoint"], resource_uri, credential=credential
            )
            self._bounded_result(result, credential)
            self.service.record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="resource_read",
                outcome="allowed",
                resource_uri=resource_uri,
            )
            return result
        except ExternalMcpError:
            self.service.record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="resource_read",
                outcome="failed",
                resource_uri=resource_uri,
            )
            raise
        except Exception as exc:
            self.service.record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="resource_read",
                outcome="failed",
                resource_uri=resource_uri,
            )
            raise ExternalMcpError("external MCP resource read failed") from exc
        finally:
            credential = None

    def _credential(self, connection: Mapping[str, Any]) -> str | None:
        credential_ref = connection.get("credential_ref")
        if credential_ref is None:
            return None
        try:
            credential = self.credentials.issue(
                tenant_id=connection["tenant_id"],
                connection_id=connection["id"],
                credential_ref=credential_ref,
                audience=connection["origin"],
            )
        except Exception as exc:
            raise ExternalMcpError("credential broker unavailable") from exc
        if not isinstance(credential, str) or not credential:
            raise ExternalMcpError("credential broker returned an invalid credential")
        return credential

    @staticmethod
    def _bounded_result(result: Any, credential: str | None) -> None:
        try:
            encoded = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError) as exc:
            raise ExternalMcpError("external MCP result is invalid") from exc
        if credential is not None and credential.encode() in encoded:
            raise ExternalMcpError("external MCP result contains credential material")
        if len(encoded) > MAX_EXTERNAL_RESULT_BYTES:
            raise ExternalMcpError("external MCP result exceeds the allowed size")
