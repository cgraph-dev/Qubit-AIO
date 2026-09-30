# Perplexity routing bug + cost reality (verified 2026-08-21)

Diagnosis session: user reported "we keep wasting money on Perplexity and it's not
working". Root cause was NOT a dead API key — it was the voice-bridge router
sending Perplexity requests to the coding agent, plus a flat per-request fee on
every paid search across three systems sharing one key.

## The routing bug — "use perplexity API to search X" never hits Perplexity

User asks the voice assistant "use perplexity API to search when GTA 6 will be
released" / "use verplexity API to check the most anticipated MMO RPG for 2026".

Journal evidence (`~/.local/state/openclaw-voice-bridge/events.jsonl`):
- `routing.decision` kind=**coding** source=**heuristic** reason=`task hints (1.0)`
  — the deterministic layer caught it first.
- kind=coding source=**llm** reason="The request involves using an API to fetch
  information, which is a coding task…" — the LLM layer also misfired.
- Aftermath: `openclaw.dispatch` (agent run) → reply spoken as "Updated GTA 6
  release date… **Changed files: gta6 release**" (the agent WROTE A FILE instead
  of answering) or the run was aborted by the user after ~4 s.

Two causes, both in `voicebridge/intent/`:
1. `classifier.py` `TASK_HINTS` regex matches `\b(…|check|update|…|api|endpoint|…|browser|file|…)\b`
   — "perplexity **API** to **check**" = 2 hits = 1.0 score → heuristic coding,
   no LLM round-trip. Any utterance containing "api", "check", "browser",
   "file", "update", "install", "run" is one hint away from coding.
2. `llm.py` SYSTEM_PROMPT: "Commands that touch files, code, repos, terminals,
   browsers, or servers are coding" — the model extrapolates "using an API" into
   coding. Nothing defines web-search / current-info questions as conversation.

The `_chat_perplexity` fast path (orchestrator.py ~line 911) only runs after the
utterance is classified `conversation` — so the bug makes every "use perplexity"
request miss it entirely. (It finally fired when the user rephrased as a
statement: "I gave you a task to search using perplexity API…" → classified
conversation → `conversation.perplexity` event.)

### Fix map (not yet applied — user confirmed diagnosis but no go-ahead yet)
1. `voicebridge/orchestrator.py`: before routing, force `"perplexity" in text.lower()`
   → conversation/fast-chat path (the existing `_chat_perplexity` already checks
   this on its own path).
2. `voicebridge/intent/classifier.py`: drop `api` (and ideally `check`, `update`,
   `browser`) from `TASK_HINTS`, or subtract hints when the text contains
   "perplexity" / "search".
3. `voicebridge/intent/llm.py` SYSTEM_PROMPT: add rule — "web search /
   current-info questions ('perplexity', 'search the web', 'what's the latest')
   are conversation, NOT coding".
4. `voicebridge/config.py`: `perplexity_model: sonar-pro` → `sonar` (3× cheaper,
   same web answers).
5. Qubit: `$QUBIT_HOME/config.yaml` `web.backend: perplexity` (default model
   sonar-pro) → `ddgs`/`brave_free` (free, plugins already installed) or at least
   `web.perplexity.model: sonar`.
6. Qubit: `~/.openclaw/openclaw.json` `tools.web.search.provider: perplexity`
   → `duckduckgo` (plugin already in `importedRuntimePluginIds`).

## Cost math (verified against live API)

- **Flat $0.005 per request** regardless of tokens. A 1-token "OK" reply on sonar
  came back `usage.cost: {request_cost: 0.005, total_cost: 0.00501}`.
- sonar: $1/M in, $1/M out + $0.005 flat → typical search ≈ $0.007–0.011.
- sonar-pro: $3/M in, $15/M out + $0.005 flat; the Qubit plugin prompt asks for
  a "comprehensive answer with citations" → 3–5k out tokens → ≈ $0.025–0.075 per
  search.
- Misrouted voice requests double-bill: full agent dispatch (DeepSeek tokens) +
  the agent's own Perplexity provider search.
- Shared key, three consumers: Qubit web backend, Qubit web search, voice
  bridge. A `429 request_rate_limit_exceeded` on sonar-pro mid-testing was the
  shared quota/rate being exhausted (transient — plain retry succeeded).

## API-key test recipe (tells you in one call what's wrong)

```bash
curl -s https://api.perplexity.ai/chat/completions \
  -H "Authorization: Bearer $PERPLEXITY_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"sonar","messages":[{"role":"user","content":"hi"}],"max_tokens":16}'
```
- HTTP 400 `max_tokens must be at least 16` → key VALID (auth passed; only param
  validation failed). Note: `max_tokens` < 16 is always rejected.
- HTTP 401 → bad/expired key.
- HTTP 429 → rate limit / quota exhausted (check all consumers on the key).
- Success → inspect `usage.cost.request_cost` to confirm the $0.005 flat fee.

## Journal greps for Perplexity health
- `grep conversation.perplexity ~/.local/state/openclaw-voice-bridge/events.jsonl`
  — actual paid fast-path calls (only 1 of 9 "perplexity"-mentioning events fired
  the paid call; the other 8 were misrouted or ASR-only).
- `grep routing.decision …` + the `source` field — heuristic vs llm tells you
  which classifier layer misjudged.
- `grep openclaw.dispatch …` — agent runs triggered by misclassification.
