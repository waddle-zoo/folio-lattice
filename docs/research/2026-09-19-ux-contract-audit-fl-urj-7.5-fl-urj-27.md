# UX contract audit: admin, sharing, and connections

- Scope: `fl-urj.7.5` and `fl-urj.27`
- Audit snapshot: checked-out `main` at `f75107d7c4fbf912d69bd24d6decdb4703730509`
- Compared with: `MANIFESTO.md`, ADRs 0010–0015, and the existing UX handoff
- Status: research/contract deliverable; implementation remains gated

## Decision

Protected `main` proves a local/fixture inspection workspace, a service
sign-in route and auth-state rendering, an artifact ACL slice, and a partial
external-MCP connection administration surface. It does not prove hosted
identity, safe-link continuation, Slack OAuth/notification delivery, or a
production credential broker. Treat those hosted surfaces as unimplemented
until the tasks and evidence below land.

Do not describe the current share form or local Connections page as the hosted
sharing or connector experience. The share form is a development ACL control
with technical identifiers and no notification or safe-link contract. The
Connections page is a local/fixture admin path for an external-MCP registry;
it is not Slack OAuth or production credential mediation. Do not add a Slack
button, token field, public link, or generic connection URL as a stopgap: ADRs
0012–0014 require named same-tenant access, brokered credentials, explicit
capability approval, bounded egress, and independent audit outcomes.

This audit closes the research/contract side only. It does not change the
product boundary or mark any hosted gate passed.

## Evidence from protected `main`

### Aligned or useful foundation

- `src/folio_lattice/inspection.py` labels local mode as
  `Unauthenticated local development` and tells operators not to expose it to
  untrusted users. This follows the local-versus-hosted honesty rule in the
  manifesto and ADR 0010.
- The UI uses `no-store` and `no-referrer` control headers, a separate preview
  origin, an opaque iframe sandbox, and a narrow public-MCP adapter. These are
  useful foundations for the public-contract and sandbox boundaries.
- Artifact creation is presented as a first version, editing says
  `Saving creates a new version`, and the access card says `Private`. The ACL
  service and browser evidence cover private-by-default read/search behavior,
  named sharing, role labels, revoke, and generic unavailable-state recovery.
- Authentication failures have a visible recovery action. The browser state
  preserves only an allowlisted return path in the test harness and uses
  `Your session expired. Sign in again.` for an expired session.
- `src/folio_lattice/connections.py` and the web/browser tests provide a
  local/fixture `Settings > Connections` path with exact tool/resource
  registration, HTTPS-origin checks, status, revoke, connection audit, and
  secret-free returned state. This is an implementation slice, not hosted
  readiness evidence.
- `src/folio_lattice/external_mcp.py` provides endpoint fencing, exact
  allowlists, a bounded broker seam, credential redaction, status/revoke, and
  audit behavior. Production secret-manager selection, OAuth/PKCE, discovery
  quarantine, capability expiry/rotation, and hosted identity binding remain
  open.
- `src/folio_lattice/slack.py` and its tests cover a bounded approved
  `slack.search`/provenance-save adapter fixture. It is not Slack Web API
  OAuth, `chat.postMessage`, or share-notification delivery.
- `docs/qa/audit-lifecycle.md` and the audit tests cover bounded MCP audit
  export, redaction, retention, and purge. They do not establish the full
  hosted document Access & audit UX.

### Partial: artifact sharing is not yet the hosted UX contract

Current UI locations: `#sharing`, `#share-recipient`, `#share-role`,
`artifact_share`, and `artifact_revoke` in `inspection.py`.

Observed gaps:

1. The input is `Person identifier` with `person-id` placeholder. It does not
   resolve a named same-tenant directory identity before confirmation.
2. `Share` submits immediately. There is no confirmation naming the document,
   recipient, role, effect, expiry, or notification outcome.
3. Share success exposes the raw subject identifier in
   `Shared with {subject}.`; the normal user contract requires a human-readable
   name.
4. `Remove access` has no confirmation. It does not state that access stops
   now, that downloaded copies cannot be recalled, or that the operation is
   independent from connection state.
5. There is no expiry display, safe-link control, notification choice, audit
   result, stale-update recovery, idempotency state, or partial-success state.
6. A generic `403` handler hides the workspace and says the document is
   unavailable for every protected action. This is non-enumerating but cannot
   distinguish “document is unavailable” from “you cannot change access”. The
   final contract needs safe recovery without leaking object existence.
7. The gateway error body is currently a plain `{"error": ...}` response. The
   hosted contract requires stable `code`, safe `message`, `request_id`, and
   `retryable` fields for new UI/API errors.

### Blocked: hosted sharing and provider integrations remain unproved

- `Settings > Connections` exists for the local/fixture external-MCP path and
  has register/list/status/revoke/audit controls. There is no separate full
  document `Access & audit` surface, and no hosted or independent accessibility
  evidence for the target contract.
- No Slack OAuth install, workspace/destination approval, notification sender,
  preview suppression, reconnect, rotation, or independent disconnect state is
  implemented.
- External-MCP exact allowlists, endpoint fencing, status/revoke, and audit are
  implemented in the local/fixture path. Production secret-manager selection,
  OAuth/PKCE, discovery quarantine, capability expiry/rotation, and hosted
  TLS/identity evidence remain unimplemented.
- No safe-link route exists. `/sign-in` is now served by `InspectionApp`, but
  safe-link continuation and a hosted identity-provider flow remain unproved.
- Hosted-auth rendering hides the local warning and uses normal navigation;
  debug/admin context still exposes `Organization`/`Actor` values, and the
  share form remains technical. The target contract keeps technical IDs out
  of ordinary document flows.
- Tests now cover local/fixture connection API/browser paths and secret-free
  states, but no test proves Slack OAuth/notification, safe-link continuation,
  production hosted connections, or the independent usability/accessibility
  evidence required for sign-off.

### ADR and manifesto reading

| Requirement | Current result | Gate consequence |
| --- | --- | --- |
| Manifesto: local UI inspects the lifecycle; MCP remains the contract | Partial pass | Keep UI and Connections calls on the public MCP adapter. Do not add a private REST model for admin/share. |
| Manifesto/ADR 0012: private by default and named same-tenant grants | ACL foundation only | Add identity resolution, confirmation, expiry, full-path revocation, and audit evidence before hosted sharing sign-off. |
| ADR 0011: verified principal owns tenant/actor context | Sign-in route and fixture auth-state rendering exist; hosted lifecycle is open | Add real hosted sign-in/session lifecycle. Never let form fields or safe links select tenant, actor, or role. |
| ADR 0013: admin-managed external MCP registry and broker | Local/fixture registry, exact allowlists, status/revoke, and audit exist; production authorization is open | Add brokered OAuth/PKCE or approved secret-manager reference, discovery quarantine, expiry/rotation, and hosted identity evidence. |
| ADR 0014: Slack is named, approved egress | Bounded provider-neutral search adapter exists; Slack notification integration is not implemented | Design OAuth, workspace/destination policy, no-preview payload, rotation, revoke, and bounded retry states. |
| ADR 0015: attributable audit without content/secrets | Bounded MCP export and connection activity exist; full hosted document audit UX is open | Add Access & audit with safe event language and redacted technical details. |

## Concrete nontechnical UX tasks

These are the smallest tasks that can be handed to UX visual, UX usability,
product, and implementation owners. Each task has a user-visible artifact and
an acceptance check. Technical controls remain owned by the service/security
work described in the ADRs.

### P0 — document access and recovery

1. **Private document/share dialog**
   - Design a dialog with document name, current `People with access`, named
     same-organization person search, `Can view`/`Can edit`, optional expiry,
     and a notification choice only when an approved connection exists.
   - Show the privacy consequence before submit. Confirm exact recipient and
     effect. Keep the form usable without Slack.
   - Acceptance: participant can explain who gains access, what `Can edit`
     means, and that saving creates a new version rather than overwriting
     history.

2. **Grant and notification outcomes**
   - Design separate success states for access and delivery. The first state
     must remain successful if notification delivery fails.
   - Acceptance: participant chooses `Copy safe link` or `Retry notification`
     without revoking a successful grant.

3. **Safe link and revoke**
   - Design `Copy safe link`, sign-in continuation, authorized open, revoked
     open, expired grant, and invalid-link recovery.
   - Add revoke confirmation with person, document, role, immediate effect, and
     downloaded-copy residual risk. Existing Slack messages stay visible while
     their link stops opening.
   - Acceptance: authorized and revoked participants receive the same
     unavailable copy; URL itself is understood not to grant access.

4. **Access & audit**
   - Design `Settings > Access & audit` with filters for person, document,
     action, result, and time. Show who did what, when, and whether it worked.
   - Do not make admins infer document access from Slack delivery. Do not show
     document text, tokens, raw headers, or secret values in the default view.
   - Acceptance: a nontechnical admin can answer “who can open this document?”
     and “did the notification send?” as separate questions.

### P0 — Slack connection

5. **Slack install consent**
   - Design `Settings > Connections > Connect Slack` with workspace identity,
     one approved notification destination, requested minimum scope, and the
     statement that Folio sends only share notifications and does not read
     channel history.
   - Use provider redirect/OAuth. Never design token paste, token display, or a
     browser-side code exchange.
   - Acceptance: admin can state what Folio can send, where it can send it, and
     what it cannot read or post.

6. **Slack lifecycle**
   - Design connected, needs attention, reconnect, rotate, disconnect, consent
     denied, missing scope, provider outage, and verification failure states.
   - Keep document grants unchanged across rotate, reconnect, and disconnect.
   - Acceptance: admin can reconnect or rotate without confusing connection
     access with document access.

7. **Slack notification**
   - Design generic accessible fallback text, `Open document`, safe-link-only
     payload, previews disabled, pending, sent, failed, and retry states.
   - Acceptance: user understands `Access was updated, but the notification did
     not send.` and sees both recovery actions.

### P0 — external MCP connection

8. **Connection review**
   - Design `Connect external MCP` with endpoint/TLS identity review,
     quarantined discovery, `Not enabled` capability rows, exact tools/resource
     patterns, read/write/destructive labels, limits, expiry, and explicit
     confirmation.
   - State plainly that discovery is information only and nothing runs until
     capabilities are approved.
   - Acceptance: admin can name which capability is enabled and can find its
     expiry/revoke action without seeing a credential.

9. **Brokered authorization and lifecycle**
   - Design OAuth/PKCE or approved secret-reference choice, wrong-resource,
     TLS/identity failure, blocked endpoint, broker outage, rotation, and
     revoke states.
   - Acceptance: admin never pastes or copies a token and understands that
     revoking the connection does not remove document grants.

### P1 — validation and accessibility

10. **Usability run**
    - Run these six participant tasks: create private document; share as `Can
      view`; interpret Slack failure; open safe link before/after revoke; install
      Slack; review/revoke one external capability.
    - Record completion, help count, privacy misunderstanding, exact error
      copy, device/browser, and recovery action. Target at least 8/9 contract
      tasks independently completed, zero privacy/authorization
      misunderstandings, and no critical keyboard/screen-reader blocker.

11. **Accessible state review**
    - Verify visible focus, labels, field errors, `aria-live` outcomes, keyboard
      order, 200% zoom, mobile reflow, dark theme, and non-color-only status.
    - Run the same paths in local and hosted modes. Local warning must remain
      explicit; hosted UI must not say `Local workspace`.

## Safe language contract

Use plain terms in document and notification flows. Keep technical terms in
`Advanced details` and administrator connection review only.

### Replace current technical language

| Current or unsafe label | Use instead | Why |
| --- | --- | --- |
| `Person identifier` / `person-id` | `Person from your organization` / directory search | A person chooses a named identity, not an internal key. |
| `Artifact` in ordinary UI | `Document` or `file`; keep `artifact` in advanced details | Matches user mental model without changing public contract vocabulary. |
| `Local workspace` in hosted view | `Workspace` | Does not imply hosted deployment is local or unauthenticated. |
| `Organization: {tenant_id}` / `Actor: {actor_id}` | Signed-in person and organization name; IDs only in advanced/admin details | Do not expose authorization keys as ordinary identity cues. |
| `Can manage` in document access | Omit from this slice; use named administrator controls | Document owner/admin authority is not a normal document role. |
| `token`, `secret`, `bearer`, `ACL`, `tenant`, `endpoint` | `Secure connection`, `access`, `organization`, `server address` where needed | Keep secrets and implementation vocabulary out of nontechnical action paths. |

### Required copy

| State | Copy | Action |
| --- | --- | --- |
| New private document | `Private — Only you and people you choose can open this document.` | `Share` / `Copy safe link` |
| Safe-link helper | `This link helps someone find the document. It does not grant access.` | `Copy safe link` |
| Share confirmation | `Share “{document}” with {person} as {role}? They will be able to {effect}.` | `Share` / `Cancel` |
| Share success | `Shared with {person}.` | `Copy safe link`; show notification result separately |
| Notification unavailable | `Connect Slack to send share notifications.` | `Connect Slack` |
| Notification failure | `Access was updated, but the notification did not send.` | `Retry notification`; `Copy safe link` |
| Revoke confirmation | `Remove {person}’s access to “{document}”? They will lose access now. Copies they already downloaded cannot be recalled.` | `Remove access` / `Cancel` |
| Revoke success | `Access removed from {person}.` | `View access` |
| Safe link unavailable | `This document is not available to you.` | `Return to Library` |
| Session expired | `Your session expired. Sign in again.` | `Sign in again` |
| Slack install consent | `Folio will send a notification only after you share a document with a named person. Folio will not read channel history or post outside the approved destination.` | `Continue to Slack` |
| Slack connected | `Slack connected to {workspace}. Folio can now send share notifications.` | `View connection` |
| Slack denied | `Slack was not connected. No Folio access changed.` | `Try again` |
| Connection rotate failure | `Credentials were not changed.` | `Try again` |
| Connection revoke | `Disconnect {connection}? Notifications and client calls will stop. Existing document access grants remain unchanged.` | `Disconnect` / `Cancel` |
| External-MCP discovery | `Discovery is information only. Nothing is enabled until you review and approve specific capabilities.` | `Review server` |
| External-MCP unavailable | `The secure connection service is unavailable. Nothing was enabled.` | `Try again` |

Avoid these claims in UI, docs, screenshots, or demos until their evidence
exists: `secure link` when the URL itself grants access, `Slack connected` when
only a token was stored, `all Slack messages`, `public link`, `automatic
capabilities`, `admin can see everything`, and `enterprise-ready`.

## Gate evidence required before sign-off

The following evidence is the minimum contract package. A screenshot or unit
test alone cannot close these items.

- UX frames cover all P0 states above in desktop/mobile, light/dark, keyboard,
  and screen-reader annotations.
- Hosted browser run proves sign-in, share, safe-link open, revoke, and the
  same unavailable copy after revoke; URL and response captures show no token,
  tenant ID, content, or open redirect.
- Slack fixture proves OAuth state/redirect validation, approved workspace and
  destination, minimum scope, safe-link-only message, disabled previews,
  separate grant/notification outcomes, rotation, revoke, and no secret in
  browser, logs, audit, or evidence.
- External-MCP fixture proves brokered authorization, TLS/identity review,
  quarantined discovery, exact allowlists, bounded capabilities, rotation,
  revoke, and no credential exposure.
- Access/audit fixture proves named actor, target, decision, outcome, request
  ID, and time without raw document content or secrets; admin scope is
  tenant-scoped and read-minimized.
- `make check`, browser accessibility/usability run, and public MCP/Docker
  end-to-end checks pass on the same build. Any missing implementation remains
  a blocker, not a documentation pass.

## Handoff order

1. UX visual produces annotated frames and exact copy from this document.
2. Product/security approve roles, privacy consequences, and connection scope.
3. UX usability runs the six tasks and records defects against the acceptance
   thresholds.
4. Service/UI owners implement only the approved public-contract paths; no
   second REST model or connector shortcut.
5. QA attaches redacted browser, MCP, Slack, connection, and audit evidence.
6. Mayor decides whether the implementation and evidence close
   `fl-urj.7.5` and `fl-urj.27`.
