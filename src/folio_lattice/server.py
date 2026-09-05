from __future__ import annotations

import argparse
import hmac
import os
from dataclasses import dataclass
from typing import Any

import uvicorn
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .mcp_protocol import build_mcp_server
from .service import DEFAULT_MAX_ARTIFACT_BYTES, FolioLattice

DEFAULT_MAX_REQUEST_BYTES = 13 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    db_path: str
    blob_root: str
    tenant_id: str
    actor: str
    api_token: str | None
    max_artifact_bytes: int
    max_request_bytes: int

    @classmethod
    def from_env(cls) -> Settings:
        tenant_id = os.environ.get("FOLIO_TENANT_ID", "dev")
        actor = os.environ.get("FOLIO_ACTOR", "folio-client")
        if not tenant_id.strip() or not actor.strip():
            raise ValueError("FOLIO_TENANT_ID and FOLIO_ACTOR must not be empty")
        return cls(
            db_path=os.environ.get("FOLIO_DB_PATH", ".data/folio.db"),
            blob_root=os.environ.get("FOLIO_BLOB_ROOT", ".data/blobs"),
            tenant_id=tenant_id,
            actor=actor,
            api_token=os.environ.get("FOLIO_API_TOKEN"),
            max_artifact_bytes=_positive_env(
                "FOLIO_MAX_ARTIFACT_BYTES", DEFAULT_MAX_ARTIFACT_BYTES
            ),
            max_request_bytes=_positive_env("FOLIO_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES),
        )


def _positive_env(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


class BearerAuthApp:
    """Expose public health while authenticating every MCP request."""

    def __init__(self, app: ASGIApp, service: FolioLattice, token: str):
        self.app = app
        self.service = service
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)
            return
        if scope["path"] == "/health":
            health = self.service.health()
            status = 200 if health["ready"] else 503
            await JSONResponse(health, status_code=status)(scope, receive, send)
            return
        authorization = _header(scope, b"authorization")
        expected = f"Bearer {self.token}".encode()
        if authorization is None or not hmac.compare_digest(authorization, expected):
            await JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _header(scope: Scope, name: bytes) -> bytes | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value
    return None


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
    if not settings.api_token:
        raise ValueError("FOLIO_API_TOKEN is required for HTTP transport")
    service, mcp = build_runtime(settings)
    app = mcp.streamable_http_app(
        json_response=True,
        max_request_body_size=settings.max_request_bytes,
        host=host,
    )
    uvicorn.run(BearerAuthApp(app, service, settings.api_token), host=host, port=port)


def run_stdio() -> None:
    _, mcp = build_runtime(Settings.from_env())
    mcp.run("stdio")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("http", "stdio"), default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.transport == "stdio":
        run_stdio()
    else:
        run_http(args.host, args.port)


if __name__ == "__main__":
    main()
