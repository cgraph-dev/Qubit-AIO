# Security audit — Wave 3 (Agent I)

Status: **AUDITED — in-scope gaps fixed; out-of-scope gaps recorded**
Baseline: commit `f5fc5776` (verified, not regressed).
Owner: Agent I (security / permissions / audit / e-stop / resource parity).

This report audits the shared security contract — authentication, permission
grants, confirmation (expiry + single-use), audit, redaction, emergency stop,
cancellation, and resource limits — across every dispatch path, and records the
gaps fixed inside the Agent-I-owned modules plus the gaps that live outside
those files for the orchestrator to route.

Owned files audited and edited: `voicebridge/capabilities.py`,
`voicebridge/broker.py`, `voicebridge/approvals.py`, `voicebridge/redaction.py`,
`voicebridge/dispatch_guard.py`. New evidence: this file and
`tests/test_security_parity_wave3.py`.

## 1. Contract coverage

| Concern | Single authority | Status |
| --- | --- | --- |
| Authentication (dashboard/CLI HTTP token) | `voicebridge/webui.py` (`token_ok`, header-only, query-token rejected) | Present; not owned by Agent I |
| Permission grants (scope-bound, expiring) | `voicebridge/permissions.py` (`Grant.is_active`, `_matching_grant`) | Present; scope enforcement is opt-in (gap **G4**) |
| Confirmation expiry + single-use | `voicebridge/capabilities.py` `ConfirmationStore`; `voicebridge/approvals.py` `ApprovalJournal` (durable); `voice_commands._mark_executed` | Present, hardened (fixes **F3–F5**) |
| Confirmation issuer authority | `capabilities.HUMAN_ISSUERS` | **Fixed** (was model-only; now human allowlist) — fix **F3** |
| Audit | `voicebridge/logging_setup.py:log_event` (redacts at the choke point) + `broker._audit`, `capabilities.PolicyEngine._audit` | Present |
| Redaction (logs/prompts/reports/receipts) | `voicebridge/redaction.py` | Present; quoted-JSON leak **fixed** — fix **F1**, receipt gap **G2** |
| Emergency stop (fail closed, one latch) | `voicebridge/plugins.py:estop_engaged` over the durable `PowerControlStore`; broker fallback now in `voicebridge/broker.py:estop_engaged` | **Fixed fail-open** — fix **F2**, second-authority gap **G3** |
| Cancellation | `CapabilityBroker.cancel` (idempotent, SIGKILL process group) + `PowerControl` | Present |
| Resource limits | broker arg/str/output/timeout/concurrency bounds; `ConfirmationStore(max_records)` | Present, store bound added — fix **F6** |

## 2. Dispatch-path trace

* **voice** → `voice_say` → `dashboard_ops.run_op` → Tier-1 `voice_commands.execute`
  / Tier-2 `run_extra_op`. Tier-1 confirm entries stage a durable single-use
  record in the shared `ApprovalJournal` (`voice_commands.py:7668-7707`) and
  settle via `confirm_pending` + `_mark_executed` (`voice_commands.py:7752-7756`).
  Mutation probes the shared e-stop first (`voice_commands.py:7669`). Source is
  gated to `user|cli|dashboard` (`voice_commands.py:7656`).
* **dashboard** → `dashboard_ops.run_op` → same Tier-1 pipeline and Tier-2
  journal entries (`dashboard_ops.py:407-456`, `:459-522`). Session is bound in
  the journal meta and checked at settle (`dashboard_ops.py:476-479`). Sensitive
  args are never journaled (`dashboard_ops.py:369-372`, `:436-438`).
* **CLI / hermes_compat** → `hermes_compat.h_*` handlers route to the same
  `dashboard_ops`/`voice_commands` authorities for staged confirmations
  (`hermes_compat.py:1675-1690`). The CLI `qubit agent` runtime honors the
  caller-supplied `estop_engaged` callback (`voicebridge/agent.py:57-69`).
* **plugin/OpenClaw** → `plugin_runtime` gates each mutation on e-stop + a
  boolean `confirmed` flag (`plugin_runtime.py:389-506`, `:1059-1085`); the
  device/pairing/channel/node modules probe `plugins.estop_engaged`.

No path in the owned modules offers a shell or executable fallback: `broker.py`
has no `shell=True` path, argv tools are explicit lists, and shell
metacharacters in templates are rejected (`broker.py:64-77`, `:559-596`).

## 3. Fixes made (owned files)

* **F1 — quoted-JSON secret leak** (`voicebridge/redaction.py:17-27`).
  `{"password": "hunter2"}`, `{"token": "…"}`, `{"access_token": …}`,
  `{"client_secret": …}` were **not** masked because the separator regex did not
  allow the closing/opening double quotes. The separator group now allows
  optional quotes, so logs, prompts, reports, and `log_event` payloads redact
  quoted JSON secrets. Benign JSON is unchanged.
* **F2 — emergency-stop fail-open in `hermes_compat._estop`**
  (`voicebridge/broker.py:131-145`). `hermes_compat._estop`
  (`hermes_compat.py:53-61`) imports `broker.estop_engaged`, which did not exist;
  the fallback (`power.estop_engaged`) also does not exist, so the probe
  returned **False** and `hermes_compat.h_chat_turn` (`hermes_compat.py:1688`)
  ran agent turns while the latch was engaged. Added fail-closed
  `broker.estop_engaged(cfg)` delegating to the one durable
  `plugins.estop_engaged` latch; any read error reports ENGAGED.
* **F3 — non-human confirmation issuers** (`voicebridge/capabilities.py:61-64`,
  `:157-164`, `:184-194`, `:200-217`, `:219-236`). `ConfirmationStore.issue`
  previously rejected only `issued_by="model"`; `"page"`, `"tool"`,
  `"automation"`, `"system"`, `"webui"`, or any arbitrary string could mint a
  record that a later user request could consume. Issuance and consumption now
  require `issued_by ∈ HUMAN_ISSUERS = {user, cli:user, dashboard, voice,
  voice:user}`; anything else is refused (`ISSUER_NOT_HUMAN`, model keeps
  `ISSUER_MODEL_FORBIDDEN`). The consumption check also protects records loaded
  from a durable/tampered store (`consume` and `verify`).
* **F4 — broker power-probe fail-open** (`voicebridge/broker.py:169-193`).
  A raising `PowerControl.refresh()/state`/`breaker_state()` now denies the run
  with `POWER_UNAVAILABLE` instead of propagating; the run never proceeds.
* **F5 — `ApprovalJournal.finish` guard** (`voicebridge/approvals.py:157-162`).
  `assert item.state == EXECUTING` is stripped under `python -O`, which could
  move an unapproved pending record to `COMPLETE`. Replaced with an explicit
  `raise AssertionError(...)` (preserves the existing test contract).
* **F6 — bounded confirmation store** (`voicebridge/capabilities.py:144-194`).
  `ConfirmationStore(max_records=DEFAULT_MAX_RECORDS=4096)` prunes
  consumed/expired records and fails closed with `CONFIRMATION_STORE_FULL` when
  the bound is reached, closing an unbounded-memory resource gap.

## 4. Gaps found outside owned files (for the orchestrator — NOT edited)

* **G1 — CLI confirm routes bypass the policy/audit/e-stop path.**
  `cli_hermes_forward.route_family`/`confirm_pair` treat `--confirm` as a
  boolean presence check (`cli_hermes_forward.py:243-254`, `:256-274`) and
  `cli.main` forwards the argv straight into the fork
  (`cli.py:3177-3187`) with no `PolicyEngine` Decision, no e-stop probe, no
  redaction, and no single-use/expiring confirmation record. This is weaker than
  the shared contract; it should settle a real confirmation through the broker
  or `ApprovalJournal` and probe `broker.estop_engaged` before forwarding.
* **G2 — receipts are returned unredacted.** `voice_commands.execute` /
  `confirm_pending` build `receipt = {..., "result": result}` without
  `redact_obj` (`voice_commands.py:7719-7723`, `:7775-7781`) and
  `dashboard_ops.run_registry_op` serves it back (`dashboard_ops.py:272-275`,
  `:291-294`). A backend result carrying a secret under an unlabelled key can be
  served. Wrap the receipt with `redaction.redact_obj` at those boundaries.
* **G3 — more than one emergency-stop authority.**
  `voicebridge/opencode/runner.py:122-127` and `voicebridge/ghost.py:1146-1161`
  latch adapter-private state (`emergency_stop` in their own state files)
  instead of the single `PowerControlStore`. `PowerControl.emergency_stop`
  (`power.py:1514-1539`) is the required single authority; the adapter latches
  should delegate or be removed. (The broker path already shares
  `power_control`, so `ghost`/`opencode` brokered runs are covered; the adapter
  methods are the remaining divergence.)
* **G4 — permission scopes are opt-in, so empty scopes skip binding.**
  `PermissionPolicy.decide` only checks `scopes` when truthy
  (`permissions.py:632`) and `_matching_grant` only narrows when scopes are
  supplied (`permissions.py:683`). A caller that passes `scopes=()` gets
  pre-authorization/grant matching without the declared-scope constraint.
  Require the capability's declared scopes to be supplied (or derive them from
  the spec) before granting.
* **G5 — plugin/OpenClaw mutations use a boolean `confirmed` flag, not the
  shared single-use expiring confirmation.** `plugin_runtime.register/bind/
  invoke/unregister` and MCP attach/revoke (`plugin_runtime.py:389-506`,
  `:646-710`) accept `confirmed: bool = False` plus an e-stop probe. They are
  fail-closed but not bound to the shared `ApprovalJournal`, so replay/expiry
  does not apply. Route them through the shared confirmation pipeline.
* **G6 — residual fail-open in `hermes_compat._estop` fallback.**
  `hermes_compat.py:53-61` still returns `False` if both imports raise. With F2
  (`broker.estop_engaged`) the first import now succeeds, but the `except`
  branch should return `True` (fail closed) rather than `False`.

## 5. Tests and evidence

New adversarial suite `tests/test_security_parity_wave3.py` (27 tests):
bypass/refusal of unclassified fork subcommands and unconfirmed dashboard ops,
unknown/malformed denial before execution, non-user execution and issuance
refusal, forged/persisted non-human confirmation refusal, replay/expiry/target
mismatch, prompt-injection non-escalation, quoted-JSON secret redaction in
logs/receipts, e-stop latch denial, fail-closed probe on read error, the
`hermes_compat._estop` regression (latched chat turn refused, agent not called),
idempotent cancellation, arg/store/concurrency limits, approval finish guard +
settle-once/expiry, cross-session confirmation refusal, and no-fabricated-success.

Commands run (all green):

```
python3 -m pytest -q tests/test_security_parity_wave3.py \
  tests/test_packet1a.py tests/test_packet1b.py tests/test_packet3a.py \
  tests/test_packet4d_mitigation.py tests/test_approvals_cli8tf.py \
  tests/test_capability_catalog.py tests/test_capability_wiring8u.py \
  tests/test_hermes_routes_parity8u.py
  -> 207 passed

python3 -m pytest -q tests/test_packet8o.py tests/test_packet8f.py \
  tests/test_packet8n.py tests/test_packet7e.py tests/test_upstream_vendored.py
  -> 377 passed

python3 -m compileall -q <changed files>   -> ok
git diff --check                           -> clean
```

## 6. Residual risks

* G1/G2/G4/G5/G6 above remain open until their owning agents land the changes;
  the shared contract is not yet uniform across CLI-fork, plugin, and receipt
  boundaries.
* The confirmation-issuer allowlist rejects unknown-but-legitimate future
  channels; add the new channel name to `HUMAN_ISSUERS` deliberately (never
  accept arbitrary caller strings).
* Redaction is pattern-based; a secret under a benign key name with no known
  credential shape is not masked. Treat it as best-effort defence in depth, not
  a guarantee.
* `ConfirmationStore` is in-memory per broker; the durable single-use flow for
  actions/power is `PersistentConfirmationStore`, which inherits the new issuer
  checks.

## 7. Rollback

All edits are confined to the owned modules. Revert individually:

* `git checkout -- voicebridge/redaction.py` (F1)
* `git checkout -- voicebridge/broker.py` (F2, F4)
* `git checkout -- voicebridge/capabilities.py` (F3, F6)
* `git checkout -- voicebridge/approvals.py` (F5)
* `rm tests/test_security_parity_wave3.py docs/SECURITY_AUDIT_WAVE3.md`

No commit was made; no other module, state file, credential, or live service was
touched. Reverting restores the pre-Wave-3 behaviour (including the
`hermes_compat._estop` fail-open and the quoted-JSON redaction leak).
