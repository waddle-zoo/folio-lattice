# Slack + secure external-MCP connection/admin UX handoff

- Handoff: `fl-urj.7.5`
- Historical baseline: [`origin/main` at `861eda6d1e3536c6791dbe1d6e1022ad1c0f5106`](https://github.com/waddle-zoo/folio-lattice/commit/861eda6d1e3536c6791dbe1d6e1022ad1c0f5106)
- Status: target UX contract; current local/fixture connection surfaces are partial, while hosted Slack OAuth/notification, safe-link, and production broker lifecycle remain gated.

Minimal check-in-ready implementation brief for `ux_visual`, `ux_usability`,
and the hosted UI owner. Use plain-language controls, server-side
authorization, private-by-default artifacts, opaque safe links, and
independently visible access/notification outcomes.

## Current-state note

Verified against protected `main` at
[`f75107d7c4fbf912d69bd24d6decdb4703730509`](https://github.com/waddle-zoo/folio-lattice/commit/f75107d7c4fbf912d69bd24d6decdb4703730509)
on 2026-09-19: `Settings > Connections` and the external-MCP registry provide
local/fixture registration, exact tool/resource allowlists, status, revoke, and
audit activity (`connections.py`, `external_mcp.py`, and their focused tests).
`ApprovedSlackConsumer` is a bounded `slack.search` and
provenance-save adapter fixture, not Slack Web API OAuth or share-notification
delivery. No hosted Slack install/notification sender or safe-link route is
implemented. This document remains a target contract and makes no readiness
claim.

## Thin slice

Ship only these human flows:

1. Admin installs one tenant-scoped Slack connection with minimum notification scope and one approved notification destination.
2. Admin connects one tenant-scoped external MCP server through brokered authorization code + PKCE, or an approved secret reference, then explicitly enables a bounded capability set.
3. Owner shares one private document with one named same-tenant person as `Can view` or `Can edit`.
4. Folio sends a safe-link-only Slack notification with previews disabled.
5. Recipient opens the opaque safe link; Folio re-checks session and access server-side.
6. Owner/admin revokes named access; link, graph reachability, search, preview, and attached access re-check the revocation.
7. Admin reconnects, rotates, or revokes a provider/client connection without changing document grants.
8. Every success, partial success, cancellation, stale update, and failure has the exact copy and recovery action below.

### Roles and authority

| Role | Can do in this slice | Must not receive or control |
| --- | --- | --- |
| Tenant admin | Install/reconnect/rotate/revoke Slack; register, review, enable, rotate, and revoke one external MCP connection; inspect audit outcomes. | Tokens, secrets, arbitrary egress, or automatic capability enablement. |
| Document owner | Share one private document with a named same-tenant person as `Can view` or `Can edit`; revoke that grant. | Cross-tenant sharing, public links, or Slack destination policy. |
| Named recipient | Open an authorized safe link and use the granted document role; reauthenticate when asked. | Any access from the link itself, hidden previews, or unrelated documents. |
| Agent or sandboxed artifact | Use only a capability already attached and allowlisted by policy. | Endpoint selection, credential material, tenant/actor selection, or policy changes. |
| Folio service/broker | Resolve verified identity, re-check access, enforce egress and capability policy, perform bounded verification, and audit outcomes. | Raw secrets, document content in connector payloads, or trust in caller-supplied identity. |

## Non-goals

- For this share-notification slice, Slack channel history, message search,
  content ingestion, or arbitrary Slack posting.
- Slack unfurls in the first slice; do not request `links:write` until controlled unfurling is separately approved.
- Token paste, token display, client-secret display, bearer tokens in URLs, or browser-side credential exchange.
- Anonymous/public links, cross-tenant sharing, group invites, or undocumented authorization bypasses.
- Slack-driven artifact edits, collaborative editing, live presence, or notification-driven access grants.
- Provider-specific MCP tools, shared databases, private-module coupling, or a second REST contract.
- Bulk revoke/rotate, self-service tenant administration beyond listed connection/access actions, or destructive deletion.

## 1. Admin navigation and page anatomy

Add `Settings > Connections` and `Settings > Access & audit`.

### Connections page

Each connection row shows:

- provider: `Slack` or `External MCP`;
- status: `Connected`, `Needs attention`, or `Disconnected`;
- workspace/client name;
- approved destination, scopes, or capabilities in plain language;
- installed/created by, last verified, and last used;
- actions: `Reconnect`, `Rotate credentials`, `Revoke`.

Primary action: `Connect Slack` or `Connect external MCP`.

Never display access tokens, refresh tokens, client secrets, authorization
codes, or credential material. Technical identifiers belong in
`Advanced details` and are truncated with copy disabled by default.

### Access & audit page

Show filters for document, person, action, and time. Each row states who did
what, to which document, when, and whether the action succeeded. Do not make
an administrator infer access from Slack delivery status.

## 2. Slack install flow

### Start

1. Admin selects `Settings > Connections > Connect Slack`.
2. Show the destination workspace, one approved notification destination, requested scopes, and why each is needed:
   - `chat:write`: send a document-share notification;
   - `links:write`: do not request it for the first safe-link-only flow; request
     only after controlled Slack link unfurling is separately approved.
3. Show: `Folio will send a notification only after you share a document with
   a named person. Folio will not read channel history or post outside the
   approved destination.`
4. Admin selects `Continue to Slack`.

### OAuth callback

1. Generate a one-time `state`, bind it to the signed-in tenant/admin/session,
   and use the registered HTTPS redirect URI.
2. Redirect to Slack OAuth authorization. Never ask the admin to paste a Slack
   token into Folio.
3. On callback, validate `state`, provider/team identity, redirect target, and
   returned scopes server-side.
4. Exchange the authorization code server-side. Store provider credentials in
   the server-side secret store, encrypted at rest; never return them to the
   browser, URL, logs, audit text, or artifact content.
5. Verify the connection with a provider identity check that sends no
   document content.
6. Create the tenant-scoped connection only after verification succeeds.
7. Return to `Connections` with:
   `Slack connected to {workspace}. Folio can now send share notifications.`

### Install cancellation/failure

- Admin denies Slack consent: `Slack was not connected. No Folio access changed.`
  Action: `Try again`.
- State/redirect mismatch: `We could not verify this connection. Nothing was
  connected.` Action: `Start again`; create a security audit event.
- Missing required scope: `Slack did not grant the permission Folio needs.`
  Action: `Reconnect`; do not create a partial connection.
- Provider unavailable: `Slack could not be reached. No connection was changed.`
  Action: `Try again`.
- Existing connection: `Slack is already connected to {workspace}.` Actions:
  `Reconnect`, `Rotate credentials`, `Revoke`.

## 3. External-MCP connection flow

### Start

1. Admin selects `Settings > Connections > Connect external MCP`.
2. Show the server endpoint, transport, TLS/identity evidence, protocol
   version, and tenant that will own the connection. The browser never calls
   the endpoint directly; the egress gateway performs all discovery.
3. Show the capability categories the admin may review: named tools, resource
   URI patterns, read/write/destructive classification, data classification,
   input/output limits, rate limits, and expiry.
4. Show: `Discovery is information only. Nothing is enabled until you review
   and approve specific capabilities.`
5. Admin selects `Review server`.

### Discovery and consent

1. Broker performs `initialize`, `tools/list`, and `resources/list` through the
   approved egress path. Treat returned names, descriptions, annotations,
   resource URIs, and server instructions as untrusted metadata.
2. Show discovered items with `Not enabled` state. `listChanged` cannot widen
   the allowlist.
3. Admin selects exact tools and resource URI patterns to enable, records
   read/write/destructive class, sets limits/expiry, and confirms:
   `Connect {server} for {tenant}? Only the capabilities you approve can run.`
4. If the server supports OAuth, use authorization-code + PKCE with exact
   redirect registration and bind `state`, `code_verifier`, redirect URI,
   resource/audience, tenant, and connection to the pending transaction.
   Otherwise select an approved secret-manager reference; the broker alone may
   retrieve and inject it.
5. Exchange credentials server-side. Never display, log, or pass through the
   Folio session or inbound bearer token.
6. Verify identity, TLS, and capability policy with a bounded no-content
   check. Create or enable the connection only after verification succeeds.
7. Show `Connected: {server}` with enabled capabilities, created by, last used,
   expiry, policy version, and `Revoke` / `Rotate credentials`.

### External-MCP connection errors

- Endpoint blocked: `This server address needs security review before it can be connected.` Action: `Review server`.
- TLS or identity failure: `This server could not be verified. Nothing was enabled.` Action: `Start again`.
- Discovery failure: `We could not inspect this server. Nothing was enabled.` Action: `Try again`.
- Consent denied: `Connection was not created. No access changed.` Action: `Try again`.
- Invalid client/redirect: `This connection could not be verified. Contact your administrator.` Do not reveal provider configuration details.
- Wrong resource/audience: `This credential is for a different server. Nothing was enabled.` Action: `Start again`.
- Missing capability: `This connection is active, but {capability} is not enabled.` Action: `Review connection`.
- Broker unavailable: `The secure connection service is unavailable. Nothing was enabled.` Action: `Try again`.
- Failed verification: `Connection was not verified. Nothing is enabled.` Action: `Try again`.

## 4. Share and Slack notify flow

1. Owner selects `Share` on a private document.
2. Dialog shows document name, current `People with access`, and privacy
   consequence before input.
3. Admin/owner resolves a named same-tenant person; unresolved identity cannot
   be silently invited.
4. Select `Can view` or `Can edit`; show expiry when policy allows it.
5. Confirmation copy:
   `Share “{document}” with {person} as {role}? They will be able to {effect}.`
6. On confirm, create the access grant first. Grant success is independent from
   notification delivery.
7. If Slack is connected, the approved destination is available, and the user
   chooses notify, send a message with:

   - top-level accessibility text: `A Folio document was shared with you. Open
     it in Folio: {safe_link}`;
   - optional button label: `Open document`;
   - safe link only; no document bytes, excerpt, tenant ID, bearer token, or
     authorization decision;
   - `unfurl_links=false` and `unfurl_media=false` for the first slice;
   - no private content in block fields, alt text, notification preview, or
     logs.

8. Success copy:
   `Shared with {person}.` Then show `Copy safe link` and notification result.
9. Notification failure copy:
   `Access was updated, but the notification did not send.` Actions:
   `Copy safe link`, `Retry notification`. Do not revoke access automatically.
10. If retry succeeds: `Notification sent to {destination}.` If retry fails,
    preserve access and show a request ID in `Advanced details`.

## 5. Safe-link flow

### Create/copy

- Button: `Copy safe link`.
- Supporting copy: `This link helps someone find the document. It does not
  grant access.`
- URL contains only an opaque navigation handle and an allowlisted destination;
  never content, tenant ID, token, role, or authorization result.
- Use HTTPS, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` for
  sensitive responses, and reject open redirects.

### Open

1. Browser requests the Folio route.
2. If the session is valid and the grant is active, open the document.
3. If unauthenticated, sign in with the organization provider, preserve only
   the allowlisted destination, then re-check access server-side.
4. If access is absent, expired, or revoked, show:
   `This document is not available to you.`
5. Do not reveal whether the handle was invalid, private, revoked, or from
   another tenant.

## 6. Revoke flow

1. Owner/admin opens `People with access`.
2. Selects a named person and `Remove access`.
3. Confirmation names document, person, role, and effect:
   `Remove {person}’s access to “{document}”? They will lose access now. Copies
   they already downloaded cannot be recalled.`
4. On confirm, revoke server-side across current content, versions, graph
   reachability, search/index results, previews, attached MCP access, cached
   responses, safe-link checks, and audit visibility as applicable.
5. Existing Slack messages remain as messages; their safe link stops opening.
6. Success: `Access removed from {person}.` Show updated access list and audit
   entry.
7. Stale update: `Access changed while you were here. Refresh to see the latest
   list.` Reload; never overwrite another access change.

## 7. Rotate/reconnect flow

### Provider connection

1. Admin selects `Rotate credentials`.
2. Confirmation:
   `Rotate credentials for {connection}? Slack/MCP calls may pause briefly.
   Document access grants will not change.`
3. Server starts provider rotation or reauthorization. If the provider requires
   OAuth reauthorization, route through the install/connect flow with a fresh
   state and PKCE transaction.
4. Validate the new credential with a no-content readiness check.
5. Switch credentials atomically only after validation; invalidate the old
   credential and any old refresh material according to provider policy.
6. Log actor, connection, provider, result, and time; never log secrets.
7. Success: `Credentials rotated. Last verified just now.`
8. Cancel/failure: `Credentials were not changed.` Keep the old verified
   connection active and offer `Try again`.

### Revoke connection

- Confirmation: `Disconnect {connection}? Notifications/client calls will stop.
  Existing document access grants remain unchanged.`
- Success: `Disconnected {connection}.` Existing share grants remain visible;
  only the integration path is disabled.
- Reconnect is a new verified OAuth flow, never a token paste.

## 8. Error contract and trust presentation

Every UI/API error carries stable `code`, safe human `message`, `request_id`,
and `retryable`; authorization failures may carry `reauthenticate: true`.
Human copy stays short, names the next action, and never leaks private object
existence. Show request IDs only under `Advanced details` or support copy.

Required states:

| State | Exact copy | Action |
| --- | --- | --- |
| Slack not installed | `Connect Slack to send share notifications.` | `Connect Slack` |
| Connection needs attention | `Slack needs attention before it can send notifications.` | `Reconnect` |
| Notification pending | `Sending notification…` | No duplicate submit. |
| Notification sent | `Notification sent.` | `View connection`, `Copy safe link` |
| Notification failed | `Access was updated, but the notification did not send.` | `Retry notification`, `Copy safe link` |
| Link unavailable | `This document is not available to you.` | `Return to Library` |
| Session expired | `Your session expired. Sign in again.` | `Sign in again` |
| Rotate failed | `Credentials were not changed.` | `Try again` |
| Revoke stale | `Access changed while you were here. Refresh to see the latest list.` | `Refresh` |
| Service unavailable | `We couldn’t complete that connection. No access was changed.` | `Try again` |

Use visible focus, field-level text errors, `aria-live` status messages, and
keyboard-reachable recovery actions. Never make a shield, lock, color, or Slack
logo the only trust cue.

## Evidence-to-decision register

| Evidence | Direct source | UX decision |
| --- | --- | --- |
| Slack message payloads need accessible top-level fallback text; link/media unfurling is separately controllable. | [Slack `chat.postMessage`](https://docs.slack.dev/reference/methods/chat.postMessage) | Send generic top-level text, safe link only, `unfurl_links=false`, and `unfurl_media=false`. |
| Slack link unfurling has its own permission and product behavior. | [Slack link unfurling](https://docs.slack.dev/messaging/unfurling-links-in-messages/) | Do not request `links:write` or promise previews in the thin slice. |
| Slack OAuth is an authorization/install flow, not a token-paste UX; the server exchanges the temporary code. | [Slack OAuth installation](https://docs.slack.dev/authentication/installing-with-oauth/) | Redirect admin to Slack, validate callback, exchange server-side, store credentials server-side. |
| Slack token rotation uses expiring access tokens and replaces refresh tokens; uninstall revokes installation tokens. | [Slack token rotation](https://docs.slack.dev/authentication/using-token-rotation/) | Keep rotation/revocation server-side, switch only after verification, and keep document grants unchanged. |
| MCP authorization requires protected-resource and authorization-server discovery, resource indicators, token audience validation, no token passthrough, PKCE, and distinct `401`/`403` recovery. | [MCP authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization) | Connect one approved external server through the broker; bind credentials to that resource; show reauth vs permission errors. |
| OAuth metadata and PKCE have standardized discovery and code-verifier flows. | [RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414), [RFC 7636](https://datatracker.ietf.org/doc/html/rfc7636) | Keep state, verifier, redirect, resource, tenant, and client bound to one pending connection; never expose codes or tokens. |
| OAuth security guidance favors authorization-code + PKCE, exact redirect binding, and refresh-token protection/rotation. | [OAuth security BCP](https://datatracker.ietf.org/doc/html/rfc9700) | Reconnect/rotate through a fresh verified authorization flow; atomically switch only after readiness succeeds. |
| Object identifiers do not replace object-level authorization. | [OWASP IDOR prevention](https://cheatsheetseries.owasp.org/cheatsheets/Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.html) | Re-check document access on every safe-link, graph, search, preview, version, and attached-client read. |
| Sessions require secure handling, server-side invalidation, logout/expiry behavior, and no-store for sensitive responses. | [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) | Session expiry gets explicit reauth copy; revoke/connection disconnect invalidates server-side access; sensitive routes are not cached. |
| Sensitive URL paths can leak through referrers; `no-referrer` prevents that channel. | [MDN Referrer-Policy](https://developer.mozilla.org/en-US/docs/Web/Security/Practical_implementation_guides/Referrer_policy) | Safe links carry no secret/content and responses use `Referrer-Policy: no-referrer`. |
| Status and error messages must be announced and identified in text, not by color alone. | [WCAG status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages), [WCAG error identification](https://www.w3.org/WAI/WCAG22/Understanding/error-identification) | Every async outcome has exact visible copy, live status, focus recovery, and a next action. |

## 10. Historical repository evidence at original handoff baseline

Baseline resolves to [`861eda6d1e3536c6791dbe1d6e1022ad1c0f5106`](https://github.com/waddle-zoo/folio-lattice/commit/861eda6d1e3536c6791dbe1d6e1022ad1c0f5106),
`feat(ui): add artifact access controls`. It proves the document-sharing UI
slice only. It does not prove Slack OAuth, an external-MCP registry, a secret
manager, an egress gateway, safe-link delivery, or connector rotation.

This section is historical evidence from the original handoff. Do not use it
as a current-main claim; the protected-main audit and current boundaries are in
the companion audit dated 2026-09-19.

| Exact evidence | What it proves | Boundary |
| --- | --- | --- |
| [`inspection.py#L300-L345`](https://github.com/waddle-zoo/folio-lattice/blob/861eda6d1e3536c6791dbe1d6e1022ad1c0f5106/src/folio_lattice/inspection.py#L300-L345) | `401` shows sign-in recovery; `403` shows the same non-enumerating unavailable copy; error receives focus. | Browser/UI state only; no hosted connector. |
| [`inspection.py#L445-L489`](https://github.com/waddle-zoo/folio-lattice/blob/861eda6d1e3536c6791dbe1d6e1022ad1c0f5106/src/folio_lattice/inspection.py#L445-L489) and [`inspection.py#L565-L573`](https://github.com/waddle-zoo/folio-lattice/blob/861eda6d1e3536c6791dbe1d6e1022ad1c0f5106/src/folio_lattice/inspection.py#L565-L573) | UI reads ACL state, labels `Can view`/`Can edit`, shares, revokes, reloads, and announces outcomes. | Does not send Slack notifications or create safe links. |
| [`public_mcp.py#L10-L24`](https://github.com/waddle-zoo/folio-lattice/blob/861eda6d1e3536c6791dbe1d6e1022ad1c0f5106/src/folio_lattice/public_mcp.py#L10-L24) | `artifact_share`, `artifact_revoke`, and `artifact_acl` are in the public tool allowlist. | No external-MCP connection or credential path exists. |
| [`service.py#L255-L305`](https://github.com/waddle-zoo/folio-lattice/blob/861eda6d1e3536c6791dbe1d6e1022ad1c0f5106/src/folio_lattice/service.py#L255-L305) | Server-side actor/action checks and active-grant filtering gate artifact access. | This is artifact ACL evidence, not connector policy evidence. |
| [`test_acl.py#L24-L95`](https://github.com/waddle-zoo/folio-lattice/blob/861eda6d1e3536c6791dbe1d6e1022ad1c0f5106/tests/test_acl.py#L24-L95) | Private-by-default read/search/grep, named share, write role, revoke, restart durability, and revoked-read denial. | No Slack, external-MCP, safe-link, or egress test. |
| [`test_acl.py#L97-L120`](https://github.com/waddle-zoo/folio-lattice/blob/861eda6d1e3536c6791dbe1d6e1022ad1c0f5106/tests/test_acl.py#L97-L120) | Cross-tenant share is rejected. | Does not establish hosted connector identity. |
| [`test_web.py#L233-L277`](https://github.com/waddle-zoo/folio-lattice/blob/861eda6d1e3536c6791dbe1d6e1022ad1c0f5106/tests/test_web.py#L233-L277) | Static UI exposes access controls, public tools, accessible status, and omits debug/credential markers. | No connector implementation. |
| [`test_browser_e2e.py#L764-L832`](https://github.com/waddle-zoo/folio-lattice/blob/861eda6d1e3536c6791dbe1d6e1022ad1c0f5106/tests/test_browser_e2e.py#L764-L832) | Real-browser evidence covers `People with access`, `Can view`, share, revoke, empty state, and generic `403` recovery. | No Slack/provider callback or external-MCP broker path. |

Required handoff validation: the audit candidate must record the exact protected
main SHA it inspected; `git diff --check` must be clean for tracked changes;
direct URLs must remain live; and this handoff must retain the explicit “not
proved” boundary. No Python or service-boundary files are changed by this
handoff.

## 11. Prioritized handoff

### P0 — `ux_visual`

1. Design `Settings > Connections` list and detail states: connected, needs
   attention, reconnect, rotate confirmation, revoke confirmation, and failure.
2. Design Slack install consent, share confirmation, notification success,
   partial failure, retry, and safe-link copy states.
3. Design external-MCP endpoint review, quarantined discovery, explicit
   capability consent, session-expired, wrong-resource, and verification states.
4. Annotate exact copy, focus order, privacy consequences, request-ID location,
   and no-token display rule in light/dark and mobile layouts.

### P0 — `ux_usability`

1. Admin installs Slack and can explain requested scope and notification-only
   behavior.
2. Owner shares a private document, chooses `Can view`, and understands that
   grant success and Slack delivery are separate.
3. Recipient opens a safe link while authorized, then after revocation, and
   receives the same unavailable copy without leakage.
4. Admin connects one external MCP server with brokered OAuth/PKCE or an
   approved secret reference; can find capabilities, last used, revoke, and
   rotate controls.
5. Admin rotates credentials, cancels once, completes once, and understands
   that document grants are unchanged.
6. Participants recover from denied consent, expired session, notification
   failure, stale revoke, and provider outage without pasting a token.

### P1 — both

- Validate keyboard/screen-reader status announcements, 200% zoom, mobile
  reflow, and dark theme.
- Measure completion, time, help count, privacy/authorization misunderstanding,
  and unsafe-token behavior.
- Record redacted screenshots and browser/device versions in the existing UX
  evidence register.

## Primary sources

- [Folio UX contract](2026-09-07-hyperset-first-consumer-ux-contract.md)
- [ADR 0012: private tenancy, ACL grants, and revocation](../adr/0012-private-tenancy-acl-and-revocation.md)
- [ADR 0013: external MCP registry and credential brokerage](../adr/0013-external-mcp-registry-and-credential-brokerage.md)
- [ADR 0014: egress boundary, Slack connector, and TLS proxy](../adr/0014-egress-slack-and-tls-boundary.md)
- [Slack OAuth installation](https://docs.slack.dev/authentication/installing-with-oauth/)
- [Slack token rotation](https://docs.slack.dev/authentication/using-token-rotation/)
- [Slack scopes](https://docs.slack.dev/reference/scopes/)
- [Slack `chat.postMessage`](https://docs.slack.dev/reference/methods/chat.postMessage/)
- [Slack link unfurling](https://docs.slack.dev/messaging/unfurling-links-in-messages/)
- [MCP authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
- [MCP tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [OAuth 2.0](https://datatracker.ietf.org/doc/html/rfc6749)
- [PKCE](https://datatracker.ietf.org/doc/html/rfc7636)
- [OAuth authorization-server metadata](https://datatracker.ietf.org/doc/html/rfc8414)
- [OAuth security BCP](https://datatracker.ietf.org/doc/html/rfc9700)
- [OWASP IDOR prevention](https://cheatsheetseries.owasp.org/cheatsheets/Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.html)
- [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [MDN Referrer-Policy](https://developer.mozilla.org/en-US/docs/Web/Security/Practical_implementation_guides/Referrer_policy)
- [WCAG status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages)
- [WCAG error identification](https://www.w3.org/WAI/WCAG22/Understanding/error-identification)
