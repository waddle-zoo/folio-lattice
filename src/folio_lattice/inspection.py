from __future__ import annotations

import json
from html import escape
from typing import Any

from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import Receive, Scope, Send

from .service import FolioError, FolioLattice

CONTROL_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

UI_CSS = """
:root { color-scheme: light dark; font: 15px/1.45 system-ui, sans-serif; }
body { margin: 0; }
header, main { max-width: 1100px; margin: auto; padding: 1rem; }
header { display: flex; gap: .6rem; align-items: center; border-bottom: 1px solid #8885; }
header form { display: flex; gap: .4rem; flex: 1; }
input, textarea, button { font: inherit; }
input, textarea { box-sizing: border-box; padding: .55rem; }
input { flex: 1; }
textarea { width: 100%; min-height: 18rem; font-family: ui-monospace, monospace; }
.grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 1rem; }
section { min-width: 0; }
iframe { width: 100%; min-height: 28rem; border: 1px solid #8888; background: white; }
pre { overflow: auto; padding: .7rem; border: 1px solid #8885; white-space: pre-wrap; }
.error { color: #c33; }
label { display: block; margin: .6rem 0; }
@media (max-width: 760px) { .grid { grid-template-columns: 1fr; } }
""".strip()

UI_JS = r"""
const byId = (id) => document.getElementById(id);
const parts = location.pathname.split('/').filter(Boolean);
const artifactId = parts[0] === 'inspect' ? decodeURIComponent(parts[1] || '') : '';
const renderOrigin = document.body.dataset.renderOrigin;

byId('open').addEventListener('submit', (event) => {
  event.preventDefault();
  const id = byId('artifact-id').value.trim();
  if (id) location.assign(`/inspect/${encodeURIComponent(id)}`);
});

async function loadArtifact() {
  byId('artifact-id').value = artifactId;
  if (!artifactId) {
    byId('workspace').hidden = true;
    return;
  }
  const response = await fetch(`/api/artifacts/${encodeURIComponent(artifactId)}`);
  const state = await response.json();
  if (!response.ok) throw new Error(state.error || `read failed (${response.status})`);
  byId('title').textContent = `${state.artifact.name} (${state.artifact.media_type})`;
  const editable = Object.hasOwn(state.read, 'text');
  byId('content').value = state.read.text ?? '';
  byId('content').disabled = !editable;
  byId('save').disabled = !editable;
  if (!editable) byId('status').textContent = 'Binary content is read-only in this slice.';
  byId('media-type').value = state.read.version.media_type;
  byId('parent-version').value = state.read.version.id;
  byId('versions').textContent = JSON.stringify(state.versions, null, 2);
  byId('graph').textContent = JSON.stringify(state.graph, null, 2);
  const query = new URLSearchParams({version_id: state.read.version.id});
  byId('preview').src = `${renderOrigin}/render/${encodeURIComponent(artifactId)}?${query}`;
}

byId('edit').addEventListener('submit', async (event) => {
  event.preventDefault();
  byId('status').textContent = 'Saving…';
  const response = await fetch(`/api/artifacts/${encodeURIComponent(artifactId)}/versions`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      parent_version_id: byId('parent-version').value,
      media_type: byId('media-type').value,
      reason: byId('reason').value,
      text: byId('content').value,
    }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `write failed (${response.status})`);
  byId('status').textContent = `Saved ${result.id}`;
  await loadArtifact();
});

loadArtifact().catch((error) => {
  byId('status').textContent = error.message;
  byId('status').className = 'error';
});
""".strip()


def ui_html(render_origin: str) -> str:
    origin = escape(render_origin, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Folio Lattice inspection</title>
  <link rel="stylesheet" href="/ui.css">
</head>
<body data-render-origin="{origin}">
  <header>
    <strong>Folio Lattice</strong>
    <form id="open"><label for="artifact-id">Artifact</label><input id="artifact-id" required><button>Open</button></form>
  </header>
  <main id="workspace">
    <h1 id="title">Artifact inspection</h1>
    <p id="status" role="status"></p>
    <div class="grid">
      <section>
        <h2>Edit current text</h2>
        <form id="edit">
          <input id="parent-version" type="hidden">
          <label>Media type <input id="media-type" required maxlength="255"></label>
          <label>Reason <input id="reason" value="inspection UI edit" required maxlength="2000"></label>
          <label for="content">Content</label>
          <textarea id="content" spellcheck="false"></textarea>
          <button id="save" type="submit">Save new version</button>
        </form>
        <h2>Versions</h2><pre id="versions"></pre>
        <h2>Outgoing graph</h2><pre id="graph"></pre>
      </section>
      <section>
        <h2>Isolated preview</h2>
        <iframe id="preview" title="Artifact preview" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>
      </section>
    </div>
  </main>
  <script src="/ui.js"></script>
</body>
</html>"""


def control_headers(render_origin: str) -> dict[str, str]:
    return {
        **CONTROL_HEADERS,
        "Content-Security-Policy": "; ".join(
            [
                "default-src 'none'",
                "base-uri 'none'",
                "connect-src 'self'",
                "form-action 'self'",
                "frame-ancestors 'none'",
                f"frame-src {render_origin}",
                "img-src 'none'",
                "object-src 'none'",
                "script-src 'self'",
                "style-src 'self'",
            ]
        ),
    }


async def _body(receive: Receive, maximum: int) -> bytes:
    body = bytearray()
    more = True
    while more:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise FolioError("request disconnected")
        body.extend(message.get("body", b""))
        if len(body) > maximum:
            raise OverflowError
        more = message.get("more_body", False)
    return bytes(body)


def _status_for(error: FolioError) -> int:
    message = str(error)
    if message.endswith("not found"):
        return 404
    if message.startswith("parent version mismatch"):
        return 409
    return 400


class InspectionApp:
    """Two private JSON operations and one static inspection page."""

    def __init__(
        self,
        service: FolioLattice,
        *,
        tenant_id: str,
        actor: str,
        render_origin: str,
        max_request_bytes: int,
    ):
        self.service = service
        self.tenant_id = tenant_id
        self.actor = actor
        self.render_origin = render_origin
        self.max_request_bytes = max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        method = scope["method"]
        path = scope["path"]
        headers = control_headers(self.render_origin)
        if method == "GET" and (path == "/" or path.startswith("/inspect/")):
            await HTMLResponse(ui_html(self.render_origin), headers=headers)(scope, receive, send)
            return
        if method == "GET" and path == "/ui.css":
            await Response(UI_CSS, media_type="text/css", headers=headers)(scope, receive, send)
            return
        if method == "GET" and path == "/ui.js":
            await Response(UI_JS, media_type="application/javascript", headers=headers)(
                scope, receive, send
            )
            return

        parts = path.split("/")
        if len(parts) >= 4 and parts[1:3] == ["api", "artifacts"] and parts[3]:
            artifact_id = parts[3]
            try:
                if method == "GET" and len(parts) == 4:
                    payload = self._read(artifact_id)
                    await JSONResponse(payload, headers=CONTROL_HEADERS)(scope, receive, send)
                    return
                if method == "POST" and parts[4:] == ["versions"]:
                    content_type = (
                        dict(scope["headers"]).get(b"content-type", b"").decode("latin-1")
                    )
                    if content_type.split(";", 1)[0].strip().lower() != "application/json":
                        raise FolioError("Content-Type must be application/json")
                    raw = await _body(receive, self.max_request_bytes)
                    try:
                        payload = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise FolioError("request body must be valid JSON") from exc
                    result = self._write(artifact_id, payload)
                    await JSONResponse(result, status_code=201, headers=CONTROL_HEADERS)(
                        scope, receive, send
                    )
                    return
            except OverflowError:
                await JSONResponse(
                    {"error": "request body too large"}, status_code=413, headers=CONTROL_HEADERS
                )(scope, receive, send)
                return
            except FolioError as exc:
                await JSONResponse(
                    {"error": str(exc)}, status_code=_status_for(exc), headers=CONTROL_HEADERS
                )(scope, receive, send)
                return
            except (KeyError, TypeError):
                await JSONResponse(
                    {"error": "request body has invalid fields"},
                    status_code=400,
                    headers=CONTROL_HEADERS,
                )(scope, receive, send)
                return

        await JSONResponse({"error": "not found"}, status_code=404, headers=CONTROL_HEADERS)(
            scope, receive, send
        )

    def _read(self, artifact_id: str) -> dict[str, Any]:
        return {
            "artifact": self.service.get_artifact(self.tenant_id, artifact_id),
            "read": self.service.read_artifact(self.tenant_id, artifact_id),
            "versions": self.service.versions(self.tenant_id, artifact_id),
            "graph": self.service.traverse(self.tenant_id, artifact_id, max_depth=2, limit=100),
        }

    def _write(self, artifact_id: str, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise FolioError("request body must be a JSON object")
        if "text" not in self.service.read_artifact(self.tenant_id, artifact_id):
            raise FolioError("inspection UI edits text artifacts only")
        text = payload["text"]
        parent_version_id = payload["parent_version_id"]
        media_type = payload["media_type"]
        reason = payload.get("reason", "inspection UI edit")
        if not all(
            isinstance(value, str) for value in (text, parent_version_id, media_type, reason)
        ):
            raise FolioError("text, parent_version_id, media_type, and reason must be strings")
        return self.service.write_version(
            tenant_id=self.tenant_id,
            artifact_id=artifact_id,
            data=text.encode(),
            media_type=media_type,
            actor=self.actor,
            reason=reason,
            source_context={"interface": "inspection-ui"},
            parent_version_id=parent_version_id,
        )
