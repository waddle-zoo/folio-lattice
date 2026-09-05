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
and [`docs/adr/0007-private-tenancy-and-sharing.md`](docs/adr/0007-private-tenancy-and-sharing.md).
The current implementation is not a production security certification; the
full isolated renderer and hosted authorization surfaces remain explicit ship
gates.

The v0 HTTP endpoint requires a deployment-provided bearer token and binds one
configured tenant and actor to the process. The Docker Compose token is a local
development default, not a production secret. OAuth/OIDC, token rotation,
multi-principal policy, TLS termination, audit export, and rate limiting remain
deployment or post-v0 work.
