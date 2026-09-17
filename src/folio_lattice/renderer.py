from __future__ import annotations

import asyncio
import json
import math
import uuid
from collections.abc import Awaitable, Callable, Mapping
from html import escape
from urllib.parse import parse_qs, quote

from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import Message, Receive, Scope, Send

from .public_mcp import PublicMcpError, ToolCaller
from .resource_limits import BoundedConcurrencyLimiter, DimensionRateLimiter
from .sandbox import sandbox_headers

RENDERABLE_MEDIA_TYPES = {
    "application/javascript",
    "text/css",
    "text/html",
    "text/javascript",
}
DEFAULT_RENDERER_RATE_LIMIT = 600
DEFAULT_RENDERER_RATE_WINDOW_SECONDS = 60.0
DEFAULT_RENDERER_RATE_MAX_KEYS = 4096
DEFAULT_RENDERER_CONCURRENCY_LIMIT = 32
DEFAULT_RENDERER_CONCURRENCY_PER_KEY = 8
DEFAULT_RENDERER_TIMEOUT_SECONDS = 30.0
MAX_RENDERER_TIMEOUT_SECONDS = 300.0
DEFAULT_RENDERER_RESPONSE_BYTES = 1 * 1024 * 1024
MAX_RENDERER_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_RENDERER_PATH_BYTES = 1024


class _RendererResponseTooLarge(Exception):
    pass


def _base_media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def versioned_content_url(artifact_id: str, version_id: str) -> str:
    """Build the only asset URL form exposed by the isolated renderer.

    Asset links must retain both stable IDs.  Names are labels (and can be
    duplicated), while omitting the version would make an old HTML version
    silently load a later mutable pointer.  Keep URL construction in one place
    so renderer wrappers and agent-facing examples cannot drift apart.
    """

    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("artifact_id is required")
    if not isinstance(version_id, str) or not version_id:
        raise ValueError("version_id is required")
    return f"/content/{quote(artifact_id, safe='')}/{quote(version_id, safe='')}"


def _wrapper(artifact_id: str, version_id: str, media_type: str) -> str:
    source = versioned_content_url(artifact_id, version_id)
    if media_type == "text/css":
        head = f'<link rel="stylesheet" href="{escape(source, quote=True)}">'
        body = "<main><h1>Stylesheet preview</h1><p>Folio Lattice CSS artifact.</p><button>Button</button></main>"
        tail = (
            "<script>document.body.dataset.computedColor="
            "getComputedStyle(document.body).color</script>"
        )
    else:
        head = ""
        body = '<main><h1>JavaScript preview</h1><p id="output">Script loaded.</p></main>'
        tail = f'<script src="{escape(source, quote=True)}"></script>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Artifact preview</title>{head}</head>
<body>{body}{tail}</body></html>"""


class RendererApp:
    """Origin-isolated renderer that reads only through public MCP."""

    def __init__(
        self,
        caller: ToolCaller,
        *,
        control_origin: str,
        hsts_max_age: int = 0,
        timeout_seconds: float = DEFAULT_RENDERER_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_RENDERER_RESPONSE_BYTES,
        rate_limits: Mapping[str, int] | None = None,
        rate_window_seconds: float = DEFAULT_RENDERER_RATE_WINDOW_SECONDS,
        concurrency_limit: int = DEFAULT_RENDERER_CONCURRENCY_LIMIT,
        concurrency_per_key: int = DEFAULT_RENDERER_CONCURRENCY_PER_KEY,
    ):
        if (
            not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > MAX_RENDERER_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout_seconds is outside the allowed renderer bound")
        if max_response_bytes < 1 or max_response_bytes > MAX_RENDERER_RESPONSE_BYTES:
            raise ValueError("max_response_bytes is outside the allowed renderer bound")
        self.caller = caller
        self.headers = sandbox_headers(control_origin)
        self.hsts_max_age = hsts_max_age
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._rate_limiter = DimensionRateLimiter(
            limits=rate_limits or {"ip": DEFAULT_RENDERER_RATE_LIMIT},
            window_seconds=rate_window_seconds,
            max_keys=DEFAULT_RENDERER_RATE_MAX_KEYS,
        )
        self._concurrency_limiter = BoundedConcurrencyLimiter(
            limit=concurrency_limit,
            per_key_limit=concurrency_per_key,
            max_keys=DEFAULT_RENDERER_RATE_MAX_KEYS,
        )

    async def _error(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        code: str,
        message: str,
        request_id: str,
        retry_after: int | None = None,
    ) -> None:
        headers = dict(self.headers)
        if retry_after is not None:
            headers["Retry-After"] = str(max(0, retry_after))
        await JSONResponse(
            {"code": code, "error": message, "request_id": request_id},
            status_code=status_code,
            headers=headers,
        )(scope, receive, send)

    async def _bounded(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        operation: Callable[[], Awaitable[None]],
    ) -> None:
        request_id = uuid.uuid4().hex
        client = scope.get("client")
        ip = str(client[0]) if isinstance(client, (tuple, list)) and client else "unknown"
        allowed, retry_after = self._rate_limiter.allow({"ip": ip})
        if not allowed:
            await self._error(
                scope,
                receive,
                send,
                status_code=429,
                code="renderer_rate_limited",
                message="Too many render requests. Try again later.",
                request_id=request_id,
                retry_after=retry_after,
            )
            return
        if not self._concurrency_limiter.try_acquire(ip):
            await self._error(
                scope,
                receive,
                send,
                status_code=429,
                code="renderer_concurrency_limited",
                message="Renderer is busy. Try again later.",
                request_id=request_id,
                retry_after=1,
            )
            return
        try:
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    await operation()
            except TimeoutError:
                await self._error(
                    scope,
                    receive,
                    send,
                    status_code=504,
                    code="renderer_timeout",
                    message="Render request timed out. Try again later.",
                    request_id=request_id,
                    retry_after=1,
                )
            except _RendererResponseTooLarge:
                await self._error(
                    scope,
                    receive,
                    send,
                    status_code=502,
                    code="renderer_response_too_large",
                    message="Render response exceeds the allowed size.",
                    request_id=request_id,
                )
            except PublicMcpError as exc:
                await self._error(
                    scope,
                    receive,
                    send,
                    status_code=404 if str(exc).endswith("not found") else 400,
                    code="renderer_request_failed",
                    message=str(exc),
                    request_id=request_id,
                )
        finally:
            self._concurrency_limiter.release(ip)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)
            return

        async def response_send(message: Message) -> None:
            if message["type"] == "http.response.start" and scope.get("scheme") == "https":
                headers = list(message.get("headers", []))
                if self.hsts_max_age and not any(
                    key.lower() == b"strict-transport-security" for key, _ in headers
                ):
                    headers.append(
                        (
                            b"strict-transport-security",
                            f"max-age={self.hsts_max_age}; includeSubDomains".encode(),
                        )
                    )
                message["headers"] = headers
            await send(message)

        path = scope["path"]
        if path in {"/health", "/readyz"}:
            await self._bounded(
                scope,
                receive,
                response_send,
                lambda: self._health(scope, receive, response_send),
            )
            return
        if scope["method"] != "GET":
            await JSONResponse(
                {"error": "method not allowed"}, status_code=405, headers=self.headers
            )(scope, receive, response_send)
            return
        if path.startswith("/render/"):
            request_headers = {key.lower(): value.lower() for key, value in scope["headers"]}
            if request_headers.get(b"sec-fetch-dest") != b"iframe":
                await JSONResponse({"error": "not found"}, status_code=404, headers=self.headers)(
                    scope, receive, response_send
                )
                return
            artifact_id = path.removeprefix("/render/")
            if not artifact_id or "/" in artifact_id:
                await JSONResponse({"error": "not found"}, status_code=404, headers=self.headers)(
                    scope, receive, response_send
                )
                return
            query = parse_qs(scope["query_string"].decode())
            version_id = query.get("version_id", [None])[0]
            await self._bounded(
                scope,
                receive,
                response_send,
                lambda: self._render(scope, receive, response_send, artifact_id, version_id),
            )
            return
        if path.startswith("/content/"):
            parts = path.split("/")
            if len(parts) != 4 or not parts[2] or not parts[3]:
                await JSONResponse({"error": "not found"}, status_code=404, headers=self.headers)(
                    scope, receive, response_send
                )
                return
            await self._bounded(
                scope,
                receive,
                response_send,
                lambda: self._content(scope, receive, response_send, parts[2], parts[3]),
            )
            return
        await JSONResponse({"error": "not found"}, status_code=404, headers=self.headers)(
            scope, receive, response_send
        )

    async def _health(self, scope: Scope, receive: Receive, send: Send) -> None:
        ready = await self.caller.ready()
        health = {"status": "ok" if ready else "not_ready", "ready": ready}
        await JSONResponse(health, status_code=200 if ready else 503, headers=self.headers)(
            scope, receive, send
        )

    async def _render(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        artifact_id: str,
        version_id: str | None,
    ) -> None:
        if len(scope["raw_path"]) + len(scope["query_string"]) > MAX_RENDERER_PATH_BYTES:
            raise PublicMcpError("render request is too large")
        if len(artifact_id) > 255 or (version_id is not None and len(version_id) > 255):
            raise PublicMcpError("render identifier is too large")
        arguments = {"artifact_id": artifact_id}
        if version_id is not None:
            arguments["version_id"] = version_id
        read = await self.caller.call("artifact_read", arguments)
        self._check_response_size(read)
        resolved_version = read["version"]
        media_type = _base_media_type(resolved_version["media_type"])
        if media_type not in RENDERABLE_MEDIA_TYPES:
            await JSONResponse(
                {"error": f"unsupported render media type: {media_type}"},
                status_code=415,
                headers=self.headers,
            )(scope, receive, send)
            return
        if media_type == "text/html":
            await Response(
                content=read["text"].encode(), media_type="text/html", headers=self.headers
            )(scope, receive, send)
            return
        await HTMLResponse(
            _wrapper(artifact_id, resolved_version["id"], media_type), headers=self.headers
        )(scope, receive, send)

    async def _content(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        artifact_id: str,
        version_id: str,
    ) -> None:
        if len(scope["raw_path"]) + len(scope["query_string"]) > MAX_RENDERER_PATH_BYTES:
            raise PublicMcpError("content request is too large")
        if len(artifact_id) > 255 or len(version_id) > 255:
            raise PublicMcpError("content identifier is too large")
        read = await self.caller.call(
            "artifact_read", {"artifact_id": artifact_id, "version_id": version_id}
        )
        self._check_response_size(read)
        media_type = _base_media_type(read["version"]["media_type"])
        if media_type not in {"application/javascript", "text/css", "text/javascript"}:
            raise PublicMcpError("artifact content is not a render resource")
        await Response(content=read["text"].encode(), media_type=media_type, headers=self.headers)(
            scope, receive, send
        )

    def _check_response_size(self, value: object) -> None:
        try:
            size = len(json.dumps(value, separators=(",", ":"), allow_nan=False).encode())
        except (TypeError, ValueError) as exc:
            raise PublicMcpError("renderer response is invalid") from exc
        if size > self.max_response_bytes:
            raise _RendererResponseTooLarge
