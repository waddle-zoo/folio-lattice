"""Live loopback MCP upstream used by hosted conformance evidence.

The target is deliberately small. It is a real Streamable HTTP MCP server,
with an independent process and bearer check; it is not a fake transport.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

UPSTREAM_SECRET = "upstream-conformance-secret"
TOOL = "calendar.events.list"
RESOURCE = "calendar://events/today"


def _authorization(scope: dict[str, Any]) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            return value.decode("latin-1")
    return None


def app() -> Any:
    from mcp.server import MCPServer
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    server = MCPServer("approved-conformance-upstream", version="1")

    @server.tool(name=TOOL, description="Return one bounded approved-upstream result.")
    def events_list(limit: int = 5) -> dict[str, Any]:
        return {
            "upstream": "approved-conformance",
            "events": [{"id": "event-1", "title": "Conformance event"}][:limit],
        }

    @server.tool(name="calendar.events.slow", description="Sleep past the broker timeout.")
    def events_slow() -> dict[str, str]:
        time.sleep(2)
        return {"upstream": "approved-conformance", "status": "late"}

    @server.tool(name="calendar.events.oversize", description="Return an oversized result.")
    def events_oversize() -> dict[str, str]:
        return {"upstream": "approved-conformance", "payload": "x" * (1024 * 1024 + 1)}

    @server.resource(RESOURCE, name="today", description="Return today's approved resource.")
    def events_today() -> str:
        return "approved conformance resource"

    mcp_app = server.streamable_http_app(json_response=True)

    async def health(_: Any) -> Any:
        return JSONResponse({"ready": True, "service": "approved-conformance"})

    @asynccontextmanager
    async def lifespan(_: Any):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    base = Starlette(
        lifespan=lifespan,
        routes=[Route("/health", health, methods=["GET"]), Mount("/", app=mcp_app)],
    )

    async def authenticated(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("path") == "/mcp" and _authorization(scope) != f"Bearer {UPSTREAM_SECRET}":
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        await base(scope, receive, send)

    return authenticated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    asyncio.run(_serve(args.host, args.port))


async def _serve(host: str, port: int) -> None:
    import uvicorn

    config = uvicorn.Config(app(), host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    main()
