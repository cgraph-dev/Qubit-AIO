# Routing & Template Hygiene — Aug 23 Sweep

## Template shadowing (zero-mistake routing)
- `task-templates.yaml` is checked **before** the heuristic classifier; first `match` wins.
- `research-current` (`use perplexity to search for (the )?latest ...` → `kind=coding`) shadowed `search-web` (`(use )?(perplexity|search the web|search|look up|google) ...` → `kind=local-script` free Perplexity Pro browser). Result: web searches paid an agent round-trip instead of 0-cost local script.
- **Fix:** deleted `research-current` in both `config/task-templates.yaml` and `~/.config/openclaw-voice-bridge/task-templates.yaml`; moved `search-web` to position 0 so `perplexity` always hits the free path. 18 → 16 templates.
- Duplicate `check-email` entries kept (broader vs Gmail-specific patterns don't truly shadow), but `what-day` (`what (day|date) is (it|today)` → `kind=conversation`) was 0.5s LLM — replaced by instant `local:time_date`.
- **Rule:** templates must not contain a narrow pattern that is a subset of a broader cheaper `local-script`/`local` pattern. Put cheapest (local) matches first.

## Instant local skills (no agent, no LLM)
- New `voicebridge/local/actions.py:time_date` — `Europe/Bucharest` zoneinfo, `Today is Saturday, August 23. The time is 00:47 EEST.` (~0.01s). Patterns: `what (day|date|time) is (it|today)`, `what time is it`, `current (date|time)`.
- `what-day` template removed; intent now resolves via `local_actions.match()` directly.
- `_handle_local` now mirrors to `WebCtx`: `ctx.summary = msg[:300]; ctx.route="local"; ctx.done.set()` so `/api/result` returns the spoken summary (was `""` — activity correct but dashboard poll empty). Also mirrors `ctx→_last_web_summary` under `_web_lock` for legacy pollers.
- Verified: `POST /api/type {"text":"what time is it"}` now `route=local` with summary, not `""`.

## Summarizer / config drift
- Code defaults were `qwen3-vl:8b` (6GB, cold, 25s timeout) while live config is `huihui_ai/llama3.2-abliterate:3b` (2.6GB, hot, `keep_alive=-1`). Fallback always hit extractive.
- **Fix:** `voicebridge/summarizer.py` (`summarize_ollama` default + `cfg.get("ollama_model"...)` fallback) and `voicebridge/config.py DEFAULTS` changed to `huihui_ai/llama3.2-abliterate:3b`, timeout 25→5s. `config/voice.local.yaml` drift (`extractive/qwen` → `ollama/huihui`, added missing `webui` block) synced to `~/.config/.../voice.yaml`.
- Rule: when tuning `voice.yaml` on disk, sync the repo template `config/voice.local.yaml` so fresh installs don't revert.

## Frontend payload regression
- `scripts/inline-background.mjs` re-inlined 40MB `data:image/gif;base64` into both `frontend/index.html` and `frontend/activity.html` on every run → 290KB → 40MB.
- **Fix:** external by default (`/static/Logo/ascii-magic-4.gif` with `loading=lazy`, bridge serves `/static`), `INLINE_GIF=1` only for Vercel static deploy. `vercel.json` now `buildCommand: "INLINE_GIF=1 node scripts/inline-background.mjs"`. Verified `index.html 290KB`, `activity.html 66KB`, no `data:image/gif;base64`.

## Verification for this class
- `POST /api/type` for `what time is it` / `system stats` / `How are you?` / `Salut ce faci?` must all return correct `route` + non-empty `summary`.
- Concurrent `POST /api/type` (EN+RO) must not swap; `GET /api/activity` and `/api/result` must agree.

See also: `concurrent-request-isolation.md` (thread-local `WebCtx`), `dashboard-stability-aug23.md` (BrokenPipe / GIF first fix).
