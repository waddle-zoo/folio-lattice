# Historical UX and accessibility evidence

Status: historical archive only. This candidate does not claim current release
readiness or replace the protected-main evidence already present in this tree.

Candidate base: `da19a21a8ebe936e91f1573b5ef788efc377618e`.

The records below preserve earlier adversarial usability and accessibility
work with their original target commits and outcomes. Each record is either
explicitly blocked or states that live/manual evidence was not run.

| Record | Original target | Outcome |
| --- | --- | --- |
| `docs/qa/ux-usability-review-2026-09-07.md` | Source/contract review dated 2026-09-07 | Historical review; no live pass claimed |
| `docs/research/evidence/fl-urj.27/fl-urj.27-baseline-2026-09-16.*` | `861eda6` | Blocked; host browser, Docker, and AT unavailable |
| `docs/research/evidence/fl-urj.27/fl-urj.27-retest-2026-09-16.*` | `75ff776` | Blocked before live product assertions |
| `docs/research/evidence/fl-urj.6/fl-urj.6.2-current-main-2026-09-09.json` | `861eda6` | Blocked; no browser/Docker/AT pass |
| `docs/research/evidence/fl-urj.6/fl-urj.6.5-current-main-2026-09-09.md` | `861eda6` | Assertion handoff; browser execution blocked |
| `docs/research/evidence/fl-urj.7/*` | `861eda6` | Host execution pending or blocked; no readiness claim |

Current protected-main browser records remain the authoritative current
evidence, including `docs/research/evidence/fl-urj.27/20260917T0716Z-176a2ce-browser.json`
and `20260917T0731Z-dbfc3da-admin-browser.json`, which are already present on
the parent commit and are not duplicated here.

Excluded from this scoped candidate:

- `.gitignore` changes for local Gas Town metadata.
- `MAYOR-HANDOFF-2026-09-09.md`, which is workflow handoff metadata rather
  than product release evidence.
