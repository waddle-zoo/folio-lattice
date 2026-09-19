# fl-urj.27 UX usability baseline

Date: 2026-09-16 local / 2026-09-17 UTC
Target: exact `origin/main` at `861eda6d1e3536c6791dbe1d6e1022ad1c0f5106`
Worker checkout: `206325f9304b106e6e00e49e92923102068fdcb9`
Owner: UX/usability crew
Result: **BLOCKED; gate remains OPEN**

No product UX code changed. Existing dirty worker files were preserved. The
target source was audited from an archive of exact `origin/main`, not from the
older dirty checkout.

## Scope

Nontechnical create/open/search/read/edit/version flows; keyboard-only use;
focus order and recovery; screen-reader semantics; 320/375/768/1280 responsive
behavior; duplicate-name search and graph navigation; admin/share/connection
discoverability; local/hosted security copy.

## Evidence run

Verified without a live server:

- `tests/test_web.py`: 11 passed.
- `tests/test_acl.py tests/test_service.py`: 10 passed.
- Python compile: passed.
- Ruff check and format check: passed for exact archived source.
- Direct service check: punctuation query `budget:` returned the named result.

Host-gated evidence not available in this worker:

- Focused Chrome tests: 2 failed before assertions. Chrome version probe timed
  out; the other test hit `PermissionError: [Errno 1] Operation not permitted`
  binding `127.0.0.1`.
- HTTP e2e: 6 failed before live flows for the same loopback restriction;
  stdio child also lacked `uvicorn` in the reconstructed cache path.
- `make check`: exit 2 while resolving uncached `mcp-types==2.1.1`; network is
  disabled.
- Docker build: Docker daemon socket denied (`operation not permitted`).
- Manual keyboard-only run: not run.
- Manual screen-reader run: not run; no approved CUA browser or AT surface.

These are execution blockers, not product-pass evidence.

## Defects for UX

### UX-27-01 — P1 — Skip link does not move focus to main

Repro on `/`: Tab to `Skip to content`, press Enter, inspect the focused
element. The link targets `#main`, but `<main id="main">` is not focusable;
only the error paragraph has `tabindex="-1"`.

Observed source: `src/folio_lattice/inspection.py:624,633-634`. The browser
assertion must use a real key event and verify `document.activeElement.id ===
"main"`.

Impact: keyboard and screen-reader users do not reliably reach the primary
content landmark. This is a release-blocking keyboard defect.

### UX-27-02 — P1 — Duplicate-name navigation is opaque and ID-first

Repro: create two artifacts with the same name, search for identical content,
then inspect relationship results. Search buttons render
`<artifact_id> — <artifact_name>`; graph buttons use the same ID-first label;
the top `Open` field and relationship form require an artifact identifier.

Observed source: `src/folio_lattice/inspection.py:629,461-474,518-529,660-661`.

Impact: a nontechnical user cannot reliably distinguish same-named artifacts
or choose a graph target without copying opaque `art_…` identifiers. Search
snippets may help in some cases but are not a stable disambiguator.

### UX-27-03 — P2 — Create form does not explain type detection or binary limits

Repro on first visit: open `New document or file` and tab through `Name`,
`Type`, `File (optional)`, `Content`, and `Reason`. No visible help explains
that type may be detected, text is editable, binary files are metadata-only,
or the configured size limit.

Observed source: `src/folio_lattice/inspection.py:640-645`.

Impact: Morgan must know implementation terminology and discovers binary
behavior only after submission.

### UX-27-04 — P2 — Search modes lack plain-language distinction

Repro on first visit: compare the two search forms. They say `Search documents
and files` and `Exact text`; neither explains indexed search syntax versus
literal matching or that exact text is not regular-expression search.

Observed source: `src/folio_lattice/inspection.py:648-651,518-530`.

Impact: ordinary punctuation, phrase, and regex-like input produce uncertain
expectations even though backend behavior is bounded.

### UX-27-05 — P2 — Version history and chunk controls lack human context

Repro on an artifact with multiple versions: navigate `Versions` and `Chunks`
by keyboard. Version buttons announce timestamp plus reason, not `Version 1`,
`Version 2`, etc.; chunk buttons announce numeric offsets without their text
offset unit.

Observed source: `src/folio_lattice/inspection.py:418-427,461-465`.

Impact: screen-reader and sighted users must infer which historical version or
text range they are opening, increasing wrong-version recovery risk.

### UX-27-06 — P1 — Authenticated artifact view is mislabeled as local

Repro with `ui_html(..., auth_state="authenticated", organization=..., actor=...)`:
the active account is shown, but the artifact header still says `Local
workspace`.

Observed source: `src/folio_lattice/inspection.py:606-618,653`.

Impact: users can misread hosted authenticated state as local development or
vice versa. Security-state copy must match deployment state.

### UX-27-07 — P1 gap/dependency — Admin controls have no discoverable UI

Repro from `/`: inspect primary navigation and all reachable sections, then
try `/admin` or `/connections`. Navigation exposes only `Workspace` and
`Find`; the inspection app serves only `/`, `/inspect/*`, UI assets, and the
two API paths. No tenant-admin or member-management surface exists.

Observed source: `src/folio_lattice/inspection.py:628,660-664` and
`src/folio_lattice/inspection.py:741-772`.

Impact: users cannot discover tenant administration or understand where
organization-level sharing policy is managed. Treat as an explicit dependency,
not as evidence that the current artifact share control is complete.

### UX-27-08 — P1 gap/dependency — Connection management has no UI entry point

Repro from `/`: search the navigation, first-visit content, artifact sections,
and routes for connection/connector management. Only the preview capability
readout exists; there is no connection list, add/revoke control, or admin route.

Observed source: generated page has no `admin`, `connection`, or `connector`
surface; route handling is limited to `src/folio_lattice/inspection.py:741-772`.

Impact: users cannot discover or manage external MCP/connection policy from the
product UI. Keep this open with the declared hosted/admin dependencies.

### UX-27-09 — P2 — Share control uses opaque person IDs and sits only in artifact view

Repro: create/open an artifact and tab to `People with access`. The control is
`Person identifier` with placeholder `person-id`; there is no explanation of
member/group lookup, scope, or whether the recipient can locate the artifact.
The share surface is absent from the first-visit navigation.

Observed source: `src/folio_lattice/inspection.py:660`.

Impact: sharing is technically present but not discoverable or understandable
for nontechnical users. Revoke also has no confirmation or undo affordance.

## Disposition

Static contract and service/ACL checks pass, but no live browser, Docker, or
assistive-technology claim is made. The gate stays open until `fl-urj.6.2`,
`fl-urj.7.7`, and their declared dependencies land. After that, retest this
exact scope against the exact release build, including real key events,
accessibility tree, screen reader, mobile widths, duplicate names, share/admin
recovery, and connection discoverability before closure.
