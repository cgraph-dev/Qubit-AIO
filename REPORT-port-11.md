# REPORT-port-11.md — chunk 11 (`hermes_cli/gateway_chat*` + `gateway/session_*`)

Lane: `port/chunk-11` (worktree `/tmp/port-11`). Method: `PORT-METHOD.md`, verbatim.
Upstream ref: `origin/main` = **53bec2a311979ad6f77580ba491eca261ab988e0** (2026-10-16).
9 files, all blocker-unblocking (`gateway/` + the `hermes_cli/gateway_chat*` family).

## Outcome

| # | status | file |
|---|---|---|
| 1 | **ported** (import-clean) | `qubit_runtime/gateway/session_tool_events.py` |
| 2 | **ported** (import-clean) | `qubit_runtime/gateway/session_worker_construct.py` |
| 3 | **ported** (import-clean) | `qubit_runtime/qubit_cli/gateway_chat_inject.py` |
| 4 | **ported** (import-clean) | `qubit_runtime/qubit_cli/gateway_chat_listing.py` |
| 5 | **ported** (import-clean) | `qubit_runtime/qubit_cli/gateway_chat_startup.py` |
| 6 | **dep-missing** (top-level) | `qubit_runtime/gateway/session_policy.py` |
| 7 | **dep-missing** (top-level) | `qubit_runtime/gateway/session_policy_credentials.py` |
| 8 | **dep-missing** (top-level) | `qubit_runtime/qubit_cli/gateway_chat.py` |
| 9 | **dep-missing** (top-level) | `qubit_runtime/qubit_cli/gateway_chat_commands.py` |

**5 import-clean / 9. No file was stubbed.** The four `dep-missing` files are real
ported code; each is blocked solely by a **top-level** import of a module that is
itself absent from our tree and named in the 266-file list.

## Mapping (confirmed against our tree before writing)

`hermes_cli/` → `qubit_runtime/qubit_cli/` (module renamed). Confirmed: our tree has
`qubit_runtime/qubit_cli/` (4087 `qubit_cli` hits, zero `qubit_runtime/hermes_cli/`)
and `qubit_runtime/gateway/` exists. Upstream root == `qubit_runtime/`.

## Missing dependencies (all named, all in the 266-list)

Two absent modules are the *only* top-level blockers; nothing else in any of the nine
files is unresolvable.

- `hermes_state_runtime.py` (list **L256**) → ours `qubit_state_runtime.py` — absent.
  Top-level `from qubit_state_runtime import RuntimeStoreError` in
  `session_policy.py:9` and `session_policy_credentials.py:9`. **Blocks #6, #7.**
- `hermes_cli/gateway_client.py` (list **L193**) → ours `qubit_cli/gateway_client.py` —
  absent. Top-level `from qubit_cli.gateway_client import GatewayClientError[, connect_gateway]`
  in `gateway_chat.py:12` and `gateway_chat_commands.py:21`. **Blocks #8, #9.**

Deferred (inside-function) imports that are also absent, orderable, do not block import:
`gateway/session_authorities.py` (L94), `gateway/session_a2a.py` (L86),
`gateway/session_local.py` (L131), `gateway/session_local_editor.py` (L133),
`gateway/session_local_mcp.py` (L135), `gateway/session_local_route.py` (L138),
`gateway/session_commands.py` (L103), `hermes_state_local.py` (L243 → `qubit_state_local.py`),
`hermes_cli/gateway_chat_view.py` (L191 → `qubit_cli/gateway_chat_view.py`).

**Proof they are the only blockers** (§6, honest isolation): injecting a minimal fake
for exactly `qubit_state_runtime` (RuntimeStoreError) and `qubit_cli.gateway_client`
(GatewayClientError/GatewayRPCError/connect_gateway) makes **all four** dep-missing files
import cleanly. So every other import in them resolves today; there is no unnamed gap.

## Proven imports (§6)

```
cd /tmp/port-11/qubit_runtime
PYTHONPATH=/tmp/port-11/qubit_runtime <venv-python> -c 'import <dotted>'
```
- import OK (5): `gateway.session_tool_events`, `gateway.session_worker_construct`,
  `qubit_cli.gateway_chat_inject`, `qubit_cli.gateway_chat_listing`, `qubit_cli.gateway_chat_startup`.
- `py_compile` OK on all nine.
- **Exercised** (not just imported):
  - `session_tool_events`: `bounded_args` passthrough on small input; oversize input
    bounded below `MAX_TOOL_EVENT_CHARS`; `bounded_result` valid-JSON truncation with
    `truncated: True` on the error path.
  - `session_worker_construct`: `construct_kwargs` returns `{}` for a frame without
    `construct_v1`; accepts a valid closed-shape `construct_v1`; **refuses** a
    wrong-shape one with `invalid_managed_worker_bootstrap`.

## What these unblock

- `qubit_cli/gateway_chat_startup.py` imports `qubit_cli.gateway_chat` (deferred) → the
  `gateway_chat*` family is now internally consistent.
- `agent/managed_worker.py` (already ported) imports `gateway.session_tool_events` and
  `gateway.session_worker_construct` (deferred) — **now resolve** — and
  `gateway.session_policy` (remains blocked on `qubit_state_runtime`).
- `agent/runtime_session_store.py` (already ported) imports `qubit_cli.gateway_client`
  (deferred) — the module side lands; still blocked pending `gateway_client`.

**Ordering to finish chunk 11:** port `hermes_state_runtime.py` (unblocks #6/#7 and
`managed_worker`'s session_policy path) and `hermes_cli/gateway_client.py` (unblocks
#8/#9 and `runtime_session_store`).

## Brand decisions (the two-halves trap)

Applied mechanically (`scripts/qubit_rebrand.py` `MODULE_TOKENS`/`ENV_TOKENS`):
- module tokens: `hermes_cli`→`qubit_cli`, `hermes_constants`→`qubit_constants`,
  `hermes_state_runtime`→`qubit_state_runtime`, `hermes_state_local`→`qubit_state_local`.
- `HERMES_HOME` → `QUBIT_HOME` (the one canonical env rename).
- `get_hermes_home` → `get_qubit_home`: sibling convention (every other `gateway/` and
  `qubit_cli/` reader uses `get_qubit_home`; `qubit_constants` keeps an
  `get_hermes_home` alias for unconverted callers).
- User-facing CLI name: `` `hermes chat/--tui/logs/sessions list` `` → `` `qubit …` ``,
  matching `qubit_cli/main.py:1506`.

**Left verbatim (NOT brand) and reported as collisions:**
- `PARITY_DOC = "https://hermes-agent.nousresearch.com/..."` — a doc/wire URL; 22 other
  occurrences in-tree. Preserved.
- **Env-var wire contract left in the `HERMES_` spelling**: `HERMES_TUI_GATEWAY_URL`
  (`gateway_chat_startup.py`), and `HERMES_YOLO_MODE` / `HERMES_IGNORE_RULES` /
  `HERMES_SAFE_MODE` / `HERMES_IGNORE_USER_CONFIG` / `HERMES_ACCEPT_HOOKS`
  (`gateway_chat.py` `_ENV_LAUNCH_FLAGS`). **This is the ad2e66acc dual-read hazard:**
  `qubit_cli/config.py:133-134` documents the *canonical* names as `QUBIT_*` with the
  `HERMES_*` spellings kept on the writer denylist, but the **live writers/readers** in
  our production tree still use the `HERMES_` spelling — `qubit_cli/main.py:2956`
  writes `HERMES_YOLO_MODE`, `tools/approval.py:46` reads it (114 in-tree hits);
  `qubit_cli/web_server_chat.py:509` and `shared_session_attach.py:95` write
  `HERMES_TUI_GATEWAY_URL`. `gateway_chat.py` is the *reader* of the env a user sets, so
  renaming the reader alone would desync it from those writers. Kept the writers'
  spelling; **flagged for a coordinated rename**, not a one-sided one.

## Commits

- `90a4eb0a8` `port(cli+gateway): chunk-11 clean-import half …` (5 files)
- `015a97835` `wip(port-11): chunk-11 dep-missing half …` (4 files)
