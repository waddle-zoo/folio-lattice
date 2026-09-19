from __future__ import annotations

import time
import unittest

from hosted_auth_target import (
    APPROVED_UPSTREAM_ENDPOINT,
    _ApprovedUpstream,
    _LoopbackExternalTransport,
)

from folio_lattice.external_mcp import ExternalMcpError


class HostedAuthTargetFixtureTests(unittest.TestCase):
    def test_normal_upstream_calls_pass_but_slow_call_is_bounded(self) -> None:
        upstream = _ApprovedUpstream()
        try:
            transport = _LoopbackExternalTransport(upstream.endpoint)
            credential = "upstream-conformance-secret"
            self.assertEqual(
                transport.health(APPROVED_UPSTREAM_ENDPOINT, credential=credential), "healthy"
            )
            result = transport.call_tool(
                APPROVED_UPSTREAM_ENDPOINT,
                "calendar.events.list",
                {"limit": 1},
                credential=credential,
            )
            self.assertEqual(result["events"][0]["id"], "event-1")

            started = time.monotonic()
            with self.assertRaisesRegex(ExternalMcpError, "external MCP transport timed out"):
                transport.call_tool(
                    APPROVED_UPSTREAM_ENDPOINT,
                    "calendar.events.slow",
                    {},
                    credential=credential,
                )
            self.assertLess(time.monotonic() - started, 1.5)
        finally:
            upstream.close()


if __name__ == "__main__":
    unittest.main()
