# Bulletproof summary + 40-word spoken cap (Aug 2026)

Source session: every long Qubit/Qubit task must emit **both** a full raw report and a **≤40-word spoken summary**; raw must survive `events.jsonl` truncation and summary/ollama failure.

## Raw vs spoken — never mix
- Raw full report: all tool outputs, images (`![...](path)` when the agent actually captured them — **optional, only when needed for browser/vision verification**, not every task), perplexity/ddg excerpts kept **verbatim**. Pushed as `raw: reply[:50000]` in `feed_push` for the dashboard `raw` expand.
- `events.jsonl` `openclaw.full_reply` / `hermes.full_reply` must log `reply[:50000]` (not `[:2000]`), otherwise the raw is lost after a crash/restart when `feed_push` is gone.
- Persist to disk: `voicebridge/orchestrator.py: _persist_full_report(session_key, reply, status, elapsed_s)` → `<state_dir>/reports/<safe(session_key)>.md` with header `status/elapsed/at` + raw. Feed a `raw_report` item so dashboard shows the file path inline. On failure, log `openclaw.report.save_failed`.

## Code pattern — summarize must never hide the raw
```python
# 1) journal + persist raw FIRST
log_event("openclaw.full_reply", ..., reply=reply[:50000])
self._persist_full_report(session_key, reply, status, elapsed_s)
try:
    if self.cfg.get("reports","verify", True): self._verify_task_result(session_key, reply)
except: ...

# 2) summarize — bulletproof
try:
    summary = summarize(reply, self.cfg.section("summarizer"))
except Exception:
    from .summarizer import clamp_words as _cw, _strip_markdown as _sm
    summary = _cw(_sm(reply or ""), 40) or "Task finished — check the full report on the dashboard."

# 3) journal summary AFTER raw, then speak
log_event("openclaw.summary", summary=summary[:400])
self.feed_push({"kind":"summary", ..., "summary": summary, "raw": reply[:50000]})
self.speak(summary)
```

## Summarizer internals
- `voicebridge/summarizer.py: SUMMARY_PROMPT` is capped at **max 40 words** (was 70).
- `summarize()` never throws: `None`/`""` → `""`, `ollama` unavailability → `summarize_extractive`, all paths `clamp_words(..., 40)`; ultimate fallback `clamp_words(text, 40)`.
- `_strip_markdown` removes code fences so the extractive fallback doesn't speak markdown.
- Qubit mirrors the same flow: `hermes.full_reply[:50000]` + `_persist_full_report(f"qubit:{task.session_key}")` + verified + bulletproof 40w.

## Agent prompt contract
`_launch_task` skill-nudge appendix forces the agent: `web fetching including perplexity/ddg`, `Screenshots/images are OPTIONAL — capture only when needed`, `keep relevant excerpts verbatim`, and **two parts: 1) FULL detailed report 2) last paragraph = tight 40-word max spoken summary**. Config flag `reports.save_dir/verify` controls the behavior.

## Verify what they did (non-crashing)
`_verify_task_result(session_key, reply)` is best-effort: regex-extract up to 4 mentioned paths, check `Path.exists()`, run `git status --porcelain` (3s timeout), log `openclaw.verify` with `paths` + `git`, push `route: verify` warning if any `missing`. Wrapped so it never crashes the task.

## Verification
- `voicebridge.summarizer.summarize("", {}) == ""` and `summarize(None, {}) == ""`
- `summarize("Hello world. "*300, {"provider":"ollama","ollama_model":"does-not-exist", "max_words":40})` → `<=40` words, no exception
- `summarize("```python\\n" + "x=1\\n"*300 + "```\\nFinal.", {"provider":"extractive","max_words":40})` → no ` ``` `, `<=40` words
