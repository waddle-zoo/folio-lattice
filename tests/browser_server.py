"""Browser-only server wrapper with deterministic external registration validation."""

import runpy
from unittest.mock import patch

from folio_lattice.external_mcp import HttpExternalMcpTransport

_validate_registration = HttpExternalMcpTransport.validate_registration


def _allow_registration(transport: HttpExternalMcpTransport, endpoint: str) -> None:
    """Keep the browser admin flow independent of public DNS availability."""

    if endpoint == "https://example.com/mcp":
        return
    _validate_registration(transport, endpoint)


if __name__ == "__main__":
    with patch.object(HttpExternalMcpTransport, "validate_registration", _allow_registration):
        runpy.run_module("folio_lattice.server", run_name="__main__")
