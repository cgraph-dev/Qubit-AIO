# Session 2026-08-23 — parity fixes (STT blank, 41MB ghost page, wttr 500, report crash, slow filler)

## STT blank (`chars 0` on loud capture p 0.26-0.37, not VAD)
- `parakeet` can return 0 on a real wav even when energy is present — not a threshold issue.
- Bench on same wav (68204B, 2.13s, peak 0.13): parakeet 172ms/41 chars, moonshine 48ms/40 chars, whisper base 460ms/41 chars, small 1.21s misuse-drops. whisper large-v3-turbo 5s.
- Fix: `_transcribe_with_fallback()` — if primary empty, retry once with the other engine (`parakeet ↔ moonshine`, 48-172ms). Don't use whisper as fallback (0.46-1.34s, no better). True silence (23KB wav) stays 0 on both — no hallucination.
- Also tuned `vad.rms_gate 0.0018→0.0015` for soft syllables; verified `mic.gain 8.0` correct for Scarlett 2i2.

## 41 MB ghost page (all routes served huge HTML)
- Cause: `voicebridge/webui.py:_frontend_dir` preferred `~/Projects/Repos/voice-dashboard/frontend` (React build) first — that build is 40 MB index (inlined `data:image/gif;base64,` 30MB gif). Bridge hit 41846736 B.
- Fix: prefer `Path(...)/frontend` (bridge, 9.5-12 KB) first, only fall back to `voice-dashboard/frontend` when bridge missing. After: `/ 12859 /activity 9555 /reports 8311` (200). Re-applies to both activity rebuilds.

## wttr.in 500 on `Bucharest tomorrow`
- `scripts/weather.py` forwarded `tomorrow/tommorrow/tommorow` verbatim to `https://wttr.in/<city> tomorrow?format=j1` → 500 → orchestrator said `The search failed.`.
- Fix: `city = re.sub(r"\b(today|tomorrow|tommorrow|tommorow|now|...)\b.*", "", city)` + 500 fallback `temporarily unavailable` + retry stripped city. Also fixed in `web_search.py` weather detour.

## Reports not saved / `pathlib` crash
- `voicebridge/orchestrator.py:_persist_full_report` and `_verify_task_result` used `pathlib.Path` while file imports `from pathlib import Path` — `NameError: name 'pathlib' is not defined`, report never hit `<state_dir>/reports/` so `/reports` looked empty even though activity was live.
- Fix: `pathlib.Path` → `Path(...)`. Verified reports dir now holds `ValutRank_snapshot-…` etc.

## Duplicate shared ledger
- `voicebridge/webui.py:/api/reports` appended `shared_memory` twice (two blocks with `shared ledger`), doubling report items.
- Fix: remove second block (the `shared-` prefixed one), keep first.

## `ask_executor: true` froze UX
- `live voice.yaml` had `ask_executor: true` — every coding task blocked on `Qubit or Qubit?`. Felt frozen, was just waiting.
- Fix: live `~/.config/.../voice.yaml` `ask_executor: false` (repo `config.py` already False). Diagnose via `tail events.jsonl | grep routing.decision`.

## Slow filler (`Yeah.` 4.79s)
- `Yeah.` went through `intent.llm` (`qwen3-vl:8b` 6.2GB, then `3b` still 4.79s for `Yeah.`). Should be 0ms.
- Fix: filler fast-path **before** `Classifier.classify()` in both `_route_intent` (mic) and `_process_web_utterance_inner` (web): `if low in ("hello","yeah","hi"…) → _handle_conversation` (logs `routing.filler_fast`). Also switched `intent/llm.py` default `qwen3-vl:8b → huihui_ai/llama3.2-abliterate:3b`.

## STT verbose hallucinations before
- `intent.llm_classifier: false` + fallback 3B hallucinated Romanian on short captures. Re-enabled `true` and kept filler bypass so determinism + LLM coexist without latency.
