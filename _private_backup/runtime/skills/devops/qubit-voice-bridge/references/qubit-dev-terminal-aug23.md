# Qubit-Dev Terminal — qubit/openclaw-Style REPL For the Voice Bridge (Aug 23, 2026)

One-liner that was missing: `qubit-dev` now opens like `qubit`/`openclaw` — interactive REPL plus slash commands, backed by the same bridge state (`~/.local/state/openclaw-voice-bridge/events.jsonl`, `~/.config/openclaw-voice-bridge/`).

## Entry point

- `pyproject.toml: [project.scripts] qubit-dev = "voicebridge.terminal:main"` — **single** entry point. Do NOT add a `qubit` alias (collides with `~/.local/bin/qubit`, the Qubit workspace/dashboard launcher at `~/Projects/Repos/voice-dashboard/open.sh`). The stale `qubit = ...` alias was the reason `qubit-dev: command not found` after `uv pip install -e .`.
- Launcher shim `~/.local/bin/qubit-dev` (same pattern as `~/.local/bin/qubit`):
  ```bash
  # ~/.local/bin/qubit-dev
  #!/usr/bin/env bash
  set -euo pipefail
  exec "$HOME/Projects/Repos/openclaw-voice-bridge/.venv/bin/qubit-dev" "$@"
  ```
- Install: `uv pip install --python ~/.venv/bin/python -e .` (no bare `uv pip install -e .` without `--python`). Verify with `bash -lc 'qubit-dev --help'` from a clean shell (no venv activated).

## Terminal surface

- File: `voicebridge/terminal.py` (+ optional `voicebridge/terminal/commands.py`). Uses `prompt_toolkit` when present (`PromptSession` + `FileHistory` at `~/.local/state/qubit-dev/history`), falls back to `input()`+`readline`.
- Slash commands (all go through `handle_slash()`): `/help /status /logs /activity /interrupt /talk /clear /quit` (`/q`, `/exit` aliases, `/a` for activity, `/l` for logs). Bare text without `/` is treated as `/talk <text>` — same as `qubit chat`.
- One-shot: `qubit-dev /status`, `qubit-dev "who are you"`, `qubit-dev --attach /talk "fix the CGraph sanitizer"`.
- `--attach` starts `VoiceOrchestrator(no_hotkey=True).start()` in-process so `/talk` is synchronous (no HTTP hop through `POST /api/type` → `/api/result`).

## Shared journal

- Both `qubit-dev` and the dashboard read/write the same journal `~/.local/state/openclaw-voice-bridge/events.jsonl` (wired via `Config.state_dir()` + `logging_setup.setup(state_dir=…)`). `qubit-dev /logs` and `curl /api/logs` must agree; `voicebridge task "hi"` (CLI typed path) and dashboard web both surface `log_event(...)` into the same feed.
- `/logs [limit] [q]` and `/activity [limit] [q]` share the same filter logic as the bridge — keep them reading from the same `events.jsonl` tail, not from divergent state.

## Pitfalls

- Don't regress the launcher: `~/.local/bin/qubit` must stay the Qubit dashboard opener, not the terminal. Only `qubit-dev` belongs to the voice bridge.
- `prompt_toolkit` is optional — keep the `input()` fallback so `qubit-dev` works on minimal installs. Don't add it as a hard dep without updating `scripts/install.sh`.
