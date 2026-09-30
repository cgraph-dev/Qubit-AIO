# Qubit Vendor Delta — `qubit_runtime/` vs working Hermes install (2026-09-22)

**Purpose:** establish, from evidence, whether the Qubit runtime is a stub
foundation or a faithful rebranded fork, and why the "gap" exists.

## What was compared

| | Path | Version | Revision |
|---|---|---|---|
| Working Hermes | `/home/trick/.hermes/hermes-agent` | `hermes-agent` 0.21.3 | git `db39ee3f28` |
| Qubit fork | `qubit_runtime/` | `qubit-agent` 0.21.1 | tracked in this repo (12,173 tracked files) |

Method: exclude `node_modules/`, `venv/`, `__pycache__/`, `.git/`; normalize
`hermes`→`qubit` case-insensitively; diff top-level names and per-directory
module inventories.

## Verdict

`qubit_runtime/` is the **same Hermes lineage, renamed**, and is **~2 patch
releases behind** the working install. It is **not** a stub foundation. The
absent modules are upstream structure changes, not stripped functionality.

## Scale

| Metric (excl. node_modules/venv/pycache/.git) | Hermes | Qubit |
|---|---:|---:|
| files | 15,637 | 12,186 |
| `.py` files | 7,008 | 5,819 |
| `hermes_cli` / `qubit_cli` top-level `.py` | 403 | 332 |
| `hermes_state` / `qubit_state` modules | 29 | 22 |
| CLI subcommands | 65 | 62 |

Per-subsystem file counts:

| subdir | Hermes | Qubit |
|---|---:|---:|
| agent | 302 | 269 |
| gateway | 176 | 152 |
| providers | 3 | 3 |
| plugins | 389 | 384 |
| native | 5 | 5 |
| cron | 33 | 24 |
| tools | 322 | 292 |
| skills | 331 | **337** |
| apps | 3,853 | 2,413 |
| web | 207 | 191 |

Qubit keeps every top-level family and has **more** skills than the working
install. The gaps concentrate in `hermes_cli/` and the decomposition-heavy
areas.

## The "missing" modules are upstream decomposition, not stripped code

- 72 modules exist in `hermes_cli/` but not `qubit_cli/`: an entire
  `gateway_launchd` / `gateway_migrate` / `gateway_multiplex_*` /
  `gateway_service_unit` / `gateway_setup_wizard` / `gateway_supervised_restart`
  family, plus `cli_init_mixin`, `cli_shutdown`, `cli_single_query`,
  `cli_config_load`, `config_effective`, `status_report`, `vault`, and more.
- 7 state modules (`qubit_state_ids`, `_lockguard`, `_lockowners`,
  `_profile_repair`, `_rewind`, `_timeline`, `_user_copy`) exist upstream but
  not in the fork.
- Only 1 module is genuinely new in Qubit: `qubit_cli/plugin_index.py`.
- **Import test:** of the 72 absent modules, `1 / 72` is referenced anywhere in
  `qubit_runtime` (the hit is the common word `vault`, not the module). Nothing
  Qubit imports is missing, so the boot is not broken by these absences.
- `qubit_cli/gateway.py` is **larger** (6,202 lines) than `hermes_cli/gateway.py`
  (5,068) because upstream split the monolith into the `gateway_*.py` family
  above (upstream `COMPAT_MANIFEST.md` documents PR #102117 decomposition, and
  the split files define real symbols: `gateway_launchd` 39,
  `gateway_migrate` 51, `gateway_service_unit` 18, etc.). Qubit is the
  pre-decomposition revision; Hermes 0.21.3 is post-decomposition.

Conclusion: the delta is a **version gap (0.21.1 → 0.21.3)**, not a deliberate
strip and not a reimplementation.

## What the stubs actually are

The compatibility/placeholder surface is the **overlay**, not the core:

- `voicebridge/qubit_compat.py` (10,392 lines) — Qubit route/handler layer.
- `voicebridge/gateway/openclaw_handlers/` (17 modules) — OpenClaw adapters.
- Qubit route/voice/state adapters around the runtime.

The Hermes core (`agent/`, `gateway/`, `providers/`, `plugins/`, `mcp_serve.py`,
`qubit_cli/`) is real.

## Residual upstream identifiers (rebrand not complete)

66 `.py` files under `qubit_runtime/` still contain `HERMES_HOME`,
`hermes_cli`, `hermes_agent`, or `HermesAgent` — e.g. `tools/computer_use/*`,
several `skills/*/scripts/*`, and `scripts/*`. These are rebrand debt, separate
from the version gap.

## Cause of the gateway instability — **CORRECTED 2026-09-22**

**Superseded:** the earlier "`hermes` owns 9119 = port/authority conflict" guess
was wrong. `127.0.0.1:9119` is the managed `hermes-dashboard.service` (the
reference); Qubit's webui is `8890`; Qubit's vendored 9119 server is inert
(`SecondListenerDisabled`). There is no Qubit port conflict.

**Actual signal:** the service restarts repeatedly (18× on 2026-09-22) with
chronic `Installing TUI dependencies… / npm install failed.` and `TUI build
failed` (`qubit_runtime/ui-tui`, `hermes-tui` workspace), plus a SQLite
WAL-reset warning. See `FINDINGS_2026-09-22.md` §B1–B3.

## Recommended sequence

1. Back up the tree; fix the TUI npm/build failure and restart churn (not a
   `:9119` conflict).
2. Repair the config source of truth, the duplicate unit, and the stale
   entrypoints (W0).
3. Compare the Qubit vs reference process across argv/env/`QUBIT_HOME`/config/
   registries/session DB.
4. Pass the boot baseline in
   [QUBIT_RESET_IMPLEMENTATION_STRATEGY_PROMPT_2026-09-22.md](QUBIT_RESET_IMPLEMENTATION_STRATEGY_PROMPT_2026-09-22.md).
5. Treat the 0.21.1→0.21.3 sync and the identifier leaks as separate,
   policy-governed follow-ups — not part of the boot fix.
