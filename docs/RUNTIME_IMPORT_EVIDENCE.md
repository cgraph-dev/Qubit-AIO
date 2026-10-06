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
