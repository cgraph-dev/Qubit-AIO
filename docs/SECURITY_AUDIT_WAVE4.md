# Security audit — Wave 4 (Agent I)

Status: **AUDITED — in-scope gaps G1/G3/G5 fixed; out-of-scope gaps recorded**
Baseline: commit `539649d4` (waves 1–3 not regressed).
Owner: Agent I (security / permissions / audit / e-stop / resource parity).

This wave closes the six gaps recorded at the end of
[`SECURITY_AUDIT_WAVE3.md`](SECURITY_AUDIT_WAVE3.md). Three of them live inside
the Agent-I-owned modules (**G1**, **G3**, **G5**) and are fixed here; the
other three (**G2**, **G4**, **G6**) live in files owned by other agents and
are recorded below with exact spans and the recommended change — not edited.

Owned files edited: `voicebridge/cli_hermes_forward.py`,
`voicebridge/dispatch_guard.py`, `voicebridge/plugin_runtime.py`,
`voicebridge/opencode/runner.py`, `voicebridge/ghost.py`. New evidence:
this file and `tests/test_security_parity_wave4.py` (23 adversarial tests).

## 1. Gap disposition

| Gap | Concern | Status | Fix / owner |
| --- | --- | --- | --- |
| **G1** | CLI fork `--confirm` routes bypassed policy/audit/e-stop | **FIXED** | `cli_hermes_forward.guard_confirm` + `dispatch_guard.WorkerGuard.authorize` |
| **G2** | Receipts served unredacted | **STILL OPEN (out of scope)** | `voice_commands.py`, `dashboard_ops.py` — recorded §4 |
| **G3** | Second adapter-private e-stop latches | **FIXED** | `opencode/runner.py`, `ghost.py` delegate to the shared `PowerControl` |
| **G4** | Permission scopes opt-in (empty scopes skip binding) | **STILL OPEN (out of scope)** | `permissions.py` — recorded §4 |
| **G5** | Plugin/OpenClaw boolean `confirmed` vs shared journal | **FIXED** | `plugin_runtime._authorize_mutation` over the durable `ApprovalJournal` |
| **G6** | Residual fail-open in `hermes_compat._estop` | **STILL OPEN (out of scope)** | `hermes_compat.py:68` — recorded §4 |

## 2. Fixes (owned files)

### G1 — CLI fork-confirm routes now use the shared guard

`cli_hermes_forward.route_family`/`confirm_pair` treated `--confirm` as a
boolean presence check and returned `forward`; `cli.main` (`cli.py:3176-3187`)
then invoked the vendored fork with **no** `PolicyEngine` Decision, **no**
e-stop probe, **no** redaction, and **no** expiring/single-use record.

* `voicebridge/cli_hermes_forward.py:147-232` — new shared authority:
  `FORK_CAPABILITY = "fork.forward"` registered once as a `MUTATING`
  capability in a shared `PolicyEngine`/`ConfirmationStore`
  (`_get_fork_engine`), and `guard_confirm(argv, family, sub, cfg=None)`.
  Order, all fail-closed: (1) the one durable `broker.estop_engaged` latch
  (a read error counts as latched); (2) `dispatch_guard.WorkerGuard.authorize`
  issues and consumes a short-lived single-use confirmation bound to the exact
  argv digest and records the policy Decision; an unavailable guard refuses.
* `voicebridge/cli_hermes_forward.py:296-398` — `route_family`, `confirm_pair`
  and `route` accept an optional `cfg` and route every `--confirm` mutation
  through `guard_confirm` before returning `forward`.
* `voicebridge/dispatch_guard.py:74-101` — `WorkerGuard.authorize`, a
  non-executing sibling of `run` that shares the same store/engine and refuses
  non-user sources; `DispatchBlocked` now exposes `.detail` (`:31`).

Diagnostic reads (`worktree list`, `bundles show`, …) still forward — the
e-stop gate applies to mutating routes. The fork's own `pause` stays a typed
`ESTOP_QUBIT_OWNED` refusal so Qubit keeps one e-stop.

### G3 — one emergency-stop authority

`OpenCodeRunner.emergency_stop`/`reset` and `GhostAdapter.emergency_stop`/
`reset` latched adapter-private state files. They now delegate to the single
durable `PowerControl`/`PowerControlStore` (via the `PowerPolicy` each runner
already holds), while keeping the local view for compatibility.

* `voicebridge/opencode/runner.py:27-55` — `_shared_call` / `_shared_stopped`
  over `power.control`; `:149-178` latches/clears the shared authority and
  `status()`/`_effective()` observe an externally-latched e-stop.
* `voicebridge/ghost.py:1110-1244` — `GhostAdapter` accepts a `power_control`,
  has `bind_power`, `_shared_stopped`; `emergency_stop`/`reset` latch/clear the
  shared control (a failed shared latch raises rather than reporting a clean
  stop) and `enable`/`mark_reconnect` observe it; `ghost_adapter(cfg)` wires the
  shared control in production. `GhostRunner` also `bind_power`s an
  internally-built adapter so `power=`-only construction stays unified.

### G5 — plugin/MCP/policy mutations bound to the shared journal

`plugin_runtime.register/bind_handler/unregister/invoke/mcp_attach/mcp_revoke/
policy_add_rule/policy_remove_rule` accepted a boolean `confirmed` plus an
e-stop probe with no durable single-use/expiring confirmation.

* `voicebridge/plugin_runtime.py:221-300` — `_approval_journal` (the same
  `<state_dir>/approvals.jsonl` the dashboard/voice/CLI approvals surface
  uses), `_record_human_confirmation` (durably records and immediately consumes
  a human `--confirm`; a journal failure fails closed), `_consume_journal_approval`
  (exactly-once, expiry-aware, capability-bound; a replay/expired/denied/wrong
  capability id is refused), and `_authorize_mutation`.
* Every mutating entry point gained a `confirmation_id=""` keyword and now
  calls `_authorize_mutation` with its typed confirm/estop codes preserved
  (`PLUGIN_*`, `MCP_*`, `POLICY_*`).

## 3. Tests and evidence

New adversarial suite `tests/test_security_parity_wave4.py` (23 tests), all
hermetic (tmp XDG, no network/live service):

* **G1:** unclassified/unconfirmed refusal; e-stop blocks family and
  `CONFIRM_TABLE` routes; probe error fails closed; unavailable guard refuses;
  `worker_dispatch=deny` blocks; a recorded `policy.decision`; single-use +
  expiry + target mismatch + non-human issuer + scope escalation; secret
  redaction in the audit; injection text is data, not authorization.
* **G3:** Ghost/OpenCode e-stop latches and clears the one shared store; enable
  refused while latched; external latch observed; human-only reset; active-run
  cancellation; shared-latch failure fails closed.
* **G5:** mutation requires a shared confirmation; approval id single-use,
  expiry, capability binding, deny; typed e-stop codes; journal failure fails
  closed; no secret in the journal; MCP attach/revoke through the journal.

Commands run (all green unless noted):

```
python3 -m pytest -q tests/test_security_parity_wave4.py \
  tests/test_security_parity_wave3.py tests/test_packet1a.py \
  tests/test_packet1b.py tests/test_packet3a.py tests/test_approvals_cli8tf.py
  -> 158 passed

python3 -m pytest -q tests/test_packet8r.py tests/test_packet8n.py \
  tests/test_desktop_parity8u.py tests/test_opencode_voice8u.py \
  tests/test_qubit_clusters_agent7_ghost.py tests/test_plugin_runtime8u.py \
  tests/test_oc_skills_agent8_plugins.py tests/test_openclaw_d4_policy.py \
  tests/test_openclaw_d4_skills.py tests/test_cli_parity_wave1.py \
  tests/test_cli_missing_wave2.py tests/test_single_surface8tf.py \
  tests/test_boundaries8tf.py
  -> 567 passed

python3 -m compileall -q <changed owned files>   -> ok
git diff --check -- <changed owned files>        -> clean
```

`git diff --check` over the whole worktree still reports
`tests/test_hermes_cutover8u.py:311: new blank line at EOF`, which is another
agent's uncommitted file (not owned by Agent I) and is left untouched.

## 4. Gaps outside owned files (for the orchestrator — NOT edited)

* **G2 — receipts returned unredacted.** `voice_commands.execute`
  (`voice_commands.py:7996-7998`) and `voice_commands.confirm_pending`
  (`voice_commands.py:8052-8054`) build
  `receipt = {..., "result": result}` without `redact_obj`, and
  `dashboard_ops.run_registry_op` (`dashboard_ops.py:273`) /
  `confirm_registry_op` (`dashboard_ops.py:292`) serve it back. A backend
  result carrying a secret under an unlabelled key can be served. **Owner:
  voice/dashboard agent.** Wrap the receipt with `redaction.redact_obj` at
  those boundaries (and the staged `{entry, session}` fields are already
  non-sensitive).
* **G4 — permission scopes are opt-in.** `PermissionPolicy.decide` checks the
  requested scopes only when truthy (`permissions.py:632`) and
  `_matching_grant` narrows only when scopes are supplied
  (`permissions.py:683`), so `scopes=()` gets pre-authorization/grant matching
  without the declared-scope constraint. **Owner: permissions agent.** Require
  the capability's declared scopes (or derive them from `spec.scopes`) before
  granting, so an empty-scope caller cannot skip the constraint.
* **G6 — residual fail-open in `hermes_compat._estop`.**
  `hermes_compat.py:59-68`: if both `broker.estop_engaged` and the
  `power.estop_engaged` fallback raise, the function returns `False` (line 68).
  With Wave-3 F2 the first import succeeds, so this is latent, but the `except`
  branch must **return True** (fail closed). **Owner: hermes_compat agent.**

## 5. Residual risks

* G2/G4/G6 remain open until their owning agents land the changes; the shared
  contract is not yet uniform across the receipt, permission-scope, and
  hermes-compat boundaries.
* G1 still authorizes a fresh human `--confirm` invocation by issuing and
  immediately consuming a single-use record; replay protection is per-record
  (a new invocation mints a new record). The guard is no longer a raw bypass,
  but a durable pre-issued approval id (like `node_host._valid_approval`) would
  be stronger if the CLI grows one.
* Diagnostic (read-only) fork routes remain available during an e-stop by
  design; only mutations are gated.
* Redaction is pattern-based defence in depth, not a guarantee.

## 6. Rollback

All edits are confined to owned modules; revert individually:

* `git checkout -- voicebridge/cli_hermes_forward.py` (G1)
* `git checkout -- voicebridge/dispatch_guard.py` (G1)
* `git checkout -- voicebridge/plugin_runtime.py` (G5)
* `git checkout -- voicebridge/opencode/runner.py` (G3)
* `git checkout -- voicebridge/ghost.py` (G3)
* `rm tests/test_security_parity_wave4.py docs/SECURITY_AUDIT_WAVE4.md`

No commit was made; no other module, state file, credential, or live service
was touched. Reverting restores the Wave-3 behaviour (unguarded fork
`--confirm`, adapter-private e-stop latches, boolean plugin confirmations).
