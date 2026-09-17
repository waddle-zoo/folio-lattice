# Agent asset bundle quickstart

`run_recipe` is a thin client helper over the public MCP primitives. It creates
one HTML, CSS, and JavaScript graph bundle, records a small manifest in version
provenance, links HTML to its assets, and returns stable artifact/version/link
IDs. Asset URLs always pin both IDs: `/content/<artifact_id>/<version_id>`.

```python
import asyncio

from folio_lattice.agent_recipe import run_recipe
from folio_lattice.public_mcp import HttpMcpClient


async def main() -> None:
    client = HttpMcpClient("https://folio.example/mcp")
    bundle = await run_recipe(
        client,
        bundle_id="demo-20260917",
        render_base_url="https://folio-render.example",
        title="Agent bundle",
    )
    print(bundle.artifact_ids, bundle.version_ids, bundle.link_ids)
    print(bundle.render_url)


asyncio.run(main())
```

The helper uses only `artifact_list`, `artifact_read`, `artifact_create`,
`graph_link`, `artifact_search`, `artifact_versions`, and `graph_traverse`.
Identity, ACL checks, versioning, and renderer isolation remain server-owned.
