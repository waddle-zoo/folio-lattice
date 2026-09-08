from __future__ import annotations

import argparse
import os
import string
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .bridge import AttachedMcpBridge
from .inspection import InspectionApp
from .mcp_protocol import build_mcp_server
from .public_mcp import HttpMcpClient
from .renderer import RendererApp
from .service import DEFAULT_MAX_ARTIFACT_BYTES, FolioLattice

DEFAULT_MAX_REQUEST_BYTES = 13 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    db_path: str
    blob_root: str
    tenant_id: str
    actor: str
    max_artifact_bytes: int
    max_request_bytes: int
    control_origin: str
    render_origin: str
    mcp_url: str | None
    deployment_mode: str
    bridge_timeout_seconds: float

    @classmethod
    def from_env(cls) -> Settings:
        deployment_mode = os.environ.get("FOLIO_DEPLOYMENT_MODE", "local").strip().lower()
        if deployment_mode not in {"local", "hosted"}:
            raise ValueError("FOLIO_DEPLOYMENT_MODE must be local or hosted")
        if deployment_mode == "hosted":
            raise ValueError("hosted mode requires an authentication adapter; none is implemented")
        tenant_id = os.environ.get("FOLIO_TENANT_ID", "dev")
        actor = os.environ.get("FOLIO_ACTOR", "folio-client")
        if not tenant_id.strip() or not actor.strip():
            raise ValueError("FOLIO_TENANT_ID and FOLIO_ACTOR must not be empty")
        return cls(
            db_path=os.environ.get("FOLIO_DB_PATH", ".data/folio.db"),
            blob_root=os.environ.get("FOLIO_BLOB_ROOT", ".data/blobs"),
            tenant_id=tenant_id,
            actor=actor,
            max_artifact_bytes=_positive_env(
                "FOLIO_MAX_ARTIFACT_BYTES", DEFAULT_MAX_ARTIFACT_BYTES
            ),
            max_request_bytes=_positive_env("FOLIO_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES),
            control_origin=_origin_env("FOLIO_CONTROL_ORIGIN", "http://127.0.0.1:8000"),
            render_origin=_origin_env("FOLIO_RENDER_ORIGIN", "http://127.0.0.1:8001"),
            mcp_url=_optional_mcp_url_env("FOLIO_MCP_URL"),
            deployment_mode=deployment_mode,
            bridge_timeout_seconds=_positive_float_env("FOLIO_BRIDGE_TIMEOUT_SECONDS", 5),
        )


def _positive_env(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float_env(name: str, default: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _origin_env(name: str, default: str) -> str:
    value = os.environ.get(name, default).rstrip("/")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(character not in string.ascii_letters + string.digits + ".:-" for character in host)
    ):
        raise ValueError(f"{name} must be an HTTP origin without a path")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be an HTTP origin without a path") from exc
    bracketed_host = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{bracketed_host}{f':{port}' if port is not None else ''}"


def _optional_mcp_url_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/mcp"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an HTTP URL ending in /mcp without credentials")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be an HTTP URL ending in /mcp without credentials") from exc
    return value


class FolioHttpApp:
    """Add readiness and inspection routes to the SDK's MCP application."""

    def __init__(
        self,
        app: ASGIApp,
        service: FolioLattice,
        inspection: InspectionApp,
        *,
        deployment_mode: str,
    ):
        self.app = app
        self.service = service
        self.inspection = inspection
        self.deployment_mode = deployment_mode

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)
            return
        if scope["path"] == "/health":
            health = self.service.health()
            health.update(
                {
                    "deployment_mode": self.deployment_mode,
                    "authentication": "none-local-development",
                }
            )
            status = 200 if health["ready"] else 503
            await JSONResponse(health, status_code=status)(scope, receive, send)
            return
        path = scope["path"]
        if (
            path == "/"
            or path.startswith("/inspect/")
            or path in {"/api/mcp", "/api/bridge", "/ui.css", "/ui.js"}
        ):
            await self.inspection(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_runtime(settings: Settings) -> tuple[FolioLattice, Any]:
    service = FolioLattice(
        settings.db_path,
        settings.blob_root,
        max_artifact_bytes=settings.max_artifact_bytes,
    )
    return service, build_mcp_server(
        service,
        tenant_id=settings.tenant_id,
        actor=settings.actor,
    )


def run_http(host: str, port: int) -> None:
    settings = Settings.from_env()
    service, mcp = build_runtime(settings)
    app = mcp.streamable_http_app(
        json_response=True,
        max_request_body_size=settings.max_request_bytes,
        host=host,
    )
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    caller = HttpMcpClient(
        settings.mcp_url or f"http://{connect_host}:{port}/mcp",
        max_result_bytes=settings.max_request_bytes,
    )
    inspection = InspectionApp(
        caller,
        control_origin=settings.control_origin,
        render_origin=settings.render_origin,
        max_request_bytes=settings.max_request_bytes,
        bridge=AttachedMcpBridge(caller, timeout_seconds=settings.bridge_timeout_seconds),
        organization=settings.tenant_id,
        actor=settings.actor,
    )
    uvicorn.run(
        FolioHttpApp(
            app,
            service,
            inspection,
            deployment_mode=settings.deployment_mode,
        ),
        host=host,
        port=port,
    )


def run_renderer(host: str, port: int) -> None:
    settings = Settings.from_env()
    caller = HttpMcpClient(
        settings.mcp_url or "http://127.0.0.1:8000/mcp",
        max_result_bytes=settings.max_request_bytes,
    )
    app = RendererApp(
        caller,
        control_origin=settings.control_origin,
    )
    uvicorn.run(app, host=host, port=port)


def run_stdio() -> None:
    _, mcp = build_runtime(Settings.from_env())
    mcp.run("stdio")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("http", "stdio", "renderer"), default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.transport == "stdio":
        run_stdio()
    elif args.transport == "renderer":
        run_renderer(args.host, args.port)
    else:
        run_http(args.host, args.port)


if __name__ == "__main__":
    main()
