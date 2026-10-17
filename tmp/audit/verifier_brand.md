# Verifier report — four-agent rebrand wave (2026-10-17, revised)

Role: VERIFIER. No source edits, no commits. The wave is LIVE — commits landed
continuously DURING verification; every number is timestamped. Allowlist upgraded to the
AUTHORITATIVE source: **docs/REBRAND_MODULE_MIGRATION_PLAN.md §4 (THE EXCLUSION LIST)**,
rule "a name stays if something OUTSIDE the repo reads it."

## The wave (as observed, HEAD advanced 316ae8298 -> 9d5e31380 during verification)

Rebrand commits landed by the four agents (and others) in the window, newest first:
9d5e31380(fix), 7ad850838, 0edfe44ff, 999e36db5, dc5699322, ce8879e3d, 6d2d8e62e,
5aa085a3c, 1f28d5b0a, 4f23dea6e(test §4 checker), f9e2a1364, 2e057efc8, 53b245569,
7c8e19192, 316ae8298, 4629727d6. All four scopes (qubit_cli/; tools+tui_gateway+gateway+
acp+root; agent+plugins+pm+cron+apps; website+evals+scripts+non-Python) are represented.

## CHECK 1 — IMPORT HEALTH

Imported every touched module with ABSOLUTE sys.path
(`cd qubit_runtime && PYTHONPATH=. ../.venv/bin/python -c 'import X'`).
15 modules imported OK: cli, qubit_cli.{setup_profile,source_build,uninstall},
qubit_constants, tools.{browser_use_cli,code_execution_env,environments.base,set_theme_tool,
setup_beat_tool,terminal_tool_macos_open,tool_search}, tui_gateway.methods_onboarding,
agent.onboarding_first_tasks.

ONE failure, **PRE-EXISTING and NOT a regression**:
  setup.py -> `error: no commands supplied`. It calls `setup()` at module top level with no
  `if __name__=="__main__"` guard, so any import runs setuptools' argparse. Identical at the
  pre-wave parent (`git show 4629727d6~1:qubit_runtime/setup.py`); AST parse clean.
**No rebrand-induced import regression.**

## CHECK 2 — ALLOWLIST INTEGRITY (upgraded to §4)

Ran two independent instruments over the FULL wave range:
1. My own scan: `git show <wave> --format='' | grep -E '^-' | grep -E '<§4 alt-regex>'`.
2. The agents' checker `tmp/audit/check_s4_diff.py f9d4900be..HEAD` (183 removed lines).

RESULT: **0 HARD §4 violations.** Every protected token survives in the CURRENT tree,
verified by grep, not diff:
- `HERMES_HOME` — 55 py files
- `HermesCLI = QubitCLI` — qubit_runtime/cli.py:1808 (intact)
- `get_hermes_home = get_qubit_home` alias family — qubit_constants.py:198-202 (intact)
- `_LEGACY_HOME_DIR = ".hermes"` read-old fallback — qubit_constants.py:55 (intact)
- `requires_hermes` — 535 files (plugin manifests)
- `hermes_agent.plugins` — 10 files (pip entry-point group)
- No `HERMES_UPDATE_PAUSED`, `.hermes-update-*`, `ai.hermes.gateway*`, `_HERMES_USER_AGENT`,
  `HERMES_OVERLAYS`, `${HERMES_SKILL_DIR}`, `__HERMES_*__`, `hermes-locale`,
  `hermes-dashboard-*`, `hermes.*` metrics, `hermes_config_overrides.json`,
  `hermes_persona.json`, `hermes_platform/resolver/app.py` appear on any REMOVED line.

Two items requiring judgement — NEITHER is a violation:

(a) `7c8e19192` REMOVED: `print_success("Buzz/IRC configuration saved to ~/.hermes/.env")`
    -> `~/.qubit/.env`. §4 protects the on-disk path `~/.hermes/`. Checked the writer:
    `save_env_value` -> `get_env_path()` -> `get_qubit_home()/".env"` (config.py:2769,484-486),
    which prefers `.qubit` and falls back to legacy `.hermes` via `_LEGACY_HOME_DIR`. For a
    fresh install the new label is CORRECT (the old label was stale). For a legacy install
    resolving via the `.hermes` fallback the label is imprecise — a label-accuracy nit, not a
    wire breach: the path-resolution code (the load-bearing part) is untouched. NOT a violation.

(b) SOFT hit: `"service.name": "hermes-gateway-smoke"` in
    `sandbox/.../gateway_health_export_probe.py` — a local OTel smoke-test label, not a systemd
    unit identity (the exact documented false-positive class), inside a vendored `sandbox/`
    snapshot. NOT a violation.

(c) `SETUP_PROFILE_NAME` "hermes-setup" -> "qubit-setup" (4629727d6). Safe: setup-profile
    discovery is marker/role-based (`SETUP_PROFILE_MARKER=.setup-profile.json` or `role: setup`,
    setup_profile.py:70-81), NOT name-based, so existing users' setups are still found.

**Zero allowlist violations.**

## CHECK 3 — WIRE / CONTRACT SURFACE (read from CURRENT files)

| surface | current content | verdict |
|---|---|---|
| mcp_serve.py | `mcp = MCPServer("hermes", instructions=(...))` (line 731) | INTACT |
| acp_adapter/auth.py:8 | `TERMINAL_SETUP_AUTH_METHOD_ID = "hermes-setup"` | INTACT |
| acp_adapter/server.py:555 | `if normalized_method == TERMINAL_SETUP_AUTH_METHOD_ID:` | INTACT |
| qubit_cli/uninstall.py:169-174 | cleanup paths `~/.local/bin/hermes`, `hermes-acp`, `hermes-agent`, `/usr/local/bin/hermes-acp`, `/usr/local/bin/hermes-agent` | INTACT |

mcp_serve.py, auth.py, server.py were never touched by any rebrand commit.
**wire_surface_ok = true.**

## COMPAT LAYER + GATES (extra checks per owner directive)

- **voicebridge/agent_compat/ (framework.py, adapters/hermes.py, adapters/openclaw.py,
  __init__.py) and adapters/registry.yaml: UNCHANGED by the wave.** No wave commit and no
  worktree modification touches them (checked both). `check_s4_diff.py` independently reports
  "compat-layer / adapters files changed: 0".
- **tests/test_agent_compat.py: 30 passed, 2 failed.** The 2 failures
  (`test_fixture_really_imports_the_foreign_module`, `test_unmodified_foreign_plugin_executes`)
  are ENVIRONMENTAL: `FileNotFoundError: /home/trick/.cache/qubit-pins/hermes-agent` — that
  external pin checkout does not exist on this box. The identity tests
  (`hermes_cli is qubit_cli` under both import orders, alias resolution) all PASS (12/12 in the
  identity/alias/order subset). No rebrand break.
- **adapters/test_registry.py: 10 passed** (all five recorded discovery seams still exist).
- **scripts/gate_all.py: NOT READY — 2 blocking checks failed (expected PASS 5/5).**
  * import gate: PASS 29/0
  * entry-point resolution: FAIL HARD prod=12
  * lazy imports: FAIL 4 unguarded
  * shadowed defs: PASS (0 new)
  * capability audit: informational
  **These 2 failures are PRE-EXISTING, not caused by the wave.** Proven against a clean
  pre-wave worktree (`git worktree add /tmp/verify-baseline 4629727d6~1`): baseline gives the
  IDENTICAL lazy-import set (same 4 files: qubit_state_mutation_binding.py,
  gateway/session_viewer_close.py, qubit_cli/web_server_app.py, tui_gateway/methods_connectors.py)
  and IDENTICAL HARD prod=12. None of the failing files were touched by the rebrand. The
  gate's own header flags the live tree: "uncommitted files: 46 -- results reflect uncommitted
  work". The wave neither caused nor regressed the gate.

## CHECK 4 — DOES THE MEASUREMENT MOVE

Baseline: **1,059 strings / 473 files**. Timestamped runs of
`python3 tmp/audit/find_brand_leaks.py` (the tree moved under me as commits landed):

| time | HEAD | strings | files |
|---|---|---|---|
| baseline | — | 1,059 | 473 |
| 18:24 | 7c8e19192 | 1,027 | 465 |
| 18:51 | 9d5e31380 | **970** | **450** |

**Delta from baseline: -89 strings / -23 files, and still falling.** The total IS moving down,
consistent with the wave's scale (many commits, four scopes).

Consistency check of the tier-1 commit's claim: its message asserts the audit reported
"1,069 strings across 479 files" at commit time. Re-running that EXACT committed script
(`git show 4629727d6:tmp/audit/find_brand_leaks.py`) against the current tree gives 1,042/472.
The classifier's `is_user_visible`/`LEGIT_PATTERNS` heuristics are loose (it counts
website/scripts and evals/ probes as production) and the tree is a moving target, so the
specific 1,069/479 figure is **not independently reproducible**; the direction (falling) is.

## Verdict

- Import health: CLEAN (1 pre-existing non-regression failure).
- §4 allowlist integrity: CLEAN — 0 HARD violations, 1 documented SOFT false positive,
  all protected tokens present in the current tree.
- Wire surface: INTACT (all 4).
- Compat layer: UNCHANGED; identity tests pass; registry seams pass.
- gate_all.py: NOT READY, but PROVEN pre-existing (identical at pre-wave baseline).
- Measurement: MOVING down (-89/1,059 and falling); tier-1's specific 1,069/479 claim not
  reproducible.
- Caveat: the wave is still in flight; a clean reading of a given scope is a snapshot, not a
  final. Files not yet committed by all four agents could not be checked.
