# Inspection UI UX test plan

Date: 2026-09-05

## Purpose and boundary

This plan tests whether a person who is not an MCP or graph expert can complete
the core artifact loop and recover from ordinary failures. It does not test a
general authoring product. The page may remain compact and utilitarian, but its
labels, focus order, status, errors, and immutable-version behavior must be
understandable without reading source code.

Automated browser checks prove mechanics. Human sessions use the scripts below
and record completion, wrong turns, questions, and the participant's own words.
No participant is told internal tool names unless the UI itself shows them.

## Personas

### Morgan — operations analyst

Morgan regularly uploads notes and edits text but does not use developer tools.
They understand files and revision history, not base64, hashes, MCP, or graph
databases. Success means they can upload a note, find it, connect it to another
note, edit it, and recover a prior version.

### Priya — security reviewer

Priya understands browser boundaries and least privilege. She wants visible
proof that previews are untrusted, attached capabilities are read-only and
specific, local mode has no authentication, and denied actions fail without
leaking content or internals.

### Eli — keyboard and screen-reader user

Eli navigates in source order using Tab, Shift+Tab, Enter, Space, headings,
landmarks, labels, and live regions. Success means no operation requires a
pointer, focus does not jump unexpectedly, and loading, success, empty, error,
and conflict states are announced in text.

## Scripted acceptance scenarios

### 1. First visit and upload

Prompt: “This is a local knowledge workspace. Add `launch-notes.txt`, then make
sure you can open what you added.”

Expected observations:

- The page plainly says it is unauthenticated local development and should not
  be exposed.
- The empty state says what can be done next rather than displaying an empty
  JSON panel.
- File selection supplies a name/media-type default that can be corrected.
- While creation runs, the relevant region reports loading and prevents a
  duplicate submission.
- Success opens the artifact and reports that the first immutable version was
  created. No base64 or transport detail appears.

### 2. Find, inspect, and read a chunk

Prompt: “Find the launch note by a phrase inside it. Open the result, inspect
its details, then open one content chunk.”

Expected observations:

- Search is labeled as indexed content search; literal grep is separately
  labeled and says it is not regular expression search.
- Empty results say “No results” and retain the query.
- Result controls identify artifact and chunk without requiring ID copying.
- Current content, media type, version, hash, size, actor, reason, and timestamp
  are readable; chunk offsets state that they are text-character offsets.

### 3. Edit and version conflict

Prompt: “Change the note, explain why, and save it. Then, in the already-open
second tab, try to save an older copy.”

Expected observations:

- The action says “Save new version,” not “Overwrite.”
- Success reports the new immutable version and refreshes history/preview.
- The stale tab gets a concise conflict message explaining that a newer version
  exists and that refreshing will preserve both the current data and history.
- The editor content is not silently discarded and focus reaches the alert.

### 4. Connect and navigate

Prompt: “Connect the launch note to the decision note as `supports`, then use
the relationship display to open the decision note.”

Expected observations:

- Source, target, and relationship type are explicit.
- Success appears in the live status region.
- Outgoing-only behavior is stated. The target is keyboard-operable and opens
  the expected artifact. A missing/cross-tenant target yields a generic error.

### 5. Preview untrusted web content

Prompt: “Upload the supplied HTML, JavaScript, and CSS examples and preview
each. Explain what the warning and capability list mean.”

Expected observations:

- Preview is labeled untrusted and isolated.
- HTML, JS, and CSS execute/render; unsupported media gets a textual state.
- The page lists the only attached tools: read, search, and outgoing traverse.
- Attempts to write, navigate the top page, open a popup, submit a form, use
  host storage/cookies, or contact the network are denied and visible to the
  security fixture without changing the control page.

### 6. Safe failure and recovery

Prompt: “Try a malformed identifier, a binary file, a search with no matches,
and an unavailable service. Recover without reloading when possible.”

Expected observations:

- Errors are textual, specific enough to act on, and omit stack traces, paths,
  tenant IDs, content, and configuration secrets.
- Binary content is inspectable as metadata but cannot be edited as text.
- Loading ends on success or error; controls return to a usable state.
- The page preserves the user's query or unsaved edit after failure.

## Keyboard and accessibility protocol

Run every scenario once without a pointer. Confirm one logical tab sequence,
visible focus, native activation, labeled inputs, distinct headings/landmarks,
descriptive iframe title, no positive `tabindex`, and no keyboard trap inside or
outside the preview. Inspect the accessibility tree for names and roles. Verify
that `aria-busy` changes during work, normal progress uses `role="status"`, and
errors/conflicts use `role="alert"`. Automated checks catch regressions; a human
screen-reader pass remains a release blocker before hosted claims.

## Session record

For each participant record browser/assistive technology, scenario completion,
elapsed time, wrong turns, exact confusing copy, assistance needed, and severity.
A scenario fails if the participant cannot finish, risks destructive behavior,
believes local mode is authenticated, or misunderstands a denied bridge call as
having run. Copy changes are allowed within this slice; new product features are
not the default response to a UX finding.
