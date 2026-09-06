# Security policy

Folio Lattice is intended for enterprise data and untrusted generated
artifacts. Security reports are welcome, especially reports involving tenant
isolation, artifact egress, host-origin access, MCP capability escalation,
version history, or provenance loss.

Please use a private GitHub Security Advisory for this repository rather than
opening a public issue. Include a minimal reproduction, affected commit or
version, impact, and any required deployment assumptions. Do not include
customer data or credentials in the report.

The v0 security boundary is documented in
[`MANIFESTO.md`](MANIFESTO.md),
[`docs/adr/0004-sandboxed-web-artifacts.md`](docs/adr/0004-sandboxed-web-artifacts.md),
[`docs/adr/0009-thin-inspection-ui-and-isolated-renderer.md`](docs/adr/0009-thin-inspection-ui-and-isolated-renderer.md),
and [`docs/adr/0007-private-tenancy-and-sharing.md`](docs/adr/0007-private-tenancy-and-sharing.md).
The current implementation is not a production security certification. The
bounded bridge and its regression plan are documented in
[`ADR 0010`](docs/adr/0010-public-contract-ui-bridge-and-hosted-gate.md) and
[`docs/SECURITY-TEST-MATRIX.md`](docs/SECURITY-TEST-MATRIX.md). Hosted
authentication and the other blockers in
[`docs/RELEASE-EVIDENCE.md`](docs/RELEASE-EVIDENCE.md) remain explicit gates.

The v0 control, MCP, and renderer endpoints are unauthenticated local-development
interfaces. Docker Compose binds both origins to loopback and assigns one
configured namespace and actor to the processes. Do not expose them to an
untrusted network. Product credentials, sharing policy, TLS termination, audit
export, and rate limiting remain deferred until the core artifact and graph loop
is stable.

Setting `FOLIO_DEPLOYMENT_MODE=hosted` does not turn v0 into a hosted service.
It intentionally refuses startup until an authentication adapter exists. Do not
work around that refusal with tenant headers, URL secrecy, or a shared local
token.
