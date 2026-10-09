# Session-Fix Verification + Regression Sweep (2026-10-09)

Scope: confirm the five fixes merged this session HOLD on the live serving path, and
run the regression sweep (gate, parity, touched-surface tests). Repo worktree at
`95f2307e` (fresh worktree `/tmp/qb-w4`; pristine copy `/tmp/qb-w4-pristine` at the same sha).

## Fix verification (claims reproduced, not trusted)

| # | Fix | Verdict | Evidence |
|---|-----|---------|----------|
| 1 | Cron fails-closed on phantom-done | **PASS** | Drove the guard `_cron_phantom_done` in `qubit_runtime/cron/scheduler.py`: promise reply + NO tool receipts → returns failure (True); same reply WITH a `role='tool'` receipt → `None` (ok). Chattiness/no-tools → failure; ordinary briefing → `None`; promise + past-tense acted verb → `None`. Table below. |
| 2 | Cron `repeat` persists E2E | **PASS** | Temp home; `create_job_with_scheduler_registration(repeat=3)` → store `cron/jobs.json` row is `{"times": 3, "completed": 0}`; re-read via `cron.jobs.get_job` same; string forms `"once"→1`, `"3"→3`. Owner store untouched (read-only). |
| 3 | Devices sidebar duplicate | **SOURCE-FIXED, LIVE STILL DUPLICATES (expected)** | Live `http://127.0.0.1:8890/devices` renders **two** `/devices` links ("Devices" builtin + "Qubit Devices" plugin) and emits the React duplicate-key warning. Served bundle `index-BdUAKsvJ.js` built **2026-10-07 10:31**; the App.tsx skip-guard commit is **2026-10-09 19:08**. Source fix present at `App.tsx:453` (builtin nav has `/devices` at `:361`; plugin `qubit-devices/manifest.json` `tab.path=/devices`). **A frontend rebuild is required — owner's call; not rebuilt/restarted.** |
| 4 | 2 restored frontend files are "groundwork" | **PASS (claim correct)** | Commit `95f2307e` added `frontend/qubit-web/src/components/SharedMetricsConsentBanner.tsx` and `frontend/qubit-web/src/lib/model-picker-current.ts`. **Nothing in `frontend/qubit-web/` imports either** (no importers, no barrels, no `import.meta.glob`). The importers live in the OTHER tree, `qubit_runtime/web/src/` (App.tsx, ModelPickerDialog.tsx, ModelsPage.tsx + a test), which already HAD both files (byte-identical) before `95f2307e`. So in the touched tree they are groundwork, not a wired fix. |
| 5 | `hermes` CLI compat | **PASS with one caveat** | `~/.local/bin/hermes` → `.venv/bin/qubit` (symlink). `hermes --help`, `qubit --help`, `qubit status`, `qubit skills` all exit 0. `h`/`hs`/`hd` are shell aliases in `~/.bashrc` (`h='qubit'`, `hs='qubit status'`, `hd='qubit doctor'`); `.zshrc` still spells `hermes ...` but that resolves through the symlink. **Caveat: `qubit doctor` exits 1** — BY DESIGN (`voicebridge/cli.py` `_cmd_doctor` returns `0 if all checks OK else 1`); the only non-OK check is `browser_cdp` (no Chromium listening on `127.0.0.1:9222`), an environmental condition, not a CLI-compat failure. |

### Fix 1 reproduction (verbatim results)

```
(a) promise, NO tool receipts: FAILURE=True  -> Agent turn completed with no tool activity ...
(b) promise, WITH tool receipt: FAILURE=False -> None
chatter, no tools: True
briefing, no tools (must be None): None
promise+past-acted (must be None): None
```

## Regression sweep

- **gate_all.py: PASS** — 4 checks (3 blocking + 1 informational), matching the corrected count:
  import gate 29/0, entry-point resolution prod=0 tests=0, lazy imports 0 unguarded (1 guarded),
  shadowed defs self-test FIRES / 19 known / 0 new, capability audit 1727 missing (informational).
- **frozen_target_progress.py: VERDICT = AT TARGET** — 383 missing symbols now (975 at freeze, +592 closed).
  Split: **prod 0 / tests 383** (all remaining debt is in tests; production is at target).
- **Touched-surface tests (one file at a time, `-x -q`):**

| File | Result |
|------|--------|
| test_cron_banner_settle9u.py | 3 passed |
| test_cron_port_wave19.py | 20 passed |
| test_cron_repair9u.py | 9 passed |
| test_cron_webhook_pairing_confirm9u.py | 6 passed |
| test_plugin_cron_provider_load_wave21.py | 3 passed |
| test_restart_fleet9u.py | 11 passed |
| test_chat_session_authority8u.py | 5 passed |
| test_chat_progress_bridge.py | 7 passed |
| test_chat_live_producer9u.py | 7 passed |
| test_new_chat_session9u.py | 5 passed |
| test_qubitchat_confirm_session9u.py | 8 passed |
| test_openclaw_family_sessions_advanced9u.py | 7 passed |

**Failures (all attributed PRE-EXISTING on the pristine worktree at the same sha):**
- `test_owner_session8tf.py::test_catalog_reports_owner_session_implemented` — `docs/PARITY_HERMES.json`
  absent from the repo tree (lives only in the installed venv `_data/docs/`); fails identically pristine.
- `test_chat_restart_survival_e2e.py::test_abandoned_stream_persists_reply_exactly_once` — E2E depends on a
  local model with ≥64k runtime context; local `qubit-brain4` reports 4096; fails identically pristine.
- `test_openclaw_family_sessions9u.py::test_sessions_diff_delegates_to_git_scope` — fails identically pristine.
- `test_orchestrator_session_authority9u.py::test_voice_run_session_and_qubit_chat_share_one_transcript` — fails identically pristine.
- `test_cli_chat_sessions_rebrand_9u.py::test_cli_messages_and_export_rebrand_content_and_meta` — fails identically pristine.

No NEW failures attributable to this session's changes.

## Could not verify

- Fix 3's duplication is resolved on the LIVE page — impossible without a frontend rebuild (owner's call).
  Verified only at source level + live symptom (still duplicates).
- `qubit doctor` exiting 0 — environmental (`browser_cdp` MISSING); not a code defect. Would pass on a host
  with Chromium on loopback CDP.
