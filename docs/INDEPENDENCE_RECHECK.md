# Independence Recheck — wave s7

**Tree:** `/tmp/qb-s7` (branch `s7`)
**HEAD:** `ff9983b53b9bb2d0a8a16be9c9e1a7c33323bf38` (short `ff9983b5`), working tree clean (0 dirty)
**Frozen target:** `cbe5e53e2826b949e4ab33dbd6d045e339fa162b`
**Run:** 2026-10-08T12:39Z, no service restarted or stopped.
**Scope:** re-verify independence on the post-r-wave tree and adversarially stress the r-wave's new ports.

---

## 1. Deletion harness — `./deletion_test.sh --rebuild`

Command: `cd /tmp/qb-s7 && export VIRTUAL_ENV=$PWD/.venv && export PATH=$PWD/.venv/bin:$PATH && ./deletion_test.sh --rebuild`

Raw per-check result (fresh sandbox built from this worktree):

| # | Check | Result |
|---|-------|--------|
| 1 | `env: real checkout NOT on sys.path` (probe-local `_LIVE`) | PASS |
| 2 | `env: no ~/.hermes in HOME` | PASS |
| 3 | `env: real checkout NOT on sys.path` (repo-prefix leak scan) | PASS |
| 4 | `imports: 64/64 first-level modules import clean` | PASS |
| 5 | `cli: python -m qubit_cli.main --help` exits 0 | PASS |
| 6 | `cli: python -m qubit_cli.main --version` exits 0 | PASS |
| 7 | `cli: subcommands > 70 (found 74)` | PASS |
| 8 | `cli: construct cli.QubitCLI()` | PASS |
| 9 | `import: tui_gateway.server` | PASS |
| 10 | `import: tui_gateway.entry` | PASS |
| 11 | `import: qubit_cli.gateway` | PASS |
| 12 | `import: plugins.plugin_loader` | PASS |
| 13 | `providers: list_authenticated_providers(...) returns providers` | PASS |
| 14 | `path: no hermes binary resolves on the stripped PATH` | PASS |
| 15 | `path: core home still resolves to Qubit's own with hermes off PATH` | PASS |
| 16 | `path: no ~/.hermes created with hermes off PATH` | PASS |
| 17 | `turn: real _chat_run_agent turn against local ollama` | PASS |

```
RESULT: 17 passed, 0 failed
probe exit=0
OVERALL: PASS — Qubit boots with hermes absent
```

Real-turn evidence: `model=qwen3-vl:2b api_calls=1 completed=True failed=False final_response='pong'`.
Module count rose 63 (stale `harness_run.txt`) → 64 (this tree). Subcommands 74.

PART B static scan (informational, classification unchanged): 2 `hermes_*` docstring hits
(`cronjob_job_args.py`, `skills_sync_client.py` — both COSMETIC string literals), 0 `.hermes`
creation, 1 `~/.hermes/logs` comment in `mcp_tool_transport.py` (COSMETIC). Home-resolution
probe under `QUBIT_HOME` unset: `get_qubit_home() = /tmp/qb-s7/home/.qubit` → OK. Literal-fallback
count 61 (was 63), all COSMETIC/COMPAT.

### Predicate verified BOTH ways (no harness bug)

The task asked to test the leak predicate `_is_live_checkout_path` bidirectionally after its repair.
Ran a dedicated probe (`_is_live_checkout_path` / `_is_checkout_root` from `deletion_probe.py`):

| Input | Expected | Got | Verdict |
|-------|----------|-----|---------|
| `/home/trick/Projects/Repos/qubit-voicebridge` (live main checkout) | FLAG | True | OK |
| `/tmp/qb-s7` (live worktree root) | FLAG | True | OK |
| `/tmp/qb-s7/qubit_runtime` | FLAG | True | OK |
| `/tmp/qb-s7/sandbox` (stripped then re-pinned) | True | True | OK |
| `/tmp/qb-s7/sandbox/qubit_runtime` | True | True | OK |
| `.venv/lib/python3.12/site-packages` | KEEP | False | OK |
| `.venv` root | KEEP | False | OK |
| `/usr/lib/python3.12` | KEEP | False | OK |
| `""` | KEEP | False | OK |

End-state assertions also pass: live main checkout **absent** from final `sys.path`; sandbox pinned
first two entries; shared `.venv` site-packages **still on path** (so `ruamel`/`rich`/`fire` import).
**The predicate is correct in both directions — no harness bug found.** The live checkout root
(injected by the `.venv` editable `.pth`) is still flagged; the venv is not.

### Cosmetic harness observations (not bugs, not fixed)

- The first check's **label is duplicated**: the check at `deletion_probe.py:107` and the second at
  `:126` share the name `"env: real checkout NOT on sys.path"`. Both run and both pass; only the
  printed label collides (this is the doubled line in the raw output).
- `deletion_probe.py:128` filters evidence text for the hardcoded string `"qb-n5"` and
  `deletion_test.sh:18` refers to `/tmp/qb-n5/...` — stale sandbox-name artifacts. The `:128` filter
  falls back to `"(sandbox paths)"` when `qb-n5` is absent (as here), so it degrades gracefully.
  Neither weakens a check.
- The committed `harness_run.txt` is **stale** (see §4).

No source edit was needed; the harness was not modified.

---

## 2. Adversarial half-port sweep (r-wave modules)

Method: sandbox imports (grade the ported tree, not a stale install), each public entry point called
with a plausible BAD input (None / wrong type / missing key). Classification:
- **SANE** — raises KeyError/ValueError/TypeError/AttributeError-with-real-message, or returns a
  documented tolerant value.
- **HALF-PORT** — NameError, or AttributeError naming a symbol absent from the module source.
- **PROBE-ARTIFACT** — the probe itself was malformed.

**Result: 31 probes across 16 modules, ALL SANE, ZERO HALF-PORT, ZERO PROBE-ARTIFACT.**

| Module | Probe (bad input) | Outcome |
|--------|-------------------|---------|
| `agent/initiate_setup_facts.py` | `setup_cards(block=None)` | SANE — TypeError: 'NoneType' not subscriptable |
| `agent/initiate_setup_facts.py` | `fork_card(cards='bad')` | SANE — AttributeError: 'str' has no 'get' |
| `agent/initiate_setup_prompt.py` | `build_initiate_setup_prompt(None, None, None)` | SANE — TypeError: 'NoneType' not iterable |
| `agent/initiate_setup_prompt.py` | `intro_resends(prompt=None, surface=123)` | SANE(returned) — `False` |
| `agent/turn_scripted_prelude.py` | `play_prelude(None, None, None)` | SANE(returned) — `('run', None)` |
| `agent/turn_scripted_prelude.py` | `run_scripted_prelude(None, None, object())` | SANE — AttributeError: 'NoneType' has no `_interrupt_requested` |
| `agent/first_task_prompt.py` | `first_task_tail(None, None)` | SANE — FileNotFoundError (missing SKILL.md) |
| `agent/first_task_prompt.py` | `visible_text(None)` | SANE — AttributeError: 'NoneType' has no 'partition' |
| `qubit_cli/free_tier_offer.py` | `_due_in(None)` / `_due_in({})` | SANE(returned) — `None` |
| `tools/setup_choose_tool.py` | `setup_choose_tool(123, [], 'x')` | SANE(returned) — JSON `{"error": "...only available in the Qubit desktop app."}` |
| `tools/setup_choose_tool.py` | `_result(reply='bad', options=5)` | SANE — AttributeError: 'str' has no 'get' |
| `tools/start_chat_tool.py` | `_start_chat(None)` | SANE(returned) — JSON `{"status": "rejected", ...}` |
| `tui_gateway/start_chat.py` | `start_chat(None)` / `start_chat({})` | SANE(returned) — JSON `{"status": "rejected", ...}` |
| `tui_gateway/methods_start_chat.py` | registered handler `("rid-1", None)` | SANE — AttributeError: 'NoneType' has no 'get' |
| `tui_gateway/methods_start_chat.py` | registered handler `("rid-1", {})` | SANE(returned) — JSON-RPC error `session not found` (code 4001) |
| `tui_gateway/methods_start_chat.py` | `_pending_tool_retry_notes(None)` | SANE — AttributeError: 'NoneType' has no 'get' |
| `qubit_cli/web_build_limits.py` | `web_build_limits(env='bad')` | SANE — ValueError: dictionary update sequence |
| `qubit_cli/web_build_limits.py` | `apply_web_build_limits(None)` | SANE — AttributeError: 'NoneType' has no 'update' |
| `qubit_cli/auth_external_process.py` | `resolve_external_process_command(None)` | SANE(returned) — `None` |
| `qubit_cli/auth_external_process.py` | `resolve_external_process_command(123)` | SANE — TypeError: argument of type 'int' not iterable |
| `qubit_cli/kanban_db_boards.py` | `read_board_metadata(board=123)` | SANE(returned) — dict (coerces slug) |
| `qubit_cli/kanban_db_boards.py` | `remove_board(slug=None)` | SANE — PermissionError (delegate_task child guard) |
| `tools/approval_yolo.py` | `toggle_session_yolo(session_key=None)` | SANE(returned) — `True` |
| `tools/approval_yolo.py` | `with_session_yolo(None, None)` | SANE(returned) — `None` |
| `qubit_state_tool_retries.py` | mixin `tool_row_retry('','')` / `take_unannounced('')` | SANE(returned) — `None` / `[]` (documented early-return) |
| `agent/turn_facade_lease.py` | `admit_durable_turn_lease(None, None, None)` | SANE — TypeError (arity: takes 1 positional arg) |
| `agent/turn_facade_lease.py` | `DurableTurnLease(None)` | SANE — TypeError: missing 3 required positional args |
| `agent/voice_turn_route.py` | `begin_voice_turn_route(None, None, None)` | SANE(returned) — `None` |
| `agent/voice_turn_route.py` | `end_voice_turn_route(object())` | SANE(returned) — `None` |

### The one NameError candidate — investigated and cleared as PROBE-ARTIFACT

Calling `tui_gateway.methods_start_chat._()` directly (module-global) raised
`NameError: name '_sess_nowait' is not defined`. This is **not** a half-port:

- The module's own docstring (`methods_start_chat.py:4-5`) states bodies are rebound onto
  `server.py`'s globals at install time via `method_ctx.bind_module`; they reference server globals
  **bare by design** (`_sess_nowait`, `_err`, `_ok`, `_session_db`, `logger`).
- `_sess_nowait` is defined in `tui_gateway/server.py:1210`; `_err`/`_ok`/`logger` likewise resolve
  only in `server.py`. Declared `skip=("_",)` at `register()`, so the module-level `_` is never the
  installed callable.
- Invoked through the real install-time binding — `server._methods["session.start_chat"]` after
  `register(server)` — the handler behaves correctly: `params=None` → SANE AttributeError,
  `params={}` → proper JSON-RPC `session not found` (code 4001). **No NameError.**

### Signature drift — `qubit_runtime/tools/check_signature_drift.py`

Command: `python qubit_runtime/tools/check_signature_drift.py qubit_runtime/agent qubit_runtime/qubit_cli qubit_runtime/tools qubit_runtime/tui_gateway`

```
# signature-drift check over 4 root(s): 28091 defs, 155812 unresolved calls
## REAL disagreements: none
## AMBIGUOUS (507) -- callee name maps to >1 shape; NOT flagged
```

Exit code 0. JSON summary: `{'real': 0, 'ambiguous': 507, 'defs': 28091, 'unresolved_calls': 155812}`.
No caller/callee signature disagreement in the swept roots. Ambiguous entries are same-name
definitions across scopes with differing shapes (`_emit`, `_dispatch`, `_target`, `_close`,
`_record`, `list_models`) — reported, deliberately not flagged.

---

## 3. Post-retirement state (read-only) — holds

| Claim | Evidence | Verdict |
|-------|----------|---------|
| `~/.hermes` absent | `ls -ld /home/trick/.hermes` → `No such file or directory` | ABSENT |
| `~/.hermes` not recreated | re-checked at t+5s during live service activity → still ABSENT | NOT RECREATED |
| four units enabled + active | `qubit-voicebridge` enabled/active; `qubit-gateway` enabled/active; `qubit-voice-orb` enabled/active; `qubit-voice-typing` enabled/active | ✔ |
| `hermes-dashboard` disabled + inactive | `enabled=disabled active=inactive` | ✔ |
| `get_qubit_home()` never under `~/.hermes` | `QUBIT_HOME=/tmp/qb-s7/qhome` → that path; unset + `HOME=/home/trick` → `/home/trick/.qubit`; unset + fake HOME → `<fake>/.qubit`; explicit `HERMES_HOME=/home/trick/.hermes` → still `/home/trick/.qubit` | ✔ (all 4 OK) |
| zero live processes naming `~/.hermes` | `ps aux \| grep -F '/home/trick/.hermes'` → NONE; no `/proc/*/cwd` under `.hermes` | ✔ |

Notes:
- The unit names are `qubit-voicebridge`, `qubit-gateway`, `qubit-voice-orb`, `qubit-voice-typing`
  (user scope). None of their unit files reference `hermes` or `~/.hermes`.
- `hermes-dashboard.service` (`/home/trick/.config/systemd/user/hermes-dashboard.service`) **still
  contains** `ExecStart=/home/trick/.local/bin/hermes dashboard ...` and
  `EnvironmentFile=/home/trick/.hermes/.env`. Because the unit is disabled+inactive it runs nothing
  and reads nothing; the absent `EnvironmentFile` is not created by systemd. Retired-but-present
  unit file, consistent with post-retirement.
- Live processes naming the string `hermes` (scratch cache paths `hermes_kernel_runner.py`,
  `hermes-snap-*.sh`, `HERMES_*` env exports, and `/tmp/qb-p4` pytest fixtures) do **not** name
  `~/.hermes` and are unrelated to the retired home dir.

---

## 4. Contradictions with prior claims

1. **`INDEPENDENCE_VERDICT.md` is stale vs. the repaired harness.** Lines 35 and 293 assert the
   deletion gate "must print `RESULT: 16 passed, 0 failed`". The current repaired harness on this
   tree prints **`RESULT: 17 passed, 0 failed`** (the extracted extra check + `first-level modules`
   now 64/64, was 63). The seven-word claim in the task brief ("17/17 PASS") matches reality;
   the verdict doc does not.
2. **`harness_run.txt` (committed) is stale.** It records `repo=/tmp/qb-e5`, `HEAD=7e4d180b`,
   `dirty=1`, `RESULT: 16 passed`, `imports: 63/63`, and evidence filtered for `qb-n5`. It is the
   output of the *pre-repair* harness on a different tree, salvaged as `3bd3b99e`. It does not
   describe this tree and should not be read as current evidence.
3. No other claim was contradicted. Independence, the four live units, the disabled dashboard, and
   the absent home dir all hold. Prior "0 half-ports (twice)" bar is maintained: **0 half-ports,
   third sweep.**

---

## Artifacts

- Harness raw output: `/tmp/qb-s7-harness-s7.txt`
- Half-port probe (scratch, not committed):
  `~/.local/state/qubit-voicebridge/runtime/cache/scratch/halfport_s7.py`
- Predicate bidirectional test (scratch, not committed):
  `~/.local/state/qubit-voicebridge/runtime/cache/scratch/pred_test.py`

No source edits beyond this document. No service restarted or stopped.
