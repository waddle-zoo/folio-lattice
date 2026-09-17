from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import socket
from collections.abc import Mapping
from datetime import timedelta
from importlib import import_module
from importlib.util import find_spec
from typing import Any, Protocol
from urllib.parse import urlsplit

from .service import FolioError, FolioLattice

MAX_EXTERNAL_RESULT_BYTES = 1 * 1024 * 1024
MAX_EXTERNAL_HTTP_RESPONSE_BYTES = MAX_EXTERNAL_RESULT_BYTES


class ExternalMcpError(FolioError):
    """Safe public failure from the external MCP broker."""


class CredentialStore(Protocol):
    """Secret-manager seam; implementations must enforce tenant scoping."""

    def resolve(
        self,
        *,
        tenant_id: str,
        connection_id: str,
        credential_ref: str,
        audience: str,
    ) -> str: ...


class CredentialBroker(Protocol):
    """Issue one short-lived, audience-bound credential for one approved call."""

    def issue(
        self, *, tenant_id: str, connection_id: str, credential_ref: str, audience: str
    ) -> str: ...


class CredentialResolver:
    """Resolve one tenant-scoped credential for one approved audience."""

    def __init__(self, store: CredentialStore) -> None:
        self.store = store

    def resolve(
        self, *, tenant_id: str, connection_id: str, credential_ref: str, audience: str
    ) -> str:
        try:
            credential = self.store.resolve(
                tenant_id=tenant_id,
                connection_id=connection_id,
                credential_ref=credential_ref,
                audience=audience,
            )
        except Exception:
            raise ExternalMcpError("credential broker unavailable") from None
        if not isinstance(credential, str) or not credential:
            raise ExternalMcpError("credential broker returned an invalid credential")
        return credential

    def issue(
        self, *, tenant_id: str, connection_id: str, credential_ref: str, audience: str
    ) -> str:
        return self.resolve(
            tenant_id=tenant_id,
            connection_id=connection_id,
            credential_ref=credential_ref,
            audience=audience,
        )


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


class _ExternalResponseTooLarge(Exception):
    pass


class HttpExternalMcpTransport:
    """Use MCP streamable HTTP with fixed DNS, TLS, time, and byte bounds."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10,
        max_response_bytes: int = MAX_EXTERNAL_HTTP_RESPONSE_BYTES,
        dns_resolver: Any | None = None,
        http_transport: Any | None = None,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("external MCP transport timeout must be positive")
        if max_response_bytes < 1:
            raise ValueError("external MCP transport response bound must be positive")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._dns_resolver = dns_resolver or socket.getaddrinfo
        self._http_transport = http_transport

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
            return asyncio.run(self._timed_request(endpoint, credential, operation, value))
        except _ExternalResponseTooLarge:
            raise ExternalMcpError("external MCP response exceeds the allowed size") from None
        except TimeoutError:
            raise ExternalMcpError("external MCP transport timed out") from None
        except ExternalMcpError:
            raise
        except Exception as exc:
            if self._contains(exc, _ExternalResponseTooLarge):
                raise ExternalMcpError("external MCP response exceeds the allowed size") from None
            if self._contains(exc, TimeoutError):
                raise ExternalMcpError("external MCP transport timed out") from None
            raise ExternalMcpError("external MCP transport unavailable") from None

    @staticmethod
    def _contains(error: BaseException, target: type[BaseException]) -> bool:
        if isinstance(error, target):
            return True
        if isinstance(error, BaseExceptionGroup):
            return any(
                HttpExternalMcpTransport._contains(item, target) for item in error.exceptions
            )
        return False

    async def _timed_request(
        self,
        endpoint: str,
        credential: str | None,
        operation: str,
        value: Any,
    ) -> Any:
        async with asyncio.timeout(self.timeout_seconds):
            return await self._request(endpoint, credential, operation, value)

    async def _request(
        self,
        endpoint: str,
        credential: str | None,
        operation: str,
        value: Any,
    ) -> Any:
        try:
            httpcore: Any = import_module("httpcore2")
            httpx: Any = import_module("httpx2")
        except ImportError:
            httpcore = import_module("httpcore")
            httpx = import_module("httpx")

        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        resolved = self._resolve_endpoint(endpoint)
        headers = {"Authorization": f"Bearer {credential}"} if credential is not None else {}
        transport = self._http_transport
        if transport is None:
            transport = self._build_transport(httpx, httpcore, resolved)
        transport = self._bounded_transport(httpx, transport)
        read_timeout: Any = self.timeout_seconds
        if find_spec("mcp_types") is None:
            read_timeout = timedelta(seconds=self.timeout_seconds)
        async with httpx.AsyncClient(
            headers=headers,
            timeout=self.timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(endpoint, http_client=http_client) as streams:
                async with ClientSession(
                    streams[0], streams[1], read_timeout_seconds=read_timeout
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
            if isinstance(structured, Mapping) and set(structured) == {"result"}:
                return structured["result"]
            return structured
        return result.model_dump(mode="json")

    def _resolve_endpoint(self, endpoint: str) -> tuple[str, tuple[str, ...]]:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path
        ):
            raise ExternalMcpError("external MCP endpoint is invalid")
        try:
            port = parsed.port if parsed.port is not None else 443
        except ValueError:
            raise ExternalMcpError("external MCP endpoint is invalid") from None
        host = parsed.hostname
        if host is None:
            raise ExternalMcpError("external MCP endpoint is invalid")
        try:
            canonical_host = host.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError:
            raise ExternalMcpError("external MCP endpoint is invalid") from None
        if not canonical_host:
            raise ExternalMcpError("external MCP endpoint is invalid")
        try:
            literal = ipaddress.ip_address(canonical_host)
        except ValueError:
            literal = None
        if literal is not None:
            addresses: tuple[str, ...] = (str(literal),)
        else:
            try:
                infos = self._dns_resolver(canonical_host, port, type=socket.SOCK_STREAM)
            except (OSError, ValueError):
                raise ExternalMcpError("external MCP endpoint cannot be resolved") from None
            try:
                addresses = tuple(
                    sorted(
                        {
                            str(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
                            for info in infos
                            if len(info) >= 5 and info[4]
                        }
                    )
                )
            except (IndexError, ValueError):
                raise ExternalMcpError("external MCP endpoint cannot be resolved") from None
        if not addresses or any(
            not ipaddress.ip_address(address).is_global for address in addresses
        ):
            raise ExternalMcpError("external MCP endpoint resolves to a blocked address")
        return canonical_host, addresses

    def _build_transport(
        self, httpx: Any, httpcore: Any, resolved: tuple[str, tuple[str, ...]]
    ) -> Any:
        endpoint_host, addresses = resolved
        try:
            base = httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0)
            base._pool._network_backend = self._pinned_backend(httpcore, endpoint_host, addresses)
        except Exception:
            raise ExternalMcpError("external MCP transport unavailable") from None
        return base

    @staticmethod
    def _pinned_backend(httpcore: Any, endpoint_host: str, addresses: tuple[str, ...]) -> Any:
        try:
            backend_base = httpcore.AsyncNetworkBackend
            auto_backend = httpcore.AutoBackend
        except AttributeError:
            backend_base = import_module("httpcore._backends.base").AsyncNetworkBackend
            auto_backend = import_module("httpcore._backends.auto").AutoBackend

        class PinnedBackend(backend_base):  # type: ignore[misc, valid-type]
            def __init__(self) -> None:
                self._backend = auto_backend()

            async def connect_tcp(
                self,
                host: str,
                port: int,
                timeout: float | None = None,
                local_address: str | None = None,
                socket_options: Any = None,
            ) -> Any:
                try:
                    normalized = host.encode("idna").decode("ascii").lower().rstrip(".")
                except UnicodeError:
                    raise OSError("invalid outbound host") from None
                if normalized != endpoint_host:
                    raise OSError("outbound host changed during connection")
                last_error: Exception | None = None
                for address in addresses:
                    try:
                        return await self._backend.connect_tcp(
                            address,
                            port,
                            timeout=timeout,
                            local_address=local_address,
                            socket_options=socket_options,
                        )
                    except Exception as exc:  # pragma: no cover - depends on network stack
                        last_error = exc
                if last_error is not None:
                    raise last_error
                raise OSError("no outbound address available")

        return PinnedBackend()

    def _bounded_transport(self, httpx: Any, transport: Any) -> Any:
        max_bytes = self.max_response_bytes

        class LimitedStream(httpx.AsyncByteStream):
            def __init__(self, stream: Any) -> None:
                self._stream = stream
                self._size = 0

            async def __aiter__(self) -> Any:
                async for chunk in self._stream:
                    self._size += len(chunk)
                    if self._size > max_bytes:
                        raise _ExternalResponseTooLarge()
                    yield chunk

            async def aclose(self) -> None:
                await self._stream.aclose()

        class BoundedTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: Any) -> Any:
                response = await transport.handle_async_request(request)
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        too_large = int(content_length) > max_bytes
                    except ValueError:
                        too_large = False
                    if too_large:
                        await response.aclose()
                        raise _ExternalResponseTooLarge()
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    stream=LimitedStream(response.stream),
                    extensions=response.extensions,
                    request=request,
                )

            async def aclose(self) -> None:
                await transport.aclose()

        return BoundedTransport()


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
        except Exception:
            raise ExternalMcpError("credential broker unavailable") from None
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
