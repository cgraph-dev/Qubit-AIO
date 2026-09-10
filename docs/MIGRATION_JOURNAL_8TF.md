# Packet 8T-F — migration worktree journal

## Worktree

- Clean worktree: `/tmp/wt8tfv2` (created via `git worktree add --detach HEAD`).
- Base: `5ad3795` (Packet 8T-F close-out repair).
- Purpose: verification builds from a clean tree without touching the dirty
  checkout (wheel build proven from `/tmp/wt8tf` earlier; this worktree
  tracks the close-out tip).
- The worktree shares the repository object store: creating it copies no
  user data and deletes nothing. Removal: `git worktree remove --force
  /tmp/wt8tfv2` (no checkout data affected).

## Backup

- No file backup needed for worktree creation (read-only view of committed
  objects). The dirty checkout was never checked out over, reset, or
  cleaned; unrelated dirty/untracked files were hash-inventoried before
  (`/tmp/8tfv3-baseline.sha`) and after every change.
- User-data stores (config/memory/reports/models/audio/browser/service
  intent) were never migrated by 8T-F code changes; migration/rollback
  behavior is rehearsed hermetically in isolated HOME directories
  (`tests/test_foundation8tf.py`, `tests/test_packet8s.py`).

## Journal

1. `5ad3795` — vendored Hermes executes in-tree + matrix (prior turn).
2. (this turn) Curated Qubit Agent rebrand inside dirty CLI/dashboard/
   terminal files (existing lines only; unrelated setup/banner hunks
   preserved), orchestrator speech, nav-brand, activity-feed text.
3. (this turn) Matrix re-verdict: no REUSE/ADAPT row left unintegrated.
4. (this turn) Rebrand regression suite `tests/test_rebrand8tf.py`.
5. Full hermetic suite re-run before commit (see final report).

## Rollback

- Scoped revert of this turn's commit(s) only: `git revert <hash>`.
  Never `git reset --hard`, `git clean`, or broad checkout.
- Worktree removal does not affect the main checkout.
- Runtime/user-data rollback paths are unchanged (product
  migrate-rollback, profile rollback, receipted setup rollback).

## Product-completion turn (2026-09-09, uncommitted)

Scope: canonical `qubit` launcher, Hermes source differential, voice
coverage for every CLI family, dashboard Tier-2 extras, user-facing
`qubit` spelling migration, docs single-source-of-truth.

1. Canonical launcher: `qubit = voicebridge.cli:main` added to
   `pyproject.toml [project.scripts]` (`voicebridge` kept as the
   documented compat alias, same parser/backend). `build_parser()` prog
   follows argv[0] (`qubit` canonical, `voicebridge` alias) with `qubit`
   fallback. Installed + verified: `qubit --help` (prog qubit, rc 0),
   `qubit capability summary`, `voicebridge --help` (prog voicebridge).
2. Completions: `scripts/gen_completions.py` now writes `qubit.*` canonical
   + `voicebridge.*` compat + `qubit-dev.*`; `completion --cmd` defaults to
   `qubit` with all three choices. Parity test extended (determinism +
   serve for all three progs).
3. Hermes differential re-verified against pinned source at
   `/tmp/upstream-8tfv3/hermes-agent` (commit 2237be3 == matrix pin):
   executable dump (69) ⊆ source `_BUILTIN_SUBCOMMANDS` (73); gap is
   `help` (meta) + `gui` (alias of desktop) + `learning`/`memory-graph`
   (journey family) — all classified in `docs/PARITY_HERMES.json`.
   Gap recorded in `docs/HERMES_SURFACE.json` `differential_note`.
4. Voice registry 171 → 245 entries: `task_run` ("run the task", Hermes
   runtime, confirm-gated, typed UNAVAILABLE when DISABLED), full
   actions/ghost/opencode-read/verified-skills/experiments/product/
   package/webui/frontier/profile/branding/device/command-list backends
   (same module fns the CLI calls), hardware/loop guidance entries with
   `qubit` spellings, "list my projects" / "show my sessions" aliases.
   `CLI_COVERAGE` maps all 64 CLI families; `VOICE_IMPOSSIBLE` records
   tested reasons (passphrase, nested-JSON, opencode-run non-wiring,
   standing-policy, structured proposals, desktop session).
5. Dashboard Tier-2 extras 21 → 28: export_state/import_state
   (passphrase-secret, preview-only restore), actions_preview/prepare/
   retry/recover, ghost_preview (nested-JSON, cli+dashboard).
6. User-facing spelling migration to `qubit`: README, docs/HowTo,
   voice/monitor/tui/kanban/device/product/lifecycle messages, guidance
   backends, webui CLI hints. `python -m voicebridge` and the
   `voicebridge` import package unchanged (technically required).
   README `~/.hermes/.env` → `~/.config/qubit-voicebridge/.env`.
7. Docs: launcher identity in `docs/OPERATIONS_8T_F.md` §1 + §4
   voice-parity note, `docs/8TF_SOURCE_OF_TRUTH.md` launcher line,
   this journal entry.
8. Full hermetic unit suite (final): 2686 passed, 1 skipped
   (pre-existing environmental skip, also present in the 2674-pass
   pre-change baseline), 44 deselected (live/dashboard/monitor/ui
   opt-ins) in ~11 min. Focused re-runs green after each subsystem;
   affected areas also green with `-W error` (62 passed). Full suite
   re-run with `-W error`: 2686 passed, 1 skipped (same 8U
   environmental skip), 44 deselected — zero warnings.

## Rollback (this turn, uncommitted)

- All changes are uncommitted working-tree edits; rollback is `git diff`
  review + `git checkout -- <path>` per file. No commits, no pushes.
- Reinstall entry points after any revert of `pyproject.toml`:
  `uv pip install -e ".[test]" --python .venv/bin/python`.

## Final-parity turn (2026-09-10, uncommitted)

Closed all six named gaps plus every code-side matrix row, on one backend
per capability across CLI, dashboard, and voice:

1. O-board canvas (`voicebridge/kanban.py` canvas layer): `kanban
   open/move/widget-add/list/move/remove/layout-show/set/role-grant/revoke/
   roles/snapshots/snapshot-restore`; per-board delegate roles; widget
   sandboxing (active markup stripped, counted); auto-snapshots + restore
   (widgets, layout, roles, task positions). Voice: kanban_open ("open the
   board"), kanban_move ("move this task"), widget add/list/remove, layout
   show. Dashboard: Tier-1 auto + 4 Tier-2 extras. Tests:
   tests/test_board_canvas8u.py (12 incl. atomic-claim concurrency).
2. O-docker equivalent (`voicebridge/runtime_images.py`, new): pinned
   registry (hermes-runtime @2237be3, opencode-binary 1.18.29), digest
   re-verification, staged upgrades, receipt rollback, audited removal,
   MISSING/STAGED/READY/DEGRADED states, no downloads, no daemon. Voice
   runtime_* (7 entries, incl. "docker status" alias). Tests:
   tests/test_runtime_images8u.py (8).
3. Desktop/Ghost: 4 new supervised specs (desktop.screenshot, input.key
   allowlisted, input.cursor, window.close with honest ROLLBACK_IMPOSSIBLE);
   CLI retry/recover/timeout; `computer-use session` X11/Wayland probe;
   voice ghost_action_preview/click ("click this button")/type ("fill this
   form")/read_tab ("read the browser tab")/retry/recover + grant
   target/ttl; origin binding for role actions via current-tab origin.
   Tests: tests/test_desktop_parity8u.py (19).
4. OpenCode voice: preview/run(disposable-only)/cancel/retry/recover/
   receipts/grant/revoke through request_coding_run; in-project writes
   refused (VOICE_DISPOSABLE_ONLY); fixed latent revoke_grant
   confirmed-flag bug; CLI retry/recover; dashboard prepare/hash-bound
   execute extras. Tests: tests/test_opencode_voice8u.py (10).
5. Export/import voice safety: refusal entries (no passphrase args by
   construction); passphrase resupply-at-confirm; CLI --passphrase flag
   warns (ps/history); CLI/dashboard consistency proven. Tests:
   tests/test_export_voice8u.py (7).
6. Matrix: MATRIX_8T_F.json 35/35 IMPLEMENTED (proof + evidence + owner +
   rollback + affirmative explanation per row); NATIVE_EQUIVALENTS_8U.json
   20/20 IMPLEMENTED; MATRIX_COVERAGE extended; capability-catalog gate
   tests updated to the final rule.
7. Registry hygiene: "open the board" uniquely resolves to kanban_open;
   ghost Tier-2 ids colliding with voice entries removed/renamed;
   COMPLETE_EXAMPLES + CLI_COVERAGE (new `runtime` family) extended;
   all 17 mandated utterances resolve (action ones clarify for args;
   confirm/cancel via classifier intents).
8. Newly-enabled local suites: Playwright UI 24/24 (fixed 44px
   interrupt-link + nav min-width; corrected Talk-route nav selector),
   live 32/32, monitor 1/1, dashboard 3/4 (rctrl PTT delivery needs the
   operator X session — listed manual-only).

No new upstream code copied this session (all Qubit-owned); no
THIRD_PARTY_NOTICES change required. Pinned-source facts reused:
hermes-agent v2026.9.7 @2237be3, openclaw v2026.9.2 @3928bad,
opencode 1.18.29 (env binary reports 1.18.30 — drift refused by the pin).

## Close-out turn (2026-09-10, uncommitted)

Repaired every reported blocker with real backends and re-proved all gates:

1. Right-Ctrl/dashboard stress: `tests/test_dashboard_stress.py` 4/4 green
   incl. `test_stress_rctrl_ptt_beep_event` against the running bridge
   (xdotool key 105 reaches X, hotkey/beep event observed). Prior failure
   was X-session delivery flakiness, not a code defect; no production code
   changed for this item.
2. Hermes pin staged outside the repo: `/tmp/pinned-upstream/hermes-agent`
   @ 2237be355906fbe6065ce1815711eee52b2d646e (tag v2026.9.7, LICENSE +
   cli.py + hermes_constants.py present). `/tmp/upstream-8tfv3/*` are
   symlinks to the staged clones (documented convention; /tmp is
   ephemeral — re-provision with `git clone --depth 1 --branch v2026.9.7
   ...` after a reboot). Foundation gate 12/12, vendored differentials
   green, `test_real_hermes.py` 17/17 live (provision + Ollama turns +
   receipts), existing XDG provision re-verified READY (binary reports
   `Hermes Agent v0.21.1 (2026.9.7)`), `runtime install hermes-runtime`
   proven OK→READY→upgrade against QUBIT_HERMES_CLONE.
3. OpenCode pin: `/tmp/pinned-upstream/opencode-1.18.29/bin/opencode`
   reports 1.18.29, sha256
   ca6c0e1f42be3120595bf6848937e7586ec862c87fa7aa111e89c7cc6e9a4650.
   Runner status against it: effective True, installed 1.18.29,
   version_match True (real version probe). Isolated
   `runtime install opencode-binary` → READY with matching digest; the
   installed 1.18.30 correctly refuses with MISMATCH (pin not bumped).
   New operator-gated test `test_staged_pin_binary_proof`
   (QUBIT_OPENCODE_PIN_BIN; skips with reason when unstaged).
4. Dashboard set: dashboard-marked 4/4, UI 24/24 (fixed real defects:
   `.interrupt-link` raised to 44px touch target, `.page-nav a` min-width
   44px, Talk-route nav selector corrected to `.rail-action-active`),
   live 32/32, monitor 1/1. Deselected audit: 61 = 32 live + 4 dashboard +
   1 monitor + 24 ui, each category proven green in its own gate; default
   `-m "not live and not dashboard and not monitor and not ui"` stays
   because those gates need Ollama / running bridge / clean-venv /
   Playwright-chromium.
5. No new upstream code copied; no THIRD_PARTY_NOTICES change.

## Close-out-2 turn (2026-09-10, uncommitted): owned 1.18.29 + CLI preview fix

1. Owned OpenCode binary: `qubit runtime install opencode-binary`
   MATERIALIZES the verified binary into Qubit-owned
   `$XDG_DATA_HOME/qubit-voicebridge/runtimes/opencode-1.18.29/bin/`
   (+ `PIN.json` digest receipt); `_resolve_binary` prefers the owned
   copy unless the operator configured an explicit different binary;
   `status`/`runtime status` report it as effective; execution re-probes
   --version and fails closed on mismatch. Live: owned bytes
   sha256:ca6c0e1f…, `--version` → 1.18.29, `runtime status` READY
   effective, `opencode status` resolves the owned path, no surface
   reports 1.18.30. New `--source` on `runtime install/upgrade`,
   QUBIT_OPENCODE_PIN_BIN env, optional voice `source` arg.
2. Real bug fixed: `qubit opencode preview` omitted the required
   `project` envelope arg (always failed validation); `_task_args` now
   carries it + regression test `test_cli_preview_carries_project`.
   CLI/voice/dashboard preview proven on the same owned backend.
3. Deselected audit (61, each green in its gate): dashboard 4/4 (running
   bridge), ui 24/24 (Playwright+chromium), live 32/32 (Ollama models,
   mic, audio, inbox scripts), monitor 1/1 (clean-venv). Per-test list:
   dashboard=test_dashboard_stress (status/text/voice/rctrl);
   live=test_live_stress×13, test_mic_health×1,
   test_packet4b×1+test_packet4c×1+test_packet5c_integration×3,
   test_release_smoke×5, test_smoke(piper)×1, test_steal_list_strict×7;
   monitor=test_packet7a×1; ui=test_ui_browser_8l×24. Default `-m`
   filter stays: these gates need live services the hermetic default
   must not require.
