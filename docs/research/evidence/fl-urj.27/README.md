# fl-urj.27 manual accessibility and usability evidence

Status: **DEFERRED / UNMET BY USER DIRECTIVE**

This register preserves automated evidence and a future manual-session
protocol, not a completed witness result. Native VoiceOver/screen-reader
review is explicitly deferred and unmet by user directive. No further
VoiceOver/screen-reader attempts, Mac unlocks, or Accessibility permission
changes are authorized in this workspace. Automation cannot satisfy this gate;
do not fill the result fields from browser automation or self-review.

## Retained historical automated evidence

The retained run records are:

- [`20260917T0716Z-176a2ce-browser.json`](20260917T0716Z-176a2ce-browser.json),
  initial library, graph, rendering, sharing, bridge, keyboard, focus, status,
  error, and responsive-layout evidence.
- [`20260917T0731Z-dbfc3da-admin-browser.json`](20260917T0731Z-dbfc3da-admin-browser.json),
  approved-connection admin browser evidence and async form-reset fix.
- `fl-urj.27-baseline-2026-09-16.*` and `fl-urj.27-retest-2026-09-16.*`,
  retained baseline/retest records.

Historical exact-head runs passed the initial browser suite twice (**5 passed**
each) and the admin-integrated suite twice (**6 passed** each). The admin
scenario covers pointer registration, live status and busy-state restoration,
keyboard revoke-dialog activation and focus return, pointer revoke, bounded
loopback rejection, form recovery, accessible names, and secret-free UI.
These records are automated evidence only; they do not close manual review.

The retained real-browser repair found an async UI defect: after an awaited
admin request, `event.currentTarget` was null, so the form reset raised and the
connection list did not refresh. The fix resets the stable
`#connection-register` element directly; the focused scenario passed after the
fix.

## Exact-build binding

Run from a clean checkout of the exact release candidate and record values
before each session:

| Field | Required value |
| --- | --- |
| Source commit | `0f852ac16b3ff8333e991f9d5e66c5b0494b2345` |
| Source tree | `git status --short` must be empty |
| Hosted image digest | Record exact `sha256:...`; do not substitute a local image |
| Hosted URL | Record origin, tenant fixture, and cache-busting URL |
| Session date/time | ISO-8601 with timezone |
| Evidence folder | `YYYY-MM-DD-<approved-run-id>/` under this directory |
| Screenshots/video | Store only redacted files; record SHA-256 hashes |

Raw recordings, consent forms, account addresses, tokens, and private document
content stay in approved restricted research storage. Git receives only
redacted notes, safe screenshots, and hashes.

## Current exact-build automated refresh

Source: `0f852ac16b3ff8333e991f9d5e66c5b0494b2345`.

Portable exact command from a clean candidate worktree on 2026-09-19:

```text
FOLIO_BROWSER="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" uv run pytest -q tests/test_browser_e2e.py -rA
```

Result: **6 passed in 34.93s**. The first portable invocation had 5 passed and
one Chrome teardown timeout after product assertions completed; the affected
test passed in isolation, then the repeated full command passed 6/6. This is
automated evidence only and does not satisfy the manual witness gate.

## Witness record — complete before execution

Copy this section into the run folder's `README.md`; replace every `REQUIRED`
field. An empty field is a blocker, not a pass.

```text
approved_screen_reader_reviewer: REQUIRED approved pseudonym/role
approved_nontechnical_participant: REQUIRED approved pseudonym/role
moderator: REQUIRED approved pseudonym/role
consent_record_reference: REQUIRED restricted-storage reference
consent_for_recording: yes/no/not recorded
session_date_time: REQUIRED ISO-8601 timezone
hardware: REQUIRED model or approved hardware class
os_version: REQUIRED exact version
browser_version: REQUIRED exact browser/version
assistive_technology: REQUIRED VoiceOver/version, or "none" for participant
zoom_and_viewport: REQUIRED zoom; desktop/mobile viewport
source_commit: 0f852ac16b3ff8333e991f9d5e66c5b0494b2345
hosted_image_digest: REQUIRED sha256
hosted_url: REQUIRED redacted origin/fixture reference
screenshots_sha256: REQUIRED hashes or "none"
recording_sha256: REQUIRED restricted-storage hash or "not retained"
redaction_review: REQUIRED reviewer/date
```

## VoiceOver / screen-reader traversal

Execution is deferred and must not be attempted from this workspace. It
requires a separately approved human witness and an authorized environment;
the checklist below is retained only as the future protocol.

Use a real unlocked macOS session with Accessibility permission. Start
VoiceOver with `Command-F5` (or `Fn-Command-F5`), use `Control-Option` as the
VoiceOver modifier, and do not use DevTools accessibility output as a
replacement. Read prompts verbatim. Record the first announcement, focus
target, completion, wrong turn, help, and defect for each task.

| # | Prompt | Required observation |
| --- | --- | --- |
| V1 | “Sign in and tell me which organization and account are active.” | Identity context is announced; no tenant/token internals exposed. |
| V2 | “Find the private document and open it.” | Main/heading/document name, privacy state, and usable focus are announced. |
| V3 | “Search for the phrase `launch notes`, open a result, and read one chunk.” | Search label, result name, no-match/status state, and chunk action are understandable. |
| V4 | “Open the linked HTML preview, then return to the document.” | Preview title, untrusted boundary, Back/Library navigation, and focus recovery are clear. |
| V5 | “Edit the Markdown and save a new version.” | Editor label, save action, busy state, success/error announcement, and version result are clear. |
| V6 | “Share with Alex as view-only, then remove Alex’s access.” | Recipient, role, confirmation effect, revoke result, and revoked history are announced. |
| V7 | “As Alex, try to open the revoked document.” | Denied state is understandable and does not disclose document metadata. |
| V8 | “As an administrator, register an approved connection and revoke it.” | Form labels, exact allowlist language, credential non-disclosure, dialog focus, status, and audit result are clear. |
| V9 | “Open audit history and export it.” | Audit control and result are identified without developer vocabulary. |
| V10 | “Recover from an expired session and an unavailable service.” | Alert/status gives next action; focus lands on recovery control; entered work is preserved. |

Required manual checks: headings/landmarks, form labels, button names,
status versus alert announcements, dialog focus trap and return focus, no
positive `tabindex`, no keyboard trap, 200% zoom/reflow, and no privacy or
authorization misunderstanding. Record defects verbatim and assign severity;
do not silently repair the notes after the session.

## Nontechnical-user witness

Use an approved participant who does not work with databases, MCP, OAuth, or
access-control implementation. Read each prompt exactly. Do not explain
internal terms before the participant attempts the task. Record completion as
`success`, `success with help`, or `failed`, plus duration, first action, wrong
turns, help, participant wording, and severity.

| # | Say this | Pass signal |
| --- | --- | --- |
| N1 | “Sign in and tell me whose documents you are about to see.” | Correct account/organization understood. |
| N2 | “Create a private document named `Q3 planning notes`.” | Private state and who can open it understood. |
| N3 | “Find the document by searching for `planning`, then open it.” | Search and result navigation completed without IDs. |
| N4 | “Give Alex permission to view this document.” | Correct recipient/role and confirmation effect understood. |
| N5 | “As Alex, try to edit it.” | Viewer understands edit is unavailable; no version is created. |
| N6 | “As the owner, remove Alex’s access.” | Correct person/effect confirmed before revoke. |
| N7 | “Open the old link as Alex.” | Revoked/unavailable state understood; no private metadata shown. |
| N8 | “Register the team calendar connection with only the listed tool, then check its status.” | Admin understands exact allowlist and credential boundary. |
| N9 | “Revoke that connection and find the audit record.” | Immediate future block and audit result understood. |
| N10 | “A save failed. What would you do next?” | Participant finds a clear retry/recovery action without guessing success. |

Suggested acceptance: at least 8/10 independent tasks per participant, no
privacy or authorization misunderstanding in N2–N7, and no critical
screen-reader or keyboard blocker. These are thresholds to evaluate, not a
claim that this register has passed them.

## Blocker and handoff

Current state: deferred/unmet by user directive. No further native
VoiceOver/screen-reader, Mac unlock, or Accessibility permission attempts are
authorized. Existing automated Chrome, keyboard, responsive, recovery, and
nontechnical-flow evidence is preserved; it does not satisfy the manual
witness gate. Therefore `.27` cannot close from this workspace, and no
enterprise-ready claim may rely on it. A separately authorized human owner may
schedule/approve both sessions, run this protocol on the exact source/image,
redact the results, and commit the run folder. Do not close the gate from this
checklist or from the existing automated Chrome 6/6 evidence.

## What remains for fl-urj.28

The repeated fresh-state release package still needs a frozen source/image/
configuration manifest and two independently retained runs for every required
package, including backup/restore/migration/DR, hosted auth/tenant, approved
connections, TLS/readiness/rollback, audit/observability, and final manual
browser/accessibility review. Each run needs exact commands, redacted output,
structured result, hash, reviewer, and timestamps; skips, failures, and the
manual-AT blocker must remain visible.
