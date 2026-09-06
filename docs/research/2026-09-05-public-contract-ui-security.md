# Public-contract UI and attached-MCP security research

Date: 2026-09-05

## Question

What is the smallest useful inspection, editing, and rendering slice that uses
Folio Lattice's public MCP contract end to end, gives untrusted artifacts one
useful mediated capability, and makes the remaining hosted-enterprise gates
impossible to mistake for completed work?

## Current evidence and gaps

Commit `d30a837` is a validated local core. Independent fresh-volume validation
proved all nine MCP tools, immutable persistence across restart, the dedicated
renderer, browser CSP/sandbox behavior, and the Docker hardening controls. It
did not prove a hosted trust boundary.

The thin UI and renderer currently read the `FolioLattice` service directly.
That exercises the same data, but not the public contract. The UI lacks create,
upload, chunk, search, grep, graph-link, and historical-version workflows. The
renderer has a read-only volume, but that is still private storage access. The
capability boundary is policy scaffolding; no browser-to-platform MCP call
exists. Tenant and actor are process environment values, not authenticated
identity. Those are material limits, not wording problems.

## Standards findings

The MCP tools specification makes tool schemas and tool-call results the public
contract. It says servers must validate inputs and implement access controls,
rate limits, output sanitization, and tool-use logging. It also recommends clear
UI disclosure and visible invocation state. The bridge therefore must not be a
generic JSON-RPC proxy: it needs a fixed attachment, a per-tool allowlist,
schema validation, explicit limits, and an audit decision for every request.

MCP authorization guidance prohibits token passthrough and requires servers to
validate credentials intended for them. Folio has no credential issuer,
verifier, session, or grant model. A `hosted` configuration must consequently
refuse startup until a real authentication adapter is configured; accepting a
tenant header or a shared development token would create an identity claim the
product cannot support.

An iframe without `allow-same-origin` receives an opaque origin. Its
`postMessage` events therefore report `origin === "null"`. The HTML Standard
requires receivers to check the sender origin and validate message data. An
origin check alone is insufficient because unrelated sandboxed frames may also
have an opaque origin. The control page must additionally require
`event.source === preview.contentWindow`. Replies go only to that captured
window and use a request identifier. Because an opaque origin cannot be named
as a target origin, `"*"` is acceptable only for that direct reply; it is not
used to select a receiver.

CSP and iframe sandboxing are complementary. The renderer should keep
`sandbox allow-scripts` without `allow-same-origin`, `connect-src 'none'`,
`form-action 'none'`, and denied popups, navigation, frames, workers, storage
inheritance, and cookies. The attached bridge is message mediation by the
control plane, not browser egress and not a URL available to artifact code.

WCAG 2.2 requires functionality to be keyboard operable, status changes to be
programmatically determinable without moving focus, and errors to be identified
in text. The small UI can meet this with native forms, labels, buttons, links,
logical source order, `aria-busy`, `role="status"`, and `role="alert"`; it does
not need a component framework.

Sources:

- [MCP tools, 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
- [MCP authorization security considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)
- [HTML cross-document messaging](https://html.spec.whatwg.org/multipage/web-messaging.html)
- [HTML iframe sandbox](https://html.spec.whatwg.org/multipage/iframe-embed-object.html#attr-iframe-sandbox)
- [Content Security Policy Level 3](https://www.w3.org/TR/CSP3/)
- [WCAG 2.2 keyboard](https://www.w3.org/WAI/WCAG22/Understanding/keyboard.html)
- [WCAG 2.2 status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html)
- [WCAG 2.2 error identification](https://www.w3.org/WAI/WCAG22/Understanding/error-identification.html)

## Threat model

### Assets and trust boundaries

The protected assets are artifact bytes, immutable history, tenant-scoped graph
and index results, provenance, platform state, and future credentials. There are
four boundaries: MCP client to control service; control page to same-origin UI
gateway; control page to opaque-origin renderer frame; and renderer service to
the control service's MCP endpoint. The browser artifact is always adversarial.
The local process environment is configuration, not a user identity boundary.

### Threats and required controls

| Threat | Required control and evidence |
| --- | --- |
| Stored XSS or host DOM escape | Separate renderer origin; opaque iframe sandbox; no artifact bytes inserted into control DOM as HTML; hostile real-browser fixture. |
| Direct egress or covert navigation | CSP denies connections, forms, frames, workers, media, base URLs, and remote assets; iframe omits navigation, popup, download, and same-origin tokens; browser assertions inspect failures and top URL. |
| `postMessage` spoofing or origin confusion | Exact `"null"` origin and exact `contentWindow` checks in the page; exact configured HTTP `Origin` at the server; request correlation; adversarial sibling-frame and direct-request tests. |
| MCP capability escalation | One named attachment; fixed read/search/traverse allowlist; per-tool argument schemas; deny unknown server/tool/fields; no generic upstream URL; allow/deny audit events. |
| Credential theft or confused deputy | No credentials in artifact DOM, URL, storage, messages, arguments, results, or renderer process; no inbound-token forwarding; bridge derives tenant from its configured MCP endpoint. |
| Oversized, malformed, or slow calls | Bounded HTTP body, serialized arguments, MCP result, and wall time; invalid JSON/schema rejected before invocation; generic bounded errors. |
| Cross-tenant disclosure | Every identifier lookup, search, and outgoing traversal remains scoped by the MCP server's configured tenant; two real servers over shared storage prove isolation. |
| Lost updates or partial mutations | UI sends the displayed parent version to `artifact_write`; stale writes return a visible conflict and leave history unchanged. |
| Information leakage in errors/audit | Client errors omit internals and credentials; audit records decision metadata, sizes, duration, artifact/tool names, and reason codes, never content or arguments. |
| Hosted deployment without identity | `hosted` mode fails at startup until an authentication adapter exists; local UI and health state say unauthenticated local development. |

### Residual risks and blockers

This slice cannot be called enterprise-ready. It has no credential validation,
sessions, principals, ACLs, grants, sharing, revocation, TLS policy, audit export
or retention, distributed rate limiting, deletion policy, backup/restore proof,
or external security assessment. Process-configured tenancy is safe only inside
the documented loopback local topology. Browser sandbox behavior is a strong
control, not proof against browser-engine vulnerabilities.

Search remains chunk-content FTS5, grep remains a literal substring match, and
traversal remains outgoing and bounded. Backlinks, path/subgraph queries,
name/path filters, and graph metadata search are product limits, not security
work to smuggle into this phase.

## Smallest implementation seam

1. Add one internal MCP client adapter using the official SDK and configured
   Streamable HTTP endpoint. The UI gateway and renderer use it; neither imports
   the service nor opens storage.
2. Add artifact metadata and deterministic chunk descriptors to
   `artifact_read` as additive output. Keep the existing nine tools.
3. Let the static UI call those nine tools through one same-origin, exact-origin,
   bounded gateway. Build only forms and result views for the core loop.
4. Add one bridge request shape for the `folio-lattice` attachment. Permit
   `artifact_read`, `artifact_search`, and `graph_traverse`; deny all else.
5. Add `local` and `hosted` deployment modes. Default and Compose remain
   visibly local; hosted startup fails until a future authentication adapter is
   deliberately implemented.
6. Prove the claims with official MCP clients, real service processes, a real
   browser, and fresh Docker volumes. Private service/database setup is allowed
   in unit tests only, never as end-to-end evidence.

## Rejected expansions

No REST mirror, SPA framework, attachment marketplace, remote MCP registry,
credential broker, OAuth implementation, SSO, SCIM, ACL system, sharing, vector
search, regex grep, bidirectional graph engine, or public deployment is part of
this decision.
