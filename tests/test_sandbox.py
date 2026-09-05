import unittest

from folio_lattice.sandbox import CapabilityBoundary, sandbox_headers


class SandboxTests(unittest.TestCase):
    def test_headers_deny_direct_network_and_host_embedding(self):
        headers = sandbox_headers()
        self.assertIn("connect-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_only_attached_operations_are_allowed(self):
        boundary = CapabilityBoundary()
        self.assertTrue(boundary.authorize("folio-lattice", "artifact_read"))
        self.assertFalse(boundary.authorize("other-mcp", "artifact_read"))
        with self.assertRaises(PermissionError):
            boundary.require("other-mcp", "artifact_read")


if __name__ == "__main__":
    unittest.main()
