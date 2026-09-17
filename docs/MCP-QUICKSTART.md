# MCP artifact quickstart

Use the existing artifact and graph tools directly; there is no separate asset
catalog. Tenant and actor identity come from the authenticated server context,
not tool arguments.

1. Create an HTML asset and a note with `artifact_create`. Send bytes as
   base64 and retain each returned `artifact.id` and `version.id`.

   ```json
   {"name":"agent-launch-board.html","media_type":"text/html","content_base64":"PGgxPkFnZW50IGxhdW5jaCBib2FyZDwvaDE+"}
   ```

2. Connect them with `graph_link` using the two artifact IDs and an explicit
   `edge_type`, such as `documents`.

3. Discover assets by metadata with `artifact_list`. `name` and `media_type`
   are optional exact-match filters applied before the existing `limit` bound
   of 1–100. Results remain tenant- and read-ACL-scoped.

   ```json
   {"name":"agent-launch-board.html","media_type":"text/html","limit":20}
   ```

4. Search indexed body text with `artifact_search`, or find a literal body
   substring with `artifact_grep`. These tools intentionally do not search the
   filename or media type. Add `graph_root_artifact_id` when the result must be
   restricted to one connected component.

5. Read the selected artifact with `artifact_read`; use `artifact_read_chunk`
   for a returned chunk ID when the body is large. Pass a `version_id` to read
   an immutable historical version.

6. For a human preview, open the control origin at
   `/artifacts/{artifact_id}`. It embeds the separately hosted renderer for
   supported HTML, CSS, and JavaScript; the renderer route itself is
   iframe-only and is not a second artifact API.
