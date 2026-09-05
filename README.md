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

Folio Lattice keeps the runtime dependency-free and the development toolchain
reproducible. The repository uses a `src/` package layout, a locked `uv`
development environment, Ruff linting and formatting, pytest with coverage,
pre-commit hooks, GitHub Actions, Dependabot, and a Docker smoke test. Start
with:

```bash
make install
make check
make docker-build
```

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
