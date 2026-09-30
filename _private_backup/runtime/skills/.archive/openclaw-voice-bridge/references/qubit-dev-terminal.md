# qubit-dev terminal (qubit/openclaw-style) + strict live logs

`qubit-dev` (`voicebridge.terminal:main`, installed at `~/.local/bin/qubit-dev`
alongside `~/.local/bin/qubit`, venv entry at `qubit-voice-bridge/.venv/bin/qubit-dev`)
mirrors `qubit`/`openclaw` UX for the voice bridge.

**One-shot:** `qubit-dev --help`, `qubit-dev /status`, `qubit-dev /logs 5 qubit`,
`qubit-dev /activity 5`, `qubit-dev "who are you"`. Bare text = talk.

**REPL:** `qubit-dev` → `qubit-dev>` prompt, slash commands
`/help /status /logs /activity /interrupt /talk /clear /quit` (bare text = `/talk`),
history at `~/.local/state/qubit-dev/history`, `--attach` runs the bridge in-process
so `/talk` is synchronous (no HTTP hop). `input()`+`readline` fallback when
`prompt_toolkit` is absent.

**Strict live logs — single source of truth:**
`voicebridge/logging_setup.py` writes `events.jsonl` (`_EVENT_FILE` at
`~/.local/state/openclaw-voice-bridge/events.jsonl`); `POST /api/talk|/type`,
`_handle_local`, `_handle_hermes`, `_handle_conversation`, `_spawn_waiter`
(`_handle_agent_result` + `summarize` `raw[:50000]`), and `speak()` in
`_web_mode` all `log_event` (so `conversation.local.summary/full`,
`local.action.exec/result`, `openclaw.full_reply`, `hermes.task/summary`,
`routing.decision`, `tts.speak` always land). Surfaced as:
- Dashboard **Activity** feed (`GET /api/activity`, ring `ActivityFeed`, clickable
  summary → full-report drill-down via `GET /api/item/:id` with `raw` passthrough;
  feed seeded from last 4000 `events.jsonl` lines).
- Dashboard **Talk/Activity → Gear → Settings → Live logs** (nonstop when open,
  cumulative 400-event buffer, `/api/logs?limit=&q=&since=`, filter+Copy).
- `GET /api/logs?limit=&q=&since=` (token-gated) and `qubit-dev /logs` (same file).

Pitfalls:
- `qubit-dev`'s earlier `qubit` alias collided with `~/.local/bin/qubit` (the Qubit
  workspace launcher `voice-dashboard/open.sh`). Keep `qubit-dev` only in
  `pyproject.toml [project.scripts]`; remove any `qubit = …` entry.
- Perplexity zero-cost Pro browser path (`perplexity-web-search` skill, CDP
  `$QUBIT_HOME/skills/research/perplexity-web-search/scripts/perplexity_search.py`)
  requires Chromium with `--remote-debugging-port=9222` and a logged-in session;
  nightly `auto-push` (qubit cron `30 23 * * *`, script `$QUBIT_HOME/scripts/auto-push.sh`)
  now covers 4 repos and carries live `config/personal-knowledge.md|voice.yaml|task-templates.yaml`
  into `qubit-voice-bridge/config/` so a fresh `git pull` restores them.
