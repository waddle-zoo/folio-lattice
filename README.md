<div align="center">
  <img src="docs/assets/folio-lattice-mark.svg" alt="Folio Lattice" width="96" />
  <h1>Folio Lattice</h1>
  <p><strong>A small, provider-neutral home for agent-built artifacts and knowledge graphs.</strong></p>
  <p>
    <a href="https://github.com/waddle-zoo/folio-lattice/actions/workflows/ci.yml"><img src="https://github.com/waddle-zoo/folio-lattice/actions/workflows/ci.yml/badge.svg" alt="CI status" /></a>
    <a href="https://github.com/waddle-zoo/folio-lattice/actions/workflows/supply-chain.yml"><img src="https://github.com/waddle-zoo/folio-lattice/actions/workflows/supply-chain.yml/badge.svg" alt="Supply-chain workflow status" /></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12 or newer" /></a>
    <a href="https://www.docker.com/"><img src="https://img.shields.io/badge/docker-first-2496ED?logo=docker&logoColor=white" alt="Docker first" /></a>
    <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-first-111827" alt="MCP first" /></a>
  </p>
  <p>
    <a href="MANIFESTO.md">Manifesto</a> ·
    <a href="docs/MCP-QUICKSTART.md">MCP quickstart</a> ·
    <a href="docs/AGENT-RECIPE.md">Agent recipe</a> ·
    <a href="CONTRIBUTING.md">Contributing</a> ·
    <a href="SECURITY.md">Security</a>
  </p>
</div>

Folio Lattice is a company-hosted, platform-agnostic system for creating safe
artifacts and connected knowledge graphs. It gives agents a durable place to
search, grep, read, chunk, write, version, and connect documents and arbitrary
files—and gives people a simple workspace for opening those artifacts.

HTML, JavaScript, and CSS artifacts render in an isolated sandbox. A rendered
artifact has no general network access and may call only the MCP capabilities
attached to its Folio Lattice environment.

> **Status: public development preview.** The local Docker/MCP loop is working
> and intentionally useful now. Hosted authentication, private tenancy,
> sharing, and final supply-chain evidence remain tracked release gates; see
> [`docs/RELEASE-EVIDENCE.md`](docs/RELEASE-EVIDENCE.md) before treating this as
> production-ready enterprise software.

## What it is

| For agents | For people | For operators |
| --- | --- | --- |
| MCP tools for create, read, grep, search, link, version, and render | A graph workspace with a familiar file view and artifact previews | Docker-first deployment, explicit trust boundaries, and inspectable evidence |
| Provider-neutral over HTTP or stdio | Markdown rendered as a document; web artifacts rendered as pages | Hosted OIDC posture designed to fail closed when incomplete |
| A public contract that Hyperset and other clients can consume | Private-by-default product direction | CI, locked dependencies, linting, type checks, coverage, and Docker smoke tests |

## The product boundary

```text
Agent / client
      │ MCP over HTTP or stdio
      ▼
Folio Lattice control plane ─── graph links, versions, search, audit events
      │
      ├── artifact store
      └── isolated renderer ─── HTML / CSS / JS, no database or blob mount
```

The [manifesto](MANIFESTO.md), [ADR index](docs/adr/README.md), and
[product brief](docs/PRODUCT-BRIEF.md) are the canonical product documents.
The research checkpoint and implementation sequence live in
[docs/research](docs/research/) and [docs/V0-PLAN.md](docs/V0-PLAN.md).

## Start locally

Folio Lattice is developed and tested with Docker. For the Python toolchain:

```bash
make install
make check
make docker-build
```

For the local product loop:

```bash
cp .env.example .env
make docker-up
```

Then open `http://127.0.0.1:8000`. The local workspace and external clients
reach the same MCP contract. The renderer listens separately on
`http://127.0.0.1:8001`; it is an iframe target, not an authoring API, and it
receives no database or blob mount.

`.env` contains a known development-only renderer capability secret. Keep it
local. Hosted deployments must provide their own secret and use the hosted
configuration path; local mode is deliberately unauthenticated and loopback
bound.

## Agent quickstart

Read [the MCP artifact quickstart](docs/MCP-QUICKSTART.md) for the smallest
create → link → discover → search/grep → read → render flow. Body search and
metadata discovery are separate by design:

- use `artifact_search` or `artifact_grep` for indexed content;
- use bounded `artifact_list` filters for an exact filename or media type;
- use version reads and chunks when a complete document is not needed.

For a version-pinned HTML/CSS/JavaScript graph bundle, use the
[agent asset bundle recipe](docs/AGENT-RECIPE.md).

## Security posture

Sandboxed artifacts may request only the default attached Folio read, indexed
search, and outgoing-traversal tools. The control page and server independently
validate origin, schema, attachment, tool, size, and timeout. Decisions are
audit logged without arguments or content. This is not a generic MCP proxy and
does not carry an artifact credential.

Hosted HTTP mode requires `FOLIO_OIDC_ISSUER`, `FOLIO_OIDC_AUDIENCE`, and
`FOLIO_OIDC_JWKS_URL`. It accepts only RS256 bearer tokens with exact issuer,
audience, signature, and time validation, then maps `(issuer, subject)` through
the server-owned membership table. See the
[security policy](SECURITY.md), [threat model](docs/threat-model.md), and
[enterprise adoption notes](docs/enterprise-adoption.md) before integrating
the service into a corporate system.

## First consumer: Hyperset

[Hyperset](https://github.com/waddle-zoo/hyperset) is the first intended Folio
Lattice consumer. The integration is client-level: Hyperset uses the public
MCP/HTTP contract and does not share Folio's database, import private modules,
or receive a special authorization path. The executable fixture at
`tests/hyperset_consumer.py` proves that seam using only the public MCP SDK and
HTTP endpoint.

## Documentation map

- [Manifesto](MANIFESTO.md) — product boundary and principles.
- [MCP quickstart](docs/MCP-QUICKSTART.md) — build against the public contract.
- [Agent recipe](docs/AGENT-RECIPE.md) — create a linked HTML/CSS/JS bundle.
- [V0 plan](docs/V0-PLAN.md) — current scope and sequencing.
- [Enterprise adoption](docs/enterprise-adoption.md) — deployment expectations.
- [Release evidence](docs/RELEASE-EVIDENCE.md) — what is and is not proven.
- [ADR index](docs/adr/README.md) — durable architectural decisions.
- [Contributing](CONTRIBUTING.md) — local checks and design rules.
- [Security](SECURITY.md) — responsible disclosure and deployment warnings.

## Public repository policy

`main` is protected. Changes should arrive through reviewed pull requests and
passing automation; repository administrators retain the ability to merge when
operationally necessary. The public repository is source-visible, but no open
source license has been selected yet—treat the code as unlicensed until a
license is added.

Folio Lattice is maintained by [waddle-zoo](https://github.com/waddle-zoo).
