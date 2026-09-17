from __future__ import annotations

from dataclasses import dataclass


def sandbox_headers(frame_ancestor: str = "'none'") -> dict[str, str]:
    """Headers for an opaque-origin web artifact embedded by one control origin."""
    policy = "; ".join(
        [
            "default-src 'none'",
            "base-uri 'none'",
            "connect-src 'none'",
            "child-src 'none'",
            "font-src 'none'",
            "form-action 'none'",
            f"frame-ancestors {frame_ancestor}",
            "frame-src 'none'",
            "img-src data: blob:",
            "manifest-src 'none'",
            "media-src 'none'",
            "navigate-to 'none'",
            "object-src 'none'",
            "sandbox allow-scripts",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "worker-src 'none'",
        ]
    )
    return {
        "Content-Security-Policy": policy,
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Permissions-Policy": (
            "camera=(), clipboard-read=(), clipboard-write=(), geolocation=(), "
            "microphone=(), payment=(), usb=()"
        ),
        "Cache-Control": "no-store",
    }


@dataclass(frozen=True)
class McpAttachment:
    name: str
    allowed_operations: frozenset[str]


class CapabilityBoundary:
    """Allow only operations explicitly attached to a sandbox instance."""

    def __init__(self, attachments: list[McpAttachment] | None = None):
        default = McpAttachment(
            "folio-lattice", frozenset({"artifact_read", "artifact_search", "graph_traverse"})
        )
        self.attachments = {item.name: item for item in [default, *(attachments or [])]}

    def authorize(self, mcp_name: str, operation: str) -> bool:
        attachment = self.attachments.get(mcp_name)
        return bool(attachment and operation in attachment.allowed_operations)

    def require(self, mcp_name: str, operation: str) -> None:
        if not self.authorize(mcp_name, operation):
            raise PermissionError(f"MCP capability not attached: {mcp_name}/{operation}")
