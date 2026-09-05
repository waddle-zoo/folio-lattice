# Folio Lattice agent guidance

## Product context

Folio Lattice is a platform-agnostic, company-hosted system for safe artifacts and knowledge graphs. The first interface is MCP. The first implementation target is a Docker-based development environment that can later be deployed and extended as an enterprise service.

## Current product boundary

- Make documents and arbitrary files first-class artifacts.
- Make the knowledge graph searchable and traversable through MCP.
- Support full-document reads, chunk reads, grep-like search, graph traversal, and writes.
- Save a new version for every write; preserve provenance and history.
- Support HTML, JavaScript, and CSS artifacts in a sandbox with no direct external egress or XSS-capable access.
- Permit sandboxed artifacts to call only MCPs attached to the platform. User-specific MCP credentials are later work.
- Design for private-by-default storage and sharing controls even though the early prototype may be technically public.
- Keep model and agent integrations provider-neutral: Claude Code, Codex, Cursor, and other clients should use the same graph and artifact substrate.

## Engineering defaults

- Prefer small, explicit interfaces and durable formats over provider-specific abstractions.
- Treat versions, provenance, permissions, and execution boundaries as core data, not add-ons.
- Keep the local development path Docker-first and make deployment seams explicit.
- Do not add broad external network access to generated artifacts.
- Do not add user credential handling until the core graph and artifact lifecycle is stable.
- Keep the initial Gas Town setup simple: Mayor plus one persistent Codex crew member; no polecats.

Before changing the product boundary, update the relevant ADR and ask for a decision when the change affects security, persistence, provider neutrality, or privacy.
