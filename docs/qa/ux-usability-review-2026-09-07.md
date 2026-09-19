# UX and accessibility adversarial review

Date: 2026-09-07
Reviewer: UX/usability crew
Scope: local inspection UI in `src/folio_lattice/inspection.py` against
`docs/UX-TEST-PLAN.md` and the 2026-09-07 Hyperset first-consumer UX contract.

## Execution status

This review is source- and contract-based. Host browser execution was not
available in this worker: no CUA browser surface was present, localhost was
not running, and Docker access was denied by the environment. No browser or
Docker pass is claimed. The browser assertions added in
`tests/test_browser_e2e.py` are the executable follow-up and must run on a
host with Chrome or Chromium.

## Findings

Severity uses the UX gate meaning: P0 blocks safe use or creates a misleading
security state; P1 blocks a core task for the target nontechnical user; P2
causes material confusion or accessibility friction.

### UX-01 P1 — first visit has no page heading or next-step explanation

Evidence: `ui_html()` starts with a notice and a header containing a `<strong>`
brand label. The only `<h1>` is inside the hidden artifact workspace. A first
visit therefore has no heading hierarchy and no plain-language explanation of
what the workspace is for. The empty status is set by JavaScript after load,
not present as an initial instruction in the page structure.

Impact: Eli loses a navigable page heading; Morgan sees controls without a
clear task model.

Fix: make the product name the visible page `<h1>`, add a short intro that says
the user can create, search, connect, and inspect documents, and keep an
explicit empty-state instruction in the DOM. Add a browser assertion for the
heading, landmark, and first-visit copy.

### UX-02 P1 — successful create/upload does not confirm creation

Evidence: submit status says `Creating the first immutable version…`, then the
browser navigates to `/inspect/<id>`. `loadArtifact()` replaces that with
`Loaded <name>.` No success message says that the first immutable version was
created.

Impact: Morgan cannot distinguish a newly created document from an existing
one that was merely opened. This fails the first-visit acceptance scenario.

Fix: carry a non-sensitive one-shot creation marker through navigation and
announce `Created <name>. First immutable version is ready.` before removing
the marker from the URL.

### UX-03 P1 — search, graph, and open flows expose internal IDs as user-facing navigation

Evidence: search/grep render `button(result.artifact_id)`. Graph navigation
renders `button(edge.target_artifact_id)`. The header and graph-link form ask
for an artifact identifier. Search and graph service results currently contain
IDs but not artifact names.

Impact: a nontechnical user must understand and copy `art_*` values to find or
connect documents. This conflicts with the contract requirement that core UI
tasks not require internal IDs and makes graph relationships unreadable.

Fix: return artifact names additively in search, grep, and traversal results;
use names as button labels with IDs only as secondary technical detail. Add an
artifact picker/listing seam before treating graph-link entry as complete.

### UX-04 P1 — concurrent loads clear busy state too early

Evidence: `loadArtifact()` starts three concurrent `call()` operations.
`call()` sets `aria-busy=true` and each `finally` independently sets it to
`false`. The first completed request therefore re-enables submit buttons while
the other two requests are still pending.

Impact: duplicate writes/searches can be submitted during a partial load; Eli
hears a false idle state. This violates the loading-state acceptance check.

Fix: track one request count (or one load transaction) and clear busy only when
the final request settles. Add a staggered-response browser regression.

### UX-05 P1 — attached-MCP status conflates denial with failure

Evidence: the bridge status starts as `No attached tool call yet.` and catches
every error with `Denied or failed attached tool <name>.` Tool names are raw
MCP names such as `artifact_write`.

Impact: Priya cannot tell whether a write was rejected before execution or an
allowed call failed. Morgan sees implementation terminology. This is unsafe
for the required capability disclosure.

Fix: show an explicit ready state with the three allowed capabilities; map tool
names to plain language; distinguish `allowed`, `denied; request did not run`,
and `unavailable; request did not complete`. Keep raw names in audit logs, not
the primary UI copy.

### UX-06 P2 — error and helper colors are not a documented contrast baseline

Evidence: `.error { color: #c33 }` and `.muted { color: #777 }` are used with
`color-scheme: light dark` and no matching dark-theme colors. The notice uses a
20%-alpha background, and borders rely on low-alpha `#8885`/`#8888`.

Impact: low-vision users may lose error, empty-state, border, and focus-context
information, especially in dark mode. The source has no contrast regression.

Fix: define explicit light/dark text colors meeting WCAG AA for normal text,
retain non-color error/status wording, and add a browser contrast/style
regression or an approved manual contrast record.

### UX-07 P2 — labels do not explain file-type defaults or supported content

Evidence: create uses `Name`, `Media type`, and `File (optional)` with no
instructions about detection, supported text, binary metadata-only behavior,
or the size limit. `Media type` is an implementation term for Morgan.

Impact: first-time users cannot predict what to enter or why a binary file
cannot be edited. The form does not meet the research requirement to explain
defaults and limits before submission.

Fix: use `Document or file name`, `File type (optional)`, and visible help text
that says leaving file type blank detects it from the name, text can be edited,
binary content is metadata-only, and the configured size limit applies.

### UX-08 P2 — version, chunk, and graph controls lack human context

Evidence: version buttons are timestamp/reason strings; chunk buttons expose
numeric ranges only; graph buttons expose target IDs. The current artifact name
is not repeated in these controls, and no version number is shown.

Impact: screen-reader and sighted users must infer which version/chunk they
are opening. This increases wrong-turn risk during history recovery.

Fix: label controls as `Version 1`, `Chunk 1`, and `Open <artifact name>`;
include technical offsets/IDs as secondary text. Keep current-version state
in the label.

## Current boundary disclosures retained

The review does not recommend hiding or softening these limitations:

- Local mode is unauthenticated and must remain loopback-only.
- Hosted mode has no authentication adapter and intentionally refuses startup.
- The preview is untrusted and isolated; direct network, host access,
  navigation, popups, forms, storage, and cookies are denied.
- Only read, indexed search, and outgoing traversal are attached to previews.
- Search uses indexed FTS5 syntax; grep is literal; traversal is outgoing-only.
- Docker, hosted auth, sharing, revocation, assistive-technology testing, and
  browser-matrix coverage remain unverified or out of this local milestone.

## Host validation checklist

Run on a host with Chrome/Chromium, Docker, and the locked Python environment.

1. Run `make check`, `make test-e2e`, and `make browser-test`.
2. Start a fresh isolated stack with `make docker-up`; record `/health` for
   both control and renderer and confirm the local unauthenticated banner.
3. Run the real-browser UI flow: first visit, upload, open, search, literal
   grep, chunk read, graph link/navigation, edit, history, stale conflict,
   binary metadata-only view, unavailable service, and missing identifier.
4. Repeat with keyboard only. Record browser/OS/assistive technology, focus
   order, visible focus, heading/landmark tree, label names, live status,
   alert focus, and whether the preview creates a keyboard trap.
5. Run at 320px, 375px, 768px, and desktop widths. Assert no horizontal page
   scroll, readable controls, stacked grids, and preview access without pointer
   gestures.
6. Force staggered API responses. Confirm `aria-busy` and disabled submits stay
   active until all work settles; confirm errors restore controls and preserve
   query/unsaved edits.
7. Confirm attached preview calls show ready/allowed/denied/unavailable states
   without exposing credentials, content, stack traces, paths, or tenant IDs.
8. Run `make docker-build` and the Docker-to-MCP smoke path. Record exact
   commit, image digest, command output, and any skipped gate in
   `docs/RELEASE-EVIDENCE.md`.

## Acceptance disposition

UI readiness: **blocked** by UX-01 through UX-05 until browser evidence passes.
Accessibility readiness: **blocked** by UX-01, UX-04, and UX-06 through UX-08
until keyboard, screen-reader, responsive, and contrast evidence exists.
Enterprise readiness: **not claimed**; local auth/sandbox/Docker limitations
remain explicit.
