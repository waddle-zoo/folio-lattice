# Folio Lattice human UI design brief

Implementation brief for `ux_visual` and `ux_usability`. Keep this surface
small, document-first, and compatible with the thin inspection UI boundary.

## 1. Page anatomy

Use one responsive application shell:

```text
┌ workspace switcher ───────────────────── search ───── New document ┐
│ 01 Home          breadcrumb / title / privacy / primary action      │
│ 02 Library       current document or file content                   │
│ 03 Graph         Related documents                                 │
│ 04 Shared        Versions                                           │
│ 05 Settings      Sandboxed preview                                  │
│                  Advanced details                                  │
│ account/security                                                   │
└─────────────────────────────────────────────────────────────────────┘
```

- Left rail: numbered destinations `01 Home`, `02 Library`, `03 Graph`, `04 Shared`, `05 Settings`.
- Header: workspace name, global search, one primary `New document` button.
- Detail header: breadcrumb, document title, file type, privacy badge, updated time, primary action.
- Main content: current readable content first; related documents, versions, preview, and technical details below.
- Graph is a view of the selected document, not a replacement for Library/search.
- Keep current v0 routes and public MCP behavior. Do not add collaboration, a canvas editor, or a parallel REST model.

## 2. Navigation and interaction

- `Home` shows recent documents and recovery/access notices.
- `Library` supports search plus filters for type, owner, privacy, and updated time.
- `Graph` opens around the selected document at one relationship hop.
- `Shared` means `Shared with me`; ownership remains visible on each document.
- `Settings` contains identity, workspace, access/audit, and connection details.
- Search and filters persist when opening a document and when returning with Back.
- Every relationship target is reachable as a list item and keyboard focus target.
- Use one primary action per page. Make history, sharing, graph, and advanced details secondary.

## 3. Artifact and graph mental model

Use this model in UI labels and implementation:

- A document/file is an **artifact**: readable current content plus immutable versions.
- A version is a saved state with time, person, reason, provenance, and parent.
- A graph node is an artifact the current person may open.
- A graph edge is a named relationship such as `references`, `supports`, or `derived from`.
- The selected artifact stays the anchor while users inspect relationships or versions.
- Default graph is one hop. `Load more` is explicit.
- Provide `List view` beside any spatial graph view. Do not require dragging, zooming, or color recognition.
- Never reveal the existence of an unknown or unauthorized node. Use the same unavailable state for both.

Default detail order:

1. Title, type, privacy, updated time, primary action.
2. Current content or safe file summary.
3. `Related documents` with `Explore graph`.
4. `Versions` with `View history`.
5. `Sandboxed preview` when media type allows it.
6. `Advanced details` for IDs, hashes, bytes, provenance, MCP, and connection data.

## 4. Progressive disclosure

### Always visible

Title, document type, `Private`/access state, current content, updated time,
primary action, and recovery status.

### One interaction away

Related documents, version history, people with access, share controls, and
preview controls.

### Advanced disclosure

IDs, media type, byte size, hash, actor, source context, MCP/connection status,
and audit/request identifiers. Technical terms are allowed here only.

Do not hide a privacy consequence, version consequence, or sandbox boundary in
Advanced details.

## 5. Typography, color, and tokens

```css
:root {
  --font-sans: ui-sans-serif, system-ui, -apple-system, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, monospace;
  --bg: #f7f8fa;
  --surface: #ffffff;
  --ink: #17191d;
  --muted: #626a76;
  --line: #e3e7ed;
  --accent: #2f6feb;
  --positive: #198754;
  --warning: #a15c00;
  --danger: #c0372b;
  --focus: #2f6feb;
  --radius: 8px;
  --space: 8px;
  --rail: 240px;
  --content: 1120px;
}
```

- Type scale: 12px metadata, 14px controls, 16px body, 20px section title, 28px page title.
- Body line height: 1.5. Monospace only for content, IDs, hashes, and code.
- Use `--ink` for meaning-bearing text and `--muted` for supporting text; never use color alone.
- Use `--accent` for links, selected node, primary action, and focus ring.
- Pair positive/warning/danger colors with an icon and text.
- Use 1px borders and spacing before adding cards or shadows. No gradients.
- Minimum target: 44px. Focus: 3px outline, 2px offset. Respect `prefers-reduced-motion`.
- Dark theme maps `--bg: #17191d`, `--surface: #23262d`, `--ink: #f5f7fa`, `--muted: #b7bec9`, `--line: #3a414c`.

## 6. Exact copy

### Core labels

- `New document`
- `Upload file`
- `Search documents and files`
- `Related documents`
- `Explore graph`
- `List view`
- `Versions`
- `View history`
- `Share`
- `People with access`
- `Can view`
- `Can edit`
- `Copy safe link`
- `Advanced details`
- `Sandboxed preview`

### Privacy and version copy

- `Private`
- `Only you and people you choose can open this document.`
- `This link helps someone find the document. It does not grant access.`
- `This saves a new version. Earlier versions stay available.`
- `Can edit means creating a new version. It does not overwrite history.`
- `Runs in an isolated frame. Network and host access are blocked.`

### Empty, loading, and error copy

| State | Copy | Action |
| --- | --- | --- |
| Empty Home | `Create a document or search your library to get started.` | `New document`, `Upload file` |
| Empty search | `No documents match “{query}”.` | `Clear search` |
| Empty graph | `No related documents yet.` | `Add relationship` or `Relationships will appear here.` |
| Loading | `Loading document…` / `Loading relationships…` | Keep stable layout; expose `aria-busy`. |
| Saving | `Saving a new version…` | Disable duplicate submit; preserve input. |
| Conflict | `A newer version was saved. Your edits are still here.` | `Review newer version`, retry. |
| Missing/private | `This document is not available to you.` | `Return to Library` |
| Service failure | `We couldn’t load this document.` | `Try again` |
| Preview failure | `Preview unavailable. The document is still safe to read.` | `Open content`, `Try preview again` |
| Share partial failure | `Access was updated, but the notification did not send.` | `Copy safe link`, `Retry notification` |

Use stable API fields `code`, `request_id`, and `retryable` beneath these
sentences. Never expose private existence through error wording, timing, or
technical detail.

## 7. Responsive behavior

- `>= 1024px`: 240px rail, content max 1120px, two-column document/preview layout where useful.
- `768–1023px`: rail remains icon-plus-label when space permits; preview and detail sections stack.
- `< 768px`: rail becomes a labeled menu drawer; header keeps search and `New document`; all sections stack.
- Graph controls stay fixed and reachable; graph details become a bottom sheet or inline panel.
- Preserve breadcrumb/title/privacy/action order at every width.
- Never hide the primary action, privacy state, error message, or recovery action behind hover.
- Keyboard order follows rail, header, page header, content, related documents, versions, preview, advanced details.
- At 200% zoom, content reflows without horizontal scrolling except code/file content.

## 8. Auth, sandbox, and enterprise trust states

- Hosted unauthenticated: `Sign in with your organization` and `Your documents stay private until you share them.`
- Session expired: `Your session expired. Sign in again.` Preserve only an allowlisted destination.
- Local development: `Unauthenticated local development. Do not expose this service to an untrusted network.`
- Unauthorized or unknown: `This document is not available to you.` Do not disclose which case occurred.
- Access list shows named person, role, expiry, and `Remove access`; confirmation names the person and document.
- Safe link is a navigation handle only. It contains no content, token, tenant ID, or authorization decision.
- Preview badge always says `Sandboxed preview`; show the blocked-network/host-access explanation before content.
- Every write exposes version creation and history. Every share exposes resulting access and notification result separately.
- Technical provenance is inspectable through `Advanced details`, not required for normal reading.

## 9. Prioritized handoff

### P0 — `ux_visual`

1. Produce responsive shell and document-detail frames in light/dark themes using the tokens above.
2. Include empty, populated, loading, missing/private, conflict, no-relationship, preview-failure, and share-partial-failure states.
3. Annotate focus, keyboard order, 44px targets, responsive collapse, badge meaning, and exact copy.

### P0 — `ux_usability`

1. Test create-private, search/open, traverse one relationship, save a new version, resolve conflict, and open a safe link.
2. Verify participants can explain `Private`, immutable versions, `Can view`/`Can edit`, and sandboxed preview without database/MCP vocabulary.
3. Verify unavailable/private copy does not leak authorization state and every failure has a clear next action.

### P1 — both

- Validate keyboard-only navigation, screen-reader status announcements, 200% zoom, dark theme, and mobile reflow.
- Measure task completion, duration, help count, privacy misunderstandings, and critical accessibility blockers.
- Attach redacted screenshots and browser/device details to `docs/research/evidence/fl-urj.2/` after host validation.

### Acceptance gate

- 8/9 core tasks completed independently.
- Zero privacy or authorization misunderstandings.
- Zero critical keyboard or screen-reader blockers.
- Every loading/error/empty state has visible copy, an announced status where applicable, and a recovery action.
- No implementation expands v0 persistence, security, provider-neutrality, or privacy boundaries without an ADR and Mayor decision.

## Sources

- [Folio Lattice reference UI](https://folio-lattice.netlify.app/)
- [Linear](https://linear.app/)
- [Notion workspace navigation](https://www.notion.com/help/intro-to-workspaces)
- [Obsidian Canvas](https://obsidian.md/help/Plugins/Canvas)
- [Excalidraw](https://excalidraw.com/)
- [Thin inspection UI ADR](../adr/0009-thin-inspection-ui-and-isolated-renderer.md)
- [Hyperset first-consumer UX contract](2026-09-07-hyperset-first-consumer-ux-contract.md)
- [`inspection.py`](../../src/folio_lattice/inspection.py)

