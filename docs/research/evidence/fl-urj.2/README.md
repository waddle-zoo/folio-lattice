# fl-urj.2 evidence register

This directory is the retained evidence location for the nontechnical
usability test defined in
[the UX contract](../../2026-09-07-hyperset-first-consumer-ux-contract.md).

No participant session was run for the pre-implementation contract commit.
That absence is intentional: no usability result is fabricated before a real
hosted UI exists. The first implementation gate must add one redacted run
folder named `YYYY-MM-DD-<short-run-id>` containing:

- `README.md` with role codes, consent status, build commit, browser/device,
  and redaction status;
- `task-metrics.csv` with task outcome, duration, help count, and issue ID;
- `moderator-notes.md` with observations and redacted quotes;
- `accessibility.md` with keyboard and assistive-technology results;
- `browser-matrix.md` with exact versions used for that run; and
- `summary.md` with findings, severity, decision, and tracked fixes.

Keep raw recordings and consent forms in approved restricted research storage,
not Git. Commit only redacted notes, safe screenshots, and references or
hashes for restricted originals.
