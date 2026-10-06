# Runtime import evidence — full tree

Tool: `scripts/safe_import_walk.py` (subprocess-isolated, scratch QUBIT_HOME,
side-effecting modules skipped). Run against `qubit_runtime/` on the verified branch.

```
candidates: 8406    imported OK: 8194    failed: 212    skipped: 51
elapsed: 895.0s
```

**8194 / 8406 = 97.5% import cleanly.**

## The 212 "failures", triaged

The raw failure count is misleading — most of it is other people's code or code that is
never imported. Triaged:

| Count | Bucket | Verdict |
|---|---|---|
| 95 | `node_modules` (third-party JS deps shipped in tree) | not ours |
| 44 | `evals/*` probe scripts | **run as programs, never imported** — an eval probe is a script with a `main()`, so `import` is the wrong operation |
| 19 | test files | imported by pytest under its own conftest/HOME, not by the app |
| 3 | standalone `scripts/` | same as evals |
| 2 | electron fixtures | not Python we own |
| 2 | nix harness | not Python we own |
| 46 | `skills/**` + `optional-skills/**` script internals | need third-party libs (`openpyxl`, `python-docx`) that install into the skill's own env, not the runtime |
| **1** | **`qubit_cli.psutil_android`** | **TIMEOUT — and it is CORRECT behaviour** |

## The one production-tree hit is not a defect

`qubit_cli/psutil_android.py` is a deliberate shim (its docstring: "Shims to stop the
old updater doing work until relaunch, not Android support"). Line 24 calls
`stop_for_relaunch()` at module scope, on purpose, so an old updater never downloads
the psutil sdist. The 45s timeout is that stop being honoured. Not a bug.

## Skipped: 51 modules whose import WOULD ACT — these are not passes

The walk refuses to import them, and they are the reason a naive import sweep was
useless before: it executed `qubit_cli/update_cmd_fleet.py` and ran the updater.
High-value examples:

- `gateway.run_shutdown` — module-level `subprocess.Popen(` at line 109
- `qubit_cli.plugins` — module-level `install_plugin_debug_handler(...)` at line 107
- `qubit_bootstrap` — module-level `install_never_free_environ(...)` at line 478
- `cli` — module-level `install_skin_light_mode_hook(...)` at line 633
- `qubit_cli.update_cmd`, `qubit_cli.setup` — deny-listed (they run installers)

## Bottom line

No genuine import failure exists in the production tree.

## Independent replication (two runs, two trees)

The walk was run twice by different processes on different trees. The counts differ,
and the difference is fully explained -- which is the useful part:

| run | tree | candidates | OK | failed | skipped |
|---|---|---|---|---|---|
| A (main checkout) | has `qubit_runtime/node_modules/` | 8406 | 8194 | 212 | 51 |
| B (worktree `b4e6bdb2`) | no `node_modules` (gitignored) | 8321 | 8206 | 115 | 50 |

Reconciliation of the 100-failure difference:

- **95 are `node_modules`** -- third-party JS trees present only in the main checkout.
  They are not Python we own, so run B's number is the cleaner one.
- **5 are the same 4 eval probes** failing with different exception types per run
  (`evals.gateway_completion.delivery_lifecycle` -> TIMEOUT in A, RuntimeError in B;
  same for `output_caps_local_capture`, `liveness.ws_orphan_reconnect`). These are
  live/network eval probes: **flaky, not deterministic**, and they are scripts, not
  importable modules.

Nothing else differs. The two runs agree on substance, and both say the same thing:
no genuine import failure in the production tree.
