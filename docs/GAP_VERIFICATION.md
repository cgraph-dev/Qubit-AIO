# GAP VERIFICATION — is the 42-file gap real? (wave r8)

**Frozen revision:** `41a406a722c2332b61175ff6c5e1ba966bb8c9fc` (upstream `/home/trick/upstream/hermes-agent`).
Never `origin/main`.
**Measured at:** our tree @ `37b687f7` (worktree `/tmp/qb-r8`, branch `r8`).
**Method:** `scripts/audit_sync_completeness.py --rev 41a406a7… --show-symbols`, then a
rebrand-normalized, per-file comparison of every flagged file against upstream at the frozen rev.

## Headline

The audit at the frozen rev reports:

```
upstream .py files: 8355   exists: 8220   absent: 135   current: 8028
short_port  files/symbols: 75 / 198   (0 production files — all tests)
behind_head files/symbols: 117 / 277  (26 production files)
ABSENT: 135 total = 119 tests + 16 production
```

**The production gap is 16 absent + 26 behind-head = 42 files. It is REAL.**

- **40 of 42 production files are REAL work** (TRULY ABSENT or TRULY BEHIND).
- **2 of 42 are the refactor-split false pattern** — an upstream extraction whose body we
  already hold inline:
  - `hermes_cli/kanban_db_boards.py` → **REFACTOR-SPLIT**: upstream's own docstring says
    *"Split out of `hermes_cli.kanban_db`"*. All 8 of its top-level defs
    (`create_board`, `remove_board`, `list_boards`, `read/write_board_metadata`,
    `board_metadata_path`, `_dir_holds_board`, `_default_board_display_name`) are **defined
    in our `qubit_cli/kanban_db.py`** (4586 lines). 64.4% normalized-line overlap. **Not work.**
  - `agent/conversation_loop.py` → **REFACTOR-SPLIT**: upstream factored a 3-line call +
    fallback into `_codex_app_server_turn(agent, s)`; **our identical body is inlined at the
    call site** (`conversation_loop.py:1643`, 77% normalized overlap, calls the same
    `agent._run_codex_app_server_turn`). **Not work.**
- **2 more behind-head files are partial splits** (real symbols remain, one symbol each is an
  upstream extraction we inline): `hermes_cli/web_server_dashboard.py`
  (`_mount_one_plugin_api`, 80% inlined in our `_mount_plugin_api_routes`),
  `scripts/releases/entrypoint.py` (`_abandon_attempt`, 82% inlined). These stay real because
  each still has genuine missing symbols.

### Corrected REAL-work count

| scope | count |
|---|---|
| production files flagged (16 absent + 26 behind) | **42** |
| **REAL production files** | **40** |
| not-real (pure refactor-split) | 2 |
| REAL distinct symbols (absent top-defs + behind missing) | **≈204** (123 + 59 substantive + 22 trivial consts) |

The file-granularity number (40) is barely smaller than 42 — **this wave does NOT find the gap
materially inflated at file granularity.** Unlike `cli_commands_session_tools.py` (a ratchet
split), only two production files are false. **Scope the remaining waves against 40 files / ~204
symbols, not 42.**

The one place the number IS inflated is at *symbol* granularity inside single files: 3 behind
files carry exactly one trivial constant (`agent/usage_pricing.py::_OPUS_5_5`,
`hermes_cli/profiles.py::SETUP_PROFILE_MARKER`, `hermes_cli/update_lock.py::_WHOLE_SECOND_CT`)
and several carry 1–2 symbols. These are real but cheap; they should be batched, not waved.

## (I-a) The 16 ABSENT production files — classification

`TRULY ABSENT` = routed path missing AND no rebranded equivalent / inline body found.
`REFACTOR-SPLIT` = upstream split a file we hold inlined.

| # | upstream file | class | evidence |
|---|---|---|---|
| 1 | `agent/first_task_prompt.py` | TRULY ABSENT | `first_task_tail`, `MARKER` (`[/first-task]`), `visible_text` — marker string absent tree-wide |
| 2 | `agent/initiate_setup_facts.py` | TRULY ABSENT | 29 distinctive defs, 0 co-located; best tree overlap 2.9% |
| 3 | `agent/initiate_setup_prompt.py` | TRULY ABSENT | `build_initiate_setup_prompt`, `initiate_setup_prelude` — absent tree-wide |
| 4 | `agent/turn_scripted_prelude.py` | TRULY ABSENT | `Prelude`, `play_prelude`, `run_scripted_prelude` — absent tree-wide |
| 5 | `hermes_cli/auth_external_process.py` | TRULY ABSENT | `resolve_external_process_command` absent; only its Claude-fallback half inlined at `auth.py:2176` |
| 6 | `hermes_cli/free_tier_offer.py` | TRULY ABSENT | `claim_offer`, `offer_due_in`, `record_task_done` — absent tree-wide |
| 7 | `hermes_cli/kanban_db_boards.py` | **REFACTOR-SPLIT** | all 8 defs present in our `qubit_cli/kanban_db.py`, 64.4% overlap |
| 8 | `hermes_cli/web_build_limits.py` | TRULY ABSENT | `apply_web_build_limits`, `web_build_limits` — absent tree-wide |
| 9 | `hermes_state_tool_retries.py` | TRULY ABSENT | `SessionToolRetriesMixin` — absent tree-wide; concept 14% overlap only |
| 10 | `pm/_venv_entry.py` | TRULY ABSENT | sealed-payload venv entry; no `_venv_entry.py`/`venv_command` anywhere |
| 11 | `tools/approval_yolo.py` | TRULY ABSENT | `toggle_session_yolo`, `restore_session_yolo`, `with_session_yolo` — absent |
| 12 | `tools/setup_choose_tool.py` | TRULY ABSENT | 28 distinctive defs, best overlap 1.5% |
| 13 | `tools/start_chat_tool.py` | TRULY ABSENT | `START_CHAT_SCHEMA`, `_start_chat` — absent tree-wide |
| 14 | `tui_gateway/free_tier_task_done.py` | TRULY ABSENT | `note_task_done`, `is_clean_task` — absent tree-wide |
| 15 | `tui_gateway/methods_start_chat.py` | TRULY ABSENT | `_start_chat_retry_lock`, `_pending_tool_retry_notes` absent; 38.7% hit is tui_gateway module *skeleton*, not code |
| 16 | `tui_gateway/start_chat.py` | TRULY ABSENT | `start_chat`, `_PICK_LINES`, `_first_steps` — absent tree-wide |

**Absent verdict: 15 TRULY ABSENT + 1 REFACTOR-SPLIT.**

## (I-b) The 26 BEHIND-HEAD production files — classification

These files EXIST in our tree but trail the frozen upstream rev. **Zero of the missing symbols
are rebrand twins or tree-wide moves** — the audit's twin subtraction already ran, so what
surfaces is genuinely absent. `real` counts missing symbols whose upstream body is NOT inlined
in our file.

| # | upstream file | missing | real | not-real (inlined) |
|---|---|---|---|---|
| 1 | `agent/conversation_loop.py` | 1 | 0 | `_codex_app_server_turn` → REFACTOR-SPLIT |
| 2 | `agent/nous_rate_guard.py` | 1 | 1 | — |
| 3 | `agent/onboarding.py` | 3 | 3 | — |
| 4 | `agent/prompt_builder.py` | 1 | 1 | — |
| 5 | `agent/turn_facade_lease.py` | 3 | 3 | — |
| 6 | `agent/usage_pricing.py` | 1 | 1 | (1 const) |
| 7 | `agent/voice_turn_route.py` | 2 | 2 | — |
| 8 | `hermes_cli/kanban_db_connect.py` | 1 | 1 | — |
| 9 | `hermes_cli/mcp_catalog.py` | 1 | 1 | — |
| 10 | `hermes_cli/plugins_cmd.py` | 3 | 3 | — |
| 11 | `hermes_cli/plugins_cmd_remove.py` | 4 | 4 | — |
| 12 | `hermes_cli/profiles.py` | 1 | 1 | (1 const) |
| 13 | `hermes_cli/setup_profile.py` | 30 | 30 | ← largest single gap (ours 100 lines vs upstream 325) |
| 14 | `hermes_cli/update_lock.py` | 1 | 1 | (1 const) |
| 15 | `hermes_cli/web_server_dashboard.py` | 7 | 6 | `_mount_one_plugin_api` (80% inlined) |
| 16 | `plugins/kanban/dashboard/plugin_api.py` | 1 | 1 | — |
| 17 | `pm/environments.py` | 2 | 2 | — |
| 18 | `scripts/releases/entrypoint.py` | 2 | 1 | `_abandon_attempt` (82% inlined) |
| 19 | `tools/browser_tool_session.py` | 1 | 1 | — |
| 20 | `tui_gateway/agent_callbacks.py` | 1 | 1 | — |
| 21 | `tui_gateway/contracts/config_free_tier_control.py` | 1 | 1 | — |
| 22 | `tui_gateway/contracts/profiles_vault_complete_foreign_subagents.py` | 4 | 4 | — |
| 23 | `tui_gateway/contracts/server_requests.py` | 4 | 4 | — |
| 24 | `tui_gateway/contracts/sessions.py` | 4 | 4 | — |
| 25 | `tui_gateway/methods_onboarding.py` | 3 | 3 | — |
| 26 | `tui_gateway/methods_session_interrupt.py` | 1 | 1 | — |

**Behind verdict: 25 TRULY BEHIND + 1 REFACTOR-SPLIT (pure).**

## Note on the audit's deliberate no-fallback rule

The prior wave added an explicit comment refusing a name-anywhere fallback so a file at the
wrong path still counts ABSENT. That is correct and is preserved: this verification classified
files by *content equivalence* (rebrand-normalized, co-location and inline-body tests), NOT by
relaxing the audit. The audit's absent set is unchanged; this doc only judges whether each
absence is real.

## (II) The Python 3.14 spurious warning — root cause

Warning text (reproduced exactly):

```
qubit: dependency environment /home/trick/.qubit/installs/997646afeeee2375/environments/
06a3fca62ad248db8a4428a9a085c498/venv targets Python 3.14 but this process runs 3.12;
keeping this interpreter's own packages (run `qubit pm repair` to rebuild the environment)
```

### Emitter

`qubit_runtime/pm/environments.py` — `activate_dependencies()`, **line 496** (guard at 458–460,
the `print(...)` at 496–500, `_ABI_MISMATCH_WARNED` a module-level once-guard at 489–495):

```python
env_version = venv_python_version(environment)                      # 458
running_version = (sys.version_info.major, sys.version_info.minor)  # 459
if env_version is not None and (env_version[0], env_version[1]) != running_version:  # 460
    ...
    print(f"qubit: dependency environment {environment} targets "
          f"Python {env_version[0]}.{env_version[1]} but this process runs "
          f"{running_version[0]}.{running_version[1]}; keeping this "
          f"interpreter's own packages (run `qubit pm repair` to rebuild "
          f"the environment)", file=sys.stderr)                        # 496-500
```

`venv_python_version()` (line 341) reads `version` from the environment's `pyvenv.cfg`
(`version_info = 3.14.7`), falling back to the `lib/python3*` dir name.

### Why it fires on a healthy 3.12 environment

1. `qubit_bootstrap.py:483` sets `_root = Path(__file__).resolve().parent` — i.e.
   **`.../qubit-voicebridge/qubit_runtime`** (not the checkout root). At boot it calls
   `activate_dependencies(_root)` (`qubit_bootstrap.py:613`).
2. `install_key(_root)` hashes that path → **`997646afeeee2375`**. That install's
   `facts.json` records a committed environment built with **CPython 3.14.7**
   (`pyvenv.cfg: version_info = 3.14.7`, store python
   `~/.qubit/tools/python-3.14.7+…`). Confirmed: `committed_venv(_root)` resolves and
   `venv_python_version() == (3, 14)`.
3. The launcher (`.venv/bin/python`, **3.12.3**) is a developer checkout with **no
   `install-stamp.json`** → `read_install_stamp()` returns `{}` → `prepare_launch()`
   returns `None` (`qubit_cli/venv_sync.py`: `stamp.get("updateMechanism") != "self"` →
   "Developer checkouts retain their owner") → **no relaunch into the store Python.**
   `pm.venv_is_current(_root)` is `False`.
4. So a 3.12 process reaches `activate_dependencies()` against a committed **3.14** env.
   Versions differ → the branch at line 460 fires, drops the foreign site-packages, and
   prints the warning once per process.

### Is it a bug?

**No — it is a correct, fail-closed warning given the state; the "spurious" quality comes from
a real state/harness mismatch, not a detection bug.**

- The version *detection* is correct: `venv_python_version()` reads the true committed env
  (3.14.7) and `sys.version_info` (3.12.3); the inequality is real.
- The *action* is correct and necessary: activating a 3.14 environment's site-packages under a
  3.12 interpreter breaks C-extension ABIs (the code comment cites the observed
  `No module named 'pydantic_core._pydantic_core'`). Dropping the foreign entries is the right
  fail-closed move.
- The *noise* is because the committed environment was built by a **3.14 store Python** while
  the running CLI is the **3.12 developer venv** that never relaunches. On a properly-launched
  install the store Python (3.14) is the interpreter that activates its own 3.14 environment and
  the versions match — no warning. The dev-checkout-on-.venv path is the divergence.

**Verdict: genuine environment condition, honest warning — NOT a suppression-worthy bug.** It is
disclosing that this checkout's `.venv` (3.12) and the committed PM environment (3.14.7) are
mismatched. The correct remedies are either (a) `qubit pm repair` to commit a 3.12-generation for
this install, or (b) run through the store launcher. Do **not** suppress it — hiding it would mask
a real ABI-safety decision on a real interpreter mismatch.

## Reproduce

```sh
# audit (≈2–4 min)
.venv/bin/python scripts/audit_sync_completeness.py \
    --rev 41a406a722c2332b61175ff6c5e1ba966bb8c9fc --show-symbols --json out.json

# warning (drop the relaunch, hit the committed 3.14 env from the 3.12 venv)
env -u QUBIT_HOME .venv/bin/python -c \
 'import sys; sys.path.insert(0,"'"$PWD"'/qubit_runtime");
  from pathlib import Path; from pm import environments as E;
  E.activate_dependencies(Path("'"$PWD"'/qubit_runtime"))'
```

## Acceptance

`scripts/gate_all.py` → **PASS** (import gate 29 OK/0 FAIL, entry-point HARD prod=0, lazy
imports 0 unguarded, capability audit informational). Verdict: all blocking checks green.
