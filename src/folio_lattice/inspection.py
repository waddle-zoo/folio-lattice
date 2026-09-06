from __future__ import annotations

import json
from html import escape

from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import Receive, Scope, Send

from .bridge import (
    MAX_BRIDGE_BODY_BYTES,
    AttachedMcpBridge,
    BridgeRequestError,
    validate_bridge_request,
)
from .public_mcp import PUBLIC_TOOLS, PublicMcpError, ToolCaller

CONTROL_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

UI_CSS = """
:root { color-scheme: light dark; font: 15px/1.45 system-ui, sans-serif; }
body { margin: 0; }
header, main, .notice { max-width: 1120px; margin: auto; padding: 1rem; }
header { display: flex; gap: .8rem; align-items: end; border-bottom: 1px solid #8885; }
header form { display: flex; gap: .4rem; align-items: end; flex: 1; }
input, textarea, button { font: inherit; }
input, textarea { box-sizing: border-box; padding: .5rem; max-width: 100%; }
textarea { width: 100%; min-height: 12rem; font-family: ui-monospace, monospace; }
button { cursor: pointer; padding: .5rem .7rem; }
button:focus-visible, input:focus-visible, textarea:focus-visible, iframe:focus-visible {
  outline: 3px solid #579dff; outline-offset: 2px;
}
.notice { box-sizing: border-box; background: #7a410020; border: 1px solid #b66b32; }
.grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 1rem; }
.row { display: flex; gap: .5rem; align-items: end; flex-wrap: wrap; }
.row > label { flex: 1 1 12rem; }
section { min-width: 0; border-top: 1px solid #8885; margin-top: 1rem; }
iframe { width: 100%; min-height: 32rem; border: 1px solid #8888; background: white; }
pre { overflow: auto; padding: .7rem; border: 1px solid #8885; white-space: pre-wrap; }
label { display: block; margin: .55rem 0; }
label > input { display: block; width: 100%; }
ul { padding-left: 1.3rem; }
li { margin: .35rem 0; }
.error { color: #c33; font-weight: 600; }
.muted { color: #777; }
dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: .25rem .8rem; }
dt { font-weight: 600; }
dd { margin: 0; overflow-wrap: anywhere; }
[hidden] { display: none !important; }
@media (max-width: 760px) { .grid { grid-template-columns: 1fr; } header { display: block; } }
""".strip()

UI_JS = r"""
const byId = (id) => document.getElementById(id);
const parts = location.pathname.split('/').filter(Boolean);
const artifactId = parts[0] === 'inspect' ? decodeURIComponent(parts[1] || '') : '';
const renderOrigin = document.body.dataset.renderOrigin;

function status(message) {
  byId('error').hidden = true;
  byId('status').textContent = message;
}
function failure(message) {
  byId('status').textContent = '';
  byId('error').textContent = message;
  byId('error').hidden = false;
  byId('error').focus();
}
function busy(value) {
  byId('main').setAttribute('aria-busy', String(value));
  document.querySelectorAll('button[type="submit"]').forEach((item) => { item.disabled = value; });
}
async function call(tool, args, endpoint = '/api/mcp') {
  busy(true);
  try {
    const response = await fetch(endpoint, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(endpoint === '/api/bridge' ? args : {tool, arguments: args}),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(result.error || `Request failed (${response.status})`);
      error.status = response.status; throw error;
    }
    return result;
  } finally { busy(false); }
}
function list(id, items, render, empty) {
  const target = byId(id); target.replaceChildren();
  if (!items.length) {
    const item = document.createElement('li'); item.className = 'muted';
    item.textContent = empty; target.append(item); return;
  }
  items.forEach((value) => target.append(render(value)));
}
function button(label, action) {
  const value = document.createElement('button'); value.type = 'button';
  value.textContent = label; value.addEventListener('click', action); return value;
}
function base64(bytes) {
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  }
  return btoa(binary);
}
function showRead(read) {
  const artifact = read.artifact; const version = read.version;
  byId('title').textContent = artifact.name;
  byId('artifact-details').textContent = '';
  const values = [
    ['Artifact', artifact.id], ['Media type', version.media_type], ['Version', version.id],
    ['SHA-256', version.blob_hash], ['Bytes', String(version.byte_size)],
    ['Actor', version.actor], ['Reason', version.reason], ['Created', version.created_at],
  ];
  values.forEach(([term, description]) => {
    const dt = document.createElement('dt'); dt.textContent = term;
    const dd = document.createElement('dd'); dd.textContent = description;
    byId('artifact-details').append(dt, dd);
  });
  const editable = Object.hasOwn(read, 'text');
  byId('content').value = read.text ?? '';
  byId('content').disabled = !editable; byId('save').disabled = !editable;
  byId('media-type').value = version.media_type;
  byId('parent-version').value = version.id; byId('binary-note').hidden = editable;
  list('chunks', read.chunks || [], (chunk) => {
    const li = document.createElement('li');
    li.append(button(`Chunk ${chunk.ordinal + 1}: ${chunk.start_offset}–${chunk.end_offset}`, async () => {
      try {
        status('Reading chunk…');
        const value = await call('artifact_read_chunk', {chunk_id: chunk.id});
        byId('chunk-content').textContent = value.content;
        status(`Read chunk ${chunk.ordinal + 1}; offsets are Unicode code points.`);
      } catch (error) { failure(error.message); }
    })); return li;
  }, 'No text chunks for this version.');
  const query = new URLSearchParams({version_id: version.id});
  byId('preview').src = `${renderOrigin}/render/${encodeURIComponent(artifact.id)}?${query}`;
}
async function loadArtifact(versionId = null) {
  if (!artifactId) {
    byId('workspace').hidden = true;
    status('Create or upload an artifact, or open one by identifier.'); return;
  }
  byId('artifact-id').value = artifactId; status('Loading artifact…');
  const args = {artifact_id: artifactId}; if (versionId) args.version_id = versionId;
  const [read, versions, graph] = await Promise.all([
    call('artifact_read', args),
    call('artifact_versions', {artifact_id: artifactId, limit: 100}),
    call('graph_traverse', {start_artifact_id: artifactId, max_depth: 2, limit: 100}),
  ]);
  byId('workspace').hidden = false; showRead(read);
  list('versions', versions, (version) => {
    const li = document.createElement('li');
    const label = `${version.created_at} — ${version.reason}${version.id === read.version.id ? ' (shown)' : ''}`;
    li.append(button(label, () => loadArtifact(version.id).catch((error) => failure(error.message))));
    return li;
  }, 'No versions found.');
  list('graph', graph, (edge) => {
    const li = document.createElement('li');
    li.append(`${edge.edge_type} → `, button(edge.target_artifact_id, () => {
      location.assign(`/inspect/${encodeURIComponent(edge.target_artifact_id)}`);
    })); return li;
  }, 'No outgoing relationships.');
  status(`Loaded ${read.artifact.name}.`);
}

byId('open').addEventListener('submit', (event) => {
  event.preventDefault(); const id = byId('artifact-id').value.trim();
  if (id) location.assign(`/inspect/${encodeURIComponent(id)}`);
});
byId('create-file').addEventListener('change', () => {
  const file = byId('create-file').files[0]; if (!file) return;
  if (!byId('create-name').value) byId('create-name').value = file.name;
  if (file.type) byId('create-media').value = file.type;
});
byId('create').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    status('Creating the first immutable version…');
    const file = byId('create-file').files[0];
    const bytes = file ? new Uint8Array(await file.arrayBuffer()) : new TextEncoder().encode(byId('create-text').value);
    const created = await call('artifact_create', {
      name: byId('create-name').value, media_type: byId('create-media').value || null,
      reason: byId('create-reason').value, content_base64: base64(bytes),
      source_context: {interface: 'inspection-ui'},
    });
    location.assign(`/inspect/${encodeURIComponent(created.artifact.id)}`);
  } catch (error) { failure(error.message); }
});
async function discover(tool, field, inputId) {
  try {
    status(tool === 'artifact_search' ? 'Searching indexed content…' : 'Running literal grep…');
    const results = await call(tool, {[field]: byId(inputId).value, limit: 20});
    list('results', results, (result) => {
      const li = document.createElement('li'); const excerpt = result.snippet || result.content || '';
      li.append(button(result.artifact_id, () => location.assign(`/inspect/${encodeURIComponent(result.artifact_id)}`)));
      const span = document.createElement('span'); span.textContent = ` — ${excerpt.slice(0, 240)}`;
      li.append(span); return li;
    }, 'No results.'); status(`${results.length} result${results.length === 1 ? '' : 's'}.`);
  } catch (error) { failure(error.message); }
}
byId('search').addEventListener('submit', (event) => {
  event.preventDefault(); discover('artifact_search', 'query', 'search-query');
});
byId('grep').addEventListener('submit', (event) => {
  event.preventDefault(); discover('artifact_grep', 'pattern', 'grep-pattern');
});
byId('edit').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    status('Saving a new immutable version…');
    const written = await call('artifact_write', {
      artifact_id: artifactId, parent_version_id: byId('parent-version').value,
      media_type: byId('media-type').value, reason: byId('reason').value,
      content_base64: base64(new TextEncoder().encode(byId('content').value)),
      source_context: {interface: 'inspection-ui'},
    });
    status(`Saved new version ${written.id}.`); await loadArtifact();
  } catch (error) {
    if (error.status === 409) failure('A newer version already exists. Refresh before saving again; your edit remains here.');
    else failure(error.message);
  }
});
byId('link').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    status('Creating relationship…');
    await call('graph_link', {source_artifact_id: artifactId,
      target_artifact_id: byId('target-id').value, edge_type: byId('edge-type').value,
      metadata: {}});
    await loadArtifact(); status('Relationship created. Traversal follows outgoing edges only.');
  } catch (error) { failure(error.message); }
});
addEventListener('message', async (event) => {
  const frame = byId('preview');
  if (event.origin !== 'null' || event.source !== frame.contentWindow) return;
  const value = event.data;
  if (!value || typeof value !== 'object' || value.type !== 'folio.mcp.request' ||
      typeof value.id !== 'string' || typeof value.attachment !== 'string' ||
      typeof value.tool !== 'string' || !value.arguments || typeof value.arguments !== 'object') return;
  const source = event.source;
  try {
    byId('bridge-status').textContent = `Attached artifact requested ${value.tool}…`;
    const response = await call('', {request_id: value.id, artifact_id: artifactId,
      attachment: value.attachment, tool: value.tool, arguments: value.arguments}, '/api/bridge');
    source.postMessage({type: 'folio.mcp.response', id: value.id, ok: true, result: response.result}, '*');
    byId('bridge-status').textContent = `Allowed attached tool ${value.tool}.`;
  } catch (error) {
    source.postMessage({type: 'folio.mcp.response', id: value.id, ok: false, error: error.message}, '*');
    byId('bridge-status').textContent = `Denied or failed attached tool ${value.tool}.`;
  }
});

loadArtifact().catch((error) => failure(error.message));
""".strip()


def ui_html(render_origin: str) -> str:
    origin = escape(render_origin, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Folio Lattice inspection</title><link rel="stylesheet" href="/ui.css"></head>
<body data-render-origin="{origin}">
<p class="notice" role="note"><strong>Unauthenticated local development.</strong> Do not expose this service to an untrusted network.</p>
<header><strong>Folio Lattice</strong><form id="open"><label for="artifact-id">Open artifact identifier<input id="artifact-id" required maxlength="255"></label><button type="submit">Open</button></form></header>
<main id="main" aria-busy="false"><p id="status" role="status" aria-live="polite"></p><p id="error" class="error" role="alert" tabindex="-1" hidden></p>
<details open><summary>Create or upload an artifact</summary><form id="create">
<div class="row"><label>Name<input id="create-name" required maxlength="255"></label><label>Media type<input id="create-media" maxlength="255" placeholder="text/plain"></label></div>
<label>File (optional)<input id="create-file" type="file"></label><label>Text content when no file is selected<textarea id="create-text"></textarea></label>
<label>Reason<input id="create-reason" value="inspection UI create" required maxlength="2000"></label><button type="submit">Create first version</button></form></details>
<section aria-labelledby="discover-title"><h2 id="discover-title">Find content</h2><div class="grid">
<form id="search"><label>Indexed content search<input id="search-query" required maxlength="500"></label><button type="submit">Search</button></form>
<form id="grep"><label>Literal grep (not regular expressions)<input id="grep-pattern" required maxlength="500"></label><button type="submit">Grep</button></form></div><ul id="results"><li class="muted">No search run yet.</li></ul></section>
<article id="workspace" hidden><h1 id="title">Artifact</h1><dl id="artifact-details"></dl><div class="grid">
<section aria-labelledby="edit-title"><h2 id="edit-title">Edit current text</h2><p id="binary-note" hidden>Binary content is metadata-only and cannot be edited as text.</p>
<form id="edit"><input id="parent-version" type="hidden"><label>Media type<input id="media-type" required maxlength="255"></label>
<label>Reason<input id="reason" value="inspection UI edit" required maxlength="2000"></label><label for="content">Content</label><textarea id="content" spellcheck="false"></textarea><button id="save" type="submit">Save new version</button></form>
<h2>Chunks</h2><ul id="chunks"></ul><pre id="chunk-content">Choose a chunk to read it.</pre><h2>Version history</h2><ul id="versions"></ul>
<h2>Outgoing graph</h2><p>Navigation and traversal follow outgoing edges only.</p><ul id="graph"></ul>
<form id="link"><div class="row"><label>Target artifact identifier<input id="target-id" required maxlength="255"></label><label>Relationship type<input id="edge-type" value="references" required maxlength="100"></label></div><button type="submit">Create relationship</button></form></section>
<section aria-labelledby="preview-title"><h2 id="preview-title">Isolated untrusted preview</h2><p>Direct network, host access, navigation, popups, forms, storage, and cookies are denied. Attached Folio capability: read, indexed search, and outgoing traversal only.</p>
<p id="bridge-status" role="status">No attached tool call yet.</p><iframe id="preview" title="Untrusted artifact preview" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe></section>
</div></article></main><script src="/ui.js"></script></body></html>"""


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
            raise PublicMcpError("request disconnected")
        body.extend(message.get("body", b""))
        if len(body) > maximum:
            raise OverflowError
        more = message.get("more_body", False)
    return bytes(body)


def _error_status(error: Exception) -> int:
    message = str(error)
    if isinstance(error, BridgeRequestError) and error.reason == "capability_not_attached":
        return 403
    if message.endswith("not found"):
        return 404
    if "parent version mismatch" in message:
        return 409
    if message.endswith("timed out"):
        return 504
    if "unavailable" in message or "result exceeds" in message:
        return 502
    return 400


class InspectionApp:
    """Static core-loop UI over one bounded public-MCP adapter."""

    def __init__(
        self,
        caller: ToolCaller,
        *,
        control_origin: str,
        render_origin: str,
        max_request_bytes: int,
        bridge: AttachedMcpBridge | None = None,
    ):
        self.caller = caller
        self.control_origin = control_origin
        self.render_origin = render_origin
        self.max_request_bytes = max_request_bytes
        self.bridge = bridge or AttachedMcpBridge(caller)

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
        if method == "POST" and path in {"/api/mcp", "/api/bridge"}:
            await self._api(scope, receive, send, bridge=path == "/api/bridge")
            return
        await JSONResponse({"error": "not found"}, status_code=404, headers=CONTROL_HEADERS)(
            scope, receive, send
        )

    async def _api(self, scope: Scope, receive: Receive, send: Send, *, bridge: bool) -> None:
        headers = {key.lower(): value for key, value in scope["headers"]}
        origin = headers.get(b"origin", b"").decode("latin-1")
        if origin != self.control_origin:
            if bridge:
                self.bridge.audit_rejection("origin_mismatch")
            await JSONResponse(
                {"error": "request origin is not permitted"},
                status_code=403,
                headers=CONTROL_HEADERS,
            )(scope, receive, send)
            return
        content_type = headers.get(b"content-type", b"").decode("latin-1")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            if bridge:
                self.bridge.audit_rejection("invalid_content_type")
            await JSONResponse(
                {"error": "Content-Type must be application/json"},
                status_code=400,
                headers=CONTROL_HEADERS,
            )(scope, receive, send)
            return
        try:
            maximum = (
                min(self.max_request_bytes, MAX_BRIDGE_BODY_BYTES)
                if bridge
                else self.max_request_bytes
            )
            raw = await _body(receive, maximum)
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BridgeRequestError("request body must be valid JSON") from exc
            if bridge:
                request = validate_bridge_request(payload)
                result = await self.bridge.call(request)
                response = {"request_id": request["request_id"], "result": result}
            else:
                if not isinstance(payload, dict) or set(payload) != {"tool", "arguments"}:
                    raise PublicMcpError("MCP request has invalid fields")
                tool = payload["tool"]
                arguments = payload["arguments"]
                if not isinstance(tool, str) or tool not in PUBLIC_TOOLS:
                    raise PublicMcpError("tool is not part of the Folio MCP contract")
                if not isinstance(arguments, dict):
                    raise PublicMcpError("arguments must be a JSON object")
                response = await self.caller.call(tool, arguments)
            await JSONResponse(response, headers=CONTROL_HEADERS)(scope, receive, send)
        except OverflowError:
            if bridge:
                self.bridge.audit_rejection("oversized_body")
            await JSONResponse(
                {"error": "request body too large"}, status_code=413, headers=CONTROL_HEADERS
            )(scope, receive, send)
        except (BridgeRequestError, PublicMcpError) as exc:
            if (
                bridge
                and isinstance(exc, BridgeRequestError)
                and exc.reason != "capability_not_attached"
            ):
                self.bridge.audit_rejection(exc.reason)
            await JSONResponse(
                {"error": str(exc)}, status_code=_error_status(exc), headers=CONTROL_HEADERS
            )(scope, receive, send)
        except Exception:
            await JSONResponse(
                {"error": "request failed safely"}, status_code=502, headers=CONTROL_HEADERS
            )(scope, receive, send)
