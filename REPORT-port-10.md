# REPORT-port-10 — gateway blocker-unblocking chunk (6 files)

Upstream clone: `/home/trick/upstream/hermes-agent` (READ-ONLY)
Upstream ref: `origin/main` = **`53bec2a311979ad6f77580ba491eca261ab988e0`** (2026-10-16)
Worktree: `/tmp/port-10`, branch `port/chunk-10`
Method: `PORT-METHOD.md` (followed verbatim)
Mapping: upstream root == `qubit_runtime/`

## 1. Files ported

| # | upstream path | local path | upstream lines | status | imports-clean |
|---|---|---|---|---|---|
| 1 | `gateway/session_local_inventory.py` | `qubit_runtime/gateway/session_local_inventory.py` | 222 | `ported` | yes |
| 2 | `gateway/session_local_route.py` | `qubit_runtime/gateway/session_local_route.py` | 37 | `ported` | yes |
| 3 | `gateway/session_local_mcp.py` | `qubit_runtime/gateway/session_local_mcp.py` | 511 | `dep-missing` | no |
| 4 | `gateway/session_local_title.py` | `qubit_runtime/gateway/session_local_title.py` | 79 | `dep-missing` | no |
| 5 | `gateway/session_local_migration.py` | `qubit_runtime/gateway/session_local_migration.py` | 128 | `dep-missing` | no |
| 6 | `gateway/session_local_recovery.py` | `qubit_runtime/gateway/session_local_recovery.py` | 178 | `dep-missing` | no |

All 6 files are complete production modules — none is a facade/shim (§4). They are all ported
(no `shim-skipped`). Four cannot *import* until named top-level deps land; that is an ordering
fact, not a reason to withhold a complete module.

## 2. Dependencies-missing (named; every one is in the 299-list, so orderable)

Top-level imports that block import:

| mapped module | upstream source | 299-list line | blocks |
|---|---|---|---|
| `qubit_state_runtime` | `hermes_state_runtime.py` | 256 | mcp, title, migration, recovery |
| `gateway.session_contract` (`SessionRef`) | `gateway/session_contract.py` | 107 | migration, recovery |
| `qubit_state_keys` (`local_identity`) | `hermes_state_keys.py` | 242 | recovery |
| `qubit_state_local` (`POLICY_PREFIX`, `local_receipt`, `commit_local_session`) | `hermes_state_local.py` | 243 | recovery |

`hermes_state_runtime_workers.py` (299 line 257) is a likely companion of `qubit_state_runtime`.

Deferred (function-level, absent, non-blocking — will resolve at call time):
`gateway.session_authorities`, `gateway.session_policy`, `gateway.session_local`,
`gateway.session_authority`, `gateway.session_local_editor`, `hermes_state_local_lineage`,
`hermes_state_local_migration`, `hermes_state_mutation_binding`, `hermes_cli.active_sessions`,
`tui_gateway`.

Resolved, not missing: `hermes_state_sessions` maps to our existing `qubit_state_sessions.py`;
`hermes_cli.model_switch` maps to our existing `qubit_cli/model_switch.py`.

No genuine gaps (§5): every absent dep is an absent-299 file, none is inventable.

## 3. Brand handling

Applied `scripts/qubit_rebrand.py`'s **canonical token maps** (`_TOKEN_RE` + `_rewrite_embedded`
+ `_IDENT_RE` + `_PRIVATE_HERMES_RE` + `_BARE_HOME_RE`) to the 6 files only (never `--apply`, which
sweeps the whole tree).

Renamed module tokens: `hermes_cli` -> `qubit_cli`, `hermes_state` -> `qubit_state`,
`hermes_state_*` -> `qubit_state_*`.

- No `HERMES_HOME` / `~/.hermes` literal appears in any of the 6 files (checked).
- `HERMES_KANBAN_BOARD` (`session_local_inventory.py` docstring) left **verbatim** — it is a
  preserved wire/env id, canonical in our tree (`tools/kanban_tools_schemas.py:18`,
  `tools/code_execution_env.py:110`, kanban docs). Renaming it would have introduced a fourth
  inconsistency, so it is reported, not changed.
- Bare prose `hermes chat --resume`, `hermes -c`, `HermesCLI` in docstrings left verbatim
  (upstream-lineage prose, §3d — not decoration).
- `grep` for a colliding HERMES_ spelling of any newly written name: none introduced.

## 4. Proof

```
$ cd /tmp/port-10/qubit_runtime && for f in gateway/session_local_*.py; do python -m py_compile $f; done
OK  gateway/session_local_inventory.py
OK  gateway/session_local_mcp.py
OK  gateway/session_local_migration.py
OK  gateway/session_local_recovery.py
OK  gateway/session_local_route.py
OK  gateway/session_local_title.py

$ cd /tmp/port-10 && PYTHONPATH=/tmp/port-10/qubit_runtime python -c 'import gateway.X'
gateway.session_local_inventory  -> clean
gateway.session_local_route      -> clean
gateway.session_local_mcp        -> ModuleNotFoundError: No module named 'qubit_state_runtime'   (line 24)
gateway.session_local_title      -> ModuleNotFoundError: No module named 'qubit_state_runtime'   (line 9)
gateway.session_local_migration  -> ModuleNotFoundError: No module named 'gateway.session_contract' (line 15)
gateway.session_local_recovery   -> ModuleNotFoundError: No module named 'gateway.session_contract' (line 7)
```

The four failures are exactly the named top-level deps — no surprise gaps. Interpreter used:
`/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python`.

**Exercised, not just imported** (real execution):

```
resolve_launch_route({"model":"gpt-x"}, {})                -> {'model': 'gpt-x'}          # untouched
resolve_launch_route({"model":"foo","base_url":"http://x"}, {}) -> unchanged              # untouched
resolve_launch_route({"model":"  "}, {})                    -> {'model': '  '}             # untouched
resolve_launch_route({"model":"nonexistent-alias-zzz"}, {"model":{}}) -> {'model': 'nonexistent-alias-zzz'}
   # alias path resolves through the REAL qubit_cli.model_switch.resolve_startup_model_route
_project(None) / _project("") -> None ; _grouped([]) -> {}
```

## 5. AGENTS.md contract engagement (gateway/AGENTS.md)

- **Streaming-delivery contract / two message guards / profile-scope rule:** NONE of these 6 files
  touch adapter egress, inbound guards, or `_process_message_background`. They are the
  owner-side local-session plumbing (route resolution, title selection, legacy adoption, recovery,
  editor MCP, banner inventory).
- **Profile-scope rule — relevant and honored:** `session_local_recovery.py::local_source`
  intentionally carries a served secondary's profile so `build_session_key` namespaces local
  sessions (`agent:<name>:local:...`); `local_adapter_map` routes a secondary's local sessions to
  `runner._profile_adapters[name]`. No unbound default-profile read is introduced. Callers that
  need a profile home use the identity/registry, not `os.environ`.

## 6. Commit

`port(gateway): chunk-10 from upstream origin/main 53bec2a3` — see `git log` on `port/chunk-10`.
