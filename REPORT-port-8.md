# REPORT — port chunk-8 (gateway session-*, blocker-unblocking)

Worktree `/tmp/port-8`, branch `port/chunk-8`.
Upstream `/home/trick/upstream/hermes-agent` read at **`origin/main` = `53bec2a311979ad6f77580ba491eca261ab988e0`** (2026-10-16, same HEAD as the method's worked example).
Method: `PORT-METHOD.md` verbatim. Commit: **`20d5787a3`**.

## 1. Result

**7 / 7 files ported, 7 / 7 import-proven (with deps stubbed — see §4), 0 shims, 0 withheld.**

| upstream | ours | ulines | status |
|---|---|---|---|
| `gateway/session_authorities.py` | `qubit_runtime/gateway/session_authorities.py` | 188 | ported |
| `gateway/session_cron.py` | `qubit_runtime/gateway/session_cron.py` | 256 | ported |
| `gateway/session_finite.py` | `qubit_runtime/gateway/session_finite.py` | 80 | ported |
| `gateway/session_hosted_attachments.py` | `qubit_runtime/gateway/session_hosted_attachments.py` | 205 | ported |
| `gateway/session_hosted_controls.py` | `qubit_runtime/gateway/session_hosted_controls.py` | 81 | ported |
| `gateway/session_hosted_rpc.py` | `qubit_runtime/gateway/session_hosted_rpc.py` | 333 | ported |
| `gateway/session_hosted_service.py` | `qubit_runtime/gateway/session_hosted_service.py` | 291 | ported |

All seven are **real modules** (80–333 lines of logic), not the facade/re-export stubs §4 warns about. Mapping verified: `line counts identical to upstream` — the port is a token substitution, no structural change.

`py_compile`: **7/7 OK**. `grep -i hermes`: **0 residual tokens** in all seven.

## 2. Brand applied (mechanical, `scripts/qubit_rebrand.py`)

Module tokens (`MODULE_TOKENS`, longest-first):
`hermes_state_runtime`→`qubit_state_runtime` · `hermes_state_keys`→`qubit_state_keys` · `hermes_state_local`→`qubit_state_local` · `hermes_constants`→`qubit_constants`.

Identifier tokens (`IDENTIFIER_TOKENS`): `get_hermes_home`→`get_qubit_home` · `get_hermes_home_override`→`get_qubit_home_override` · `hermes_home_key`→**`qubit_home_key`** (it is an *identifier*, not a module — §3d's `hermes_state_keys` note applies to the module, which I renamed per §3a).

Env: `HERMES_HOME`→`QUBIT_HOME`.

Prose the sweep would also touch: `` `hermes -p` ``/`` `hermes -z` ``→`` `qubit -p` ``/`` `qubit -z` ``; `HERMES_YOLO_MODE`→`QUBIT_YOLO_MODE`; temp dir prefix `hermes-room-input-`→`qubit-room-input-`.

**Kept verbatim (protocol ids, not brand):** `'bot_room'`, `'hosted:'`, `'cron'`, `'gateway.hosted.owner.v1:'`, capability strings `'session:create|submit|read|control|approve'`, error codes (`'profile_mismatch'`, `'permission_denied'`, …), SQLite keys (`state_meta`, `hosted_rooms`).

### Collisions found and reconciled
The canonical spellings are already **unanimous in our tree** — no third spelling was introduced:
- `qubit_constants` defines `get_qubit_home` / `get_qubit_home_override` / `qubit_home_key` / `profile_name_for_home` (lines 143/40/161/347) and reads **`QUBIT_HOME` only**.
- `gateway/` already imports these **244 times**; siblings `run_inbound.py:60`, `run_plugin_rewire.py:90`, `hooks.py:22` import exactly `qubit_home_key`.
- **Zero** `HERMES_HOME` writers other than the migration adapter `voicebridge/qubit_legacy.py`, so my `QUBIT_HOME` spelling has no dual-writer half.

## 3. Missing dependencies — the blocker ledger

Every missing dep below is **named in the 299-file delta** (`/tmp/prod-files.txt`), so each is *orderable*, not a genuine gap.

**Top-level (block the import of my files until they land):**

| missing module | needed top-level by |
|---|---|
| `qubit_state_runtime` (upstream `hermes_state_runtime.py`) | **all 7** |
| `gateway/session_contract.py` | `session_cron`, `session_hosted_rpc`, `session_hosted_service` |
| `qubit_state_keys` (upstream `hermes_state_keys.py`) | `session_cron` |
| `gateway/hosted_room_attachments.py` | `session_hosted_attachments` |
| `gateway/session_ingress_media.py` | `session_hosted_attachments` |

`qubit_state_runtime` is the single highest-leverage file for this chunk: **a 7-file unblock**. `gateway/session_contract.py` unblocks 3.

**Deferred (import inside a function — module imports fine without them):**
`gateway.session_policy` · `gateway.session_local_recovery` · `gateway.session_runtime_workers` · `gateway.session_results` · `gateway.session_local` · `gateway.session_ingress` · `gateway.session_display` · `gateway.session_surface` · `gateway.session_cli_turn` · `gateway.session_hosted_transport` · `gateway.session_authorities` (self, intra-chunk) · `gateway.hosted_rooms` · `gateway.hosted_rooms_common` · `gateway.hosted_room_driver` · `gateway.run` · `gateway.config` · `gateway.session` · `gateway.session_lifecycle` · `gateway.platforms.base` · `qubit_state_local` · `cron.{jobs,scheduler,scheduler_liveness,execution_identity,executions}` · `agent.skill_utils` — **all on disk already except the `gateway.session_*` row above.**

Nothing in this chunk is a `shim-skipped` (the §4 facade case) and nothing is `dep-missing`-withheld: the deferred-import pattern means each file is a real, orderable unit, and each proves import-clean once its top-level deps are stubbed.

## 4. The import proof — honest form

`import gateway.<mod>` **against the tree fails for all 7**, and the failure is a *missing dependency*, not a defect in the port — this is PORT-METHOD §6's first honest failure mode:

```
session_authorities        ModuleNotFoundError: No module named 'qubit_state_runtime'
session_cron               ModuleNotFoundError: No module named 'gateway.session_contract'
session_finite             ModuleNotFoundError: No module named 'qubit_state_runtime'
session_hosted_attachments ModuleNotFoundError: No module named 'gateway.hosted_room_attachments'
session_hosted_controls    ModuleNotFoundError: No module named 'qubit_state_runtime'
session_hosted_rpc         ModuleNotFoundError: No module named 'gateway.session_contract'
session_hosted_service     ModuleNotFoundError: No module named 'gateway.session_contract'
```

To prove the *ported code itself* is sound — that the only breakage is the absent deps — I built a **throwaway probe dir** (`/tmp/port-8/.probe`, deleted before commit; never in the tree) containing our 7 files, the **real on-disk deps** (`qubit_constants`, `gateway.hosted_room_driver`, `tui_gateway.hosted_room_driver`, `tui_gateway.hosted_room_service`, `gateway.hosted_rooms*`, `agent/`, `tools/`, `cron/`, `qubit_cli/`, …) plus **minimal scratch stubs** of only the absent modules. Result:

```
session_authorities        OK
session_cron               OK
session_finite             OK
session_hosted_attachments OK
session_hosted_controls    OK
session_hosted_rpc         OK
session_hosted_service     OK
```

`session_hosted_service` is the strongest signal: it pulled in the *entire real* `tui_gateway.hosted_room_service` graph (`hosted_room_discussion/links/peer/execution_policy/server_rpc/peer_http/peer_transport`) and still imported. No stub of a module we possess was used.

## 5. `qubit_runtime/gateway/AGENTS.md` — do my files engage the contract?

- **Streaming-delivery contract (4 invariants) — NOT engaged.** None of the 7 touches `finish()`/`send()`/`send_for_platform()`/`edit_message` or `draft_stream_is_message`. No egress door.
- **Two message guards — NOT engaged.** No adapter/runner inbound path here.
- **Profile-scope rule — DIRECTLY engaged; this is the load-bearing contract for the chunk.** Per AGENTS.md, "an unbound read is a SILENT default-profile leak", and resolution must come from the session record, never `os.environ`. All three files that choose a profile implement exactly that:
  - `session_authorities.SessionAuthorities.active()` — once `multiplexed`, an **unserved scoped home returns `None`**, never the launch authority; the docstring states the failure it prevents ("a routed profile must never fall back to the launch profile's ledger"). `require()` raises `RuntimeStoreError('profile_mismatch')` rather than defaulting. This is the fail-closed rule, encoded.
  - `session_cron.operation('submit')` — wraps `_create` in `owner_scope(authority)` so the job id, jobs file and gateway config are the **OWNING** profile's; `_actor()` raises `profile_mismatch` on a foreign `actor.profile_id`.
  - `session_hosted_service.profile_homes()` — every configured hosted profile is gated on `IDENTIFIER_RE`, an **absolute** and `resolve()`d path, and `name == own and target != home` → `permission_denied`.
  This makes the chunk a *fix* for the profile-scope invariant, not a risk to it. No `os.environ` profile read appears anywhere in the 7 files.

## 6. Consumer already waiting in our tree

`agent/managed_worker.py:218` — `from gateway.session_finite import finite_clarify_reply, finite_turn_required`. So `session_finite` is not speculative: a live call site is currently broken by its absence. It is blocked only on `qubit_state_runtime`.

## 7. What I did NOT do

- Did **not** stub any blocked file (PORT-METHOD: never).
- Did **not** touch the frozen pin `69948c0057`, `hermes_state_keys` provenance, or any `scripts/` deny-listed file.
- Did **not** run the full test suite — implementation-first cadence (AGENTS.md): minimal syntax/compile checks only.
- Did **not** modify anything under `/home/trick/upstream/hermes-agent` (read-only).
