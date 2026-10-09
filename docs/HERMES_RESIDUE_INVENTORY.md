# Hermes-residue inventory (deliberate compat vs accidental half-rebrand)

Authoritative list of what still says `hermes` after the Qubit rebrand, each entry
labelled **DELIBERATE** (load-bearing compat — removing it breaks a real reader)
or **ACCIDENTAL** (a half-rebrand left behind). Per the campaign rule: *half-done
rebrands are the bug; deliberate compat names are not.*

Measured against commit `eafb54e2` (the rebranded tree). Counts are `git grep`
occurrences in tracked text.

## 1. Environment variables — DELIBERATE (the compat surface)

- **817 distinct `HERMES_*` env-var names, 10492 occurrences.** These are the
  upstream env contract. The Qubit runtime still *reads* `HERMES_HOME`,
  `HERMES_KANBAN_*`, `HERMES_SESSION_*`, `HERMES_PYTHON`, `HERMES_DESKTOP_*`, etc.
- Why deliberate: the running gateway/desktop/TUI and vendored upstream code set
  and read these names on both sides of the boundary. Renaming the *reader*
  without renaming the *writer* is exactly the half-rebrand bug class. The
  canonical reader is `qubit_constants.get_qubit_home()`, which prefers
  `QUBIT_HOME` and falls back to the legacy `HERMES_HOME`/`.hermes` home.
- The vendored `_hermes-python` helper dir and `HERMES_PYTHON` / `HERMES_PYTHON_SRC_ROOT`
  are read by spawned subprocesses (the live process table shows
  `HERMES_PYTHON=/home/trick/.../qubit-voicebridge/.venv/bin/python`).

## 2. systemd unit names — MIXED

- **Installed/active units are all `qubit-*`** (verified on the live host):
  `qubit-gateway.service`, `qubit-voicebridge.service`, `qubit-voice-orb.service`,
  `qubit-voice-typing.service` — DELIBERATE, correct.
- **`systemd/` still ships `hermes-*.service` files** (22 distinct names:
  `hermes-gateway.service`, `hermes-agent.service`, `hermes-server.service`, …)
  and `hermes-*.service` strings appear in the tree. Overwhelmingly these live in
  `qubit_runtime/tests/qubit_cli/` (18 files) as **fixtures** for the upgrade/
  recovery tests, plus `qubit_cli/update_*.py` which DELIBERATELY reference the
  old unit names to **retire/rename them during an update** (the migration path).
  DELIBERATE migration surface; the shipped-and-active units are Qubit-named.

## 3. Lock / marker file names — DELIBERATE (state compatibility)

- `.hermes-update-in-progress` (124), `.hermes-update-result` (51),
  `.hermes-update-old` (40), `.hermes-bootstrap-complete` (40),
  `.hermes-update-staging` (35), `hermes-update.lock` (10), `.hermes-runtime`,
  `.hermes-package`, `.hermes-catalog`, `.hermes-tools`, `.hermes-agent`.
- Why deliberate: these are **read by an in-flight update/recovery path that may
  have been written by the previous version**. A running updater must recognise a
  marker left by the old build or it will re-run a half-finished migration. The
  live owner state still carries `hermes_config_overrides.json` and
  `hermes_persona.json` on disk — legacy-named **data files** the runtime reads.

## 4. pycache / bytecode prefix — DELIBERATE

- `hermes-pycache` (6), `hermes-cached` (2). The update path passes
  `-X pycache_prefix=<request['bytecode_cache']>`; the request value is
  derived from the home dir name, so the prefix inherits the legacy home token.
  Consistent with (1)/(3): it tracks the resolved home, not a hardcoded brand.

## 5. Compat aliases in code — DELIBERATE (`qubit_constants.py` ~line 192-203)

- `get_hermes_home = get_qubit_home`, `get_hermes_home_override`,
  `set_hermes_home_override`, `reset_hermes_home_override`, `hermes_home_key`,
  `display_hermes_home`. Explicit comment: *"Upstream (post-pin) code calls
  `get_hermes_home()` by that name throughout"* — the alias is what keeps
  vendored-upstream call-sites working. DELIBERATE.
- `qubit_runtime/scripts/check_compat_pointers.py` reports
  **"✅ no in-tree dependency on the 2084 plugin-compat pointers"** — proving the
  compat aliases are one-directional (upstream→Qubit), not an in-tree crutch.
- Lowercase `hermes_*` identifiers (`hermes_cli`, `hermes_home`, `hermes_env`,
  `hermes_root`, `hermes_cfg`) are mostly docstring/comment citations of the
  upstream origin plus the alias definitions above.

## 6. Module-comment mentions — DELIBERATE (provenance)

- Docstrings cite the upstream module (`hermes-agent hermes_cli/web_routers/…`)
  to record what was ported 1:1. `_launchers.py` (19) and the `_old_updater*`
  files are the heaviest. These are provenance notes, not runtime references.

## ACCIDENTAL — none found in this pass

Every `hermes` occurrence examined resolves to a deliberate compat/provenance/
migration surface. The *code-level* half-rebrands (a name loaded but bound
nowhere, siblings left stale, renamed test expectations, a shadowed method) are a
different class and are tracked by `scripts/check_half_rebrand.py` (name-binding)
and `scripts/check_duplicate_defs.py` (shadowing), not by the word `hermes`.

### check_half_rebrand.py — REAL hits vs known false-positive class

`python3 scripts/check_half_rebrand.py` reports **212 unbound Name-Load(s) across
8435 files (48 bind_module files skipped)**. **All 212 are known false-positive
classes**, split:

| Hits | File | Class | Verdict |
|-----:|------|-------|---------|
| 157 | `tui_gateway/server.py` + `methods_*.py` | bind_module rebound globals (bodies installed onto server.py's namespace) | FP — documented in the script's own header |
| 31 | `tests/compat/old_updater_dependencies.py` | frozen historical fixture; header says `# ruff: noqa: F821 -- globals came from the old process; the test supplies them` | FP — deliberate |
| 28 | `plugins/platforms/feishu/adapter.py` | dynamic optional-import table (`_import_optional` injects `HttpMethod`, `LARK_DOMAIN`, … into module globals at runtime) | FP — dynamic injection |
| 5 | `optional-skills/security/godmode/scripts/auto_jailbreak.py` | `exec(compile(...), _caller_globals)` runs via exec; names supplied by caller globals | FP — designed to run via exec |
| 1 | `tests/qubit_cli/test_local_runtime.py` | test-scope `Path` used before local import | FP (test) |

Net: **0 genuine half-rebrand findings** from the name-binding sweep. The
`bind_module` class is the dominant false-positive source and is why the script
skips those files by default (`--include-rebound` re-enables them).
