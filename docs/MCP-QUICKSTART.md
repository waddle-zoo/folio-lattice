# Agent quickstart: MCP artifact contract

Use the existing artifact and graph tools directly; there is no separate asset
catalog. Tenant and actor identity come from the authenticated server context,
not tool arguments. Retain artifact IDs from results: filenames are labels and
need not be unique.

The two-process local stack requires one shared
`FOLIO_RENDERER_CAPABILITY_SECRET` (at least 32 characters) in both the
control and renderer processes. The renderer signs a short-lived capability
for each MCP read; the control server verifies its tenant, actor, read scope,
artifact ID, and version ID before the read. Do not put a tenant, actor, or
capability token in an asset URL. This handoff is local-only: the renderer
keeps a bounded local revocation denylist, while every control-side read
re-checks current tenant/actor ACLs. Hosted renderer mode is intentionally
unsupported and fails closed because this process-local revocation contract is
not a hosted session-revocation mechanism.

## Root and component semantics

A graph has no permanently designated root. A `start_artifact_id` or
`graph_root_artifact_id` is the readable artifact chosen as the anchor for one
operation.

- `graph_link` creates a directed edge from `source_artifact_id` to
  `target_artifact_id`.
- `graph_traverse` follows only outgoing edges from its start artifact. It
  returns bounded edge records up to `max_depth`; it does not define membership
  in a whole graph.
- `graph_component` treats readable edges as undirected and returns the
  bounded connected set, including its start artifact. It returns at most 500
  readable artifacts and neither exposes nor crosses an inaccessible artifact.
  Any readable member of the same component may be used as the start.
- `artifact_search(graph_root_artifact_id=...)` applies the same readable,
  undirected component rule and searches names, media types, and current
  bodies only inside that component. The root is an anchor, not an additional
  query term.
- `artifact_grep` is a tenant- and ACL-scoped literal body scan; it currently
  has no graph-root parameter. Use `artifact_search` when server-enforced graph
  scoping is required.

`artifact_search` is the primary discovery operation. It performs one
case-insensitive substring search across the artifact filename, media type,
and current body, then returns at most one result per stable `artifact_id`.
Each result includes the current `version_id`, `match_kind`, `match_kinds`,
`snippet`, `path`, `graph_path`, `graph_context`, and `updated_at` (plus the artifact's name
and media type). A `graph_root_artifact_id` restricts the same operation to
that root's readable connected component; the root is an anchor, not a query
term. A `cursor` continues the same query after the final result's
`<updated_at>|<artifact_id>`.

`graph_path` is empty for an unscoped library search. For a graph-scoped
search it contains only the readable root-to-result nodes, each with a stable
artifact ID, name, and path; no edge counts or hidden neighbors are returned.

`artifact_grep` remains the literal current-body scan and has no graph-root
parameter. `artifact_list` remains available for catalog listing and
backward-compatible clients, but is not the primary metadata discovery
contract. Both operations are ACL- and tenant-scoped by server context.

## Copyable end-to-end graph recipe

The following is one small flow. Replace each angle-bracket value with the
value captured from the immediately preceding response. The server generates
stable artifact and version IDs; filenames are labels, not identity. Do not
send `tenant_id` or `actor` in these requests: both come from authenticated
server context and are returned or applied by the server.

For a disposable demo or regression, run this flow against fresh isolated
server state and include a UUID in each marker/name. Never inspect private
storage to discover IDs or infer tenant boundaries; retain only the IDs and
versions returned by the public contract.

### 1. Create five bodies and capture IDs

`content_base64` is the standard base64 encoding of the complete body bytes.
These are separate `artifact_create` calls, not one multi-file request. Create
CSS and JavaScript before HTML so the HTML body can retain exact immutable
asset URLs. Capture both IDs immediately after every response:

```python
import base64

def b64(body):
    return base64.b64encode(body).decode()

root = await call("artifact_create", {
    "name": "agent-graph.md",
    "media_type": "text/markdown",
    "content_base64": b64(b"# Agent graph\nquickstart body marker\n"),
    "reason": "agent quickstart",
})
markdown_artifact_id = root["artifact"]["id"]
markdown_version_id = root["version"]["id"]

css = await call("artifact_create", {
    "name": "agent-graph.css",
    "media_type": "text/css",
    "content_base64": b64(b"body { color: navy; }\n"),
    "reason": "agent quickstart",
})
css_artifact_id = css["artifact"]["id"]
css_version_id = css["version"]["id"]

javascript = await call("artifact_create", {
    "name": "agent-graph.js",
    "media_type": "application/javascript",
    "content_base64": b64(
        b"document.body.dataset.marker = 'agent-quickstart-fixture';\n"
    ),
    "reason": "agent quickstart",
})
js_artifact_id = javascript["artifact"]["id"]
js_version_id = javascript["version"]["id"]

html_body = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><title>Agent graph</title>
<link rel="stylesheet" href="/content/{css_artifact_id}/{css_version_id}">
</head><body><main><h1>Agent graph</h1></main>
<script src="/content/{js_artifact_id}/{js_version_id}"></script>
</body></html>""".encode()
html = await call("artifact_create", {
    "name": "agent-graph.html",
    "media_type": "text/html",
    "content_base64": b64(html_body),
    "reason": "agent quickstart",
})
html_artifact_id = html["artifact"]["id"]
html_version_id = html["version"]["id"]

binary = await call("artifact_create", {
    "name": "agent-graph.bin",
    "media_type": "application/octet-stream",
    "content_base64": b64(b"\x00\x01\x02\xff"),
    "reason": "agent quickstart",
})
binary_artifact_id = binary["artifact"]["id"]
binary_version_id = binary["version"]["id"]
```

Each call returns the same shape. The server generates stable IDs; capture
them and never reconstruct identity from a filename:

```json
{
  "artifact": {
    "id": "<markdown_artifact_id>",
    "name": "agent-graph.md",
    "media_type": "text/markdown",
    "current_version_id": "<markdown_version_id>"
  },
  "version": {
    "id": "<markdown_version_id>",
    "artifact_id": "<markdown_artifact_id>",
    "parent_version_id": null
  }
}
```

The `document.body.dataset.marker` assignment above is intentional fixture
plumbing for the executable regression, not product semantics. If a fixture
also checks visible status text, label that assertion as fixture-only.

An HTML artifact does not resolve `href="agent-graph.css"` or
`src="agent-graph.js"` by filename. Names are labels and may be duplicated.

The two URLs are intentionally version-pinned: a later write creates a new
version and never changes what an older HTML version loads. The renderer serves
these URLs only after the authenticated MCP
`artifact_read` applies the verified renderer capability plus tenant and ACL
checks; missing artifacts, mismatched artifact/version pairs, cross-tenant
IDs, and denied artifacts fail closed.
Do not substitute an artifact name, a mutable `render/<id>` URL, a remote URL,
or a guessed version.

### 2. Link the Markdown root to every asset

Call `graph_link` once for each object. The edge is directed from the Markdown
root, while component membership is readable in either direction.

```json
{"source_artifact_id":"<markdown_artifact_id>","target_artifact_id":"<html_artifact_id>","edge_type":"references"}
```

```json
{"source_artifact_id":"<markdown_artifact_id>","target_artifact_id":"<css_artifact_id>","edge_type":"references"}
```

```json
{"source_artifact_id":"<markdown_artifact_id>","target_artifact_id":"<js_artifact_id>","edge_type":"references"}
```

```json
{"source_artifact_id":"<markdown_artifact_id>","target_artifact_id":"<binary_artifact_id>","edge_type":"references"}
```

### 3. Discover metadata and bodies with artifact_search

Use the same `artifact_search` contract for a filename label and a media type.
The query is case-insensitive and searches the name, media type, and current
body; `match_kind` identifies one dimension or `multiple`, and `match_kinds`
retains all matching dimensions. These requests use the exact filename and
media type values as queries:

```json
{"query":"agent-graph.html","limit":20}
```

```json
{"query":"text/html","limit":20}
```

Both responses are lists. A filename result has `match_kind: "name"`; a
media-type result has `match_kind: "media_type"`. Retain the stable IDs from
the response rather than resolving a filename later:

```json
[
  {
    "artifact_id":"<html_artifact_id>",
    "version_id":"<html_version_id>",
    "artifact_name":"agent-graph.html",
    "name":"agent-graph.html",
    "media_type":"text/html",
    "match_kind":"name",
    "match_kinds":["name"],
    "snippet":"[agent-graph.html]",
    "chunk_id":null,
    "score":null,
    "path":"agent-graph.html",
    "graph_context":{"root_artifact_id":null,"scoped":false},
    "graph_path":[],
    "graph_root_artifact_id":null,
    "updated_at":"<timestamp>"
  }
]
```

The `text/html` request returns the same fields with
`match_kind: "media_type"` and a media-type snippet:

```json
[
  {
    "artifact_id":"<html_artifact_id>",
    "version_id":"<html_version_id>",
    "artifact_name":"agent-graph.html",
    "name":"agent-graph.html",
    "media_type":"text/html",
    "match_kind":"media_type",
    "match_kinds":["media_type"],
    "snippet":"[text/html]",
    "path":"agent-graph.html",
    "graph_context":{"root_artifact_id":null,"scoped":false},
    "graph_path":[],
    "updated_at":"<timestamp>"
  }
]
```

For a bounded page, retain the same query and pass the final result's
`updated_at|artifact_id` as `cursor`. The response remains a list:

```json
{"query":"text/html","limit":2,"cursor":"<last_page_item.updated_at>|<last_page_item.artifact_id>"}
```

For backward-compatible catalog listing, `artifact_list` still accepts exact
metadata filters. Use it to enumerate a list, not as the primary discovery
operation:

```json
{"name":"agent-graph.html","media_type":"text/html","limit":20}
```

Use `artifact_search` for body discovery scoped to the readable component
anchored at the Markdown artifact. The response keeps the same metadata and
graph fields while setting `match_kind` to `"body"`:

```json
{"query":"quickstart body marker","graph_root_artifact_id":"<markdown_artifact_id>","limit":20}
```

```json
[
  {
    "artifact_id":"<markdown_artifact_id>",
    "version_id":"<markdown_version_id>",
    "artifact_name":"agent-graph.md",
    "name":"agent-graph.md",
    "media_type":"text/markdown",
    "match_kind":"body",
    "match_kinds":["body"],
    "snippet":"[quickstart body marker]",
    "chunk_id":"<markdown_chunk_id>",
    "score":0.0,
    "path":"agent-graph.md",
    "graph_context":{"root_artifact_id":"<markdown_artifact_id>","scoped":true},
    "graph_path":[
      {"artifact_id":"<markdown_artifact_id>","name":"agent-graph.md","path":"agent-graph.md"}
    ],
    "graph_root_artifact_id":"<markdown_artifact_id>",
    "updated_at":"<timestamp>"
  }
]
```

Use `artifact_grep` when the desired operation is a literal current-body
match, without graph-root scoping:

```json
{"pattern":"quickstart body marker","limit":20}
```

```json
[
  {"id":"<chunk_id>","artifact_id":"<markdown_artifact_id>","version_id":"<markdown_version_id>","ordinal":0,"content":"# Agent graph\nquickstart body marker\n","match":"quickstart body marker","offset_unit":"unicode_code_points"}
]
```

### 4. Inspect component and directed traversal

`graph_component` returns the bounded readable connected set, treating
readable edges as undirected and including the start artifact. Any member can
be the next start. `graph_traverse` follows only outgoing edges, returns edge
records, and does not return the start as a node.

```json
{"start_artifact_id":"<markdown_artifact_id>","limit":100}
```

```json
[
  {"id":"<markdown_artifact_id>","name":"agent-graph.md","media_type":"text/markdown","current_version_id":"<markdown_version_id>"},
  {"id":"<html_artifact_id>","name":"agent-graph.html","media_type":"text/html","current_version_id":"<html_version_id>"}
]
```

```json
{"start_artifact_id":"<markdown_artifact_id>","max_depth":1,"limit":100}
```

```json
[
  {"source_artifact_id":"<markdown_artifact_id>","target_artifact_id":"<html_artifact_id>","edge_type":"references","depth":1}
]
```

### 5. Read a body and its chunks

Read by stable artifact ID. A full text read includes `text`; a binary read
uses `content_base64` and has no text field.

```json
{"artifact_id":"<html_artifact_id>","version_id":"<html_version_id>"}
```

```json
{
  "artifact":{"id":"<html_artifact_id>","name":"agent-graph.html","current_version_id":"<html_version_id>"},
  "version":{"id":"<html_version_id>","artifact_id":"<html_artifact_id>"},
  "chunks":[{"id":"<html_chunk_id>","artifact_id":"<html_artifact_id>","version_id":"<html_version_id>","ordinal":0,"offset_unit":"unicode_code_points"}],
  "content_base64":"PGgxPkFnZW50IGdyYXBoPC9oMT4K",
  "text":"<h1>Agent graph</h1>\n"
}
```

Read a returned chunk by `chunk_id`. The chunk payload field is
`result.content`, not `result.text`:

```json
{"chunk_id":"<html_chunk_id>"}
```

```json
{"result":{"id":"<html_chunk_id>","artifact_id":"<html_artifact_id>","version_id":"<html_version_id>","ordinal":0,"start_offset":0,"end_offset":22,"content":"<h1>Agent graph</h1>\n","offset_unit":"unicode_code_points"}}
```

The MCP client may expose the outer structured result as
`response.structured_content["result"]`; in either client shape read the
inner `content` field. Do not substitute a `text` field for a chunk.

### 6. Write version 2 and enumerate history

Writes keep the same artifact ID and require the current version as
`parent_version_id`. They create a new immutable version:

```json
{"artifact_id":"<markdown_artifact_id>","parent_version_id":"<markdown_version_id>","content_base64":"IyBBZ2VudCBncmFwaApxdWlja3N0YXJ0IGJvZHkgbWFya2VyIHJldmlzZWQK","media_type":"text/markdown","reason":"agent quickstart v2"}
```

```json
{"id":"<markdown_version_2_id>","artifact_id":"<markdown_artifact_id>","parent_version_id":"<markdown_version_id>","media_type":"text/markdown"}
```

The artifact ID is unchanged; `current_version_id` becomes
`<markdown_version_2_id>`. `artifact_versions` returns the ordered 1 → 2
history:

```json
{"artifact_id":"<markdown_artifact_id>","limit":100}
```

```json
[
  {"id":"<markdown_version_id>","artifact_id":"<markdown_artifact_id>","parent_version_id":null},
  {"id":"<markdown_version_2_id>","artifact_id":"<markdown_artifact_id>","parent_version_id":"<markdown_version_id>"}
]
```

### 7. Control standalone, renderer, and bridge

Open the control-origin human route with the HTML artifact ID:

```text
GET <control_origin>/standalone/<html_artifact_id>
```

The HTML response is a control page whose sandboxed preview loads the renderer.
For a direct iframe request, include the browser Fetch Metadata header and pin
the exact immutable version:

```text
GET <render_origin>/render/<html_artifact_id>?version_id=<html_version_id>
Sec-Fetch-Dest: iframe
```

The HTML/CSS/JS renderer response includes `Content-Security-Policy` with
`default-src 'none'`, `connect-src 'none'`, and `sandbox allow-scripts`, plus
`X-Content-Type-Options: nosniff`. It is not a second artifact API. Arbitrary
binary artifacts can be read and linked but renderer requests for them return
`415`.

An attached MCP may call the artifact it is attached to through the bridge:

```json
{"request_id":"req-1","artifact_id":"<markdown_artifact_id>","attachment":"folio-lattice","tool":"artifact_read","arguments":{"artifact_id":"<markdown_artifact_id>"}}
```

```json
{"result":{"artifact":{"id":"<markdown_artifact_id>"},"version":{"id":"<markdown_version_2_id>"}}}
```

The bridge denies an out-of-graph target and a filename/ambiguous duplicate;
neither is an identity substitute:

```json
{"request_id":"req-2","artifact_id":"<markdown_artifact_id>","attachment":"folio-lattice","tool":"artifact_read","arguments":{"artifact_id":"<unlinked_artifact_id>"}}
```

```json
{"error":"bridge request does not target its attached artifact"}
```

```json
{"request_id":"req-3","artifact_id":"<markdown_artifact_id>","attachment":"folio-lattice","tool":"artifact_read","arguments":{"artifact_id":"agent-graph.md"}}
```

The duplicate-name denial has the same error shape: pass the stable artifact ID
returned by create, not a filename.

## Executable boundary proof

`tests/public_contract_quickstart.py` runs this same flow against fresh local
HTTP state using only MCP and renderer endpoints. It creates a unique Markdown
root linked to HTML, CSS, JavaScript, and binary artifacts; verifies stable
artifact/version IDs, unified filename/media-type/body discovery with bounded
search cursors, component and graph-root scope, backward-compatible listing,
literal grep, chunk reads, renderer headers, and the attached-MCP allow path.
It also verifies bridge rejection for an out-of-graph artifact ID and for a
filename used as an ambiguous duplicate-name target. The renderer regression
also loads an HTML version containing the exact CSS/JavaScript `/content`
links, reads the pinned old versions after a write creates new versions, and
rejects missing, mismatched, cross-tenant, and unauthorized asset IDs.
