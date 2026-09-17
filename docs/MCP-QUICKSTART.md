# Agent quickstart: MCP artifact contract

Use the existing artifact and graph tools directly; there is no separate asset
catalog. Tenant and actor identity come from the authenticated server context,
not tool arguments. Retain artifact IDs from results: filenames are labels and
need not be unique.

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
  undirected component rule and searches current indexed bodies only inside
  that component. The root is an anchor, not an additional query term.
- `artifact_grep` is a tenant- and ACL-scoped literal body scan; it currently
  has no graph-root parameter. Use `artifact_search` when server-enforced graph
  scoping is required.

Metadata is separate from body search. `artifact_list` accepts optional exact
`name` and `media_type` filters, applied before its 1–100 result bound.
`artifact_search` and `artifact_grep` intentionally do not index filenames or
media types. To continue a full page, pass the final item's
`<updated_at>|<id>` as `cursor` while retaining the same filters. The response
remains a list; an empty or short page ends enumeration.

## Copyable end-to-end graph recipe

The following is one small flow. Replace each angle-bracket value with the
value captured from the immediately preceding response. The server generates
stable artifact and version IDs; filenames are labels, not identity. Do not
send `tenant_id` or `actor` in these requests: both come from authenticated
server context and are returned or applied by the server.

### 1. Create each body separately

`content_base64` is the standard base64 encoding of the complete body bytes.
These are five separate `artifact_create` calls, not one multi-file request.

```json
{"name":"agent-graph.md","media_type":"text/markdown","content_base64":"IyBBZ2VudCBncmFwaApxdWlja3N0YXJ0IGJvZHkgbWFya2VyCg==","reason":"agent quickstart"}
```

```json
{"name":"agent-graph.html","media_type":"text/html","content_base64":"PGgxPkFnZW50IGdyYXBoPC9oMT4K","reason":"agent quickstart"}
```

```json
{"name":"agent-graph.css","media_type":"text/css","content_base64":"Ym9keSB7IGNvbG9yOiBuYXZ5OyB9Cg==","reason":"agent quickstart"}
```

```json
{"name":"agent-graph.js","media_type":"application/javascript","content_base64":"ZG9jdW1lbnQuYm9keS5kYXRhc2V0LnJlYWR5ID0gJ3llcyc7Cg==","reason":"agent quickstart"}
```

```json
{"name":"agent-graph.bin","media_type":"application/octet-stream","content_base64":"AAEC/w==","reason":"agent quickstart"}
```

Each call returns the same shape. Capture the IDs; never reconstruct them from
the name:

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

Store the corresponding values from the other four responses as
`html_artifact_id`/`html_version_id`, `css_artifact_id`/`css_version_id`,
`js_artifact_id`/`js_version_id`, and `binary_artifact_id`/`binary_version_id`.

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

### 3. Discover metadata, then search bodies

Metadata discovery and indexed body search are distinct operations. Use
`artifact_list` for exact metadata; search and grep do not search filenames or
media types.

```json
{"name":"agent-graph.html","media_type":"text/html","limit":20}
```

The response is a list, including the stable ID and current version:

```json
[
  {"id":"<html_artifact_id>","name":"agent-graph.html","media_type":"text/html","current_version_id":"<html_version_id>","updated_at":"<timestamp>","graph_edges":1}
]
```

For a bounded page, retain the exact filters and continue from the final
item. The cursor is `<updated_at>|<id>` and the response remains a list:

```json
{"name":"agent-graph.html","media_type":"text/html","limit":2,"cursor":"<last_page_item.updated_at>|<last_page_item.id>"}
```

Use `artifact_search` for indexed natural-language body search scoped to the
readable component anchored at the Markdown artifact:

```json
{"query":"Agent graph","graph_root_artifact_id":"<markdown_artifact_id>","limit":20}
```

```json
[
  {"chunk_id":"<chunk_id>","artifact_id":"<html_artifact_id>","version_id":"<html_version_id>","artifact_name":"agent-graph.html","snippet":"[Agent graph]","score":-1.0}
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
artifact/version IDs, exact metadata filters with bounded cursors, component
and graph-root search scope, literal grep, chunk reads, renderer headers, and
the attached-MCP allow path. It also verifies bridge rejection for an
out-of-graph artifact ID and for a filename used as an ambiguous duplicate-name
target.
