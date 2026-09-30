---
name: professional-reporting
description: Professional two-layer reporting for voice bridge tasks. Every coding agent task must produce a FULL detailed report plus a final 40-word spoken summary.
---

# Professional Reporting Skill

The voice bridge ALWAYS does two-layer reporting:

1. **Full detailed report** — saved verbatim to `<state_dir>/reports/<session>.md` and visible on the dashboard Reports/Activity pages. Never truncated.
2. **Spoken summary** — the LAST paragraph of your reply, max 40 words, plain language, no markdown. That paragraph is extracted and spoken by TTS.

## What the bridge does automatically

- Persists your full reply to disk (survives crashes/restarts).
- Summarizes via the local LLM (or extractive fallback) — bulletproof, never hides your report.
- Mirrors summaries to the dashboard activity feed with timing.

You do NOT need to re-implement this. Just follow the template below when you write your final reply.

## Required structure for your final reply

Copy this order. Keep headings exactly — the dashboard parses them.

```markdown
# <Task title>

**Status:** completed | partial | blocked
**Project:** <repo>
**Elapsed:** ~<duration> (if you know it)

## Executive Summary
- <2-4 bullets: outcome + most important decision/limitation>

## Actions Taken
- <what you did>

## Files Changed
| File | Change | Description |
|------|--------|-------------|
| `path` | Modified | <what/why> |

## Verification
- **Checks run:** <tests/build/lint/manual>
- **Result:** passed/failed
- **Evidence:** <brief output or metric>

## Risks / Blockers
- <or None>

## Next Steps
- [ ] <next action>

## Spoken Summary
<1-3 sentences, ≤40 words, plain language — what was done, key files, what the user must do next. No markdown, no bullets. THIS PARAGRAPH IS SPOKEN.>
```

## Rules

- Be scan-friendly: headings + bullets over long paragraphs.
- Files Changed table is mandatory when you touched files.
- Verification is mandatory: say what you ran and what happened. Never fake results.
- Spoken Summary is ALWAYS the last paragraph. Keep it ≤40 words.
- Keep verbatim excerpts (search results, terminal output, browser excerpts) in the Detailed sections — do not paraphrase away evidence.
- Screenshots/images are OPTIONAL — only when needed for layout verification. Do not capture on every task.
- Reply directly: no emojis, no filler.
- Performance: `scripts/generate_report.py` renders the full markdown deterministically in <100 ms with no LLM call — use it for every report so the voice path stays fast. Never block TTS on an extra model round-trip for formatting.

## Pitfalls (session 2026-08-23)

- `pathlib.Path` is not a valid import when the file does `from pathlib import Path` — `_persist_full_report` and `_verify_task_result` crashed on `name 'pathlib' is not defined` and no report was saved to `<state_dir>/reports/`. Use `Path(...)` in methods, never `pathlib.Path` unless `import pathlib` is present.
- `scripts/review_gmail.py` now emits `Spoken: Latest — who — subject, when` with `_human_age` (just now → weeks ago) — the task template must tell Qubit to `speak that Spoken line verbatim`, not to summarize inbox as `check notifications yourself`.
- `wttr.in/<city> tomorrow` 500s — always strip temporal words (`tomorrow/tommorrow/tommorow`) before calling wttr.in and retry stripped city on 500. See `qubit-voice-bridge` skill.
- **UX harm from verbosity (2026-08-25):** This session's activity page fix ballooned a 5.6K plain file to 9.5K by adding badge/legend verbosity. User called it "shit" and reverted — the plain page is what shipped. When improving legibility, add meaning without replacing the author's visual system. See `qubit-voice-bridge/references/frontend-theming-2026-08-25.md` — do not re-create a legacy theme. The same principle applies to reports: be concise in the spoken layer, detailed only in the raw layer. If the user says "this is too verbose / this looks like shit / revert it", treat it as a first-class signal and update `qubit-voice-bridge` not just memory.


## Durability (power loss / provider switches)

Reports survive. `SharedMemory` (`<state_dir>/shared_memory.jsonl`, `fsync` per line) + per-session `<state_dir>/reports/<session>.md` are the ground truth. On reboot the bridge replays `shared_memory.jsonl` into `ConversationState` so context returns. `/api/reports` merges the ledger + activity feed so switching Qubit ↔ Qubit ↔ VS Code doesn't blank dashboards. Never delete `shared_memory.jsonl` on deploy. See `references/power-loss-recovery.md` and `references/durable-routing-2026-08-23.md`.


## Deterministic helper (use this — no reasoning needed)

The script `scripts/generate_report.py` builds a perfect report without you having to think:

```bash
python ~/Projects/Repos/openclaw-voice-bridge/scripts/generate_report.py   --task "<title>"   --summary "<40 words, what was done + files + next step — this is spoken>"   --files "a.py:Modified:fixed X; b.ts:Added:new thing"   --verify "pytest 12 passed"   --next "deploy to staging; notify QA"   --project "~/Projects/MyRepo" --status completed
# also: --raw output.txt (auto-extracts files + evidence) --actions "did X; did Y" --risks "None"
```

Run it whenever you need a report — it's deterministic, survives reboots, and auto-saves to `<state_dir>/reports/`.

If you already produced a long raw output, just:
```bash
python ~/Projects/Repos/openclaw-voice-bridge/scripts/generate_report.py --raw output.txt --task "deploy API" --status completed
```

## VS Code + dashboards

Reports saved to `<state_dir>/reports/*.md` survive power loss and appear in `/reports` on every dashboard (voice, Qubit, Qubit, VS Code). The shared ledger (`shared_memory.jsonl`) keeps history across provider switches.

## References
- Umbrella refs: `qubit-voice-bridge/references/professional-reports-2026-08-23.md`, `references/voice-providers-2026-08-23.md`, `references/power-loss-recovery.md`, `references/durable-routing-2026-08-23.md`.
See also `qubit-voice-bridge/references/hardening-2026-08-23.md` for the worker nudge that forces 1:1 mirroring + full dumps.

## Templates on disk

- `templates/professional_report.md` — full reference
- `templates/voice_report.md` — spoken-first variant
- `templates/daily_report.md` — daily rollup
