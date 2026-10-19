# Investigation: `sudo_prompt_scope` / `secret_prompt_scope` / `run_classic_cli_turn`

## Verdict

The three functions were **not merely unwired — they were unwireable**, because every
seam they depend on was missing from `TurnRunner`. The root cause was a half-finished facade
split: the upstream versions of the turn-progress methods live in
`gateway/run_turn_progress.py::GatewayTurnProgressMixin`, but `TurnRunner` inherited **no mixer**
and still carried the *pre-upstream* copies of those methods. The cluster therefore sat
downstream of an unported cut-over.

The cut-over is now applied (upstream class head, verbatim) and the cluster is wired in `run_sync`.

## Evidence, before the fix

```
== 1. _publish_execution on TurnRunner ==
   hasattr(TurnRunner, '_publish_execution') = False
   TurnRunner.__mro__ = ['TurnRunner', 'object']
== 2. _approval_owner on TurnRunner ==
   hasattr(TurnRunner, '_approval_owner') = False
== 4. does a bare TurnRunner satisfy the local-viewer gate? ==
   runner._approval_owner = <UNSET>
   local_viewer_turn(runner) = False
   => sudo_prompt_scope installs callback: False
   => secret_prompt_scope installs callback: False
```

Driving a real `run_sync` local turn:

```
== A. state BEFORE the wired block (current tree) ==
   sudo callback installed: False
   secret capture bound: False
   run_sync final_response: ok
   sudo callback AFTER a local turn: False

== B. what wiring the upstream call shape would do ==
   runner2._approval_owner: <UNSET>
   publish_usage: no error
   publish_settled_info: no error
   local_viewer_events.wire raised: AttributeError: 'TurnRunner' object has no attribute '_publish_execution'
```

Why each line matters:

- `sudo_prompt_scope` / `secret_prompt_scope` both early-`yield` when
  `local_viewer_turn(turn)` is false, and `local_viewer_turn` is exactly
  `runner._approval_owner is not None and source.platform == LOCAL`. With no
  `_approval_owner` store, **every** turn — local included — was "not a local viewer turn".
  The sudo password callback was never installed (so `sudo` took the headless
  `sudo: a terminal is required` path) and the secret capture was never bound (so a skill's
  missing env var only produced the gateway "secret entry is not available" hint).
- `local_viewer_events.wire` (which also seeds `session_usage.TURN_BASE_ATTR`) raised
  `AttributeError` on a real runner, so the upstream `finally` block
  (`restore` / `publish_usage` / `publish_settled_info`) could not be pasted in: a local turn
  would have crashed rather than merely skipped publication.
- `publish_usage` / `publish_settled_info` swallow their own errors, so their absence was
  *silent* — no `session.usage` / `session.info` at turn end, and no crash to point at it.

## The scope was wider than the three functions

`TurnRunner` inherited neither mixin while `GatewayTurnProgressMixin`/`GatewaySessionAgentMixin`
define 49 + 9 methods; `TurnRunner` re-defined 47 of them locally (the older, pre-`_publish_execution`
bodies) — so the newer mixin bodies were dead code. Only three were genuinely orphaned, and none
is referenced anywhere (`_local_notice_callback_sync`, `_local_notice_clear_callback_sync`,
`combined_tool_complete_callback`).

## The fix

1. `TurnRunner` now inherits `(GatewayTurnProgressMixin, GatewaySessionAgentMixin)`, declares the
   class-level `_approval_owner = None`, assigns it in `__init__` from `active_authority(runner)`,
   and defines `_publish_execution` / `_publish_api_tool` — all verbatim from upstream 43faacfb8.
2. `run_sync` now wraps the turn in `sudo_prompt_scope` + `secret_prompt_scope` via
   `run_classic_cli_turn`, with the upstream `finally` (`local_viewer_events.restore` →
   `publish_usage(record=True)` → `publish_settled_info`), plus the `local_sinks` / `TURN_BASE_ATTR`
   prologue. Rebrand: `hermes_cli` → `qubit_cli` (no other rename needed).

Profile scope is unchanged: `_approval_owner` is read off the runner, never `os.environ`.

## Proof

`tests/gateway/test_local_viewer_prompt_cluster.py` — 4 tests, all red without the `run_sync`
wiring (cut-over applied), all green with it. `tests/gateway/test_local_viewer_prompt_cluster_blocked.py`
pins the preconditions and is the tripwire that fired when the cut-over landed; it is kept as the
record of the blocker and should be retired once this lands.
