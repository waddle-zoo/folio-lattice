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

## Canonical create → link → discover → search → read → render flow

1. Create a Markdown root and an HTML asset with `artifact_create`. Send bytes
   as base64 and retain each returned `artifact.id` and `version.id`.

   ```json
   {"name":"launch-notes.md","media_type":"text/markdown","content_base64":"IyBMYXVuY2ggbm90ZXM="}
   ```

   ```json
   {"name":"agent-launch-board.html","media_type":"text/html","content_base64":"PGgxPkFnZW50IGxhdW5jaCBib2FyZDwvaDE+"}
   ```

2. Link the Markdown artifact to the HTML artifact by ID.

   ```json
   {"source_artifact_id":"<markdown-artifact-id>","target_artifact_id":"<html-artifact-id>","edge_type":"documents"}
   ```

3. Discover the HTML asset by exact metadata. This works even when its filename
   is absent from the body.

   ```json
   {"name":"agent-launch-board.html","media_type":"text/html","limit":20}
   ```

4. Inspect the readable connected set with `graph_component`.

   ```json
   {"start_artifact_id":"<markdown-artifact-id>","limit":100}
   ```

5. Search current body text inside that component with `artifact_search`.

   ```json
   {"query":"Agent launch board","graph_root_artifact_id":"<markdown-artifact-id>","limit":20}
   ```

   Use `artifact_grep` for a literal tenant/ACL-scoped body substring when graph
   scoping is not required.

6. Read the selected result by `artifact_id`, never by filename. Use
   `artifact_read_chunk` for a returned chunk ID when the body is large, or pass
   `version_id` to read an immutable historical version.

   ```json
   {"artifact_id":"<html-artifact-id>"}
   ```

7. Optionally call `graph_traverse` to inspect the directed outgoing path.

   ```json
   {"start_artifact_id":"<markdown-artifact-id>","max_depth":2,"limit":100}
   ```

8. For a human preview, open the control origin at
   `/artifacts/<html-artifact-id>`. It embeds the separately hosted renderer for
   supported HTML, CSS, and JavaScript. The renderer route is iframe-only and
   is not a second artifact API.

## Executable boundary proof

`tests/public_contract_quickstart.py` runs this same flow against fresh local
HTTP state using only MCP and renderer endpoints. It creates a unique Markdown
root linked to HTML, CSS, JavaScript, and binary artifacts; verifies stable
artifact/version IDs, exact metadata filters with bounded cursors, component
and graph-root search scope, literal grep, chunk reads, renderer headers, and
the attached-MCP allow path. It also verifies bridge rejection for an
out-of-graph artifact ID and for a filename used as an ambiguous duplicate-name
target.
