# Task Execution Report — Template (v1)

> **Copy this structure for EVERY coding/agent task.** The orchestrator always keeps the FULL report and speaks only the last 40-word summary. Produce BOTH.

**Task:** <one-line task title>
**Agent:** <model / backend: openclaw | hermes> — session `<session_key>`
**Date:** <YYYY-MM-DD HH:MM>
**Project:** <repo or `~/Projects/...`>
**Status:** completed | partial | blocked | failed
**Elapsed:** <Xs>  •  **Route:** <openclaw|hermes|local-script>

## Spoken Summary (last paragraph — max 40 words, spoken aloud)
> 1–3 sentences: what was done, key files, what the user must do next. This paragraph is extracted and spoken. No markdown, no bullets — plain direct language.

## Executive Summary
- <2–4 bullets: outcome, scope, most important decision or limitation>

## Objectives
- <what the task was supposed to achieve>
- <success criteria>

## Actions Taken
- <action 1 — what you did and why>
- <action 2>
- <action 3>

## Files Changed
| File | Change | Description |
|------|--------|-------------|
| `path/to/file` | Modified | <what changed and why> |
| `path/to/file` | Added | <what was added> |
| `path/to/file` | Deleted | <why removed> |

## Verification
- **Checks run:** <tests / build / lint / manual>
- **Result:** passed | failed | partial
- **Evidence:** <brief output, observed behavior, metric>
- **Edge cases:** <skipped checks or follow-up needed>

## Risks / Blockers
- <risk or blocker, or “None”>

## Next Steps
- [ ] <next action 1 — owner if any>
- [ ] <next action 2>

## Open Questions (if any)
- <question>

## Appendix (optional)
- **Commands run:** <key commands>
- **References:** <links / excerpts kept verbatim>
- **Screenshots:** <refs if any — OPTIONAL, only when needed for verification>

---
*Rules: be scan-friendly (headings + bullets), evidence-based, decision-oriented. Spoken summary is ALWAYS the last paragraph (≤40 words). Raw report is never truncated.*
