# Sync Entry-Point Resolution Audit

**Tool:** `scripts/check_entry_point_resolution.py`
**Scope:** the two vendored Qubit runtime roots — `qubit_runtime/` (the vendored
upstream fork; imported flat, because `qubit_runtime` itself is on `sys.path`)
and `voicebridge/` (the outer wrapper; imported as `voicebridge.<x>`).
**Method:** static AST resolution. The tool never imports a target module, so no
side effects (no gateway start, no state DB, no network). It indexes every
module in the tree by name, computes each module's public surface statically,
and reports every `from X import Y` / `X.Y` read whose `Y` is not part of `X`'s
surface.

## How to reproduce

```bash
cd /home/trick/Projects/Repos/qubit-voicebridge
.venv/bin/python scripts/check_entry_point_resolution.py            # human report
.venv/bin/python scripts/check_entry_point_resolution.py --json /tmp/ep_audit.json
.venv/bin/python scripts/check_entry_point_resolution.py --include-tests   # adds tests/ bucket
```

**Runtime:** 65–69 s for the production sweep (8637 modules scanned) on the
reference host, well inside the 2-minute CI budget. `--include-tests` runs in
~100 s. Results are deterministic across runs. Exit code is `1` when any HARD
finding is present, `0` otherwise.

## Result summary (production, tests excluded)

| Bucket | Count | Meaning |
|---|---|---|
| **HARD** | **4** | Real in-tree symbol miss; raises at runtime on the affected path. |
| OPTIONAL | 5 | Import guarded by `try/except`, or an except-handler fallback, or TYPE_CHECKING-only. Handled by design. |
| UNVERIFIED | 682 | Module/name not resolvable and not provably in-tree (third-party, or the repo-root `scripts/`). Reported for completeness; **not** failures. |

Tests (`--include-tests`): 85 HARD, reported separately and **not** in scope
here (test surface is allowed to reference fixtures/monkeypatch targets).

---

## HARD findings — real dropped symbols (4)

All four are the exact incident shape: a caller references a name that the target
module does not define, with no dynamic provision and no `try/except` guard. None
of these files is uncommitted (checked `git status`), so they are in the
committed tree.

### 1 & 2. `qubit_cli.gui_uninstall.desktop_install_record` — 2 call sites

- **Callers:** `qubit_runtime/qubit_cli/main_desktop.py:1135` and `:1188`
  (`from qubit_cli.gui_uninstall import desktop_install_record`, function-local,
  **not** wrapped in try/except).
- **Target:** `qubit_runtime/qubit_cli/gui_uninstall.py` defines 11 top-level
  names (`desktop_userdata_dir`, `source_built_gui_artifacts`,
  `packaged_gui_app_paths`, `gui_install_summary`, `uninstall_gui`, …). It does
  **not** define `desktop_install_record`, has no `__getattr__`, no `__all__`,
  no dynamic injection.
- **Tree-wide grep:** `desktop_install_record` is defined nowhere.
- **Corroborating evidence it is expected to exist:** the tests
  `tests/hermes_cli/test_gui_uninstall_retention.py:46` and
  `tests/hermes_cli/test_desktop_install_after_update.py:205,214` do
  `monkeypatch.setattr(gui_uninstall, "desktop_install_record", …)` and call
  `gui_uninstall.desktop_install_record()` — i.e. the tests assume the symbol
  exists on the module.
- **Verdict: REAL** — an `ImportError` will fire on the `qubit desktop` install
  path (`_install_rebuilt_desktop_app` / `_refresh_installed_desktop_apps`).

### 3. `qubit_cli.plugins_cmd_install.shared_dependency_answers`

- **Caller:** `qubit_runtime/qubit_cli/memory_provider_migration.py:191`
  (`from qubit_cli.plugins_cmd_install import shared_dependency_answers`,
  function-local, not guarded; used at `:215` as a context manager).
- **Target:** `qubit_runtime/qubit_cli/plugins_cmd_install.py` defines
  `_install_plugin_python_deps`, `_consent_python_deps`, `_install_plugin_core`,
  `cmd_install`, `dashboard_install_plugin`, … but **not**
  `shared_dependency_answers`.
- **Tree-wide grep:** 0 definitions.
- **Verdict: REAL.**

### 4. `qubit_cli.plugins_cmd_install.recorded_install`

- **Caller:** `qubit_runtime/qubit_cli/plugin_packs.py:363`
  (`from qubit_cli.plugins_cmd_install import recorded_install`, function-local,
  not guarded; used at `:378`).
- **Target:** same module as above; does not define `recorded_install`.
- **Tree-wide grep:** 0 definitions.
- **Verdict: REAL.**

---

## OPTIONAL findings — real drift, but guarded (5)

These are **genuine name mismatches** too (worth an owner look), but each caller
wraps the import so the failure is caught and the feature degrades instead of
crashing. The tool marks them `optional` and keeps them out of the HARD count.

| File:line | Target.symbol | Why OPTIONAL | Assessment |
|---|---|---|---|
| `qubit_cli/process_identity.py:48` | `qubit_constants.PROJECT_ROOT` | inside `try/except Exception` (falls back to `Path(__file__).parents[1]`) | `qubit_constants.py` has no `PROJECT_ROOT`. Real drift; masked by fallback. |
| `qubit_cli/web_server.py:1029` | `plugins.dashboard_auth.qubit` | inside `try/except Exception: pass` | `dashboard_auth/` has no `qubit` submodule (only `basic`, `drain`, `nous`, `self_hosted`) and no `__init__.py` re-export. Real drift; masked. |
| `tui_gateway/server.py:3369` | `tools.skill_usage._read_bundled_manifest_names` | inside `try/except Exception` (returns 0/"local") | **Likely a rename miss**: `skill_usage.py` defines `_read_bundled_names`, not `_read_bundled_manifest_names`. Real drift; masked. |
| `voicebridge/skill_catalog.py:300` | `tools.skill_usage._read_bundled_manifest_names` | inside `try/except Exception` | Same rename issue as above. |
| `evals/provider_fallback/probe_104360.py:108` | `agent.chat_completion_helpers._swap_fallback_clients` | is the `except ImportError:` **fallback** of a `try` importing it from `agent.client_lifecycle` | `chat_completion_helpers.py` does not define it; the fallback target is itself missing, so if the primary import ever fails this probe breaks. Real drift; only reachable when the primary fails. |

---

## UNVERIFIED bucket (682) — not failures

The tool deliberately does **not** assert a failure when a name resolves to a
module it cannot prove is in-tree. Two situations land here:

1. **Third-party packages** (the bulk). Top roots by count:
   `prompt_toolkit` 114, `acp` 68, `telegram` 48, `openpyxl` 40, `mcp` 38,
   `mautrix` 33, `PySide6` 30, `darwinian_evolver` 22, `reportlab` 19,
   `cryptography` 19, `google` 18, `winrt` 18, `pypdf` 17, `pptx` 16, `docx` 16,
   … These are optional dependencies imported lazily under try/except; the venv
   does not have most of them, which is exactly why they are guarded. Correctly
   reported as UNVERIFIED rather than as missing symbols.
2. **The repo-root `scripts/` package** (`from scripts.web_search import …`,
   `from scripts.browser_auto import …` in `voicebridge/chat_transport.py` and
   `voicebridge/communications/monitor.py`). `scripts/` sits at the repo root,
   outside both vendored roots, and is outside this audit's scope.

If a genuinely in-tree module were renamed away, it would surface as a HARD
`unresolved-module` finding (top segment is a known in-tree namespace), not be
hidden in this bucket.

---

## False-positive sources handled (and how)

Each of these produced real noise in earlier iterations of this tool and is now
handled explicitly. The counts dropped 4436 → 952 → 245 → 63 → **4** as each was
fixed.

| Source | Handling |
|---|---|
| **(a)** `from __future__ import …` | Skipped entirely. |
| **(b)** `from X import *` | Never reported; a module *performing* a star import is marked **dynamic** (surface unknowable). |
| **(c)** lazy imports inside functions | **Still checked** (they are real call sites), flagged `lazy`. |
| **(d)** `try/except ImportError` and broader `except Exception`/`BaseException`/bare `except` | Reported but flagged `optional`; also covers imports placed in the `except` fallback branch. |
| **(e)** re-exports via `__all__` / compat shims | Names in `__all__` and any name bound by an `import` count as present. |
| **(f)** `TYPE_CHECKING`-only imports | Reported with flag `typechecking`, demoted to OPTIONAL. |
| **(g)** string annotations | Never resolved (they are string literals); only real `Name`/`Attribute` nodes are checked. |
| Scope shadowing | A parameter/local shadowing a module alias (`def body(server)`, `def f(config: dict)`) is respected — this alone removed ~40% of the noise. |
| Object vs module attribute | `sym.attr` after `from M import sym` is an **object** read and is never checked; only true module bindings (`import X`, `import X.Y as Z`, `from P import submod`) are. |
| Dynamic module surfaces | PEP 562 `__getattr__`, `_OriginProxy`-style proxies, `globals().update(...)`, `registry.export(globals())`, `setattr(sys.modules[__name__], …)`, and `…register(sys.modules[__name__])` (the `tui_gateway/server.py` pattern that rebinds ~40 split handler modules) all mark a module dynamic → nothing reported against it. |
| Conditional module-level bindings | Names assigned inside top-level `if`/`try`/`with` (e.g. `_HAS_NUMPY` in a try/except) and `global`-declared names count as part of the surface. |
| Bare sibling imports in standalone scripts | A single-segment name resolves against the importing file's own directory **only** when it is not a real tree module (`skills/`+`optional-skills/` scripts import siblings that way); `voicebridge/` never supplies bare aliases. |
| Same-named files across the tree | Resolution order is: global tree index → `qubit_runtime` root-scan → sibling fallback. `voicebridge` files are only reachable via their `voicebridge.` prefix. |

### Sanity control

The tool was validated against the two historical incidents it is meant to
catch. Both target symbols are present in the tree *now* (the fixes landed), and
the tool's surface collector confirms `tui_gateway.turn_marker.marker_writer_state`
and `qubit_cli.plugins.publish_gateway_message_host` /
`clear_published_gateway_message_host` / `_attach_published_gateway_host` exist —
so the corresponding call sites resolve clean. Had they been absent, the call
sites would have been reported HARD. A negative control
(`marker_writer_state` swapped for a non-existent name) is reported as missing,
confirming the check is live.

---

## Verdict

- **4 real dropped-symbol defects** (HARD): `desktop_install_record` (×2 call
  sites), `shared_dependency_answers`, `recorded_install`. These are the same
  class as the two production incidents and should be fixed by the parallel fix
  agents.
- **5 real-but-guarded name drifts** (OPTIONAL): `PROJECT_ROOT`,
  `dashboard_auth.qubit`, `_read_bundled_manifest_names` (×2 call sites),
  `chat_completion_helpers._swap_fallback_clients`. Non-fatal today, but at least
  two look like renames that should be reconciled.
- **682 UNVERIFIED**: third-party and repo-root-`scripts` imports — expected, not
  failures.

No finding in this report required guessing: every HARD/OPTIONAL verdict is
backed by a tree-wide grep proving the name is undefined, plus a check that the
call site is not guarded. Nothing is left as an unclassified guess.
