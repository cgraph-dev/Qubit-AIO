# Professional reports — deterministic + durable (2026-08-23)

## Perplexity-sourced template (scan-friendly, executive-first)
`templates/professional_report.md` is the canonical structure every agent must follow: Task/Agent/Date/Project/Status → Executive Summary → Objectives → Actions Taken → Files Changed (table) → Verification → Risks/Blockers → Next Steps → Open Questions → Appendix (commands/raw). Best practice from research: clear headings, bullets over paragraphs, file-level change table, explicit verification evidence. Also shipped: `templates/voice_report.md` (spoken-summary + detailed) and `templates/daily_report.md` (daily rollup).

## Two-layer delivery (full + 40-word spoken)
Orchestrator (`voicebridge/orchestrator.py:_handle_agent_result`, `voicebridge/qubit/backend.py`) always does both:
1. **Full report** persisted verbatim to `<state_dir>/reports/<session>.md` and to `shared_memory.jsonl` — never truncated, survives crash.
2. **Spoken summary** = last paragraph of worker reply, clamped to 40 words via `voicebridge/summarizer.py:clamp_words` (extra Ollama `summarize_ollama` path, extractive fallback). That paragraph is what TTS speaks; full report goes to dashboard `/reports` + `/api/reports`.

## Idiot-proof for the small local model (the "retarded" fix)
The 3B local LLM doesn't need to reason about format. `scripts/generate_report.py` is deterministic:

```bash
python ~/Projects/Repos/openclaw-voice-bridge/scripts/generate_report.py \
  --task "<title>" \
  --summary "<40w, what was done + files + next step — this is spoken>" \
  --files "a.py:Modified:fixed X; b.ts:Added:new thing" \
  --verify "pytest 12 passed" \
  --next "deploy to staging; notify QA" \
  --project "~/Projects/MyRepo" --status completed
# also: --raw output.txt (auto-extracts files/evidence) --actions "did X; did Y" --risks "None"
```

Auto-saves to `<state_dir>/reports/<safe>-<ts>.md`. Orchestrator nudge in `_launch_task` tells the worker to use it; `professional-reporting` skill documents it. VS Code: `.vscode/tasks.json` "report: generate professional report" (promptString inputs for task/summary/files/verify → same script).

## Dashboard + qubit
- Voice/Web/Qubit dashboards all read `/api/reports` (merged activity + shared ledger + file reports, category/date/search/days + `POST /api/reports/prune` 90-day auto-delete) and `/api/activity`. See `professional-reporting` skill for the required markdown order (headings must match — dashboards parse them).
- Evidence rule: keep verbatim excerpts (search results, terminal output, browser refs) in Detailed sections; don't paraphrase away.

## Testing
`tests/test_new_features.py` covers it (sticky/shared/generate_report/openai_tts/frontend + `tests/test_stress.py`). Run `python -m pytest tests/ -q` — expect 80 passed.
