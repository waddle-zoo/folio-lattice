# fl-urj.27 human-first UI retest

Date: 2026-09-16 local
Target: exact `origin/main` at `75ff7769b102ad1e8458f3eeec4006ee0a0e12fc`
Target subject: `fix(release): normalize compose project names`
Worker checkout: `206325f9304b106e6e00e49e92923102068cb9`
Owner: UX/usability crew
Result: **BLOCKED; gate remains OPEN**

No product UX code was changed. The dirty worker checkout was preserved. The
target was tested from an archive of exact `origin/main` at the target SHA.

## Scope

Library → graph → left file tree → Markdown reader/HTML content preview;
create, upload, edit, share, and recoverable-error flows; keyboard order and
accessible names; duplicate filenames; responsive/mobile CSS; standalone
back navigation; and admin/share/connection discoverability.

## Evidence run

Passed against the exact archived source:

- `python -m compileall -q src tests`.
- `pytest -q tests/test_web.py tests/test_acl.py tests/test_service.py`: **32
  passed, 9 subtests passed**.
- `ruff check src tests`: passed.
- `ruff format --check src tests`: **38 files already formatted**.
- Static generated-route audit: current `/`, `/artifacts/<id>`,
  `/workspace/<id>`, and `/standalone/<id>` shells contain the expected
  human-first landmarks, tree, tabs, reader/preview, edit, new-note, share,
  revoke, and recovery surfaces.

Live browser evidence was blocked before any product assertion:

- The two focused browser flows (`graph_first_workspace_picker_tree_views_and_standalone`
  and `real_ui_upload_search_grep_chunk_graph_versions_conflict_and_bridge`)
  both failed at fixture setup with `PermissionError: [Errno 1] Operation not
  permitted` while binding `127.0.0.1` for `free_port()`; result: **2 failed,
  3 deselected**.
- Consequently, real key events, browser accessibility tree, screen-reader
  output, rendered mobile widths, iframe loading, and live create/edit/share
  outcomes remain unverified. They must not be treated as passes.
- `make check` could not start its checks because the offline environment could
  not resolve/download `pycparser==3.0` through the `pyjwt[crypto]` dependency
  chain. The cached focused checks above are the usable exact-source evidence.

## Flow matrix

| Area | Result | Concrete evidence |
|---|---|---|
| Library → graph picker | Static/contract pass; live unverified | `loadLibrary()` calls `artifact_list`, groups graph components, and renders named graph buttons that route to `/workspace/<id>`. |
| Left file tree | Static/contract pass for unique names; duplicate case fails | `renderArtifactTree()` creates keyboard-operable native `<details>/<summary>` folders and buttons with `aria-current`; duplicate file buttons still share the same visible/accessible name. |
| Markdown reader | Static/contract pass; live unverified | `showRead()`/`showHumanRead()` render headings, lists, code, links, emphasis, and strong text through DOM text nodes. |
| HTML/content preview | Static/contract pass; live unverified | Web artifacts select the preview path; iframe uses `sandbox="allow-scripts"`, no `allow-same-origin`, and the standalone route uses the same isolated preview. |
| Create/upload | Static/contract pass; live unverified | File selection populates the name, `arrayBuffer()` is sent through `artifact_create`, and success routes to `/artifacts/<id>?created=1` with “Version 1 saved.” status. |
| Edit/new note | Static/contract pass; live unverified | `human-edit` writes a new immutable version; `new-note-form` creates Markdown and links it into the current graph. |
| Share/revoke | Static/contract pass; live unverified | Workspace Share opens a modal, loads ACL state, shares view/edit access, and revoke requires confirmation with partial-failure copy. Recipient selection remains identifier-only; see UX-27-R5. |
| Recoverable errors | Source pass; live unverified | 401 redirects to sign-in with a safe return path; 403/404 expose recovery actions; 409 preserves the edit and explains refresh; generic errors focus `role="alert"`. |
| Keyboard/accessibility | Partial fail | Roving `role="tab"` behavior, labelled tree/nav/search/dialog controls, and trigger return for Share/New note are present. Skip-to-content still targets a non-focusable `<main>`; edit/revoke close paths do not restore focus. |
| Duplicate filenames | **Fail — P1** | Search disambiguates duplicate results with `name — artifact_id`, but library rows, graph cards, graph targets, and tree files still render duplicate names without a human-readable path, date/version, or other stable differentiator. |
| Responsive/mobile | Source coverage only; live unverified | CSS includes 320px-oriented max-640 rules, collapsible tree geometry, wrapped workspace controls, and full-width dialogs. Browser rendering/overflow at 320/375/768/1280 could not run. |
| Standalone back navigation | **Fail — P1** | The standalone shell contains only the viewer/main and no Library/back link. Library/workspace launches it with `target="_blank"`; a new tab has no reliable in-product route back. |
| Admin discoverability | **Fail — P1 dependency** | Primary navigation exposes Library, Graphs, Search, and Shared with me only. The inspection app has no `/admin` or organization/member-management route. |
| Connection discoverability | **Fail — P1 dependency** | “Approved connection” exists only in debug/Inspector markup; no human route exposes connection list, add, revoke, or policy controls. |

## UX defects to hand off

### UX-27-R1 — P1 — Skip link does not move focus to main

Repro: on `/` or `/artifacts/<id>`, Tab to “Skip to content” and press Enter;
inspect `document.activeElement`. The link targets `#main`, but the generated
`<main id="main">` has no `tabindex`, so focus does not reliably land on the
primary content landmark.

Evidence: `src/folio_lattice/inspection.py:2810,2813,3063-3064`; the same
non-focusable main pattern is used by the root/workspace shell.

Impact: keyboard and screen-reader users cannot reliably skip repeated
navigation. Keep release gate open.

### UX-27-R2 — P1 — Duplicate filenames remain ambiguous in primary navigation

Repro: create two graph artifacts with the same full name, open the graph
workspace, and inspect the library/tree/graph controls. The tree renders the
basename as both button text and `aria-label="Open <full name>"`; the library
uses the name plus only an “Updated” date; graph targets use the name only.
Search adds the opaque ID only when it detects duplicate result names, so the
same-name case is not fixed across the task flow.

Evidence: `src/folio_lattice/inspection.py:1913-1917,2353-2366,2528-2537`.

Impact: a nontechnical user cannot reliably choose the intended artifact from
the main Library → graph → tree path. This is a core navigation blocker.

### UX-27-R3 — P1 — Standalone preview has no return navigation

Repro: from a web artifact, activate “Open site ↗”; in the new standalone tab,
look for a Library/back control or another in-product route. None is rendered.
The route is intentionally chrome-free, but the new-tab launch means browser
Back is not a dependable return to the originating library.

Evidence: `src/folio_lattice/inspection.py:2792-2804`; launch uses
`target="_blank"` at `2358-2362`.

Impact: users can become stranded in the preview and lose the human-first
workflow context.

### UX-27-R4 — P2 — Closing Edit or Remove-access loses keyboard focus

Repro: open Edit, then activate “Back to reading” or “Cancel”; repeat with
Remove access → “Keep access”. Focus is moved into the edit/revoke surface but
the close handlers do not return it to the invoking control. The edit section
is then hidden while focus remains on a hidden control; revoke has no trigger
focus bookkeeping at all.

Evidence: `src/folio_lattice/inspection.py:1859-1869,1881-1882,2232-2249`.

Impact: keyboard and screen-reader users lose their place after a routine
cancel/recovery action.

### UX-27-R5 — P2 — Share recipient still requires an opaque identifier

Repro: open Share in a workspace. The only recipient control is
“Approved person identifier” with placeholder `member-id`; there is no member
lookup, display-name search, or explanation of where to obtain the identifier.

Evidence: generated workspace share form in `src/folio_lattice/inspection.py:2937-2950`.

Impact: share is technically discoverable in the workspace but not
nontechnical-user discoverable or confidence-building.

### UX-27-R6 — P1 dependency — Admin and connection management remain absent

Repro: start at `/`, inspect primary navigation and all human-first surfaces,
then try `/admin` or `/connections`. No human admin or connection-management
surface is exposed; connection status is debug-only.

Evidence: route allowlist `src/folio_lattice/inspection.py:3193-3218`; debug-only
connection surface `src/folio_lattice/inspection.py:2872-2882`.

Impact: organization-level administration and connection policy are not
discoverable. Track against the declared fl-urj.6.2/fl-urj.7.7 dependencies;
do not close this gate as a product-pass omission.

## Disposition

The human-first route, graph/tree structure, Markdown/HTML reader-preview
split, create/upload/edit/share wiring, and recoverable error copy are improved
and pass static/contract checks. The gate remains open for UX-27-R1 through R6,
the blocked live browser/AT/mobile evidence, and the declared `fl-urj.6.2`,
`fl-urj.7.7`, and dependency landing conditions. Retest the exact release
build after those changes; do not self-fix product UX in this worker.
