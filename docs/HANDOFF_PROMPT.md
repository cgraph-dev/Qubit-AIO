# HANDOFF PROMPT — Qubit runtime + Tk Voice Edit continuation

> Operational handoff, not a status authority. The authorities remain
> `AGENTS.md`, `docs/STATUS.md`, `docs/OPEN_THREADS.md`, `docs/FROZEN_TARGET.md`,
> `docs/DECISIONS.md` and the program/spec under `docs/superpowers/`.

You are continuing the Qubit milestone in `/home/trick/Projects/Repos/qubit-voicebridge`.
Work autonomously, move fast, commit each coherent unit, and leave the tree clean.

## Read first
1. `AGENTS.md` (commit-as-you-go, one worktree per agent, docs authority, brand/licensing).
2. `docs/STATUS.md` (Waves 60–61 are the latest measured truth).
3. `docs/OPEN_THREADS.md` top "Resume point" (durable handoff).
4. `docs/superpowers/specs/2026-10-26-runtime-boundary-and-openjarvis-design.md` and the program plan.
5. `docs/FROZEN_TARGET.md` + `docs/DECISIONS.md` (pin, exclusions, Windows out of scope, owner rulings).
6. `docs/PHASE8_INVENTORY.md` (measured internals surface — read-only; no deletions yet).

## Exact state (2026-10-27, after commit following this file)
- `main` was `c327f999b` when this file was written; `canonical/main` is pushed; worktree clean.
- Free disk ~83 GB. A game is installed; use `nice -n 10` and focused tests, not the full matrix unless the gate says so.
- Live: `qubit-gateway`, `qubit-voicebridge`, `qubit-voice-typing` active; gateway identify `ready`, protocol 1; `/chat?resume=20261019_094333_0a4efc` → 200; Telegram `@QubitBot` polling.
- Voice Edit panel is the **native Tk HUD only** (`voicebridge/voice_edit/overlay.py`). The Next.js/Three.js web HUD was built then removed at owner direction; do NOT reintroduce a browser HUD. Tk cannot run Next.js/Three.js/GSAP; the cyberpunk look is Canvas drawing.
- Owner-observed live Tk HUD turn at 12:35: weather query → preview/diff/history, model `qubit-brain4`, 8,051 ms. Screenshot: `/tmp/opencode/tk-hud-live.png`.
- Tests at the correction: 108 focused Tk/voice tests; earlier post-merge focused: 733 passed/6 skipped (runtime/CLI/connectors/recipes/traces/evals), 134 root voice/HUD, 89 remediation/deep/optimizer. No full matrix, no `VERIFIED`.

## Program status
- P1 RuntimePort/native: done. P1b Hermes/OpenClaw skill sources: done. P2 out-of-process OpenJarvis: done.
- P3 `-z` and `chat -q` through the port: done; native default, OpenJarvis typed refusal/no fallback.
- P4 recipes: `morning_digest`, `monitor_operative`, `deep_research` implemented; weather live, health is an honest gap, Google calendar/mail require the existing token; voice commands registered.
- P5 connectors: Google Contacts, Google Calendar, Dropbox read-only wired; Apple pack contract/platform-gated (Linux `not_supported`). No live OAuth probe yet.
- P6 traces/evals: opt-in local trace store, offline eval bench, `qubit optimize variants` selector. No DSPy/energy/FLOPs sampler; stated unavailable.
- P7 `rust_local`: registered as optional; extension absent → typed `rust_engine_unavailable`; no build attempted.
- P8: measured inventory only; every deletion needs owner sign-off.

## Next work, in order
1. **Live Tk HUD pass:** on a real selection and a dictation, exercise Apply/Copy/Edit/Retry/Revert/Cancel; verify capture-before-show, live partials, preview diff, history, stale-transcript clear, and no flicker. Fix anything exposed; add regression tests.
2. **Live connector probes** where credentials exist: Google Contacts/Calendar scope (`$QUBIT_HOME/google_token.json`), Dropbox token (`$QUBIT_HOME/dropbox_token.json`), Apple on Linux must be a typed `not_supported`. Record exact refusals if absent.
3. **Owner P8 review:** walk `docs/PHASE8_INVENTORY.md` rows; no `HERMES_*`/`.hermes`/plugin-global/attribution deletion or rename without a ruling recorded in `docs/DECISIONS.md`.
4. **Final verification gate:** after implementation freezes, run the focused set once, then the full matrix once; record commands/counts in `docs/STATUS.md`. `VERIFIED` only with the required behavioral evidence.
5. Optional Phase 7: only with owner permission, build/package the OpenJarvis Rust extension; until then keep the typed refusal.

## Guardrails
- One git worktree per implementing agent; commit as you go; never end with uncommitted work (`wip:` is fine).
- Push only canonical: `git push canonical HEAD:main`. Never origin.
- Restart `qubit-gateway` and `qubit-voicebridge` when live code changes; restart `qubit-voice-typing` only for a Voice Edit change (owner-directed), then verify the Tk window and `/chat`.
- Do not add a web HUD/Next.js/Three.js/Chromium panel. Tk only.
- Do not rename `HERMES_*`, `.hermes`, `/root/.hermes`, plugin-SDK compat globals or attribution; P8 is owner-gated.
- Never print bot tokens, API keys, or `.env` contents. Redact before bounding.
- Preserve `wip/orb-ui-20261025`, `~/.hermes.pre-retirement`, the searxng container on `:8080`; never run combined orb/Qt suites.
- A typed refusal is not a capability. Do not weaken tests/assertions or add xfail/skip to go green.
- Update `docs/STATUS.md` and `docs/OPEN_THREADS.md` at session end with commands + observed output.

## Commands
```bash
cd /home/trick/Projects/Repos/qubit-voicebridge

# Tk HUD + voice core (real display)
PYTHONPATH=qubit_runtime DISPLAY=:0 .venv/bin/python -m pytest -q \
  tests/test_voice_edit_hud9u.py tests/test_voice_overlay_ui9u.py \
  tests/test_voice_overlay_close9u.py tests/test_voice_edit_live_panel9u.py \
  tests/test_voice_edit_helper9u.py tests/test_voice_edit_integration9u.py \
  tests/test_stt_client9u.py

# Runtime/recipes/connectors/traces/evals/CLI focused set
cd qubit_runtime && ../.venv/bin/python -m pytest -q \
  tests/runtime tests/recipes tests/connectors tests/traces tests/evals \
  tests/cron/test_cron_preview.py tests/cron/test_cron_notice_format.py \
  tests/qubit_cli/test_engine_gate.py tests/qubit_cli/test_chat_query_engine.py \
  tests/qubit_cli/test_oneshot_engine.py tests/tools/test_toolsets.py \
  tests/tools/test_registry.py tests/qubit_cli/test_tools_config.py

# Live
systemctl --user is-active qubit-gateway qubit-voicebridge qubit-voice-typing
curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8890/chat?resume=20261019_094333_0a4efc'
xdotool search --name "Qubit Voice Edit"   # Tk panel
```

## Known limits
- No full matrix and no `VERIFIED`; live action buttons on the Tk HUD are the next validation.
- Google/Dropbox credentials are absent here; Apple connectors require macOS frameworks.
- `qubit gateway status` may label the live service "manual" (cosmetic supervisor detection).
- Tk HUD is a Canvas approximation; true 3D/GSAP would require a webview/browser, which the owner rejected.
- The web HUD source remains only in git history (`567564088`); it is not part of the product.

## Definition of done for the next session
Live Tk HUD actions verified (or defects fixed with tests), live connector refusals/probes recorded, final matrix evidence gathered, `docs/STATUS.md` + `docs/OPEN_THREADS.md` updated, canonical/main pushed, and the tree clean. Do not claim `VERIFIED` without the gate.
