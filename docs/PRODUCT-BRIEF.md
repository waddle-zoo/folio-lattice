# Folio Lattice product brief

This is the initial human-authored brief for the Mayor to turn into the canonical manifesto and architecture decision records.

## Product thesis

Folio Lattice should be the company-hosted, platform-agnostic substrate that sits beneath AI agents and teams: a durable knowledge graph plus a safe artifact store. It should combine the useful parts of Claude Artifacts and OpenAI Sites without requiring a particular model provider or agent client.

## Near-term focus

1. Build the knowledge graph and artifact system before building a polished UI.
2. Make MCP the first-class read/write interface.
3. Let clients grep and traverse the graph, retrieve full documents or document chunks, and write new content.
4. Save a new version for every write, with enough provenance to understand what changed and why.
5. Store any file type. Treat HTML, JavaScript, and CSS as important artifact types.
6. Render web artifacts in a sandbox with no external network egress and no XSS path. Sandboxed artifacts may call only MCPs attached to the Folio Lattice platform. The platform gets its own MCP by default; administrators may attach additional MCPs later.
7. Defer user-specific MCP credentials until the core platform is working.
8. Develop neatly in Docker, while preserving clear seams for hosted deployment and enterprise operations.
9. Make the system usable from Claude Code, Codex, Cursor, and other clients without provider lock-in.
10. Design for private-by-default tenancy and authorization. Early sharing may be technically public, but v2 should support sharing with selected teammates or everyone.

## Explicit non-goals for the first phase

- A full-featured authoring UI.
- Public sharing, marketplaces, or social discovery.
- Unrestricted browser networking from generated artifacts.
- User credential brokerage for arbitrary MCPs.
- A polecat swarm or large multi-agent operating model.
- Provider-specific graph semantics that prevent other clients from participating.

## Desired first milestone

A Docker-based local deployment with an MCP server that can create and version artifacts, search and grep document content, retrieve full documents or chunks, traverse graph relationships, and write new versions. It should also be able to host a safe static HTML/CSS/JS artifact with an explicit, testable policy boundary.

## Delivery discipline

The Mayor should begin each phase with research and a written implementation plan. Implementation should follow the plan, with decisions captured in ADRs when the plan changes product scope, persistence, security, or deployment behavior. Before anything is described as ready to ship, run an end-to-end validation from Docker startup through MCP reads, graph traversal, writes, version creation, artifact retrieval, and sandbox policy enforcement.
