# ADR 0009: Thin inspection UI and isolated renderer

- Status: Accepted
- Date: 2026-09-05

## Context

The MCP core now proves durable artifact, version, search, and graph behavior,
but the first milestone still lacks a useful browser inspection loop and an
executable rendering boundary. A broad web application or parallel REST model
would distract from the product contract.

## Decision

Add one static inspection page over `FolioLattice`. It accepts an artifact
identifier and exposes only the combined read needed by the page plus a text
version write with an explicit optimistic parent. The page shows current
content, version provenance, outgoing graph edges, and a renderer preview.

Run rendering in a second process and origin configured independently from the
control/MCP process. The renderer opens the same artifact state read-only and
serves only `text/html`, `text/css`, `application/javascript`, and
`text/javascript`. HTML is returned directly; JavaScript and CSS receive small
HTML wrappers.

Every render response applies CSP `sandbox allow-scripts`, denies direct
connections and active auxiliary capabilities, and allows framing only by the
configured control origin. The inspection iframe repeats `sandbox="allow-scripts"`
and never grants `allow-same-origin`.

End-to-end evidence must cross process boundaries for read, graph inspection,
edit, version history, and rendering. A real headless browser must show that
artifact scripts execute while egress and host escape attempts fail.

## Consequences

- Browser inspection exercises the same data and optimistic version contract as
  MCP without expanding the nine-tool public MCP surface.
- The renderer can be deployed on a distinct origin and cannot mutate state.
- Inline HTML scripts and styles work inside the opaque sandbox; arbitrary
  network dependencies do not.
- Two small JSON routes exist as private UI mechanics. They are not a general
  REST API or a supported consumer boundary.

## Non-goals

- Artifact listing, creation, graph editing, collaborative editing, or diffing.
- Sharing, credentials, authorization frameworks, or public deployment policy.
- Vector search, arbitrary remote assets, a network proxy, or a frontend
  platform.
- The attached-MCP bridge described in ADR 0004; capability scaffolding remains
  unchanged until that bridge receives its own bounded phase.
