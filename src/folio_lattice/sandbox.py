from __future__ import annotations

from dataclasses import dataclass

SANDBOX_CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "base-uri 'none'",
        "connect-src 'none'",
        "font-src 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "img-src 'self' data: blob:",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
    ]
)


def sandbox_headers() -> dict[str, str]:
    """Headers for a dedicated-origin static artifact response."""
    return {
        "Content-Security-Policy": SANDBOX_CONTENT_SECURITY_POLICY,
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
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
