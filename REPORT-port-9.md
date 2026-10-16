# REPORT-port-9 — gateway chunk-9 (6 files)

Lane: `port/chunk-9`, worktree `/tmp/port-9`.
Upstream: `/home/trick/upstream/hermes-agent` @ `origin/main`
`53bec2a311979ad6f77580ba491eca261ab988e0` (2026-10-16 00:25:49 -0700).
Method: `PORT-METHOD.md` (verbatim). Mapping: upstream root == `qubit_runtime/`.

## Verdict

All 6 files ported with correct branding and syntax. **None is import-proved**:
every file carries a *top-level* import of a module that is absent from our tree
and itself in the 299-file delta. Status per `PORT-METHOD.md` §8:
`dep-missing`, dependencies named below, `imports_clean: false` (honest — the
module cannot import until those deps land).

- `py_compile` passes for all six (proof of portable syntax).
- Import **resolves cleanly** for all six against a scratch overlay that supplies
  only the named dependencies (proof that the ported files' own import names are
  correct: no `hermes_*` stragglers, no typos). The overlay is a *test fixture*,
  never committed — the deliverable tree gets no stubs.

## Files

| upstream path | our path | upstream lines | status |
|---|---|---|---|
| `gateway/session_hosted_transport.py` | `qubit_runtime/gateway/session_hosted_transport.py` | 367 | dep-missing |
| `gateway/session_ingress_media.py` | `qubit_runtime/gateway/session_ingress_media.py` | 533 | dep-missing |
| `gateway/session_kanban.py` | `qubit_runtime/gateway/session_kanban.py` | 349 | dep-missing |
| `gateway/session_local.py` | `qubit_runtime/gateway/session_local.py` | 349 | dep-missing |
| `gateway/session_local_close.py` | `qubit_runtime/gateway/session_local_close.py` | 26 | dep-missing |
| `gateway/session_local_editor.py` | `qubit_runtime/gateway/session_local_editor.py` | 62 | dep-missing |

## Brand applied (all six)

Mechanical, from `scripts/qubit_rebrand.py` maps. Zero leftover lowercase
`hermes` tokens in any ported file:

- `hermes_state_runtime` → `qubit_state_runtime`
- `hermes_state_local` → `qubit_state_local`
- `hermes_cli` → `qubit_cli`
- `hermes_constants` → `qubit_constants`
- `hermes_home_key` → `qubit_home_key`
- `get_hermes_home` → `get_qubit_home`

**Deliberately NOT renamed (provenance / wire ids):**

- `HERMES_KANBAN_TASK|BOARD|DB|RUN_ID|CLAIM_LOCK|WORKSPACE|WORKSPACES_ROOT|BRANCH|GOAL_MODE|CLAIM_TTL_SECONDS`,
  `HERMES_PROFILE`, `HERMES_TENANT`, `HERMES_SESSION_SOURCE` (`session_kanban.py`
  `bind_worker_context`). These are the dispatcher's env/wire ids: they are read
  verbatim by in-tree `tools/kanban_tools.py` and whitelisted in
  `tools/code_execution_env.py:37`. Renaming only this writer would break the
  reader — the two-halves trap. **Collision reported, not reconciled**: the
  `HERMES_*` spelling is the tree-wide convention for these ids.
- Prose token `HermesCLI` (`session_local.py:321` docstring). `cli.py:1808`
  keeps the alias `HermesCLI = QubitCLI` and sibling modules (`qubit_cli/*`,
  `evals/*`) reference it as prose, so this matches the surrounding convention.

## Missing dependencies (all in the 299-file list; named, not invented)

Per `PORT-METHOD.md` §5. Every entry below is present in `/tmp/prod-files.txt`
and belongs to another chunk (chunk id in parentheses):

- `qubit_state_runtime.py` ← `hermes_state_runtime.py` (chunk 8) — top-level in
  **all six** files.
- `qubit_state_local.py` ← `hermes_state_local.py` (chunk 7) — top-level in
  `session_local_close`, `session_local`, `session_kanban`.
- `gateway/session_contract.py` (chunk 18) — top-level in `session_local`,
  `session_hosted_transport`.
- `gateway/session_authorities.py` (chunk 2) — top-level in
  `session_hosted_transport`; deferred in `session_local`.
- `gateway/session_hosted_rpc.py` (chunk 2) — top-level in
  `session_hosted_transport`.
- `qubit_cli/gateway_runtime.py` + `qubit_cli/gateway_runtime_discovery.py`
  (chunk 6) — deferred (inside `owner_request`).
- `gateway/hosted_room_attachments.py` (chunk 0) — deferred.
- `gateway/session_hosted_attachments.py` (chunk 2) — deferred.
- `gateway/session_policy.py` (chunk 5) — deferred in `session_local`/`kanban`.
- `gateway/session_local_recovery.py` (chunk 4) — deferred.
- `gateway/session_local_route.py` (chunk 4) — deferred.
- `gateway/session_local_mcp.py` (chunk 4) — deferred in `session_local_editor`.
- `gateway/session_runtime_workers.py` (chunk 21) — deferred.
- `gateway/session_managed_worker.py`, `gateway/session_surface.py` (chunk 20/21) — deferred.
- `qubit_cli/turn_exit.py` (chunk 26) — deferred.
- `gateway/session_api_media.py` (chunk 15) — deferred.
- `gateway/session_a2a.py`, `gateway/session_display.py` (chunk 15/18) — deferred.

No dependency outside the 299-list and not on disk (no genuine gap to escalate).

## Why these are "blocker-unblocking"

They are the *targets* other chunks import: chunk 4 (`session_local_*`),
chunk 5 (`session_policy`), chunk 2 (`session_hosted_*`) and the run/runtime
chunks all reference these names. Landing them here clears the name-existence
blocker; their own import-proof waits on chunks 2/5/7/8 (the state + contract
modules), which are the true head of the order.

## Ordering

Import-proof for this chunk requires, at minimum, chunks **8**
(`hermes_state_runtime.py`) and **7** (`hermes_state_local.py`), plus chunks
**2** and **18** (`session_authorities`/`session_hosted_rpc`,
`session_contract`) for `session_hosted_transport`/`session_local`.

## gateway/AGENTS.md engagement

- **Streaming-delivery contract**: not engaged — none of these files touch
  `draft_stream_is_message`, `finish()`, `_interim_send` or an egress door.
  `session_local.py`'s `LocalSessionAdapter` deliberately publishes nothing per
  tick ("TurnRunner already publishes model/tool deltas and settlement exactly
  once") and returns synthetic `SendResult`s — it is not a stream-is-the-message
  adapter.
- **Two message guards**: not engaged (no `_pending_messages` / busy-command
  interception here).
- **Profile-scope rule**: engaged in spirit — `session_hosted_transport.owner_request`
  refuses a non-resolved home and re-checks `qubit_home_key` against the
  endpoint's profile; `install_hosted_transport.select` fails closed on a
  mismatched or unresolved profile; `session_ingress_media.collect_unheld_api_images`
  refuses to collect when the db's home is not the active profile's store
  (`get_qubit_home()`). No unbound read leaks the default profile.

## Proof commands

```sh
PY=/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python
cd /tmp/port-9
for f in session_hosted_transport session_ingress_media session_kanban \
         session_local session_local_close session_local_editor; do
  $PY -m py_compile qubit_runtime/gateway/$f.py && echo "compile OK $f"
done

# real import (expected failure: named dep absent)
PYTHONPATH=/tmp/port-9/qubit_runtime $PY -c 'import gateway.session_kanban'
#  ModuleNotFoundError: No module named 'qubit_state_runtime'
```
