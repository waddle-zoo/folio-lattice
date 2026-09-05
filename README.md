# Folio Lattice

Folio Lattice is a company-hosted, platform-agnostic system for creating safe artifacts and connected knowledge graphs.

The initial product is MCP-first: agents and tools can search, grep, read, chunk, write, version, and connect documents and arbitrary files. HTML, JavaScript, and CSS artifacts are hosted in a restricted sandbox and may call only MCPs attached to the platform.

The system is developed with Docker, designed to be self-hostable, and intended to evolve into an enterprise product that is private by default. Sharing with selected teammates or broader audiences is a later feature, not an excuse to weaken the core security model.

The [manifesto](MANIFESTO.md) and [ADR index](docs/adr/README.md) are the
canonical product documentation. The original [product brief](docs/PRODUCT-BRIEF.md)
is retained as source context.

The current research checkpoint and implementation sequence are in
[docs/research](docs/research/) and [docs/V0-PLAN.md](docs/V0-PLAN.md).
