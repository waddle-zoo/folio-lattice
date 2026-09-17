# Folio Lattice

Folio Lattice is a company-hosted, platform-agnostic system for creating safe artifacts and connected knowledge graphs.

The initial product is MCP-first: agents and tools can search, grep, read, chunk, write, version, and connect documents and arbitrary files. HTML, JavaScript, and CSS artifacts are hosted in a restricted sandbox and may call only MCPs attached to the platform.

The system is developed with Docker, designed to be self-hostable, and intended to evolve into an enterprise product that is private by default. Sharing with selected teammates or broader audiences is a later feature, not an excuse to weaken the core security model.

The [manifesto](MANIFESTO.md) and [ADR index](docs/adr/README.md) are the
canonical product documentation. The original [product brief](docs/PRODUCT-BRIEF.md)
is retained as source context.

The current research checkpoint and implementation sequence are in
[docs/research](docs/research/) and [docs/V0-PLAN.md](docs/V0-PLAN.md).

## Enterprise adoption posture

Folio Lattice uses the official, version-pinned MCP Python SDK and keeps the
development toolchain reproducible. The repository uses a `src/` package
layout, a locked `uv` environment, Ruff linting and formatting, pytest with
coverage, pre-commit hooks, GitHub Actions, Dependabot, and a Docker smoke test.
Start with:

```bash
make install
make check
make docker-build
```

## Local inspection and rendering

Start both loopback-bound processes with:

```bash
make docker-up
```

Open `http://127.0.0.1:8000` to create or upload an artifact, search or literally
grep indexed content, read complete versions and chunks, create and navigate
outgoing graph links, inspect history, edit text with an optimistic parent, and
open isolated HTML/JavaScript/CSS previews. The page and renderer reach state
through the same public MCP tools as external clients. The renderer listens
separately on `http://127.0.0.1:8001`; it is an iframe target, not a public
authoring API, and it receives no database or blob mount.

`FOLIO_RENDER_ORIGIN` tells the control process where browsers reach the
renderer. `FOLIO_CONTROL_ORIGIN` tells the renderer which exact origin may frame
artifacts. Render responses enforce an opaque sandbox and deny direct network
connections. Keep both defaults on loopback: v0 still has no product auth or
sharing layer.

Sandboxed artifacts may request only the default attached Folio read, indexed
search, and outgoing-traversal tools. The control page and server independently
validate the message source/origin, schema, attachment, tool, size, and timeout;
decisions are audit logged without arguments or content. This is not a generic
MCP proxy and carries no artifact credential.

Local HTTP and stdio derive `FOLIO_TENANT_ID` and `FOLIO_ACTOR` from process
configuration. Clients cannot override either value through tool arguments.
The Docker deployment is a local development loop bound to loopback; local
mode remains explicitly unauthenticated.

Hosted HTTP mode requires `FOLIO_OIDC_ISSUER`, `FOLIO_OIDC_AUDIENCE`, and
`FOLIO_OIDC_JWKS_URL`. It accepts only RS256 bearer tokens with exact issuer,
audience, signature, and time validation, then maps `(issuer, subject)` through
the server-owned membership table. `FOLIO_OIDC_MEMBERSHIPS_FILE` may seed
immutable mappings; status changes are limited to `active`, `disabled`, and
`revoked`. Hosted startup warms the configured JWKS and fails closed if
configuration or keys are unusable. Health and readiness return posture only,
never token or membership data. See
[`docs/RELEASE-EVIDENCE.md`](docs/RELEASE-EVIDENCE.md) before making any
readiness claim.

The [contribution guide](CONTRIBUTING.md), [security policy](SECURITY.md), and
[enterprise adoption notes](docs/enterprise-adoption.md) describe the evidence
expected before integrating the service into a corporate system.

## First consumer: Hyperset

[Hyperset](https://github.com/waddle-zoo/hyperset) will be the first Folio
Lattice consumer. The integration is intentionally client-level: Hyperset will
use Folio Lattice through its public MCP/HTTP contract and will not share
Folio's database, import private implementation modules, or receive a special
authorization path. This keeps Folio Lattice useful to Claude Code, Codex,
Cursor, and other clients while giving the Hyperset knowledge flywheel a
durable artifact and graph substrate.

The executable fixture at `tests/hyperset_consumer.py` proves that seam using
only the public MCP SDK and HTTP endpoint.

## Agent quickstart

See [the MCP artifact quickstart](docs/MCP-QUICKSTART.md) for the smallest
create, link, discover, search/grep, read, and render flow. Artifact body search
and metadata discovery are deliberately separate: use `artifact_search` or
`artifact_grep` for indexed content, and bounded `artifact_list` filters for an
exact filename or media type.
