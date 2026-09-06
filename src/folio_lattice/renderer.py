from __future__ import annotations

from html import escape
from urllib.parse import parse_qs, quote

from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import Receive, Scope, Send

from .public_mcp import PublicMcpError, ToolCaller
from .sandbox import sandbox_headers

RENDERABLE_MEDIA_TYPES = {
    "application/javascript",
    "text/css",
    "text/html",
    "text/javascript",
}


def _base_media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _wrapper(artifact_id: str, version_id: str, media_type: str) -> str:
    source = f"/content/{quote(artifact_id, safe='')}/{quote(version_id, safe='')}"
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

    def __init__(self, caller: ToolCaller, *, control_origin: str):
        self.caller = caller
        self.headers = sandbox_headers(control_origin)

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
        path = scope["path"]
        if path == "/health":
            ready = await self.caller.ready()
            health = {"status": "ok" if ready else "not_ready", "ready": ready}
            await JSONResponse(health, status_code=200 if ready else 503)(scope, receive, send)
            return
        if scope["method"] != "GET":
            await JSONResponse(
                {"error": "method not allowed"}, status_code=405, headers=self.headers
            )(scope, receive, send)
            return
        try:
            if path.startswith("/render/"):
                artifact_id = path.removeprefix("/render/")
                if not artifact_id or "/" in artifact_id:
                    raise PublicMcpError("artifact not found")
                query = parse_qs(scope["query_string"].decode())
                version_id = query.get("version_id", [None])[0]
                await self._render(scope, receive, send, artifact_id, version_id)
                return
            if path.startswith("/content/"):
                parts = path.split("/")
                if len(parts) != 4 or not parts[2] or not parts[3]:
                    raise PublicMcpError("artifact not found")
                await self._content(scope, receive, send, parts[2], parts[3])
                return
        except PublicMcpError as exc:
            status = 404 if str(exc).endswith("not found") else 400
            await JSONResponse({"error": str(exc)}, status_code=status, headers=self.headers)(
                scope, receive, send
            )
            return
        await JSONResponse({"error": "not found"}, status_code=404, headers=self.headers)(
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
        arguments = {"artifact_id": artifact_id}
        if version_id is not None:
            arguments["version_id"] = version_id
        read = await self.caller.call("artifact_read", arguments)
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
        read = await self.caller.call(
            "artifact_read", {"artifact_id": artifact_id, "version_id": version_id}
        )
        media_type = _base_media_type(read["version"]["media_type"])
        if media_type not in {"application/javascript", "text/css", "text/javascript"}:
            raise PublicMcpError("artifact content is not a render resource")
        await Response(content=read["text"].encode(), media_type=media_type, headers=self.headers)(
            scope, receive, send
        )
