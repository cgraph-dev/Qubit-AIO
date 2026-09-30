---
name: perplexity-web-search
description: "Zero-cost web search via the user's logged-in Perplexity Pro browser session (CDP) instead of the paid API. Use for any web/research query to save API money."
version: 1.0.0
author: qubit
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [perplexity, search, web, zero-cost, cdp, browser, pro]
---

# Perplexity Web Search (zero API cost)

Search the web using the user's **Perplexity Pro login** in the Qubit
Chromium profile — no Perplexity API key, no per-request fee. The user has
a 1-year Pro subscription in the browser; the API was costing money for the
same answers.

## When to use

- Any web search / research question ("what is X", "latest Y", "how to Z")
- Current events, prices, versions, docs lookups
- Replaces the paid `sonar`/`sonar-pro` API calls in Qubit, Qubit, and
  the voice bridge

## Requirements

- Same browser setup as social-inbox-check:
  - Chromium running with CDP: `--remote-debugging-port=9222
    --remote-allow-origins=*`
  - Profile: `/home/trick/snap/chromium/common/chromium`
  - Logged into `www.perplexity.ai` (cookie `__Secure-next-auth.session-token`
    present = logged in)
  - `browser_ensure.sh` starts it if needed (from social-inbox-check skill)
- Python venv with websocket-client:
  `/home/trick/Projects/Repos/openclaw-voice-bridge/.venv/bin/python`

## Script

`scripts/perplexity_search.py "<query>" [max_chars]`

- Opens/creates a Perplexity tab, navigates to
  `https://www.perplexity.ai/search?q=<query>`, waits for the answer in
  `main div.prose`, prints the answer text.
- Output is the answer body only (no sidebar/history).
- Typical latency: ~5-20s (Perplexity searches + generates), then instant.

## Integration

- **Voice bridge**: `config/task-templates.yaml` — add a `search-web` template
  that runs this script and speaks a ≤3-sentence summary. Also the
  `_chat_perplexity` fast path (orchestrator.py) can call the script instead
  of the API.
- **DDG-first fast path (Aug 2026)**: the bridge's `search-web` template now
  runs `scripts/web_search.py` which tries **DuckDuckGo first** (~1.5-3s via
  the `ddgs` lib) and falls back to THIS script only for thin DDG results or
  explicit `--perplexity`. Reason: Perplexity browser search takes 25-38s
  (browser + generation) vs DDG's ~2s for simple lookups — a daily
  "latest X version" question is 3.5s total via DDG vs ~38s via Perplexity.
  This script remains the deep-research path; do NOT re-point the template
  at it directly for routine lookups.
- **Qubit**: for web tasks, prefer running this script over the paid
  `web_search` tool when the question is one-shot research.
- **Qubit**: point web search at this script (or instruct the agent to
  run it) instead of the perplexity api provider.

## Cost math (why this matters)

- Perplexity API: flat $0.005/request + sonar $1/M + sonar-pro $3-15/M →
  $0.007-0.075 per search.
- This script: $0 (Pro subscription already paid).
- Three consumers shared one API key before (Qubit + Qubit + bridge) →
  rate limits (429s). Browser Pro removes all of that.

## Pitfalls

- **Voice-bridge invocation must use the venv python explicitly**: the
  bridge's `search-web` template runs this as a `local-script` with
  `~/Projects/Repos/openclaw-voice-bridge/.venv/bin/python <script>`.
  `shell=True` uses SYSTEM python3, which lacks `websocket-client` →
  `ModuleNotFoundError` (rc=1); a bare shebang path without +x gives
  rc=126. Always prefix the venv python in any script/template/agent that
  calls this, and `chmod +x` the script.
- Answer block selector: `main div.prose` — the body text includes the
  sidebar history, so never use `document.body.innerText` (grabs session
  history list).
- First search after a fresh tab may show an interstitial; the poll loop
  (45 × 2s) handles it.
- If Perplexity shows "Sign up" instead of an answer, the session cookie
  expired — re-login in the Chromium window once.
- Keep the browser running; `browser_ensure.sh` restarts it with CDP if
  it was closed.
- Don't open a second Chromium on the same profile (profile lock).
