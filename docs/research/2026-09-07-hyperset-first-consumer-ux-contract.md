# Hyperset first-consumer and nontechnical UX contract

- Status: implementation-ready contract; no hosted UI or sharing implementation
- Date: 2026-09-07
- Scope: hosted sign-in, private artifacts, explicit sharing, Slack notification,
  safe links, recovery, accessibility, browser support, and usability evidence
- Product boundary: named same-tenant sharing, revocation, and administration
  follow [ADR 0012](../adr/0012-private-tenancy-acl-and-revocation.md).
  Anonymous/public and cross-tenant sharing remain out of scope. This document
  does not move hosted controls into the completed local v0 milestone.

## Contract outcome

Folio Lattice presents one authorization boundary to people and agents:

1. A person uses the hosted web UI and a browser session.
2. An MCP client uses the public HTTP MCP endpoint and an OAuth authorization
   flow that may open the same hosted sign-in UI.
3. Both surfaces resolve the same user, tenant, artifact, version, graph, and
   sharing policy. A UI route, MCP call, or Slack link never gets a special
   access path.
4. Hyperset remains an external consumer. It may build an adapter and its own
   Slack integration, but it must use Folio's public MCP/HTTP contract. It must
   not share Folio's database or blob volume, import private modules, or add
   Hyperset-specific tools to the core contract.

The first useful nontechnical experience is: sign in, create a private
document, share it with a named person, notify that person in Slack, open a
safe link, revoke access, and understand what happened when any step fails.

## Research basis

This contract was derived from the repository's accepted boundary documents and
the following public specifications and guidance:

- [ADR 0012: Private tenancy, ACL grants, and revocation](../adr/0012-private-tenancy-acl-and-revocation.md)
  supersedes ADR 0007 for hosted use. It makes tenant scope and private
  creation mandatory and requires named sharing to cover versions, graph
  reachability, derived previews/indexes, attached MCP access, auditability,
  expiration, and revocation.
- [ADR 0008: Hyperset is the first consumer through public contracts](../adr/0008-hyperset-first-consumer.md)
  forbids shared storage, private imports, authorization bypasses, and
  Hyperset-specific core tools.
- [ADR 0002: MCP-first knowledge interface](../adr/0002-mcp-first-knowledge-interface.md)
  and the current [`mcp_protocol.py`](../../src/folio_lattice/mcp_protocol.py)
  establish provider-neutral artifact, graph, search, read, and version tools.
- The [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
  defines HTTP authorization discovery, bearer tokens, `401`/`403` behavior,
  resource indicators, PKCE, and protection against token passthrough.
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) requires visible focus,
  consistent identification, labels or instructions, text error
  identification, and accessible status messages. The relevant explanations
  are [labels or instructions](https://www.w3.org/WAI/WCAG22/Understanding/labels-or-instructions),
  [error identification](https://www.w3.org/WAI/WCAG22/Understanding/error-identification),
  and [status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages).
- Slack's [message API](https://docs.slack.dev/reference/methods/chat.postmessage)
  says the top-level `text` is the screen-reader fallback when blocks are
  present. Slack's [link-unfurling guidance](https://docs.slack.dev/messaging/unfurling-links-in-messages/)
  documents `links:write` and default unfurl behavior.
- OWASP's [IDOR guidance](https://cheatsheetseries.owasp.org/cheatsheets/Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.html)
  requires object-level authorization on every access and says random
  identifiers do not replace authorization. OWASP's [session guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
  covers secure cookies, server-side invalidation, logout, and no-store
  handling.
- MDN's [Referrer-Policy guidance](https://developer.mozilla.org/en-US/docs/Web/Security/Practical_implementation_guides/Referrer_policy)
  identifies sensitive URL paths and parameters as a leakage risk and
  documents `no-referrer`. MDN's [Baseline compatibility definition](https://developer.mozilla.org/en-US/docs/Glossary/Baseline/Compatibility)
  is used as a compatibility input, not as a substitute for accessibility or
  usability testing.

Research conclusion: the thin slice should share named people, keep the
artifact private until an explicit grant exists, send a link that is only a
navigation handle, suppress Slack content previews, and make every access
decision on the Folio service side.

## Vocabulary and roles

| Term | Contract meaning |
| --- | --- |
| Artifact | A document or file with immutable versions and graph relationships. |
| Owner | Actor who created the artifact or received ownership through an explicit administrative action. |
| Viewer | May read permitted artifact content and permitted metadata. |
| Editor | May read and write new versions; writes remain immutable and provenance-bearing. |
| Administrator | May manage tenant membership, grants, and audit visibility. Administrator status alone does not grant content read access. |
| Grant | An explicit tenant-scoped permission from an owner or administrator to a named person or approved group. |
| Safe link | An opaque Folio navigation URL. It contains no content, tenant ID, bearer token, or authorization decision. |
| Recipient | Person named in a grant and, when applicable, the Slack destination used to notify them. |

The UI uses plain language (“document”, “people with access”, “can view”, and
“can edit”) and exposes technical terms such as “artifact”, “tenant”, and
“MCP” only in an administrator or connection context.

## Public UI and MCP contract

### Hosted sign-in

- An unauthenticated browser request shows a public sign-in page. The page
  offers the organization-approved identity provider without requiring a user
  to paste a token into a document, Slack message, or chat prompt.
- A link to a private artifact preserves only an allowlisted destination route
  through the sign-in transaction. It must reject open redirects and must not
  carry artifact content or an access token in the URL.
- The hosted web session is established only over HTTPS. The session cookie is
  `Secure`, `HttpOnly`, and `SameSite=Lax` or stricter, with a narrow host
  scope. Sensitive responses use `Cache-Control: no-store`.
- Sign out invalidates the server-side session and clears the browser cookie.
  Session expiry shows “Your session expired. Sign in again.” and returns the
  person to the safe destination after successful reauthentication.
- The server derives actor and tenant context from the authenticated session.
  Hosted callers cannot select another `tenant_id`, actor, or role by adding a
  field to a request.

### Public HTTP MCP

- Hosted MCP is exposed at the public `/mcp` endpoint. Authorization discovery
  uses the MCP/OAuth metadata mechanisms; the exact issuer may be hosted by
  Folio or by an organization identity service.
- An unauthenticated request returns `401` with the discovery information a
  conforming MCP client needs. Invalid or expired credentials remain `401`;
  valid credentials without a required permission are `403`.
- The client opens a browser for authorization code plus PKCE, requests a token
  for the canonical Folio MCP resource, and sends the access token in the
  `Authorization: Bearer` header on every request. Tokens never appear in a
  query string, safe link, Slack message, log, or artifact content.
- Authorization is checked before tool execution and is bound to the target
  Folio MCP resource. Folio never forwards an inbound MCP token to another
  service.
- The public tool set remains provider-neutral: the existing artifact create,
  write, read, chunk, search, grep, graph link, graph traverse, and version
  operations. Sharing, notification, and administration may be added only as
  generic contract operations after their authorization semantics are defined;
  no tool name or payload may mention Hyperset.
- The UI and MCP responses must agree on object visibility. A private artifact
  must be absent from another tenant's list, search, graph traversal, version
  history, previews, and error details.

### Stable error shape

Every new UI/API contract error has a stable machine-readable `code`, a safe
human `message`, a `request_id`, and `retryable`. Validation errors may include
`field_errors`; authorization errors may include `reauthenticate: true`. Error
messages never disclose whether a private artifact exists to an unauthorized
actor.

The MCP transport retains JSON-RPC semantics. HTTP status and error code are
the recovery contract; prose may be localized or improved without changing
the code.

## Nontechnical user flows

### 1. Create a private artifact

1. Signed-in person selects “New document” or uploads a file.
2. Form asks for a plain-language name, optional description, and file. Labels
   explain supported content and size limits before submission.
3. On success, the document page shows `Private` beside the title and says
   “Only you and people you choose can open this document.”
4. The owner sees “Share” and “Copy safe link”. Copying a safe link does not
   grant access; it only copies a navigation URL for someone who already has a
   grant.
5. Creation generates one immutable version and an audit event. A failed
   notification cannot turn a successful private creation into a shared one.

### 2. Share with a named person

1. Owner or administrator selects “Share”. The dialog names the document,
   current access list, and the privacy consequence before any input.
2. Person enters an email or approved directory identity. The UI resolves it
   to a human-readable name before confirmation; unresolved identities cannot
   be silently invited.
3. Person chooses `Can view` or `Can edit`, and optionally an expiry if the
   tenant policy permits it. “Can edit” means creating a new version, not
   overwriting history or changing ownership.
4. Confirmation says exactly who will gain access, which role they get, and
   whether a Slack notification will be sent.
5. Success shows “Shared with <name>” and exposes “Copy safe link” and “Undo
   access”. The grant, policy decision, actor, expiry, and notification result
   are auditable.

Sharing an artifact grants the artifact identity and its permitted current and
   future versions. Every version, graph edge, derived preview/index, and
   attached MCP capability is independently checked against the same grant.
   A raw blob URL is never a share primitive.

### 3. Revoke access

1. Owner or administrator selects a person in “People with access” and chooses
   “Remove access”. The confirmation names the person, role, document, and
   effect: “They will lose access now. Copies they already downloaded cannot
   be recalled.”
2. The service revokes the grant before reporting success. New UI requests,
   MCP calls, safe-link visits, searches, graph traversals, previews, and
   downloads fail closed.
3. Existing browser sessions are rechecked at the service boundary; revocation
   does not rely on clearing a client cache. Slack messages remain as history,
   but their links no longer open the document.
4. Success is announced as a status message and recorded in the audit trail.
   If the request fails, the original grant remains and the UI says so.

### 4. Administrator tasks

Administrators can:

- view tenant members and their role;
- approve or remove membership according to tenant policy;
- view which people have access to an artifact;
- grant or revoke artifact access when policy permits; and
- inspect audit entries for sign-in, creation, grant, revocation, failed
  authorization, and notification outcomes.

Administrator status does not silently grant document content. Any future
break-glass read path needs an explicit security decision, visible reason,
time limit, and audit event. It is not part of this thin slice.

### 5. Slack notification and safe link thin slice

Slack is an adapter concern, not a Folio core abstraction. Hyperset or a
separately named integration surface may call Slack after Folio confirms a
grant through the public contract.

Thin-slice behavior:

1. After a named grant succeeds, the adapter sends a DM to the intended
   recipient or uses a pre-approved destination whose membership has already
   been checked. It does not post private artifact details to an arbitrary
   channel.
2. The message includes a minimal fallback `text`: “A document was shared
   with you in Folio Lattice. Open Folio Lattice to view it.” The rich block
   has the same meaning and a button with an accessible label such as “Open
   shared document”. A document title is included only when the destination
   is authorized to receive that metadata.
3. The message contains the safe link only. It contains no artifact bytes,
   excerpt, tenant ID, database key, access token, or secret. The Folio link
   checks the signed-in recipient's current grant; the URL itself is not
   authorization.
4. The adapter sets `unfurl_links=false` and `unfurl_media=false` for the
   notification. Custom Slack unfurls and public previews are out of scope.
5. Grant success and notification success are separate states. If Slack is
   unavailable, Folio says “Shared, but Slack notification was not sent” and
   offers “Copy safe link” and “Retry notification”. It does not revoke a
   successful grant merely because Slack failed.
6. A revoked or expired grant makes the same link show “This document is no
   longer available to you.” It does not reveal whether the document was
   deleted, moved, or still exists.

The safe-link response uses `Referrer-Policy: no-referrer`, does not cache
private content, and redirects only to an allowlisted Folio route. Anonymous
public links and bearer links are not part of this thin slice. A future
time-limited capability link requires a superseding privacy/security decision.

## Errors and recovery copy

| Condition | User-visible copy | Recovery and invariant |
| --- | --- | --- |
| Not signed in / expired session | “Your session expired. Sign in again.” | Reauthenticate, preserve only an allowlisted destination, do not leak content. |
| Not allowed or revoked | “This document is not available to you.” | Return to home or ask owner; same copy for unknown/private references. |
| Invalid share input | “Choose a person from your organization.” | Keep entered safe fields, mark the field in text, do not create a grant. |
| Stale access change | “Access changed while you were here. Refresh to see the latest list.” | Reload access list; do not guess or overwrite another change. |
| Grant succeeds, Slack fails | “Shared, but Slack notification was not sent.” | Copy link or retry notification; grant remains auditable. |
| Temporary service failure | “We couldn’t complete that. Try again. Reference: <request ID>.” | Retry only when `retryable`; preserve form input and idempotency key. |
| Rate limited | “Too many attempts. Wait a moment, then try again.” | Honor server retry guidance; no tight client loop. |
| Invalid/expired link | “This link is no longer available.” | Ask sender for a current grant; never echo token or object details. |

Mutating actions use an idempotency key so a retry cannot create duplicate
grants, revocations, versions, or notifications. Network loss after a submit
shows an indeterminate state with “Check access list” rather than blindly
repeating a mutation.

## Accessibility semantics

The hosted UI targets WCAG 2.2 AA for the contract surface and uses native
HTML controls before custom widgets.

- Each input has a visible label, a programmatic association, instructions for
  unusual formats, and an explicit required/optional state. Visible button
  text is included in the accessible name; icon-only controls are not used for
  primary actions.
- Pages have one descriptive `h1`, a logical heading hierarchy, a “Skip to
  content” link, landmarks, and consistent navigation. The same action has the
  same name everywhere.
- All actions work with keyboard alone. Focus is visible, never trapped, not
  hidden behind a dialog or sticky header, and returns to the invoking control
  after a dialog closes. Confirmation dialogs have an accessible name,
  description, Escape behavior, and a deliberate initial focus.
- Inline validation associates text errors with the invalid field using
  `aria-describedby` or an equivalent native mechanism. The first invalid
  field is reachable, and a summary links to every error. Color and icons are
  supplementary, never the only error signal.
- Non-blocking save, share, revoke, and notification results use a concise
  status region (`role="status"` or equivalent) without stealing focus.
  Blocking errors and expired-session transitions are announced with the
  correct focus change.
- Layout reflows at narrow widths and 200% text zoom. Text remains selectable,
  controls have comfortable targets, contrast is sufficient, motion is not
  required, and no action depends on drag, hover, sound, or color perception.
- Screen-reader users can identify document name, privacy state, owner, role,
  expiry, version, and available actions from the accessibility tree. The
  Slack fallback text conveys the same action as the visual block.

## Browser and assistive-technology matrix

Support means sign-in, create/private state, share, revoke, safe link, error
recovery, keyboard navigation, and the smoke screen-reader checks all work.
Test current and previous stable releases; do not pin fast-moving version
numbers in product code.

| Tier | Browser/device | Minimum validation |
| --- | --- | --- |
| 1 | Chrome desktop, current and previous, macOS and Windows | Full functional and keyboard suite; zoom/reflow; DevTools accessibility tree. |
| 1 | Safari macOS, current and previous | Full functional suite; VoiceOver smoke; cookie, redirect, and safe-link checks. |
| 1 | Firefox desktop, current and previous, macOS and Windows | Full functional and keyboard suite; NVDA smoke on Windows. |
| 1 | Edge desktop, current and previous, Windows | Full functional and keyboard suite; enterprise policy/cookie smoke. |
| 1 | Safari iOS, current and previous supported iOS | Sign-in, link open, read, revoke result, keyboard-equivalent touch flows, VoiceOver smoke. |
| 1 | Chrome Android, current and previous supported Android | Sign-in, link open, read, error recovery, TalkBack smoke. |
| 2 | Slack link handoff and OS default browser | Verify Slack does not expose a preview and the link opens in a supported browser; offer “Open in browser” for embedded webviews. |

Internet Explorer, obsolete releases, unsupported embedded webviews, and
arbitrary in-app browsers are not supported. A browser feature can be used only
after checking its actual matrix; MDN Baseline does not replace accessibility,
security, or usability testing.

## Scripted nontechnical usability test

### Objective and participant

Validate that a person who does not work with databases, MCP, OAuth, or access
control can complete the core flow without moderator coaching and can recover
from common failures. Recruit at least one document owner, one recipient, and
one administrator who use ordinary office tools. Do not use production
customer data.

### Moderator setup

- Use a disposable tenant with two named test accounts, a test Slack workspace
  or a deterministic Slack stub, and one seeded document containing harmless
  text.
- Exercise one Tier 1 desktop browser and one mobile browser. Record browser,
  OS, zoom, screen reader status, and whether the person uses keyboard, mouse,
  or touch.
- Start screen recording only with consent. Redact account addresses, tokens,
  private document content, and unneeded personal information before retaining
  evidence.
- Read tasks verbatim. Do not explain “artifact”, “grant”, “tenant”, “MCP”, or
  implementation details unless the participant asks what a visible label
  means. If they ask, record the question before answering.

### Participant script

| # | Say this to participant | Pass signal |
| --- | --- | --- |
| 1 | “Sign in and tell me whose documents you are about to see.” | Participant signs in and can state the active account/organization. |
| 2 | “Create a private document named `Q3 planning notes`.” | Document is created; participant can point to the `Private` state and explain who can open it. |
| 3 | “Give Alex permission to read this document, then send Alex the normal notification.” | Participant selects Alex, chooses view access, understands confirmation, and completes the share. |
| 4 | “Open the notification as Alex and read the document.” | Alex follows the safe link, signs in if needed, and reads content without seeing an unrelated preview. |
| 5 | “Try to change the document as Alex.” | Viewer understands editing is unavailable; no version is created. |
| 6 | “As the owner, remove Alex’s access.” | Participant finds access list, confirms the named person and effect, and completes revocation. |
| 7 | “As Alex, open the same notification link again.” | Participant sees clear unavailable/revoked copy; no document metadata leaks. |
| 8 | “The next share says it worked, but Slack says it could not send. What would you do?” | Participant understands access succeeded, finds copy-link/retry actions, and does not revoke access reflexively. |
| 9 | “Find out who can access the document and when their access ends.” | Owner/admin finds people, role, expiry, and audit context without technical terminology. |

### Moderator record

For each task record: start/end time, completion (`success`, `success with
help`, or `failed`), first action, wrong turns, help given, participant quote,
and severity of any issue. A task fails if the participant guesses an
authorization outcome from a missing/unclear UI state, shares more broadly than
intended, cannot tell whether a mutation succeeded, or receives moderator help
before attempting a reasonable action.

Suggested thresholds before implementation is called usable:

- 8/9 tasks completed independently by each participant;
- no privacy or authorization misunderstanding in tasks 2–7;
- no critical accessibility blocker in keyboard or screen-reader smoke;
- median time under two minutes for create, share, revoke, and safe-link open
  individually; and
- every error state produces a clear next action without exposing private
  metadata.

These thresholds are release gates, not claims that the unimplemented UI has
already passed them.

## Evidence retention

Evidence is retained under
[`docs/research/evidence/fl-urj.2/`](evidence/fl-urj.2/) using one folder per
run: `YYYY-MM-DD-<short-run-id>/`. The folder must contain:

- `README.md`: participant role codes, consent status, browser/device, build
  commit, moderator, and redaction status;
- `task-metrics.csv`: task number, outcome, duration, help count, issue ID;
- `moderator-notes.md`: verbatim task observations and short participant
  quotes, with personal data redacted;
- `accessibility.md`: keyboard and screen-reader results, including focus and
  status announcements;
- `browser-matrix.md`: exact browser/OS results for the run; and
- `summary.md`: findings, severity, decision, and links to tracked fixes.

Raw recordings and consent forms stay in the approved restricted research
storage, not Git. Committed evidence contains only redacted notes, screenshots
with no private content, and hashes or links to restricted originals. Do not
fabricate a participant run: this pre-implementation contract retains the
script and evidence schema; the first run is an implementation gate.

## Implementation acceptance gates

Before the first hosted slice is called complete, reviewers must demonstrate:

- UI and HTTP MCP use the same authenticated actor, tenant, object checks,
  revocation behavior, and error privacy;
- new artifacts are private in reads, searches, graphs, previews, versions,
  and notifications until an explicit grant succeeds;
- sharing and revocation are auditable, idempotent, and safe under retries;
- safe links contain no secrets and remain useless to an unauthorized actor;
- Slack messages include accessible fallback text, no content preview, and a
  separate notification failure state;
- keyboard, screen-reader, zoom, reflow, and browser-matrix checks pass; and
- a redacted usability run is retained in the evidence path above.

Any implementation that needs shared storage, private module imports,
Hyperset-specific semantics, anonymous bearer links, or a change to ADR 0012's
privacy boundary must stop and update or supersede the relevant ADR first.
