from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import re
import socket
from collections.abc import Mapping
from datetime import timedelta
from importlib import import_module
from importlib.util import find_spec
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from .resource_limits import BoundedConcurrencyLimiter, DimensionRateLimiter
from .service import FolioError, FolioLattice

MAX_EXTERNAL_RESULT_BYTES = 1 * 1024 * 1024
MAX_EXTERNAL_HTTP_RESPONSE_BYTES = MAX_EXTERNAL_RESULT_BYTES
MAX_EXTERNAL_REQUEST_BYTES = 64 * 1024
MAX_EXTERNAL_TIMEOUT_SECONDS = 30.0
MAX_CREDENTIAL_SCAN_DEPTH = 32
MAX_CREDENTIAL_SCAN_NODES = 4_096
MAX_CREDENTIAL_SCAN_STRING_BYTES = 64 * 1024
MAX_CREDENTIAL_DECODE_PASSES = 2
EXTERNAL_RATE_LIMIT = 600
EXTERNAL_RATE_WINDOW_SECONDS = 60.0
EXTERNAL_RATE_MAX_KEYS = 4096
EXTERNAL_CONCURRENCY_LIMIT = 32
EXTERNAL_CONCURRENCY_PER_KEY = 4
_FACTORY_REFERENCE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_SAFE_EXTERNAL_ERRORS = frozenset(
    {
        "credential broker unavailable",
        "credential broker returned an invalid credential",
        "external MCP response exceeds the allowed size",
        "external MCP transport timed out",
        "external MCP transport unavailable",
        "external MCP upstream call failed",
        "external MCP endpoint resolution timed out",
        "external MCP endpoint is invalid",
        "external MCP endpoint resolves to a blocked address",
        "external MCP endpoint cannot be resolved",
        "external MCP transport cannot validate registration",
        "external MCP result is invalid",
        "external MCP result contains credential material",
        "external MCP result exceeds the allowed size",
        "external MCP health result is invalid",
        "external MCP tool call failed",
        "external MCP resource read failed",
        "external MCP health check failed",
        "external MCP connection is revoked",
    }
)


class ExternalMcpError(FolioError):
    """Safe public failure from the external MCP broker."""


class ExternalMcpValidationError(ExternalMcpError):
    """A trusted adapter rejected a bounded request or result."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        outcome: str = "failed",
        safe_message: bool = False,
    ) -> None:
        public_message = message if safe_message else "external MCP adapter validation failed"
        super().__init__(public_message)
        self.reason = (
            reason
            if isinstance(reason, str)
            and 1 <= len(reason) <= 64
            and all(
                character.isascii() and (character.isalnum() or character in "_.-")
                for character in reason
            )
            else "adapter_validation_failed"
        )
        self.outcome = outcome if outcome in {"denied", "failed"} else "failed"


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

    def validate_registration(self, endpoint: str) -> None: ...

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


def build_credential_broker(
    environ: Mapping[str, str] | None = None,
) -> CredentialBroker:
    """Load an explicitly injected provider-neutral credential broker."""

    env = os.environ if environ is None else environ
    reference = env.get("FOLIO_EXTERNAL_MCP_CREDENTIAL_BROKER_FACTORY")
    if reference is None or _FACTORY_REFERENCE.fullmatch(reference.strip()) is None:
        return UnavailableCredentialBroker()
    module_name, attribute_name = reference.strip().split(":", 1)
    try:
        factory = getattr(import_module(module_name), attribute_name)
        broker = factory()
    except Exception:
        return UnavailableCredentialBroker()
    if not callable(getattr(broker, "issue", None)):
        return UnavailableCredentialBroker()
    return broker


class UnavailableExternalMcpTransport:
    """Fail closed until a deployment supplies its bounded MCP transport adapter."""

    def validate_registration(self, endpoint: str) -> None:
        del endpoint
        raise ExternalMcpError("external MCP transport unavailable")

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
        if (
            not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > MAX_EXTERNAL_TIMEOUT_SECONDS
        ):
            raise ValueError("external MCP transport timeout is outside the allowed bound")
        if max_response_bytes < 1:
            raise ValueError("external MCP transport response bound must be positive")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._dns_resolver = dns_resolver or socket.getaddrinfo
        self._http_transport = http_transport

    def validate_registration(self, endpoint: str) -> None:
        """Reject unsafe destinations before the registry writes them."""
        try:
            asyncio.run(self._bounded_resolve(endpoint))
        except TimeoutError:
            raise ExternalMcpError("external MCP endpoint resolution timed out") from None

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
        self._check_request_size(arguments)
        return self._run(endpoint, credential, "tool", (tool_name, dict(arguments)))

    def read_resource(self, endpoint: str, resource_uri: str, *, credential: str | None) -> Any:
        if len(resource_uri.encode()) > 2_048:
            raise ExternalMcpError("external MCP resource URI exceeds the allowed size")
        return self._run(endpoint, credential, "resource", resource_uri)

    @staticmethod
    def _check_request_size(value: object) -> None:
        try:
            encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError):
            raise ExternalMcpError("external MCP request arguments are invalid") from None
        if len(encoded) > MAX_EXTERNAL_REQUEST_BYTES:
            raise ExternalMcpError("external MCP request arguments exceed the allowed size")

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

    async def _bounded_resolve(self, endpoint: str) -> tuple[str, tuple[str, ...]]:
        async with asyncio.timeout(self.timeout_seconds):
            return await asyncio.to_thread(self._resolve_endpoint, endpoint)

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

        # DNS resolution is blocking on common runtimes. Keep it off the event
        # loop so the surrounding end-to-end timeout can fail closed on a slow
        # or malicious resolver.
        resolved = await asyncio.to_thread(self._resolve_endpoint, endpoint)
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
        if canonical_host == "localhost" or canonical_host.endswith(".localhost"):
            raise ExternalMcpError("external MCP endpoint resolves to a blocked address")
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
            module_name = httpcore.__name__
            backend_base = import_module(f"{module_name}._backends.base").AsyncNetworkBackend
            auto_backend = import_module(f"{module_name}._backends.auto").AutoBackend

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
        rate_limits: Mapping[str, int] | None = None,
        rate_window_seconds: float = EXTERNAL_RATE_WINDOW_SECONDS,
        concurrency_limit: int = EXTERNAL_CONCURRENCY_LIMIT,
        concurrency_per_key: int = EXTERNAL_CONCURRENCY_PER_KEY,
    ):
        self.service = service
        self.credentials = credentials if credentials is not None else build_credential_broker()
        self.transport = transport or HttpExternalMcpTransport()
        self._rate_limiter = DimensionRateLimiter(
            limits=rate_limits
            or {
                "tenant": EXTERNAL_RATE_LIMIT,
                "actor": EXTERNAL_RATE_LIMIT,
                "connection": EXTERNAL_RATE_LIMIT,
            },
            window_seconds=rate_window_seconds,
            max_keys=EXTERNAL_RATE_MAX_KEYS,
        )
        self._concurrency_limiter = BoundedConcurrencyLimiter(
            limit=concurrency_limit,
            per_key_limit=concurrency_per_key,
            max_keys=EXTERNAL_RATE_MAX_KEYS,
        )

    def _admit(self, *, tenant_id: str, actor: str, connection_id: str, operation: str) -> str:
        allowed, retry_after = self._rate_limiter.allow(
            {"tenant": tenant_id, "actor": actor, "connection": connection_id}
        )
        if not allowed:
            raise ExternalMcpError(f"external MCP rate limited; retry after {retry_after} seconds")
        key = "\x1f".join((tenant_id, actor, connection_id, operation))
        if not self._concurrency_limiter.try_acquire(key):
            raise ExternalMcpError("external MCP concurrency limit exceeded")
        return key

    def _release(self, key: str) -> None:
        self._concurrency_limiter.release(key)

    def _record_external_call(
        self,
        tenant_id: str,
        connection_id: str,
        *,
        actor: str,
        action: str,
        outcome: str,
        reason: str = "unspecified",
        tool_name: str | None = None,
        resource_uri: str | None = None,
    ) -> bool:
        try:
            self.service.record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action=action,
                outcome=outcome,
                reason=reason,
                tool_name=tool_name,
                resource_uri=resource_uri,
            )
        except Exception:
            return False
        return True

    def _record_external_health(
        self, tenant_id: str, connection_id: str, *, actor: str, status: str
    ) -> bool:
        try:
            self.service.record_external_health(
                tenant_id, connection_id, actor=actor, status=status
            )
        except Exception:
            return False
        return True

    @staticmethod
    def _check_registration_bounds(
        *,
        name: str,
        endpoint: str,
        approved_tools: list[str],
        approved_resources: list[str],
        allowed_origins: list[str],
        policy: object,
    ) -> None:
        lists = (approved_tools, approved_resources, allowed_origins)
        if (
            not isinstance(name, str)
            or not isinstance(endpoint, str)
            or any(not isinstance(items, list) for items in lists)
            or any(not isinstance(value, str) for items in lists for value in items)
            or not isinstance(policy, Mapping)
        ):
            raise ExternalMcpError("external MCP registration is invalid")
        if len(name) > 255 or len(endpoint) > 4_096:
            raise ExternalMcpError("external MCP registration value is too large")
        if any(len(items) > 128 for items in (approved_tools, approved_resources, allowed_origins)):
            raise ExternalMcpError("external MCP registration list is too large")
        if any(
            len(value) > 2_048
            for items in (approved_tools, approved_resources, allowed_origins)
            for value in items
        ):
            raise ExternalMcpError("external MCP registration item is too large")
        try:
            encoded = json.dumps(
                {
                    "name": name,
                    "endpoint": endpoint,
                    "approved_tools": approved_tools,
                    "approved_resources": approved_resources,
                    "allowed_origins": allowed_origins,
                    "policy": policy,
                },
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        except (TypeError, ValueError):
            encoded = None
        if encoded is None:
            raise ExternalMcpError("external MCP registration is invalid")
        if len(encoded) > MAX_EXTERNAL_REQUEST_BYTES:
            raise ExternalMcpError("external MCP registration exceeds the allowed size")

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
        policy: Mapping[str, Any] | None = None,
        credential_ref: str | None = None,
        reason: str = "approved external MCP connection",
    ) -> dict[str, Any]:
        self._check_registration_bounds(
            name=name,
            endpoint=endpoint,
            approved_tools=approved_tools,
            approved_resources=approved_resources,
            allowed_origins=allowed_origins,
            policy={} if policy is None else policy,
        )
        key = self._admit(
            tenant_id=tenant_id, actor=actor, connection_id=name, operation="register"
        )
        try:
            return self._register_admitted(
                tenant_id=tenant_id,
                actor=actor,
                name=name,
                endpoint=endpoint,
                approved_tools=approved_tools,
                approved_resources=approved_resources,
                allowed_origins=allowed_origins,
                policy=policy,
                credential_ref=credential_ref,
                reason=reason,
            )
        finally:
            self._release(key)

    def _register_admitted(
        self,
        *,
        tenant_id: str,
        actor: str,
        name: str,
        endpoint: str,
        approved_tools: list[str],
        approved_resources: list[str],
        allowed_origins: list[str],
        policy: Mapping[str, Any] | None = None,
        credential_ref: str | None = None,
        reason: str = "approved external MCP connection",
    ) -> dict[str, Any]:
        validator = getattr(self.transport, "validate_registration", None)
        if not callable(validator):
            raise ExternalMcpError("external MCP transport cannot validate registration")
        validation_error: ExternalMcpError | None = None
        try:
            validator(endpoint)
        except ExternalMcpError as exc:
            validation_error = self._sanitize_external_error(
                exc, "external MCP endpoint validation failed"
            )
        except Exception:
            validation_error = ExternalMcpError("external MCP endpoint validation failed")
        if validation_error is not None:
            raise validation_error from None
        return self.service.register_external_connection(
            tenant_id=tenant_id,
            actor=actor,
            name=name,
            endpoint=endpoint,
            approved_tools=approved_tools,
            approved_resources=approved_resources,
            allowed_origins=allowed_origins,
            policy=policy,
            credential_ref=credential_ref,
            reason=reason,
        )

    def list_connections(
        self, *, tenant_id: str, actor: str, limit: int = 128
    ) -> list[dict[str, Any]]:
        key = self._admit(tenant_id=tenant_id, actor=actor, connection_id="list", operation="list")
        try:
            return self.service.list_external_connections(tenant_id, actor=actor, limit=limit)
        finally:
            self._release(key)

    def status(self, *, tenant_id: str, actor: str, connection_id: str) -> dict[str, Any]:
        key = self._admit(
            tenant_id=tenant_id, actor=actor, connection_id=connection_id, operation="status"
        )
        try:
            return self.service.external_connection_status(tenant_id, connection_id, actor=actor)
        finally:
            self._release(key)

    def health(
        self, *, tenant_id: str, actor: str, connection_id: str, probe: bool
    ) -> dict[str, Any]:
        key = self._admit(
            tenant_id=tenant_id, actor=actor, connection_id=connection_id, operation="health"
        )
        try:
            connection = self.service.external_connection_for_broker(
                tenant_id, connection_id, actor=actor
            )
            if not probe:
                return self.service.external_connection_status(
                    tenant_id, connection_id, actor=actor
                )
            if connection["status"] != "active":
                raise ExternalMcpError("external MCP connection is revoked")
            credential: str | None = None
            health_status: str = ""
            failure: ExternalMcpError | None = None
            try:
                credential = self._credential(connection)
                with self.service.external_call_fence(
                    tenant_id, connection_id, actor=actor
                ) as fenced:
                    health_status = self.transport.health(fenced["endpoint"], credential=credential)
                if health_status not in {"healthy", "unhealthy"}:
                    raise ExternalMcpError("external MCP health result is invalid")
            except ExternalMcpError as exc:
                if not self._record_external_health(
                    tenant_id, connection_id, actor=actor, status="unhealthy"
                ):
                    failure = ExternalMcpError("external MCP health check failed")
                else:
                    failure = self._sanitize_external_error(exc, "external MCP health check failed")
            except FolioError as exc:
                self._record_external_health(
                    tenant_id, connection_id, actor=actor, status="unhealthy"
                )
                if str(exc) == "external MCP connection is revoked":
                    failure = ExternalMcpError(str(exc))
                else:
                    failure = ExternalMcpError("external MCP health check failed")
            except Exception:
                self._record_external_health(
                    tenant_id, connection_id, actor=actor, status="unhealthy"
                )
                failure = ExternalMcpError("external MCP health check failed")
            finally:
                credential = None
            if failure is not None:
                raise failure from None
            if not self._record_external_health(
                tenant_id, connection_id, actor=actor, status=health_status
            ):
                raise ExternalMcpError("external MCP health check failed") from None
            return self.service.external_connection_status(tenant_id, connection_id, actor=actor)
        finally:
            self._release(key)

    def revoke(
        self, *, tenant_id: str, actor: str, connection_id: str, reason: str
    ) -> dict[str, Any]:
        key = self._admit(
            tenant_id=tenant_id, actor=actor, connection_id=connection_id, operation="revoke"
        )
        try:
            return self.service.revoke_external_connection(
                tenant_id, connection_id, actor=actor, reason=reason
            )
        finally:
            self._release(key)

    def audit(
        self, *, tenant_id: str, actor: str, connection_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        key = self._admit(
            tenant_id=tenant_id,
            actor=actor,
            connection_id=connection_id or "audit",
            operation="audit",
        )
        try:
            return self.service.external_mcp_audit(
                tenant_id, actor=actor, connection_id=connection_id, limit=limit
            )
        finally:
            self._release(key)

    def call_tool(
        self,
        *,
        tenant_id: str,
        actor: str,
        connection_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        pre_validator: Any | None = None,
        result_validator: Any | None = None,
    ) -> Any:
        HttpExternalMcpTransport._check_request_size(arguments)
        key = self._admit(
            tenant_id=tenant_id,
            actor=actor,
            connection_id=connection_id,
            operation="tool",
        )
        try:
            return self._call_tool_admitted(
                tenant_id=tenant_id,
                actor=actor,
                connection_id=connection_id,
                tool_name=tool_name,
                arguments=arguments,
                pre_validator=pre_validator,
                result_validator=result_validator,
            )
        finally:
            self._release(key)

    def authorize_tool(
        self,
        *,
        tenant_id: str,
        actor: str,
        connection_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Authorize an exact tool without contacting its upstream."""

        HttpExternalMcpTransport._check_request_size(arguments)
        return self.service.authorize_external_tool(
            tenant_id,
            connection_id,
            tool_name,
            actor=actor,
            arguments=arguments,
        )

    def _call_tool_admitted(
        self,
        *,
        tenant_id: str,
        actor: str,
        connection_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        pre_validator: Any | None = None,
        result_validator: Any | None = None,
    ) -> Any:
        connection = self.service.authorize_external_tool(
            tenant_id,
            connection_id,
            tool_name,
            actor=actor,
            arguments=arguments,
        )
        credential: str | None = None
        result: Any = None
        failure: ExternalMcpError | None = None
        try:
            if pre_validator is not None:
                try:
                    pre_validator(connection)
                except ExternalMcpValidationError:
                    raise
                except FolioError as exc:
                    raise ExternalMcpValidationError(
                        str(exc), reason="adapter_policy_denied", outcome="denied"
                    ) from None
                except Exception:
                    raise ExternalMcpValidationError(
                        "external MCP adapter validation failed",
                        reason="adapter_policy_denied",
                        outcome="denied",
                    ) from None
            credential = self._credential(connection)
            with self.service.external_call_fence(tenant_id, connection_id, actor=actor) as fenced:
                result = self.transport.call_tool(
                    fenced["endpoint"],
                    tool_name,
                    arguments,
                    credential=credential,
                )
                self._bounded_result(result, credential)
                if result_validator is not None:
                    try:
                        result_validator(result)
                    except ExternalMcpValidationError:
                        raise
                    except FolioError as exc:
                        raise ExternalMcpValidationError(
                            str(exc), reason="adapter_result_invalid", outcome="failed"
                        ) from None
                    except Exception:
                        raise ExternalMcpValidationError(
                            "external MCP result failed adapter validation",
                            reason="adapter_result_invalid",
                            outcome="failed",
                        ) from None
            if not self._record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="tool_call",
                outcome="allowed",
                reason="completed",
                tool_name=tool_name,
            ):
                failure = ExternalMcpError("external MCP tool call failed")
            else:
                return result
        except ExternalMcpValidationError as exc:
            self._record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="tool_call",
                outcome=exc.outcome,
                reason=exc.reason,
                tool_name=tool_name,
            )
            failure = ExternalMcpError(str(exc))
        except ExternalMcpError as exc:
            self._record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="tool_call",
                outcome="failed",
                reason=self._failure_reason(exc),
                tool_name=tool_name,
            )
            failure = self._sanitize_external_error(exc, "external MCP tool call failed")
        except FolioError as exc:
            self._record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="tool_call",
                outcome="failed",
                reason="authorization_failed",
                tool_name=tool_name,
            )
            if str(exc) == "external MCP connection is revoked":
                failure = ExternalMcpError(str(exc))
            else:
                failure = ExternalMcpError("external MCP tool call failed")
        except Exception:
            self._record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="tool_call",
                outcome="failed",
                reason="unexpected_failure",
                tool_name=tool_name,
            )
            failure = ExternalMcpError("external MCP tool call failed")
        finally:
            credential = None
        if failure is not None:
            raise failure from None
        return result

    def read_resource(
        self,
        *,
        tenant_id: str,
        actor: str,
        connection_id: str,
        resource_uri: str,
    ) -> Any:
        if len(resource_uri.encode()) > 2_048:
            raise ExternalMcpError("external MCP resource URI exceeds the allowed size")
        key = self._admit(
            tenant_id=tenant_id,
            actor=actor,
            connection_id=connection_id,
            operation="resource",
        )
        try:
            return self._read_resource_admitted(
                tenant_id=tenant_id,
                actor=actor,
                connection_id=connection_id,
                resource_uri=resource_uri,
            )
        finally:
            self._release(key)

    def _read_resource_admitted(
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
        result: Any = None
        failure: ExternalMcpError | None = None
        try:
            credential = self._credential(connection)
            with self.service.external_call_fence(tenant_id, connection_id, actor=actor) as fenced:
                result = self.transport.read_resource(
                    fenced["endpoint"], resource_uri, credential=credential
                )
                self._bounded_result(result, credential)
            if not self._record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="resource_read",
                outcome="allowed",
                reason="completed",
                resource_uri=resource_uri,
            ):
                failure = ExternalMcpError("external MCP resource read failed")
            else:
                return result
        except ExternalMcpError as exc:
            self._record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="resource_read",
                outcome="failed",
                reason=self._failure_reason(exc),
                resource_uri=resource_uri,
            )
            failure = self._sanitize_external_error(exc, "external MCP resource read failed")
        except FolioError as exc:
            self._record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="resource_read",
                outcome="failed",
                reason="authorization_failed",
                resource_uri=resource_uri,
            )
            if str(exc) == "external MCP connection is revoked":
                failure = ExternalMcpError(str(exc))
            else:
                failure = ExternalMcpError("external MCP resource read failed")
        except Exception:
            self._record_external_call(
                tenant_id,
                connection_id,
                actor=actor,
                action="resource_read",
                outcome="failed",
                reason="unexpected_failure",
                resource_uri=resource_uri,
            )
            failure = ExternalMcpError("external MCP resource read failed")
        finally:
            credential = None
        if failure is not None:
            raise failure from None
        return result

    @staticmethod
    def _sanitize_external_error(error: ExternalMcpError, fallback: str) -> ExternalMcpError:
        message = str(error)
        if message in _SAFE_EXTERNAL_ERRORS:
            return ExternalMcpError(message)
        return ExternalMcpError(fallback)

    @staticmethod
    def _failure_reason(error: ExternalMcpError) -> str:
        safe = {
            "credential broker unavailable": "credential_unavailable",
            "credential broker returned an invalid credential": "credential_invalid",
            "external MCP response exceeds the allowed size": "response_too_large",
            "external MCP transport timed out": "transport_timeout",
            "external MCP transport unavailable": "transport_unavailable",
            "external MCP upstream call failed": "upstream_failed",
            "external MCP result is invalid": "result_invalid",
            "external MCP result contains credential material": "result_secret_detected",
            "external MCP result exceeds the allowed size": "result_too_large",
            "external MCP connection is revoked": "connection_revoked",
        }
        return safe.get(str(error), "external_mcp_failed")

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
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise ExternalMcpError("external MCP result is invalid") from None
        if len(encoded) > MAX_EXTERNAL_RESULT_BYTES:
            raise ExternalMcpError("external MCP result exceeds the allowed size") from None
        if credential is not None and ExternalMcpBroker._contains_credential(result, credential):
            raise ExternalMcpError("external MCP result contains credential material") from None

    @staticmethod
    def _contains_credential(value: Any, credential: str) -> bool:
        try:
            if len(credential.encode("utf-8")) > MAX_CREDENTIAL_SCAN_STRING_BYTES:
                return True
        except (UnicodeError, AttributeError):
            return True
        stack: list[tuple[Any, int]] = [(value, 0)]
        nodes = 0
        while stack:
            current, depth = stack.pop()
            nodes += 1
            if nodes > MAX_CREDENTIAL_SCAN_NODES or depth > MAX_CREDENTIAL_SCAN_DEPTH:
                return True
            if isinstance(current, str):
                try:
                    if len(current.encode("utf-8")) > MAX_CREDENTIAL_SCAN_STRING_BYTES:
                        return True
                except UnicodeError:
                    return True
                candidate = current
                for decode_pass in range(MAX_CREDENTIAL_DECODE_PASSES + 1):
                    if credential in candidate:
                        return True
                    decoded = unquote(candidate)
                    if decoded == candidate:
                        break
                    if decode_pass == MAX_CREDENTIAL_DECODE_PASSES:
                        # Do not continue decoding past the bounded scan budget:
                        # an encoded credential may still be hidden at a deeper
                        # level, so reject the result conservatively.
                        return True
                    candidate = decoded
                try:
                    decoded_json = json.loads(current)
                except RecursionError:
                    return True
                except (TypeError, ValueError):
                    continue
                if decoded_json != current:
                    stack.append((decoded_json, depth + 1))
                continue
            if isinstance(current, bytes):
                if len(current) > MAX_CREDENTIAL_SCAN_STRING_BYTES:
                    return True
                if credential.encode("utf-8") in current:
                    return True
                try:
                    stack.append((current.decode("utf-8"), depth + 1))
                except UnicodeDecodeError:
                    return True
                continue
            if isinstance(current, Mapping):
                if len(current) > MAX_CREDENTIAL_SCAN_NODES:
                    return True
                stack.extend((item, depth + 1) for pair in current.items() for item in pair)
                continue
            if isinstance(current, (list, tuple, set, frozenset)):
                if len(current) > MAX_CREDENTIAL_SCAN_NODES:
                    return True
                stack.extend((item, depth + 1) for item in current)
        return False
