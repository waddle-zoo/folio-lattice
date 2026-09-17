"""Provider-neutral MCP recipe for one version-pinned web asset bundle."""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from html import escape
from typing import Any, Protocol
from urllib.parse import quote

from .renderer import versioned_content_url

BUNDLE_SCHEMA = "folio-lattice.asset-bundle.v1"
MAX_BUNDLE_ID_LENGTH = 255
MAX_NAME_LENGTH = 255
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_IDEMPOTENCY_CANDIDATES = 100


class ToolClient(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class AgentRecipeError(RuntimeError):
    """Bounded recipe failure, retaining already-created asset references."""

    def __init__(
        self,
        message: str,
        *,
        completed: Mapping[str, BundleAsset] | None = None,
    ) -> None:
        super().__init__(message)
        self.completed = dict(completed or {})


@dataclass(frozen=True, slots=True)
class BundleAsset:
    role: str
    artifact_id: str
    version_id: str
    name: str
    media_type: str

    @property
    def content_url(self) -> str:
        return versioned_content_url(self.artifact_id, self.version_id)


@dataclass(frozen=True, slots=True)
class AgentRecipeResult:
    bundle_id: str
    manifest: dict[str, Any]
    artifacts: dict[str, BundleAsset]
    links: list[dict[str, Any]]
    searches: dict[str, list[Mapping[str, Any]]]
    read: dict[str, Any]
    versions: list[Mapping[str, Any]]
    traversal: list[Mapping[str, Any]]
    artifact_ids: dict[str, str]
    version_ids: dict[str, str]
    link_ids: list[str]
    render_url: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _object(value: Any, operation: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentRecipeError(f"{operation} returned an invalid object")
    return value


def _records(value: Any, operation: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise AgentRecipeError(f"{operation} returned an invalid list")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AgentRecipeError(f"response omitted {field}")
    return value


def _safe_response_text(response: Any) -> str:
    texts: list[str] = []
    for item in getattr(response, "content", ()):
        value = getattr(item, "text", None)
        if value is None and isinstance(item, Mapping):
            value = item.get("text")
        if isinstance(value, str) and value:
            texts.append(value)
    return " ".join(texts)[:200] or "tool returned an error"


async def _call(client: ToolClient, tool: str, arguments: dict[str, Any]) -> Any:
    call_tool = getattr(client, "call_tool", None)
    if not callable(call_tool):
        call = getattr(client, "call", None)
        if not callable(call):
            raise AgentRecipeError(f"{tool} client has no MCP call method")
        try:
            return await call(tool, arguments)
        except Exception:
            raise AgentRecipeError(f"{tool} transport failed") from None
    try:
        response = await call_tool(tool, arguments)
    except Exception:
        raise AgentRecipeError(f"{tool} transport failed") from None
    if getattr(response, "is_error", False):
        raise AgentRecipeError(f"{tool} rejected: {_safe_response_text(response)}")
    structured = getattr(response, "structured_content", None)
    if not isinstance(structured, Mapping):
        raise AgentRecipeError(f"{tool} returned no structured content")
    return structured.get("result", structured)


def _decode_body(read: Mapping[str, Any]) -> bytes:
    encoded = read.get("content_base64")
    if not isinstance(encoded, str):
        raise AgentRecipeError("artifact_read omitted content_base64")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise AgentRecipeError("artifact_read returned invalid content_base64") from None


def _asset(read: Mapping[str, Any], role: str) -> BundleAsset:
    artifact = _object(read.get("artifact"), "artifact_read")
    version = _object(read.get("version"), "artifact_read")
    return BundleAsset(
        role=role,
        artifact_id=_text(artifact.get("id"), "artifact.id"),
        version_id=_text(version.get("id"), "version.id"),
        name=_text(artifact.get("name"), "artifact.name"),
        media_type=_text(artifact.get("media_type"), "artifact.media_type"),
    )


def _context(bundle_id: str, role: str, name: str, body: bytes) -> dict[str, str]:
    return {
        "schema": BUNDLE_SCHEMA,
        "bundle_id": bundle_id,
        "role": role,
        "relative_path": name,
        "content_sha256": hashlib.sha256(body).hexdigest(),
    }


async def _ensure(
    client: ToolClient,
    *,
    bundle_id: str,
    role: str,
    name: str,
    media_type: str,
    body: bytes,
    completed: Mapping[str, BundleAsset],
) -> BundleAsset:
    candidates = _records(
        await _call(
            client,
            "artifact_list",
            {"name": name, "media_type": media_type, "limit": MAX_IDEMPOTENCY_CANDIDATES},
        ),
        "artifact_list",
    )
    expected_hash = hashlib.sha256(body).hexdigest()
    matches: list[BundleAsset] = []
    for candidate in candidates:
        candidate_id = _text(candidate.get("id"), "artifact.id")
        read = _object(
            await _call(client, "artifact_read", {"artifact_id": candidate_id}),
            "artifact_read",
        )
        version = _object(read.get("version"), "artifact_read")
        context = version.get("source_context")
        if not isinstance(context, Mapping):
            continue
        if (
            context.get("schema") != BUNDLE_SCHEMA
            or context.get("bundle_id") != bundle_id
            or context.get("role") != role
        ):
            continue
        if (
            context.get("content_sha256") != expected_hash
            or hashlib.sha256(_decode_body(read)).hexdigest() != expected_hash
        ):
            raise AgentRecipeError(f"{role} bundle ID has different content", completed=completed)
        matches.append(_asset(read, role))
    if len(matches) > 1:
        raise AgentRecipeError(f"{role} bundle ID is ambiguous", completed=completed)
    if matches:
        return matches[0]
    if len(candidates) >= MAX_IDEMPOTENCY_CANDIDATES:
        raise AgentRecipeError(f"{role} idempotency search reached its bound", completed=completed)

    created = _object(
        await _call(
            client,
            "artifact_create",
            {
                "name": name,
                "media_type": media_type,
                "content_base64": base64.b64encode(body).decode("ascii"),
                "reason": f"asset bundle {role}",
                "source_context": _context(bundle_id, role, name, body),
            },
        ),
        "artifact_create",
    )
    return _asset(created, role)


def _validate(
    bundle_id: str,
    render_base_url: str,
    title: str,
    names: tuple[str, ...],
    bodies: tuple[bytes, ...],
) -> None:
    if not isinstance(bundle_id, str) or not bundle_id or len(bundle_id) > MAX_BUNDLE_ID_LENGTH:
        raise AgentRecipeError("bundle_id is invalid")
    if not isinstance(render_base_url, str) or not render_base_url.strip():
        raise AgentRecipeError("render_base_url is invalid")
    if not isinstance(title, str) or not title or len(title) > MAX_NAME_LENGTH:
        raise AgentRecipeError("title is invalid")
    if any(not isinstance(name, str) or not name or len(name) > MAX_NAME_LENGTH for name in names):
        raise AgentRecipeError("asset name is invalid")
    if len(set(names)) != len(names):
        raise AgentRecipeError("asset names must be unique")
    if any(not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES for body in bodies):
        raise AgentRecipeError("asset body exceeds the allowed size")


async def run_recipe(
    client: ToolClient,
    *,
    bundle_id: str,
    render_base_url: str,
    title: str = "Folio Lattice asset bundle",
    html_name: str = "index.html",
    css_name: str = "style.css",
    js_name: str = "app.js",
    css_body: bytes = b"body { color: #17324d; font: 16px system-ui; }",
    js_body: bytes = b"document.querySelector('[data-agent-status]').textContent = 'Ready';",
) -> AgentRecipeResult:
    _validate(
        bundle_id,
        render_base_url,
        title,
        (html_name, css_name, js_name),
        (css_body, js_body),
    )
    completed: dict[str, BundleAsset] = {}

    async def ensure(role: str, name: str, media_type: str, body: bytes) -> BundleAsset:
        asset = await _ensure(
            client,
            bundle_id=bundle_id,
            role=role,
            name=name,
            media_type=media_type,
            body=body,
            completed=completed,
        )
        completed[role] = asset
        return asset

    css = await ensure("css", css_name, "text/css", css_body)
    javascript = await ensure("js", js_name, "application/javascript", js_body)
    escaped_title = escape(title, quote=True)
    html_body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{escaped_title}</title>
<link rel="stylesheet" href="{escape(css.content_url, quote=True)}"></head>
<body><main><h1>{escaped_title}</h1><p data-agent-status>Loading</p></main>
<script src="{escape(javascript.content_url, quote=True)}"></script></body></html>""".encode()
    html = await ensure("html", html_name, "text/html", html_body)

    links: list[dict[str, Any]] = []
    for target in (css, javascript):
        link = _object(
            await _call(
                client,
                "graph_link",
                {
                    "source_artifact_id": html.artifact_id,
                    "target_artifact_id": target.artifact_id,
                    "edge_type": "references",
                },
            ),
            "graph_link",
        )
        links.append(dict(link))

    artifacts = {"html": html, "css": css, "js": javascript}
    searches = {
        "name": _records(
            await _call(client, "artifact_search", {"query": html.name}), "artifact_search"
        ),
        "type": _records(
            await _call(client, "artifact_search", {"query": "text/html"}), "artifact_search"
        ),
    }
    read = dict(
        _object(
            await _call(
                client,
                "artifact_read",
                {"artifact_id": html.artifact_id, "version_id": html.version_id},
            ),
            "artifact_read",
        )
    )
    versions = _records(
        await _call(client, "artifact_versions", {"artifact_id": html.artifact_id}),
        "artifact_versions",
    )
    traversal = _records(
        await _call(client, "graph_traverse", {"start_artifact_id": html.artifact_id}),
        "graph_traverse",
    )
    manifest = {
        "schema": BUNDLE_SCHEMA,
        "bundle_id": bundle_id,
        "assets": {
            role: {
                "artifact_id": asset.artifact_id,
                "version_id": asset.version_id,
                "name": asset.name,
                "media_type": asset.media_type,
                "content_url": asset.content_url,
            }
            for role, asset in artifacts.items()
        },
        "links": links,
    }
    html_path = quote(html.artifact_id, safe="")
    html_version = quote(html.version_id, safe="")
    return AgentRecipeResult(
        bundle_id=bundle_id,
        manifest=manifest,
        artifacts=artifacts,
        links=links,
        searches=searches,
        read=read,
        versions=versions,
        traversal=traversal,
        artifact_ids={role: asset.artifact_id for role, asset in artifacts.items()},
        version_ids={role: asset.version_id for role, asset in artifacts.items()},
        link_ids=[_text(link.get("id"), "link.id") for link in links],
        render_url=f"{render_base_url.rstrip('/')}/render/{html_path}?version_id={html_version}",
    )


create_bundle = run_recipe
