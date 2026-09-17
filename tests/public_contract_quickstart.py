"""Black-box public MCP/HTTP quickstart and regression fixture."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from mcp import Client


async def call(client: Client, name: str, arguments: dict[str, Any]) -> Any:
    response = await client.call_tool(name, arguments)
    if response.is_error:
        raise RuntimeError(response.content[0].text)
    structured = response.structured_content
    if structured is None:
        raise RuntimeError(f"{name} returned no structured content")
    return structured.get("result", structured)


def create_arguments(name: str, content: bytes, media_type: str) -> dict[str, Any]:
    return {
        "name": name,
        "media_type": media_type,
        "content_base64": base64.b64encode(content).decode(),
        "reason": "fl-urj.13.1 public contract fixture",
    }


def post_json(url: str, payload: dict[str, Any], *, origin: str) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Origin": origin},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def bridge_call(
    base_url: str,
    *,
    request_id: str,
    attached_artifact_id: str,
    target_artifact_id: str,
    tool: str = "artifact_read",
    arguments: dict[str, Any] | None = None,
) -> Any:
    return post_json(
        f"{base_url.rstrip('/')}/api/bridge",
        {
            "request_id": request_id,
            "artifact_id": attached_artifact_id,
            "attachment": "folio-lattice",
            "tool": tool,
            "arguments": arguments or {"artifact_id": target_artifact_id},
        },
        origin=base_url,
    )


def expect_denied(url: str, payload: dict[str, Any], *, origin: str) -> str:
    try:
        post_json(url, payload, origin=origin)
    except urllib.error.HTTPError as error:
        body = error.read().decode()
        assert error.code == 400, body
        return body
    raise AssertionError("request unexpectedly succeeded")


def iframe_get(url: str) -> Any:
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"Sec-Fetch-Dest": "iframe"}),
        timeout=10,
    )


async def exercise(base_url: str, render_url: str) -> dict[str, Any]:
    started = time.perf_counter()
    marker = f"flurj131{uuid.uuid4().hex}"
    body_marker = f"{marker}body"
    literal = f"[{marker}]:?"
    root_name = f"{marker}.md"
    root_text = f"# {marker}\n{body_marker} root {literal}\n"
    updated_root_text = f"{root_text}{body_marker} revised\n"
    duplicate_name = f"{marker}.duplicate"
    child_specs = (
        (f"{marker}.html", f"<h1>{body_marker}</h1>".encode(), "text/html"),
        (f"{marker}.css", f"body {{ color: red; }} {body_marker}".encode(), "text/css"),
        (
            f"{marker}.js",
            f"document.body.dataset.marker='{body_marker}'".encode(),
            "application/javascript",
        ),
        (f"{marker}.bin", b"\x00\x01\x02\xff", "application/octet-stream"),
    )

    async with Client(f"{base_url.rstrip('/')}/mcp", raise_exceptions=True) as client:
        root = await call(
            client,
            "artifact_create",
            create_arguments(root_name, root_text.encode(), "text/markdown"),
        )
        matching_duplicates = [
            await call(
                client,
                "artifact_create",
                create_arguments(duplicate_name, f"duplicate-{index}".encode(), "text/plain"),
            )
            for index in range(5)
        ]
        wrong_type = await call(
            client,
            "artifact_create",
            create_arguments(duplicate_name, b"wrong media type", "text/html"),
        )
        outside = await call(
            client,
            "artifact_create",
            create_arguments(f"{marker}.outside", body_marker.encode(), "text/plain"),
        )
        children = [
            await call(client, "artifact_create", create_arguments(*spec)) for spec in child_specs
        ]

        root_id = root["artifact"]["id"]
        root_version_id = root["version"]["id"]
        child_ids = {item["artifact"]["id"] for item in children}
        duplicate_ids = {item["artifact"]["id"] for item in matching_duplicates}
        outside_id = outside["artifact"]["id"]
        all_created = [root, *matching_duplicates, wrong_type, outside, *children]
        artifact_ids = {item["artifact"]["id"] for item in all_created}
        version_ids = {item["version"]["id"] for item in all_created}
        assert len(artifact_ids) == len(all_created)
        assert len(version_ids) == len(all_created)

        for child, spec in zip(children, child_specs, strict=True):
            read = await call(client, "artifact_read", {"artifact_id": child["artifact"]["id"]})
            assert read["artifact"]["id"] == child["artifact"]["id"]
            assert read["version"]["id"] == child["version"]["id"]
            assert read["content_base64"] == base64.b64encode(spec[1]).decode()

        html = next(item for item in children if item["artifact"]["name"].endswith(".html"))
        css = next(item for item in children if item["artifact"]["name"].endswith(".css"))
        javascript = next(item for item in children if item["artifact"]["name"].endswith(".js"))
        css_url = f"/content/{css['artifact']['id']}/{css['version']['id']}"
        javascript_url = f"/content/{javascript['artifact']['id']}/{javascript['version']['id']}"
        linked_html = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><title>{marker}</title>
<link rel="stylesheet" href="{css_url}"></head><body>
<main><h1>{body_marker}</h1><p data-asset-status>Loading</p></main>
<script src="{javascript_url}"></script></body></html>"""
        html_version_2 = await call(
            client,
            "artifact_write",
            {
                "artifact_id": html["artifact"]["id"],
                "parent_version_id": html["version"]["id"],
                "content_base64": base64.b64encode(linked_html.encode()).decode(),
                "media_type": "text/html",
                "reason": "version-pinned linked asset regression",
            },
        )
        html_render_version = html_version_2["id"]

        first_page = await call(
            client,
            "artifact_list",
            {"name": duplicate_name, "media_type": "text/plain", "limit": 2},
        )
        assert len(first_page) == 2
        assert all(
            item["name"] == duplicate_name and item["media_type"] == "text/plain"
            for item in first_page
        )
        cursor = f"{first_page[-1]['updated_at']}|{first_page[-1]['id']}"
        second_page = await call(
            client,
            "artifact_list",
            {
                "name": duplicate_name,
                "media_type": "text/plain",
                "limit": 2,
                "cursor": cursor,
            },
        )
        second_cursor = f"{second_page[-1]['updated_at']}|{second_page[-1]['id']}"
        third_page = await call(
            client,
            "artifact_list",
            {
                "name": duplicate_name,
                "media_type": "text/plain",
                "limit": 2,
                "cursor": second_cursor,
            },
        )
        discovered_ids = {item["id"] for item in [*first_page, *second_page, *third_page]}
        assert discovered_ids == duplicate_ids
        assert len(second_page) == 2
        assert len(third_page) == 1
        assert wrong_type["artifact"]["id"] not in discovered_ids

        for child in children:
            await call(
                client,
                "graph_link",
                {
                    "source_artifact_id": root_id,
                    "target_artifact_id": child["artifact"]["id"],
                    "edge_type": "contains",
                },
            )

        updated = await call(
            client,
            "artifact_write",
            {
                "artifact_id": root_id,
                "parent_version_id": root_version_id,
                "content_base64": base64.b64encode(updated_root_text.encode()).decode(),
                "media_type": "text/markdown",
                "reason": "stable version regression",
            },
        )
        assert updated["artifact_id"] == root_id
        assert updated["parent_version_id"] == root_version_id
        updated_version_id = updated["id"]
        assert updated_version_id != root_version_id

        current_root = await call(client, "artifact_read", {"artifact_id": root_id})
        old_root = await call(
            client,
            "artifact_read",
            {"artifact_id": root_id, "version_id": root_version_id},
        )
        assert current_root["artifact"]["id"] == root_id
        assert current_root["version"]["id"] == updated_version_id
        assert current_root["text"] == updated_root_text
        assert old_root["version"]["id"] == root_version_id
        assert old_root["text"] == root_text
        assert len(await call(client, "artifact_versions", {"artifact_id": root_id})) == 2

        chunks = [
            await call(client, "artifact_read_chunk", {"chunk_id": descriptor["id"]})
            for descriptor in current_root["chunks"]
        ]
        assert all("content" in chunk and "text" not in chunk for chunk in chunks)
        assert "".join(chunk["content"] for chunk in chunks) == updated_root_text
        assert all(chunk["offset_unit"] == "unicode_code_points" for chunk in chunks)

        traversed = await call(
            client,
            "graph_traverse",
            {"start_artifact_id": root_id, "max_depth": 1, "limit": 10},
        )
        assert {edge["target_artifact_id"] for edge in traversed} == child_ids
        assert root_id not in {edge["target_artifact_id"] for edge in traversed}

        component = await call(
            client, "graph_component", {"start_artifact_id": root_id, "limit": 100}
        )
        component_ids = {item["id"] for item in component}
        assert component_ids == {root_id, *child_ids}
        child_component = await call(
            client,
            "graph_component",
            {"start_artifact_id": next(iter(child_ids)), "limit": 100},
        )
        assert {item["id"] for item in child_component} == component_ids

        scoped_search = await call(
            client,
            "artifact_search",
            {"query": body_marker, "graph_root_artifact_id": root_id, "limit": 100},
        )
        scoped_ids = {item["artifact_id"] for item in scoped_search}
        assert scoped_ids <= component_ids
        assert outside_id not in scoped_ids
        assert duplicate_ids.isdisjoint(scoped_ids)
        global_search = await call(client, "artifact_search", {"query": body_marker, "limit": 100})
        assert outside_id in {item["artifact_id"] for item in global_search}

        grep = await call(client, "artifact_grep", {"pattern": literal, "limit": 10})
        assert any(item["artifact_id"] == root_id and item["match"] == literal for item in grep)

        root_bridge = bridge_call(
            base_url,
            request_id=f"{marker}-allow-read",
            attached_artifact_id=root_id,
            target_artifact_id=root_id,
        )
        assert root_bridge["result"]["artifact"]["id"] == root_id
        bridge_search = bridge_call(
            base_url,
            request_id=f"{marker}-allow-search",
            attached_artifact_id=root_id,
            target_artifact_id=root_id,
            tool="artifact_search",
            arguments={"query": body_marker, "limit": 100},
        )
        assert all(item["artifact_id"] == root_id for item in bridge_search["result"])

        bridge_url = f"{base_url.rstrip('/')}/api/bridge"
        outside_error = expect_denied(
            bridge_url,
            {
                "request_id": f"{marker}-deny-outside",
                "artifact_id": root_id,
                "attachment": "folio-lattice",
                "tool": "artifact_read",
                "arguments": {"artifact_id": outside_id},
            },
            origin=base_url,
        )
        assert "attached artifact" in outside_error
        ambiguous_error = expect_denied(
            bridge_url,
            {
                "request_id": f"{marker}-deny-ambiguous-name",
                "artifact_id": root_id,
                "attachment": "folio-lattice",
                "tool": "artifact_read",
                "arguments": {"artifact_id": duplicate_name},
            },
            origin=base_url,
        )
        assert "attached artifact" in ambiguous_error

    render_url = render_url.rstrip("/")
    binary = next(item for item in children if item["artifact"]["name"].endswith(".bin"))
    with urllib.request.urlopen(
        f"{base_url.rstrip('/')}/standalone/{html['artifact']['id']}", timeout=10
    ) as response:
        standalone = response.read().decode()
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/html")
        assert 'id="human-preview"' in standalone
    with iframe_get(
        f"{render_url}/render/{html['artifact']['id']}?version_id={html_render_version}"
    ) as response:
        rendered_html = response.read().decode()
        assert rendered_html == linked_html
        assert css_url in rendered_html
        assert javascript_url in rendered_html
        policy = response.headers["Content-Security-Policy"]
        assert "default-src 'none'" in policy
        assert "connect-src 'none'" in policy
        assert "sandbox allow-scripts" in policy
        assert f"frame-ancestors {base_url}" in policy
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    for resource, element in ((css, "<link"), (javascript, "<script")):
        with iframe_get(f"{render_url}/render/{resource['artifact']['id']}") as response:
            assert element in response.read().decode()
            assert response.headers["X-Content-Type-Options"] == "nosniff"
        with urllib.request.urlopen(
            f"{render_url}/content/{resource['artifact']['id']}/{resource['version']['id']}",
            timeout=10,
        ) as response:
            assert response.read()

    # The immutable HTML version continues to resolve its original CSS/JS
    # IDs. A mismatched pair and a missing asset are not alternate lookup
    # forms and must fail closed at the renderer/MCP boundary.
    try:
        iframe_get(f"{render_url}/content/{css['artifact']['id']}/{javascript['version']['id']}")
    except urllib.error.HTTPError as error:
        assert error.code in {400, 404}
    else:
        raise AssertionError("mismatched artifact/version URL unexpectedly succeeded")
    try:
        iframe_get(f"{render_url}/content/art_missing/ver_missing")
    except urllib.error.HTTPError as error:
        assert error.code in {400, 404}
    else:
        raise AssertionError("missing asset URL unexpectedly succeeded")

    try:
        iframe_get(f"{render_url}/render/{binary['artifact']['id']}")
    except urllib.error.HTTPError as error:
        assert error.code == 415
    else:
        raise AssertionError("binary artifact unexpectedly rendered")

    return {
        "status": "ok",
        "marker": marker,
        "artifact_count": len(all_created),
        "linked_count": len(child_ids),
        "discovered_count": len(discovered_ids),
        "component_count": len(component_ids),
        "scoped_search_count": len(scoped_search),
        "grep_count": len(grep),
        "chunk_field": "result.content",
        "version_count": 2,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


if __name__ == "__main__":
    print(
        json.dumps(
            asyncio.run(
                exercise(
                    os.environ["FOLIO_BASE_URL"],
                    os.environ.get("FOLIO_RENDER_URL", os.environ["FOLIO_BASE_URL"]),
                )
            ),
            sort_keys=True,
        )
    )
