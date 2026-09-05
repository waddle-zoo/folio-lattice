# ADR 0004: Sandboxed web artifacts and mediated MCP access

- Status: Accepted
- Date: 2026-09-05

## Context

HTML, JavaScript, and CSS artifacts are valuable because they can be interactive,
but they are untrusted code. Rendering them in the application origin or giving
them normal browser networking would create paths to credentials, tenant data,
host DOM state, and arbitrary external services.

## Decision

Render web artifacts in a separately isolated, sandboxed origin with no ambient
authority. The runtime must enforce all of the following:

- no same-origin relationship with the control plane or authenticated product UI;
- a restrictive iframe/process sandbox that denies top navigation, pop-ups,
  downloads, storage inheritance, and host DOM access unless a later ADR grants
  a narrowly tested capability;
- a Content Security Policy that denies external network destinations and limits
  scripts, styles, images, fonts, frames, workers, and form actions to packaged
  artifact resources or explicitly mediated platform endpoints;
- no tenant credentials, platform tokens, cookies, or secrets in artifact code,
  URLs, storage, or messages; and
- schema-validated `postMessage`-style communication over a narrow bridge with
  exact origin and source checks.

Sandboxed artifacts cannot call the public network directly. They may request
calls only to MCP servers attached to Folio Lattice. The platform bridge checks
the tenant, artifact, attached-server allowlist, tool/resource permission,
request schema, size and time limits, and audit policy before making a call. The
platform's own MCP is attached by default. Administrators may attach additional
MCPs, but artifact code receives neither their credentials nor a general proxy.

The first milestone ships a security test suite with hostile fixtures covering
DOM escape, cross-origin access, navigation, exfiltration attempts, CSP bypasses,
bridge spoofing, and unauthorized MCP calls. The sandbox is not considered
complete merely because normal browser behavior appears constrained.

## Consequences

- Some ordinary web applications will not run without being packaged or adapted.
- Rendering requires a dedicated origin and careful deployment configuration.
- MCP calls gain latency and platform-enforced limits, but remain auditable and
  revocable.
- Sandbox policy and regression tests become release-critical security controls.

## Non-goals

- Unrestricted browser networking or a general HTTP proxy.
- XSS-capable integration with the host application.
- User-specific MCP credentials or OAuth brokerage.
- Compatibility with artifacts that require arbitrary CDN scripts, remote fonts,
  trackers, advertisements, or third-party frames.

## Open questions

- Should the hosted deployment add process or micro-VM isolation beyond browser
  origin isolation for particular artifact classes?
- How are attached MCP capabilities presented for informed administrator review?
- Which static assets may be bundled or cached without becoming an egress path?
