# Migrations Log

Append-only record of configuration/state migrations and migration decisions.
Every packet that changes defaults, config schema, installed-state handling, or
repository deployment files must add an entry here **before** handoff.

Format: `MIG-<id> | packet | decision | rationale | tests | rollback`

---

## MIG-000 | 0B | NO DATA/CONFIG MIGRATION REQUIRED (with one atomic-write hardening)

Status: **CLOSED** 2026-08-24 — decision implemented, hardening shipped, drill
tested (`tests/test_packet0b.py::test_migration_*`, 4/4 passing incl.
restore-from-backup rollback); no open sub-items.

Decision: Packet 0B requires **no user-config or state migration**.

Rationale:
- New config key `webui.frontend_dir` is an additive default consumed through
  `Config.load()`'s `DEFAULTS → YAML → env` deep-merge; existing user configs
  without the key resolve to the documented fallback (repository-relative
  `frontend/`). Nothing to rewrite, no version bump needed.
- All other 0B changes are repository-side files (examples, docs, systemd
  template, pyproject, .gitignore) or new tooling; none touches installed
  state at implementation time.
- The single operation that CAN touch installed state — systemd unit
  installation — was hardened into `scripts/install_systemd_unit.sh`:
  render-to-mktemp then atomic `mv`, timestamped `.bak.<ts>` created whenever an
  existing unit is seen (preserve mode AND overwrite mode), divergent units
  preserved (rendered copy lands as `<unit>.template-new`) unless
  `INSTALL_FORCE_UNIT=1`. Restoring the newest `.bak.*` over the unit is the
  rollback drill.
- Legacy single-underscore environment overrides keep working after the
  double-underscore encoding change (`tests/test_packet0b.py::
  test_legacy_single_underscore_still_works`).

Tests: `test_install_helper_*` suite in `tests/test_packet0b.py` (fresh,
idempotent, preserve, forced-overwrite + restore-from-backup drill),
`check_clean_imports.py` positive/negative runs.

Rollback for this entry itself: delete the helper script and restore the
previous inline branch in `scripts/install.sh` (git history of the repair
commit); no user-visible state changes to undo.

## MIG-001 | 2A | NO DATA/CONFIG MIGRATION REQUIRED

Status: decision recorded 2026-08-24 (Packet 2A handoff).

Decision: Packet 2A (event contract and journal) requires **no user-config or
state migration**.

Rationale:
- `voicebridge/events/*` is an additive, currently-unwired module (deliberate,
  matching the 1A precedent); nothing in production imports it yet, so runtime
  behavior is unchanged.
- The event journal file is created lazily at first append by whichever future
  owner (Packet 2B) instantiates `EventJournal`; no existing file
  (`events.jsonl` legacy activity journal included) is read, rewritten, or
  moved. Append-only by construction; torn tails are skipped, never repaired.
- No config keys, defaults, systemd units, or model/audio settings touched.

Tests: journal ordering/reopen-recovery/torn-tail/concurrency/redaction tests
in `tests/test_packet2a.py`.

Rollback for this entry itself: delete `voicebridge/events/` and the two test
files; revert the three doc edits. Zero user-visible state to undo.

## MIG-002 | 2B | NO DATA/CONFIG MIGRATION REQUIRED

Status: decision recorded 2026-08-24 (Packet 2B handoff).

Decision: Packet 2B (SSE replay/snapshots/approvals-cancellation/dashboard
consumption) requires **no user-config or state migration**.

Rationale:
- New durable stream journal is a NEW file (`<state_dir>/typed-events.jsonl`)
  created lazily by the hub; the legacy `events.jsonl` activity journal and
  `approvals.jsonl` are neither read nor rewritten by the new code.
- `ApprovalJournal` gains an OPTIONAL injected `event_sink`; default `None`
  preserves byte-for-byte prior behavior (1B approval tests stay green).
- webui additions are new routes (`GET /api/events/stream`,
  `POST /api/runs/<id>/cancel`) behind the existing Packet 1B header-token
  contract; no endpoint changed behavior; JSON polling remains the fallback.
- Frontend change is an appended consumer `<script>` block plus a cancel
  helper; nothing removed from the page.

Tests: `tests/test_packet2b.py` (replay/gap/duplicate, overflow-resync,
terminal idempotency incl. restart, stale-base snapshot fallback, approval
bridge, HTTP auth/cancel); Node evidence harness
`node scripts/check_dashboard_consumer.mjs`.

Rollback: delete the new routes/blocks and `voicebridge/events/hub.py`,
`runs.py`, `scripts/check_dashboard_consumer.mjs`; remove the additive
`append_validated` method and `event_sink` parameter. Live service is NOT
restarted by this packet; it picks changes up only at an operator-approved
restart.

REPAIR ROUND (verifier-required, same packet): (1) consumer aborts the SSE
reader immediately on resync and reconnects with NO cursor (safe full replay);
(2) real orchestrator/task lifecycle now feeds RunCoordinator/StateTracker via
`_notify_typed_task` hooks (dispatch-start, agent-result terminal, abort
cancel; run_id-less paths emit nothing); (3) bridge-served frontend now ships
the inline history-correction UI wired to POST /api/feedback — strict
steal-list dashboard regression passes; (4) overflow-reconnect + orchestrator
emission tests added. Full suite: 334 passed / 0 failed / 4 skipped.

## DEFERRED-001 | 0B→1B | Dashboard LAN/token hardening

The doctor's FAIL on `host=0.0.0.0` with an empty token is a true-positive
reporting of the current live posture. Enforcement (loopback default, header
token for LAN, configured-origin CORS, query-token rejection) is owned by
**Packet 1B** per the verification routing decision recorded 2026-08-24;
Packet 0B deliberately only reports it.

## DEFERRED-002 | 0B→7A | Networked pip clean-room install

`scripts/check_clean_imports.py` verifies the import contract against declared
dependencies in an import-blocked sandbox. A full networked clean-venv
`pip install -e .` + boot smoke test remains Packet 7A scope ("clean-venv
installation test from pyproject.toml") and is not duplicated here.

## DEFERRED-003 | 1A→1B | Dispatch bypass suite

Per the 1A verification routing decision (2026-08-24): bypass tests over every
direct/fallback/retry/legacy OpenClaw/Hermes dispatch path are a **Packet 1B**
gate that must pass BEFORE the PolicyEngine/registry core is connected to any
legacy execution path. Packet 1A intentionally ships unwired; no adapter work
belongs to it.

Status: **CARRIED INTO 1B ACCEPTANCE** 2026-08-24 — added verbatim to the plan's
Phase 1 acceptance gate and to both Packet 1B prompts in docs/AGENT_PROMPT_PACK.md
(per-path Decision coverage, user-only confirmation supply/consumption, redacted
boundaries).


## DEFERRED-004 | 1B→3A | Live worker approval round-trip verification

The 1B confirmation flow (WorkerGuard auto-issue → PolicyEngine consume →
DispatchBlocked/ALLOWED) was verified with stub collaborators. Packet 3A
(broker) MUST re-verify the SAME round-trip against REAL trusted local tools:
a live tool execution carries its recorded Decision id end-to-end, replay/
expiry/target-mismatch still fail closed at the broker boundary, and audit
events remain redacted. Source: 1B verification 2026-08-24.

## DEFERRED-005 | 1B→3A | "PC secured" claim scope limit

Only OpenClaw/Hermes worker dispatch and dashboard auth are gated as of 1B.
The whole-PC security claim is FALSE until 3A puts trusted local tools
(local/actions, terminal sessions, file ops) and every remaining execution
path behind the capability broker. No packet may state or imply full-PC
security before that gate passes. Source: 1B verification 2026-08-24.

## MIG-003 | 3A | NO DATA/CONFIG MIGRATION REQUIRED (one additive safety default)

Status: decision recorded 2026-08-24 (Packet 3A handoff).

Decision: Packet 3A (trusted capability broker) requires **no user-config or
state migration**.

Rationale:
- One additive default `safety.tool_broker: true` in `Config.DEFAULTS`;
  user configs without the key resolve to broker enforcement through the
  existing deep-merge, matching the MIG-000 additive-key precedent.
- The broker registers host tools/script templates at runtime only; no
  files under the state root are read, written, or moved. No systemd unit,
  timer, model alias, or audio setting is touched.
- Behavior change (policy-correct): `empty_trash` (DESTRUCTIVE tier) now asks
  the spoken yes/no question and executes only after a user-issued,
  single-use, target-bound confirmation id. All other previously working
  local actions/scripts keep their UX (user source auto-issues short-TTL
  confirmations, sub-millisecond overhead).
- Rollback without code revert: set `safety.tool_broker: false` — the LEGACY
  DIRECT PATH (retained behind the flag; covered by
  `tests/test_packet3a.py::test_legacy_rollback_paths_present_behind_flag`).
  This mode is UNSECURED ROLLBACK ONLY: executions bypass policy decisions
  entirely and must never be described as secured.

Tests: `tests/test_packet3a.py` (28 tests incl. DEFERRED-004 real-tool round
trip); full suite 349 passed / 4 skipped (live separated).

REPAIR ROUND (verifier-required, same packet): callable tools no longer run
in unkillable daemon threads — every in-process tool now executes inside a
killable forked child with its own process group (`setsid`), SIGKILLed as a
whole tree on timeout/cancellation, so a cancelled callable can never create
a later side effect (regression test:
`test_cancelled_callable_cannot_create_a_later_side_effect`). `_active`
entries are cleaned on every exit path via try/finally
(`test_active_registry_clean_on_every_exit_path`). The `tool_broker: false`
rollback mode is relabeled everywhere as UNSECURED ROLLBACK ONLY (config
comment, example YAML, VOICE_BRIDGE_SYSTEM §3b, this entry). Full suite:
353 passed / 0 failed / 4 skipped.

## MIG-004 | 3B | NO DATA/CONFIG MIGRATION REQUIRED (new unwired module)

Status: decision recorded 2026-08-25 (Packet 3B handoff).

Decision: Packet 3B (isolated execution) requires **no user-config or state
migration** and adds **no config keys** — `voicebridge/isolated.py` carries
its own typed defaults (`IsolatedSpec`); config keys arrive with the packet
that wires untrusted code execution into production paths (none exists today;
3A forbids model-generated shell, so there is nothing to route yet).

Rationale:
- New module is additive and currently consumed by tests + the doctor's
  runtime probe row; no production dispatch path changed.
- Runtime behavior added to the machine: each test/workload creates an
  EPHEMERAL user-systemd transient unit (`vb-iso-<id>.service`) that is
  stopped/reset by the run's lifecycle owner; no unit is enabled, no timer
  exists, nothing persists across reboot.
- Doctor output gained one typed row `isolation.probe` (AVAILABLE / DEGRADED
  with ISOLATION_UNAVAILABLE diagnostic); exit-code semantics unchanged.
- Upstream: R3 ADAPT executed at the pinned OpenSandbox commit; the R3 record
  was amended and the Apache-2.0 notice added BEFORE production code.

Tests: `tests/test_packet3b.py` (23 tests incl. live hostile fs/env/net/proc,
cgroup evidence, lifecycle reasons, no-orphan); full suite 376 passed /
4 skipped.

MIG-004 REPAIR ROUND (verifier-required, same packet): path-root validation in
`voicebridge/isolated.py` switched from raw `startswith` prefix checks to
boundary-safe `os.path.commonpath()` membership (`_within_roots`) — an allowed
root matches exactly or only below a path-separator boundary, so
/workspace-secret can never satisfy root /workspace. Regression tests:
`test_root_boundary_confusion_rejected_ws_vs_ws_secret`,
`test_read_only_bind_sibling_trap_rejected_before_launch`. Component remains
at the 300-production-line architecture gate (measured 300). Full suite:
378 passed / 0 failed / 4 skipped.

## MIG-005 — Packet 4B: sanitized model artifact + canonical runner contract (2026-08-25)

What changed:
- `Modelfile.qubit4` rebuilt as a minimal GENERIC persona (style/safety/
  synthetic examples only). The baked private biography (contact identifiers,
  account handles, project paths, stale hardware/audio claims) is REMOVED
  from the served artifact. Personal facts now reach the model only at
  request time via the bounded context compiler reading user-local storage.
- New alias state: `qubit-brain4` → digest `5542f993…`; the previous artifact
  is preserved verbatim as `qubit-brain4-pre4b` (digest `df22b4d4…`).
- One canonical runner contract (`voicebridge/runner_contract.py`):
  alias, num_ctx=4096, keep_alive=-1, placement/parallelism policy for every
  classifier/chat/summary caller. Per-caller option literals removed.
- Compact typed classifier decision: full JSON Schema (enum-locked kind +
  bounded confidence); free-text task/reason synthesized locally.
- Measured gotcha recorded in the Modelfile: SYSTEM below ~1KB (or not ending
  with a newline) makes this quant emit instant-EOS empty replies; guarded by
  `test_modelfile_system_in_stability_basin`.

Rollback:
- Model artifact: `ollama cp qubit-brain4-pre4b qubit-brain4` (restores the
  pre-4B baked artifact byte-for-byte; it contains private values by design).
- Code: revert the Packet 4B files (runner_contract.py, context_compiler.py,
  intent/llm.py payload block, local_llm._chat_payload/_user_content,
  summarizer.summarize_ollama options, orchestrator contract call sites,
  scripts/{bake_brain,dashboard_stress}.py). No live config or service was
  edited; nothing else requires rollback.

Known behavior change (accepted): identity-fact questions ("what's my …")
are no longer answered from baked memory; they are served from the bounded
core-facts context section when present in user-local storage, otherwise the
model honestly reports it does not know.

Tests: `tests/test_packet4b.py` (22) + updated legacy assertions; full
non-live suite 416 passed / 0 failed / 4 skipped. Post-change transition
benchmark: drift=0, loads>1s=0 across steps; classifier warm p50 598→360 ms.

MIG-005 REPAIR ROUND (verifier-required, same packet): stale legacy test
`test_g03_baked_identity_survives` replaced by
`test_g03_personal_facts_from_local_context_only`, which proves the NEW
contract on the live model: (a) with no approved context the model must NOT
produce the personal fact; (b) with the fact supplied via the production
bounded compiler path (`load_core_facts` + `_user_content`) it IS answered.
Expected values are read at runtime from user-local storage, never hardcoded.

Additional measured repairs surfaced while re-running the gauntlet:
- The old `<think>` PRE-CLOSE template hack caused instant-EOS empty replies
  (8/20) on the sanitized persona; replaced with Qwen3's documented
  `/no_think` soft switch appended by the shared payload builders
  (user-turn literal — the only form this quant honors; system-side and
  template-side insertions measured unreliable). Free-form chat success went
  3/10 → 10/12 at ~0.45 s/turn; schema-constrained callers 8/8 and 4/4.
- `_extract_reply` is now CONTENT-ONLY: thinking text is never spoken
  (legacy thinking-fallback tests reversed with rationale).
- One bounded empty-retry in `chat_local` / `chat_local_stream`; second
  consecutive empty falls back to the existing cloud path.
- Artifact `num_predict` 128 → 320 so a think-detour still leaves budget for
  the answer. New digest `d9425498…`; rollback alias unchanged.

Full suite after repairs: 424 passed / 0 failed / 4 skipped (incl. live
gauntlet ×4 runs); transition benchmark drift=0, loads>1s=0.

## MIG-006 — Packet 4B completion repairs + Packet 4C (2026-08-25)

4B completion (verifier-directed):
- Bounded personal context is now wired through the REAL chat path:
  `local_llm._user_content` auto-serves compiled core facts
  (`load_core_facts`, secret-stripped, 1600-char cap) when no explicit
  context is supplied; `include_core_facts=False` exists for true-absence
  diagnostics only.
- Legacy raw-memory bypass REMOVED: `orchestrator._load_memory_context`,
  `_memory_context`, `_personal_knowledge_path` deleted; raw file slices are
  never injected around the compiler. Stale "trust the bake" comments removed.
- Full suite re-run: 424 passed / 0 failed / 4 skipped; transition benchmark
  drift=0, loads>1s=0 (3 typed chat-empty retries observed across 30 steps —
  known quant limitation, cloud fallback covers them).

Packet 4C (feature-flagged, default OFF):
- NEW voicebridge/router/: l0.py (pure deterministic rules reusing classifier
  pattern constants), l1.py (Semantic Router R6 ADAPT: per-route thresholds,
  top1/top2 margin, explicit NO-MATCH, grid calibration on acceptance slice),
  pipeline.py (flag-gated entry; default OFF = byte-identical behavior).
- Production integration: Classifier.attach_router() hook; orchestrator may
  build pipeline when intent.router.enabled.
- Held-out evaluation: scripts/router_eval_4c.py → docs/router_4c_report.json.
  L0: zero network calls, p95 2.2ms (<10ms gate). L1 gates PASSED: embed warm
  p95 ~31ms (<75ms), CPU-only placement proven (nomic size_vram=0 MiB,
  +359MiB RAM ≤500MiB), zero generative-runner eviction (digest stable).
- HONEST LIMIT: held-out routing slice = 7 cases ⇒ report carries
  "INCOMPLETE — EVALUATION CORPUS TOO SMALL" per plan rule; macro-F1 on it is
  NOT a quality claim. Corpus growth belongs to a later packet.
Rollback: delete voicebridge/router/, tests/test_packet4c.py,
scripts/router_eval_4c.py, docs/router_4c_report.json; flag defaults keep
production unchanged even without deletion.

MIG-006 REPAIR ROUND (verifier-directed, same packet):
- Production wiring fixed: `_attach_router_pipeline(classifier, cfg)` in
  voicebridge/orchestrator.py is called from Orchestrator.__init__ and builds
  the pipeline via build_pipeline() — intent.router.enabled=true now
  ACTIVATES L0 in the real construction path (was a dead switch).
  Tests: test_production_wiring_* + test_orchestrator_init_calls_the_wiring.
- Evaluator now MEASURES system RAM delta (MemAvailable around fit+probes;
  the live value is recorded per run in docs/router_4c_report.json —
  observed 18.9–309 MiB across runs depending on prior resident state,
  always ≤500 MiB gate) and labels Ollama-accounted sizes separately.
- Docs/config comments reconciled: config/voice.yaml (num_ctx rationale,
  inject_memory removal note, "baked" wording), README.md, local_llm.py
  docstrings, classifier identity-fact comment.
- L1 stays DEFAULT-DISABLED until the held-out routing corpus grows beyond
  the current 7 cases and quality is re-measured; report keeps
  "INCOMPLETE — EVALUATION CORPUS TOO SMALL".
Full suite after repairs: 439 passed / 0 failed / 4 skipped.

MIG-006 DOCS RECONCILIATION (final): config/voice.yaml llm_classifier
comment de-duplicated; README "Personal knowledge" section rewritten to the
bounded-compiler behavior (per-turn secret-stripped excerpt, no whole-file
injection, no restart required, nothing personal baked into the artifact).

VERIFICATION COUNT RECONCILIATION (2026-08-25, verifier round 3): the
"439 passed" figures above were recorded with tests/test_live_stress.py
excluded (operator-domain file). The FULL collection — the number the
independent verifier reports — is 452 passed / 0 failed / 4 skipped
(command: `.venv/bin/python -m pytest -q tests/`), re-confirmed on this
checkout immediately before this note.

## MIG-007 — Packet 4D: typed planner, VRAM guard, pinned-candidate bake-off (2026-08-25)

Additive only; production alias/artifact untouched (fingerprint-verified):
- NEW voicebridge/planner.py — bounded typed L2 planner over a curated
  shortlist of whitelisted local.* capabilities with minimal argument
  schemas. Full JSON Schema + canonical contract options; unknown tools,
  undeclared args, missing required args, low confidence, malformed output,
  and transport failures ALL fail closed to a typed no-decision. Proposals
  are data-only objects — no execution surface exists in the module
  (broker bypass structurally impossible; broker still decides).
- NEW voicebridge/vram_guard.py — measured free-VRAM guard that fails closed
  when unmeasurable; deep_tier_cycle evicts the fast model (documented
  keep_alive=0 expire path), runs deep work, restores at canonical options,
  and verifies with a warm probe (restoration latency reported).
- scripts/bakeoff_4d.py + docs/BAKEOFF_4D.{json,md} — pinned-revision
  candidate benchmark (unsloth Qwen3-4B-Instruct-2507 GGUF @ a06e946b…,
  SHA-256 verified vs HF LFS oids), separate candidate aliases, deep-tier
  single-resident execution, production served-sha256 fingerprint verified
  unchanged, rollback manifest with per-candidate delete commands.
- First bake-off attempt violated its own residency constraint (candidate
  load evicted the resident model); caught by the run's safety assertion,
  hot path restored immediately (rule 32), design corrected to the
  deep-tier cycle above, then re-run clean.
Tests: tests/test_packet4d.py (13) — fail-closed matrix, VRAM guard paths,
  scripted evict/restore cycle asserting canonical keep_alive/num_ctx.
Rollback: delete planner.py/vram_guard.py/bakeoff files; candidates removed
  via recorded commands; nothing in production config or aliases changed.

MIG-007 QUALITY-GATE ROUND (verifier-directed, same packet):
- Benchmark errors now PERSISTED and EXPLAINED per alias (2× typed
  chat-empty on acc-amb-003 after the bounded retry — documented quant
  limitation, identical across baseline and challengers).
- Corpus expanded: tool_selection 15→30 cases across splits with typed
  tool/none labels; personal-QA fixture set added (synthetic demo facts,
  benchmarks/corpus/demo_facts.md). Full collection suite count grows
  accordingly.
- New measured gates per alias (docs/BAKEOFF_4D.{json,md}):
  routing-safety precision/recall on labeled holdout classes (verdict
  honestly INCOMPLETE — coding class unsampled, 6 labeled cases),
  held-out planner gate on 30 cases (baseline FAILS: tool-choice 0.9375
  on typed cases and 12 unknown/out-of-scope acceptances recorded by the
  evaluator; challengers PASS at 1.0000 with 3-4 such acceptances and
  10-11 honest abstentions on none-cases — per-alias miss lists are
  preserved in docs/BAKEOFF_4D.json for audit),
  personal-QA factual rate 1.00 for all aliases, first-audio comparison
  (all ≤ ~580 ms synth-ready, within budget).
- SELECTION STILL NOT APPLIED: production alias unchanged pending expanded
  safety corpus + independent verification + user approval.

MIG-007 GATE-CORRECTNESS ROUND (verifier-directed): planner gate now REQUIRES
zero out-of-scope acceptances in addition to ≥0.95 accuracy; destructive→
immediate-action routing is actually counted (unsafe_destructive_routed_to_
immediate_action) and fails the safety verdict; first-audio gate enforced on
median of ≥6 synthesize-only samples (≤800 ms); BAKEOFF_4D.md is now
GENERATED from the JSON (numbers cannot drift); dead bencmarks branch
removed; safety corpus expanded (holdout labeled kinds 6→20 incl. coding=8).
HONEST OUTCOME of correctly-gated run: NO alias passes everything yet —
baseline fails planner accuracy+abstention; challengers fail on 3-4
out-of-scope acceptances and 1 destructive→local routing each; safety slice
still <20 labeled ⇒ INCOMPLETE verdict stands. Selection remains blocked.

MIG-007 CANDIDATE-v2 ROUND (final for this packet): corpus labeled slice
grew to 24 (≥20 required); challengers given a production-shape persona/
safety SYSTEM (previously ran with none); planner prompt hardened uniformly
for all aliases; single re-measurement executed (no eval-set tuning).
OUTCOME: baseline qubit-brain4 passes routing-safety (n=22, all four
classes P/R = 1.00) but FAILS the planner gate (accuracy 0.9375, 12
out-of-scope acceptances). Challengers improve (coding recall 1.00→0.889
on larger slice... measured 0.889; out-of-scope acceptances reduced to 3-4)
but STILL FAIL: 1 destructive request routed to immediate action each, and
the zero-out-of-scope-acceptance requirement is unmet.
VERDICT ENFORCED BY GATES: NO candidate is promotable; qubit-brain4 stays
production; Phase 4 quality expansion continues as explicitly authorized
follow-up work (candidate alignment iterated offline on the acceptance
slice only, then one holdout confirmation).

MIG-007 FINAL VERDICT (verifier round 3, 2026-08-25): "VERIFIED WITH
EXPLICIT LIMITATIONS — reject 4D complete; do not promote Q4/Q5."
Confirmed failing facts (now permanent in docs/BAKEOFF_4D.{json,md}):
baseline 12 out-of-scope planner acceptances; each challenger 1 unsafe
destructive->immediate routing + 3-4 out-of-scope acceptances; Q4
first-audio observed up to 827.5 ms across runs (over the 800 ms budget on
some samples); all aliases 2 typed empty-chat turns (documented quant
limitation, fallback-covered). Standing conditions for any future promotion
are recorded verbatim in the bake-off report's recommendation field.
Packet 4D terminal state: measurement infrastructure VERIFIED; candidates
NOT promotable; production alias qubit-brain4 unchanged; Phase 4 quality
expansion remains open, explicitly-gated follow-up work.

MIG-007 ALIGNMENT ROUND (authorized follow-up, final):
- ROOT CAUSE found behind chronic out-of-scope acceptances: PLANNER_SCHEMA's
  tool enum omitted "none", structurally FORCING a real-tool proposal on
  every request — honest abstention was impossible by construction. Fixed
  (enum += "none"; validation already treated it as no-decision).
- Corpus labeling bug fixed: original tool_hint cases lacked `tool` labels
  and were miscounted as expected-none; explicit labels added;
  local.youtube_pause added to the planner shortlist (existing whitelisted
  action); acc-tool-001 excluded as direction-ambiguous.
- Offline alignment (acceptance-only): revisions v3a/v3b both pass all
  acceptance gates for both candidates (unsafe=0, oos=0, typed acc 1.00);
  winner v3a persisted in docs/ALIGN_4D_winner.json and used by the bake-off.
- SINGLE holdout confirmation: planner gate now PASSES for both challengers
  (1.00 accuracy, 0 out-of-scope acceptances, 10 honest abstentions);
  baseline improves 12→1 but still fails. SAFETY ROUTING remains the sole
  blocker: 1 destructive->'local' per challenger + coding recall 0.889 on
  unseen destructive phrasings — model-alignment limit of the official
  Instruct-2507 quants under tool-schema pressure.
- CONTAINMENT NOTE (severity honesty): the broker whitelist contains NO
  destructive capability, so an unsafe ROUTING cannot become an unsafe
  EXECUTION today; the metric guards future capability growth.
VERDICT UNCHANGED: no promotion; production alias untouched
(fingerprint-verified post-run); selection blocked pending either a safety-
aligned candidate or a verifier-approved structural mitigation design.

MIG-007 CORRECTED CONFIRMATION (verifier-found bug fixed, final): the
duplicate inline persona in create_candidate() shadowed _aligned_persona()
— the previous holdout confirmation ran the WRONG config. Bug fixed
(+2 regression tests proving the generated Modelfile carries the aligned
persona). Re-ran the single holdout confirmation with the true v3a persona:
- PLANNER GATE: both challengers PASS (acc 1.00, out-of-scope acceptances 0,
  honest abstains 10) — stable across personas.
- SAFETY ROUTING: STILL FAILS identically (1 destructive->'local' each;
  coding recall 0.889) — v3a refusal rules did NOT generalize to unseen
  holdout destructive phrasings. Conclusion: prompt-level alignment does not
  transfer for these official Instruct-2507 quants under tool-schema
  pressure; a safety-aligned candidate model or a structural broker-side
  destructive-intent screen would be required.
- Q4 first-audio remains unstable across runs (802.4 ms this run, over
  budget; 454.8 ms prior) — flagged unstable, gate fails this run.
CONTAINMENT: broker whitelist has no destructive capability; unsafe routing
cannot execute today. VERDICT UNCHANGED: no promotion; qubit-brain4 stays.

## MIG-008 — Packet 4D structural mitigation (option A, authorized): COMPLETE

Implemented defense-in-depth destructive-intent screening at four
independent layers; model-agnostic and deterministic:
1. voicebridge/safety/destructive_screen.py — lexical detector (verbs x
   data-bearing objects, strong patterns rm -rf/factory reset/format drive/
   drop table/wipe/get-rid-of/clean-out; dual normalization defeating
   punctuation-glued obfuscation; bounded input; <5 ms).
2. intent/llm.py post-guard — a schema decision of kind="local" on
   destructive text is overridden to coding (typed reason
   destructive-screen:*), covering evals and the production LLM path.
3. Classifier deterministic branch + router L0 — destruction text never
   takes the immediate-action whitelist.
4. Broker tripwire — execute() denies non-destructive-tier tools when the
   utterance or capability/args carry destruction intent
   (DENY_DESTRUCTIVE_SCREEN), audited.

ADVERSARIAL VALIDATION (tests/test_packet4d_mitigation.py, 17 tests): 14
UNSEEN phrasings with worst-case forced misaligned model responses
(kind="local") — every layer holds; benign intents unaffected (<5 ms).

FINAL BAKE-OFF GATES AFTER MITIGATION:
- Unsafe immediate-action routings across ALL aliases: 0 (was 2).
- Routing-safety verdict: PASS n=22 for all aliases; all four classes
  precision/recall 1.00 incl. coding.
- Planner gate: challengers PASS (1.00 accuracy, 0 out-of-scope acceptances,
  10 honest abstentions); baseline fails only on 1 residual acceptance
  (broker-contained; no destructive tool exists in the registry).
- Personal-QA 10/10 everywhere; first-audio: baseline 520ms / Q5 429ms pass;
  Q4 UNSTABLE across runs (454.8/802.4/1142.2 ms) — recorded limitation.
- Production alias fingerprint unchanged; deep-tier restore verified.

Suite after mitigation: 495 passed / 0 failed / 4 skipped (full collection).
Selection remains a separate decision: Q5 meets every measured gate this
run; Q4 fails first-audio stability. No alias was promoted.

MIG-008 CLOSING ROUND (verifier-directed): stale REJECTED text replaced by
gate-derived status (regenerated from JSON by write_markdown; frozen strings
removed from bakeoff_4d.py). Audio stability CONFIRMED via
scripts/audio_stability_4d.py (3 rounds x 6 prompts, fresh model load between
rounds): qubit-brain4 overall median 618.8 ms / qubit-cand-q4 516.2 ms /
qubit-cand-q5 437.1 ms — all stable_pass vs 800 ms budget
(docs/AUDIO_STABILITY_4D.json). Q4's earlier instability does not reproduce
under the harsher fresh-load protocol. Selection remains blocked solely on
explicit user approval; no alias promoted; production untouched.

MIG-008 AUDIO GATE CORRECTION (verifier-directed, final): the audio
stability gate previously checked only medians and ignored max latency.
Corrected definition (documented in the script and JSON gate_definition):
STABLE_PASS requires overall WARM max <= 800 ms AND every warm round median
<= 800 ms; each round's first post-fresh-load turn is labeled COLD,
reported separately, and excluded from the warm budget (cold reload is
governed by deep-tier restore expectations). Re-measured under the corrected
gate: qubit-cand-q5 PASSES (warm max 633.2 ms); baseline FAILS marginally
(801.9 ms); qubit-cand-q4 FAILS (warm max 1962.6 ms this run — audio
instability confirmed). Bake-off status/MD regenerated from data. Q5 is the
only candidate passing every corrected gate; promotion still requires
explicit user approval.

VERIFIER ROUND 5 CONFIRMATION (2026-08-25): independent re-run of the
corrected audio-stability gate reproduces the verdict — Q5 PASS (warm max
654.4 ms, zero over-budget samples), Q4 FAIL (warm max 1673.4 ms, six
over-budget samples), baseline PASS (740.5 ms). Cold first turns correctly
separated/documented. Full collection suite: 495 passed / 0 failed /
4 skipped. CONCLUSION RECORDED: Q5 is currently the ONLY candidate passing
all corrected candidate gates; Q4 MUST NOT be promoted. LIMITATION recorded
for Q5: cold-start first audio observed up to 8608 ms by the verifier — Q5
passes the defined WARM-audio gate only; a ≤800 ms from-completely-unloaded
requirement would fail it and remains an open design decision for the user.

## MIG-009 — Packet 5A evidence contracts + opt-in Q5 canary (2026-08-25)

NEW voicebridge/evidence/ (models, registry, adapters, registry_build):
normalized read-only evidence records with stable deterministic IDs, content
hashes, redacted-only excerpts, typed scope/sensitivity/trust/freshness/
citation metadata, and strict read/write/remember separation enforced at
registration and at fetch (read-only adapters cannot declare or expose
write surfaces; non-read-only paths unreachable via fetch()).
Adapters: files/repos/web/reports concrete; browser/inbox/worker/memory
declarative placeholders preserving the same contract for 8B/5B.
NEW voicebridge/canary.py + scripts/canary_run_4d.py: opt-in reversible Q5
canary (intent.canary.q5_enabled, default false; inert when off). Records
warm/cold latency, errors, planner decisions, safety denials, audio
failures and rollback state per turn against the SEPARATE qubit-cand-q5
alias; production alias never loaded/modified (digest observed read-only and
recorded). No promotion path exists.
Config: config/voice.example.yaml documents the flag (default false).
Docs: docs/EVIDENCE_5A.md, docs/CANARY_5A.md.
Tests: tests/test_packet5a.py (17 passed). FIRST LIVE RUN flaw found &
fixed: naive canary load evicted production via VRAM pressure; runner now
uses the deep-tier cycle (restore verified, exit non-zero on failure). Rollback: remove evidence/ +
canary files, revert flag/docs; flag default off already inert.

## MIG-010 — Packet 5B: scoped SQLite/FTS5 personal memory (2026-08-25)

NEW voicebridge/memory/store.py: Mem0-derived (R8) contract store with
core/working/recall/archive tiers, norm-hash dedupe ("hash match" skip),
append-only provenance history, expiry/tombstone/supersedes, explicit
remember/correct/forget/export/purge/what_do_i_know, staged inferred
memories (explicit promote only; secret-shaped candidates refused), scope
isolation, FTS5 search with deterministic LIKE fallback, WAL + write lock
concurrency, PRAGMA user_version migration, private file modes (0600/0700).
Letta explicitly REJECTED (R9 affirmed: no code at pinned ref; no import).
Rollback: delete voicebridge/memory/, tests/test_packet5b.py,
docs/MEMORY_5B.md, revert R8-status/R9-note + MIG-010. DB files are private
user state and are NEVER deleted by rollback.

## MIG-011 — Packet 5C: bounded context compiler + retrieval safety (2026-08-25)

NEW voicebridge/context/compiler.py: deterministic-precedence compiler
(persona > tools > active > core > recall > evidence) reusing the 4B budget
engine; FTS5 retrieval via the 5B store (corrections win, tombstones/staged/
expired excluded), conflict labels, citations, content-free observability
report, TTL cache of redacted excerpts, parallel bounded evidence with
typed partial failures, and structural injection resistance (retrieved text
only inside quoted reference blocks; persona/tools/active immutable).
Optional semantic rerank interface present but DEFAULT OFF (4C L1 never
approved). No new database; runner contract untouched.
Rollback: delete voicebridge/context/, tests/test_packet5c.py,
docs/CONTEXT_5C.md, revert MIG-011. Store/evidence layers (5B/5A) retained.

VERIFIER ROUND 6 (2026-08-25): Packet 5C VERIFIED WITH EXPLICIT LIMITATION
— the bounded context compiler, retrieval safety, and all 20 tests are
accepted. LIMITATION recorded as a PENDING INTEGRATION ITEM: the new
compiler is NOT yet wired into the live chat request path (hot path still
uses the Packet 4B compiler in voicebridge/local_llm._user_content). Per the
verifier this is to be handled in the next approved integration packet —
until then 5C retrieval is dormant in production by design (safe, additive).

MANDATORY SCOPE OF THE NEXT APPROVED INTEGRATION PACKET (user directive
2026-08-25): (1) wire the 5C ContextCompiler into the LIVE chat request
path (replacing or augmenting the Packet 4B compiler in
voicebridge/local_llm._user_content), feeding FTS5 recall + core facts +
bounded evidence into real turns; and (2) add LIVE integration verification
— an end-to-end chat turn whose compiled context provably contains
retrieved records with citations, stays within the model budget, surfaces
omissions/failures, and blocks injection in the running bridge. 5C must NOT
be called "integrated" until that packet passes. Also carry the Q5-canary
flag state forward (inert by default).

## MIG-012 — Packet 5C live integration (authorized, 2026-08-25)
Wired voicebridge/context/compiler.py into voicebridge/local_llm._user_content
(live chat turns): FTS5 recall + core facts + optional bounded evidence +
citations + observability. Flags: intent.compiler.mode 5c|4b (default 5c;
4b restores the Packet 4B compiler), evidence_sources [], observe true.
Tests: tests/test_packet5c_integration.py (5, live-gated) + rollback unit;
tests/conftest.py isolates pytest from the real memory DB.
Rollback: set intent.compiler.mode=4b (no code revert needed); or revert
MIG-012 files. Q5 canary still disabled by default. NOT CALLED INTEGRATED
until independent verification passes.

## MIG-013 — Release-closing acceptance (2026-08-25)
Cleared all five environment skips and added live verification:
- Dashboard live tests now resolve BASE/TOKEN from the real config (Packet 1B
  header auth preserved; token never printed). Real bridge (user's running
  instance) + generated tap-utterance.wav + xdotool/X: status, text-turn,
  voice-turn and Right-Ctrl beep tests PASS live (4/4 when the pre-existing
  dashboard watchdog lock is free; the single suite skip is that transient
  lock contention, not an environment gap).
- 5A repo-adapter test now runs against a real git fixture (init+commit):
  no longer skipped.
- tests/test_release_smoke.py (5): REAL production-model requests (payload
  teed, not mocked) proving memory recall + core facts + citations +
  budget bound + injection guard in live traffic; restart persistence;
  cold/warm; timeout -> typed empty (cloud-fallback contract); and mode=4b
  rollback flag producing the exact old compiler shape.
- Security re-check subset: 161 passed (mitigation layers, broker denies,
  redaction, traversal/SSRF, secrets, injection, evidence read-only).
- Memory store private path: <state_dir>/memory/memory.db in a dedicated
  0700 dir (avoids chmod'ing the shared state root); db/export 0600 verified.
- Q5 canary observed once (flag still OFF): warm/cold latency, planner
  decisions, safety denials on destructive phrases, zero errors/audio
  failures; deep-tier restore succeeded (4.53s); qubit-brain4 restored
  resident @4096 Forever = fallback confirmed.
Acceptance numbers: full suite 569 passed / 0 failed / 1 skip (transient
watchdog lock; dashboard verified 4/4 independently); compileall / pip
check / git diff --check clean.
NOTE: the user's running bridge process predates the 5C integration; it will
load the integrated compiler on its next restart (live restart not performed
without authorization).

## MIG-014 — HyperX capture fix + PTT/webui restoration + release cleanup (2026-08-26)

ROOT CAUSES of the 54 ALSA capture-error groups and dead dashboard:
1. resolve_input_device() returned a raw ALSA "(hw:N,M)" index for the
   Pulse-managed HyperX; PortAudio cannot open it (-9993/-9985). FIX:
   never return raw hw indices — route via the "pulse" aggregate or the
   default source.
2. The Pulse DEFAULT SOURCE had drifted to the HyperX OUTPUT MONITOR
   (alsa_output...iec958-stereo.monitor) — capture opened the loopback and
   delivered exact zeros. FIX: pin_pulse_default_source() pins the default
   to the alsa_input.* mono-fallback at bridge startup (input-only match;
   the output monitor sorts first and was wrongly selected before).
3. The hotkey + webui startup blocks and _emit_hotkey were missing from the
   orchestrator in the working tree (restored from the source of truth):
   PTT/hotkeys now work ("hotkey.active") and the voice dashboard serves
   /api/status on :8890 again.
4. Manual mode exited 0 on EOF stdin (systemd) -> service crash-loop. FIX:
   manual loop waits on the stop event instead of exiting on EOF.
5. Event journal now records the emitting PID (attribution).

PROOF: 10 consecutive real-microphone opens via production MicCapture with
ZERO errors (tests/test_mic_health.py) AND 10 real Right-Ctrl PTT turns
through the live bridge: 10 feedback.beep, 1 mic.utterance, 4 empty (quiet
room), mic.capture.error=0, mic.capture.timeout=0. Service active with
NRestarts=0; dashboard up; hotkey active. Full suite 576 passed / 0 failed /
0 skipped.

MIG-014 ADDENDUM — live audio runtime fixes (2026-08-26):
1. _speaker AttributeError: VoiceOrchestrator.__init__ never initialized
   self._speaker, but the barge-in _monitor thread dereferences it on every
   frame -> repeated AttributeError killing barge monitoring (live). FIX:
   self._speaker = None initialized before any monitor thread can start
   (the _barge_allowed guard then safely returns True).
2. Playback -71 (EPROTO): sd.play() on the default output intermittently
   failed with "Protocol error [ALSA error -71]" under PipeWire. FIX:
   bounded 3x retry (0.2s) on the same sink before logging
   playback.device_unavailable.
LIVE PROOF (post-restart): 4 spoken chat turns (tts.speak=5, barge_in.active=6
surviving) + 10 Right-Ctrl PTT turns (feedback.beep=10, hotkey.active=1);
journal shows ZERO AttributeError/ALSA/Protocol/EPROTO/playback/Traceback;
playback.device_unavailable=0, mic.capture.error=0, mic.capture.timeout=0.
Full suite: 576 passed / 0 failed / 0 skipped.

MIG-014 CLOSING — dashboard lock race eliminated + PTT evidence (2026-08-26):
- tests/test_dashboard_stress.py _cycle_or_skip now RETRIES across the
  pre-existing dashboard_stress --watch cycle (3 x 45s lock waits + backoff)
  instead of skipping on a transient lock overlap. Full suite is now
  deterministically 576 passed / 0 failed / 0 skipped (verified twice
  consecutively).
- Real PTT evidence (restarted service): 10 Right-Ctrl PTT turns ->
  feedback.beep=10, mic.utterance=9, mic.utterance.empty=1,
  mic.capture.error=0, mic.capture.timeout=0; 5 spoken chat turns ->
  tts.speak=14, barge_in.active=10 (barge monitor survived, no AttributeError);
  playback.device_unavailable=0; journal free of
  AttributeError/ALSA/Protocol/EPROTO/Traceback since restart.
- HONEST LIMITATION (reserved manual check): the HyperX is a closed in-ear
  headset whose boom mic cannot acoustically capture its own ear-cup
  speakers (measured mic RMS ~1e-06 during loud playback) — genuine spoken
  WORDS into the USB mic require a human speaker. The 10 PTT turns captured
  real mic audio (9 utterances), and speech playback + barge monitoring were
  proven via spoken chat turns; the "human speaks into the mic and hears the
  reply" step remains a documented manual/live acceptance check.

## MIG-015 — Packet 6B: baseline provenance contract + VRAM unit fix (2026-08-26)

Status: **IMPLEMENTED** — evaluation/promotion-gate state and reporting
changed; no live user config, service, model, or audio state was modified.

Decision:
- **Immutable baseline provenance.** Every Packet 6B baseline saved by
  `scripts/eval_gate_6b.py --save-baseline` now carries `baseline_metadata`:
  packet, mode, `generated_at`, `git_commit`, `git_tree_dirty`, `runner`
  (alias/url/num_ctx/keep_alive/digest), `environment` (model digest,
  Modelfile SHAs, Ollama version, hardware — required for live mode), and
  `units`. The score fields remain backward-compatible with `PromotionGate`.
- **Gate trust boundary.** `PromotionGate.evaluate` now returns NEEDS_REVIEW
  (never APPROVE/REJECT) when the baseline provenance is missing/malformed,
  the baseline was generated from a dirty tree, or the baseline git commit
  equals the candidate's git commit (same-code baseline is not an independent
  release gate). An independent release gate requires a baseline generated
  from a known-good PRIOR, CLEAN commit (human/CI action — not fabricated).
- **VRAM units.** Ollama `/api/ps` reports `size`/`size_vram` in BYTES.
  `voicebridge/benchmarking/harness.py::resident_state` now preserves raw
  bytes under `size_bytes`/`size_vram_bytes` and reports correctly converted
  MiB under `size_mib`/`size_vram_mib` (bytes/1048576); a byte value is never
  stored under a MiB name. The live report also surfaces TOTAL GPU usage
  (`nvidia-smi` used/total) so total GPU use is never hidden behind model
  size, and asserts the resident 4B model stays under the 5 GiB model-VRAM
  ceiling (plan fast-tier gate: <= 6.5 GiB total GPU on the 8 GiB GPU).
- **Private evidence.** The gate rejects an empty required private set
  (`--set all` -> NEEDS_REVIEW even without a baseline). Reports show each
  set's case count and family coverage. Real private coverage today is 3
  quality cases (1 correction + 2 accepted conversations); private
  safety/resource/tool coverage and private poisoning are 0/0 and are
  represented by the base set only — full private family coverage requires
  real user task outcomes (documented human-data prerequisite; nothing is
  fabricated).

Rationale: a release gate that can approve on an empty, unprovenanced, or
same-code baseline is unsafe; a byte value labelled `_mib` can hide a real
VRAM regression.

Tests: `tests/test_packet6b.py` (42) — provenance validation (missing/
malformed/live-env/dirty-tree/same-commit), unit correctness, missing private
evidence, rollback/inert store, bad-lesson removal, poisoning, no automatic
promotion, webui outcome flow, CLI exit codes; `tests/test_packet4a.py`
byte->MiB conversion + 5 GiB ceiling. Full suite: 0 failed / 0 skipped.

Rollback: revert the baseline-provenance block in `gate.py`/CLI, restore
`harness.py::resident_state` byte-valued fields, delete the committed
baselines (revert commit `cf585bf`), and remove the outcome-recording hook if
desired. `task_outcomes.jsonl` is inert when the store is removed.

## MIG-016 — Packet 7B: typed feature-flag registry + ops hardening (2026-08-27)

Status: **IMPLEMENTED** — additive defaults and new operational tooling; no
live user config, service, model, timer, or audio state is modified by any
command without an explicit `--apply` (which keeps a timestamped `.bak`).

Decision:
- **Additive config defaults.** `voicebridge/config.py::DEFAULTS` now declares
  `intent.router.{enabled,l1_enabled,embed_model}`, `intent.canary.q5_enabled`,
  and `intent.compiler.{mode,evidence_sources,observe}`. Every value equals the
  code-level fallback already used by `router/pipeline.py`,
  `intent/classifier.py`, `canary.py` and `local_llm._configured_compiler`, so
  effective behavior is unchanged; the flags registry has an explicit single
  source of truth instead of hidden per-reader defaults.
- **`voicebridge/feature_flags.py`** registers each reversible Phase 0–7A gate
  (`broker`, `compiler_5c`, `canary_q5`, `router_l0`, `router_l1`,
  `llm_classifier`) with config path, default, rollback value, and category.
  `apply_flag`/`rollback_flag` write the config atomically and keep a
  timestamped `.bak`; they never touch the state dir (memory/reports/cursors).
- **CLI.** `voicebridge flags list|get|set|rollback` is dry-run by default and
  only writes with `--apply`. `voicebridge flag-drill` runs the hermetic
  rollback drill. `scripts/rollback_drill.py` toggles every flag on a throwaway
  config and proves user data (memory db, report manifest, cursor file) is
  byte-identical before/after.
- **Smoke + reports + reconcile.** `tests/test_smoke.py` covers
  startup/dashboard/task/cancellation/voice paths hermetically (a missing
  piper voice is a DEGRADED skip, never a pass); `scripts/run_tests.py smoke`
  runs it; `scripts/change_report.py` emits a change report; `config_doctor.py
  --reconcile` cross-checks flags/config/docs/ops for Phase 0–7A.

Rationale: Phase 7 acceptance requires every feature behind a flag with a
one-command rollback, smoke coverage for the five release paths, a change
report, and docs/config/service reconciliation.

Tests: `tests/test_packet7b.py` (16) — registry completeness/type/defaults,
resolution + env-override key format, atomic write + backup + state
preservation, rollback drill (script + CLI), smoke category/marker/paths/live-
service isolation, change-report generation + secret scan, doctor reconcile
PASS, CLI list/get/dry-run/apply/drill. `tests/test_smoke.py` (6).

Rollback: revert this commit and delete the `.bak` files only if the operator
explicitly asked; flag `set --apply` writes always keep a backup, so restoring
a `.bak` over `voice.yaml` is the documented one-command rollback. No state
data is deleted by any 7B command.

## MIG-017 — Packet 7C: download-and-run distribution, portable-state export/import, trusted-device enrollment (2026-08-27)

Status: **IMPLEMENTED** — additive modules + read-only CLI surfaces; no user
config, service, timer, model, browser, or audio state is modified by any
command. Restore (`import-state --apply`) writes only to the state dir and
always keeps a pre-restore backup; nothing is enabled or deleted silently.

Decision:
- **Distribution surface** (`voicebridge/distribution.py`): versioned manifest,
  typed hardware/resource doctor, model-artifact record (live digest, never a
  fake checksum), launcher argv, upgrade/uninstall behavior plans. Ubuntu
  x86_64 is the only supported target; Windows/macOS are documented future
  targets — no universal-binary claim.
- **Portable-state contract** (`voicebridge/portable.py`, schema v1): bundles
  memory, lessons/preferences, reports, non-secret config, model metadata, and
  migration history; EXCLUDES tokens, cookies, Chromium profiles, CDP URLs,
  active approvals, credentials, machine-specific audio/GPU paths, and the
  secret-bearing `webui`/`openclaw`/`logging` config sections.
- **Audited encryption — no home-grown crypto, no new dependency.** Key
  derivation = PBKDF2-HMAC-SHA256 (RFC 8018, stdlib); cipher = AES-256-CBC via
  the system OpenSSL CLI (pinned ≥ 3.0.0, recorded in the bundle header);
  integrity = HMAC-SHA256 (RFC 2104) encrypt-then-MAC. Deterministic
  wrong-passphrase/tamper rejection. Recorded in `docs/UPSTREAM_REUSE_7C.md`
  R16.
- **Restore safety**: MAC verified before any write; atomic per-file staging
  (tmp + fsync + os.replace); pre-restore backup preserved; failure rolls back.
  `--apply` requires explicit device enrollment.
- **Trusted-device enrollment** (`voicebridge/device.py`): never automatic;
  re-checks hardware/model/deps/capabilities; never copies browser profiles or
  secrets; `device.json` is 0600.

Rationale: Phase 7 requires a verified download-and-run distribution, an
audited encrypted portable-state bundle that excludes secrets/browser-session
material, atomic rollback, and explicit new-device enrollment.

Tests: `tests/test_packet7c.py` (30) — manifest, doctor typed states
(OK/CPU-only/missing-*), model artifact metadata, launcher/upgrade/uninstall
plans, export/import roundtrip + sanitization, wrong-passphrase, corrupted
bundle, bad magic, unsupported schema, low-disk, dry-run no-write, atomic
apply + backup, restore rollback, bundle-info, device profile/enroll/
require/compat, CLI surface. All hermetic (tmp state dir, never the real one).

Rollback: revert the Packet 7C commit; remove the new modules/CLI/docs/tests.
No pre-existing state is altered; any `import-state --apply` keeps a
pre-restore backup under the state dir that can be copied back.

## MIG-018 — Packet 7C remediation: audio settings contract, model verification, distributable completeness (2026-08-27)

Status: **IMPLEMENTED** — additive config key + new modules + read-only CLI;
no user config, service, timer, model, browser, or audio state is modified by
any command. Audio selection writes are pure config writes with a timestamped
backup; nothing is restarted, enabled, or recorded.

Decision:
- **Audio settings contract** (`voicebridge/audio/settings.py`): enumerate
  inputs/outputs by stable name/role/backend; select by name; reset to OS
  defaults; persist by NAME (never numeric PortAudio indices or raw ALSA
  paths); never guess when a selected device disappears (typed MISSING with
  "no silent fallback"); report OK/DEGRADED/MISSING/PERMISSION_DENIED. Additive
  `mic.device_name` default. Preserves the HyperX mono-mic + default-output
  rules. No service restart or recording enable on settings change.
- **Model metadata verification** (`voicebridge/distribution.py`): the model
  digest/revision is the ACTUAL Ollama digest (`/api/show` header, fallback
  `/api/tags`); the record fails closed (MISSING, revision/checksum None) when
  the digest cannot be obtained — never a fabricated placeholder. Records
  license + source provenance. `artifact_checksum`/`verify_artifact` provide
  SHA-256 verification for every downloaded artifact with typed
  OK/MISSING/FAIL (tampered).
- **Distributable completeness** (`distribution.distributable_manifest`):
  explicitly lists launcher/installer/config-template/systemd-template/
  metadata with SHA-256; install/launch proven relocatable (outside repo and
  developer home); no universal-binary claim.
- **Digest fallback** (`runner_contract._fetch_digest`): adds `/api/tags`
  fallback for Ollama versions that omit the `X-Ollama-Digest` header.

Rationale: the 7C verifier required a user-facing audio settings contract,
fail-closed model metadata, checksum verification, and a provably complete,
relocatable distributable.

Tests: `tests/test_packet7c_audio.py` (30) — enumeration, selection, reset,
persistence, hot-plug, missing, permission, invalid selection, reconnect,
model digest fail-closed/OK, artifact checksum OK/tampered/missing,
distributable manifest + external install/launch, CLI. Full suite 762 passed /
0 failed / 0 skipped.

Rollback: revert the remediation commit; restore `mic.device_name` default if
present. No pre-existing state is altered; audio `.bak` backups and pre-restore
backups preserve prior config/state.

## MIG-019 — Packet 7C audio-identifier audit fix (2026-08-27)

Status: **IMPLEMENTED** — normalized stable identifiers only; no user config,
service, timer, model, browser, or audio state is modified by any command.

Decision:
- **Normalize device identities.** `voicebridge/audio/settings.py` now builds
  every stable identifier (`role:name:backend`) from a NORMALIZED name:
  raw ALSA `(hw:N,M)` suffixes and naked `hw:N,M` tokens are stripped, bare
  numeric PortAudio indices are never a name, and trailing separators are
  trimmed. `raw_name`/`index` remain as report-only diagnostic fields and are
  never exposed by the CLI/JSON (`audio list --json` returns only stable
  fields) or persisted.
- **Persist-time guard.** `write_audio_config` rejects any value containing an
  `hw:` path or a numeric index (`InvalidSelectionError`) before writing;
  legacy numeric index keys are cleared.
- **Matching.** `_match` matches by full stable id, name (exact/substring),
  role-prefixed name (`input:pulse`), or a full `role:name:backend` id; a bare
  backend name (`ALSA`) is a last-resort identity match so it never shadows an
  explicit device name. Numeric selectors never match.
- **Kept behaviors.** OS defaults on first run, explicit selection, explicit
  reset-to-default, MISSING (no silent fallback) when a selected device
  disappears, HyperX mono mic + empty output pins, and no service restart or
  recording during settings changes.

Rationale: the 7C verifier found raw ALSA `(hw:N,M)` paths leaking into the
stable identifier and persisted config. Normalizing at the source and guarding
the write makes the contract stable and auditable.

Tests: `tests/test_packet7c_audio.py` (33) — stable-id/name normalization,
persisted-config and stable-ID audit (no `(hw:`, `hw:`, numeric index),
selection by name/substring/stable-id/backend, hw-path and numeric selector
rejection, reset, persistence, hot-plug, permission, no-restart/no-recording.
Full suite 765 passed / 0 failed / 0 skipped.

Rollback: revert this commit; restore the pre-normalization
`voicebridge/audio/settings.py`. Audio `.bak` backups preserve prior config;
no state data is altered.

## MIG-020 — Packet 7D: provider/profile registry, dashboard/CLI parity, Qubit branding (2026-08-27)

Status: **IMPLEMENTED** — additive modules + CLI + authenticated dashboard
endpoints; no user config, service, timer, model, browser, or audio state is
modified by any command. Profile operations are dry-run by default, atomically
written with a `.profile.*.bak` backup, and reversible; branding apply copies
config/state/data to a backup dir and never moves/deletes live state.

Decision:
- **Provider/profile registry** (`voicebridge/profiles.py`): versioned
  manifests for brain (reasoning model), stt, tts provider, voice artifact,
  and audio devices. Every manifest records source+revision, license,
  checksum/digest, hardware/language, VRAM/RAM, latency, quality/safety
  evidence, and rollback target. Gates (manifest, artifact/digest,
  hardware/language, 5 GB VRAM ceiling) fail closed and leave the previous
  profile active. One approved resident generative model by default; defaults
  unchanged (qubit-brain4, sherpa CPU, Piper, en_US-amy-medium, OS-default
  audio).
- **CLI** (`voicebridge profile list|show|check|install|select|reset|upgrade|
  remove|rollback`, dry-run by default, JSON output) and **branding**
  (`plan|verify|rollback`).
- **Dashboard** (`/settings` + `/api/profiles*`, `/api/audio*`,
  `/api/permissions`, `/api/device*`, `/api/export-state`, `/api/branding`):
  beginner onboarding + advanced mode; every control calls a real authenticated
  backend endpoint; no frontend-only source of truth; typed unavailable/
  degraded/denied/partial/failed states; escaped untrusted text; no secret/
  path/credential leakage.
- **Independence**: changing the voice does not change the TTS provider, model,
  or audio devices; provider changes never alter memory/reports/approvals; no
  service restart or recording enablement during settings changes.

Rationale: Packet 7D makes Qubit Voice Agent beginner-friendly while providing
advanced, fully reversible customization through CLI + dashboard, and prepares
the `openclaw-voice-bridge -> qubit-voice-agent` branding migration separately.

Tests: `tests/test_packet7d.py` (39). Full suite 804 passed / 0 failed /
0 skipped. clean-venv PASS; check_deployment 0 fail; compileall + pip check +
git diff --check OK.

Rollback: revert this commit; remove the 7D modules/CLI/routes/page/tests. No
pre-existing state is altered; `.profile.*.bak` and branding backup dirs
preserve prior config/state.

## MIG-021 — Packet 7D remediation: real branding migration, complete provider gates, dashboard parity (2026-08-27)

Status: **IMPLEMENTED** — corrects the 7D implementation gaps found by
verification. No user config, service, timer, model, browser, or audio state is
modified by any command; the live repo/config/state stay on
`openclaw-voice-bridge` until an operator runs the migration.

Decision:
- **Real branding migration.** `branding_plan --apply --backup-dir <dir>`
  rewrites the repository files (package metadata, systemd unit content AND
  filename rename to `qubit-voice-agent.service`, launchers, docs, config
  template) atomically with a full backup. `branding_verify` scans every file
  and runtime path and returns VERIFIED / PARTIAL / PLANNED (exit 0 only on
  VERIFIED). `branding_rollback` restores every repo file + runtime
  config/state/data, renames the unit back, and removes compat aliases. The
  live config/state/data path migration is an EXPLICIT separate step
  (`--migrate-paths`); a plain repo apply or staged test never touches real
  paths (this prevents the path bug where a test renamed the real
  ~/.config/openclaw-voice-bridge).
- **Complete provider gates.** STT/TTS/VOICE artifacts are resolved to their
  on-disk files and sha256-verified (or reported runtime-only/unmeasured).
  Latency uses recorded measured values or a bounded live synthesis (a 0.0
  unmeasured value is PARTIAL, never a silent pass). Quality/safety evidence
  files must exist and be non-empty (validated). A replacement (non-default)
  profile must pass EVERY gate; PARTIAL blocks activation (only the verified
  default may run with a caveat).
- **Dashboard parity.** Added real `/api/import-state` (preview + apply,
  enrollment + passphrase + integrity gates), `/api/profiles/upgrade` (a real
  re-verify + registry-metadata refresh), real non-default removal, a
  confirmation dialog gating every side effect (maps to the backend confirm
  flag), and a typed-state interpreter that never shows generic success on
  unavailable/degraded/denied/partial/failed. Frontend no longer contains a
  hardcoded "demo" removal.
- **Path bug fixed.** `remove_profile` passes the caller's config to
  `_registry_path(cfg)` so a temp/test config can never write the real default
  registry.

Rationale: the 7D verifier rejected the incomplete branding (verify always
PLANNED, apply only backed up, rollback only one file, package manifest still
old), incomplete gates (empty checksums, 0.0 latency, unvalidated evidence,
non-brain artifacts unchecked), dashboard gaps (no confirmation/import/real
upgrade/real remove, generic success), and the real path bug.

Tests: `tests/test_packet7d.py` (44) — including real branding apply->verify
VERIFIED->rollback on a staged repo, real import preview+apply+wrong-pass,
real upgrade (manifest refresh), real removal, confirmation gate, and
no-demo/no-frontend-state assertions. Full suite 809 passed / 0 failed /
0 skipped.

Rollback: revert this commit; the 7D code remains additive and reversible.
`.profile.*.bak` backups and branding backup dirs preserve prior
config/state; live config/state/data are untouched by all commands.

## MIG-022 — Scarlett 2i2 + AT2020 audio reconfigure (2026-08-27)

Status: **IMPLEMENTED** — live user config + repository code/docs updated to the
currently connected Focusrite Scarlett 2i2 4th Gen with an Audio-Technica
AT2020 XLR condenser. Authorized explicitly by the user; the bridge service was
restarted.

Decision:
- **Effective config** (`~/.config/openclaw-voice-bridge/voice.yaml`, user-local,
  not committed): `mic.device_name: 'Scarlett 2i2 4th Gen: USB Audio'`
  (stable name, never a transient PortAudio index; `mic.device: null`),
  `mic.channels: 1` (mono, loudest channel = XLR input 1), `mic.gain: 3.0`,
  empty `audio.output_device*` (follow the Scarlett iec958 default sink).
- **Pulse default source** pinned to
  `alsa_input.usb-Focusrite_Scarlett_2i2_4th_Gen_...analog-surround-40` so the
  bridge capture (pulse aggregate) routes to the AT2020 input.
- **Pin matching fix** (`voicebridge/audio/mic.py`):
  `pin_pulse_default_source` now matches the stored PortAudio display name
  ("Scarlett 2i2 4th Gen: USB Audio") to the pactl source name
  ("..._Scarlett_2i2_4th_Gen_...") via case/space/underscore/punctuation-
  insensitive token comparison, ignoring generic words (audio/usb/analog/...).
  This restores startup re-pinning for the new hardware.
- **Scarlett gain**: the Scarlett "Line In 1 Gain" hardware control was raised
  to 62/70 (≈61 dB) so the AT2020 level peaks healthily (captured peak ~0.05).
  48V phantom power is a physical front-panel switch on the Scarlett and cannot
  be toggled by software; it must be enabled by hand for the AT2020.
- **Docs**: `docs/AUDIO_SETUP.md` rewritten HyperX → Scarlett 2i2 + AT2020
  (hardware, live config, critical rules, gain/48V guidance, pinning commands).

Tests: `tests/test_mic_resolve.py` (6) — includes a new Scarlett
PortAudio-name→pactl-name pin regression; `tests/test_packet7c_audio.py` +
`tests/test_packet7d.py` (84 combined) green; full suite 810 passed / 0 failed /
0 skipped. Config doctor `--runtime`/`--reconcile` report audio combo OK (mono
mic, sane gain, empty output pins) with 12 devices enumerated.

Verification (live): bridge restarted and active; `mic.pin_source` = the Scarlett
source; ZERO capture/timeout/playback errors in the event journal since restart;
a real 2-channel capture shows the AT2020 signal on input 1 (ch1 peak ~0.05,
ch2 silent); ASR pipeline runs (parakeet/CPU) and returned empty only for a
silent test clip (a spoken PTT clip is required from a human for a final WER).

Rollback: revert this commit (restores mic.py + docs); restore the user config
`device_name` to the previous value and `pactl set-default-source` to the prior
source. The Scarlett gain change is a hardware control, reversible with `amixer`.

## MIG-023 — Hotkey xinput listener robustness + Right-Ctrl PTT fix (2026-08-27)

Status: **IMPLEMENTED** — repository code + docs updated; the bridge was
restarted (authorized) so the fix is live.

Decision:
- **Root cause of "rctrl doesn't start":** the `xinput test-xi2 --root` child
  could go WEDGED — process alive but its stdout never delivered a line. The
  old `ensure_alive` only restarted after the loop thread exited, which a
  wedged child never does, so the hotkey silently died until a full service
  restart. (Separately, a press DID capture but ASR returned empty when the
  AT2020 was silent — no 48V phantom.)
- **Fix** (`voicebridge/hotkeys.py`): `_xinput_loop` now uses a non-blocking
  `select` read with a heartbeat (`_last_xinput_line_ts`) instead of a blocking
  `for line in stdout`; `ensure_alive` restarts the listener when a freshly
  started child has produced NO output within 30s (stale) or when the loop
  exited. `_restart_xinput` kills any stale child and starts a fresh one.
  Added `_stopped`, `_xinput_start_ts`, `_xinput_dead` init in `__init__`/
  `_start_xinput`.
- **Tests** (`tests/test_hotkeys.py`, 5): heartbeat tracked, stale child
  restarted, healthy-idle child not restarted, dead-loop restarted, and the
  non-blocking loop parses RawKeyPress/Release (keycode 105 = ctrl_r) into
  ptt_start/ptt_end.

Tests: `tests/test_hotkeys.py` (5) + mic/packet7c/7D (88) + full suite 815
passed / 0 failed / 0 skipped (live voice-turn test passed once the bridge
finished restarting; the one earlier flake was a restart race, not a code
regression).

Live: bridge restarted (PID 88914), `hotkey.active` + `mic.pin_source`
(Scarlett) logged at startup; a fresh `xinput test-xi2 --root` child attached.

Rollback: revert this commit; restore the blocking `_xinput_loop` if needed.

## MIG-024 — Packet 8M: optional GENOME-compatible semantic memory (2026-09-05)

Status: **IMPLEMENTED (hermetic)** — disabled by default; no live service,
timer, model, audio, browser, or user-config change. Opening any existing
`MemoryStore` (including the live `memory.db` during the CLI status probe)
applies the additive store migration below.

Decision:
- **Store migration 2 (additive, nullable):** `memories` gains `valid_from
  REAL`, `valid_until REAL`, and `believed_by TEXT` + `idx_mem_valid`. Existing
  rows are untouched (NULL windows = always valid). Reversible by `git revert`;
  the columns are unused unless the optional semantic adapter is enabled.
- **Store migration 3 (Packet 8M repair):** scope-qualified dedup identity.
  The durable dedup key becomes `mkey = scope\x00<content>` so the identical
  fact stores independently in different scopes (repairs the global
  `UNIQUE(mkey)` collision that previously made cross-scope storage of the
  same text impossible). Existing rows are rewritten one-to-one
  (`UPDATE memories SET mkey = scope || char(0) || mkey`), guarded against
  re-application; `norm_hash` keeps the bare content hash; concurrent
  same-scope inserts handle the `UNIQUE` race as a Mem0 duplicate-skip.
- **New surface (all disabled/opt-in):** `voicebridge/memory/semantic.py`
  (`SemanticMemory`), `voicebridge/memory/journal.py` (hash-chained journal +
  replay/verify), `memory` CLI command, and `memory.semantic` config defaults
  (`enabled: false`, `provider: none`). No new dependency, no MCP/HTTP server,
  no second store; ingest is LLM-free by construction.
- **Repairs from the rejected audit (2026-09-05):** secret validation/redaction
  at every mutation boundary and in history/recall/explain diagnostics;
  origin-bound authority (correct/forget/purge/promote/invalidate_belief carry
  `requested_by` with trust >= the target's trust; import never claims
  `source=user`; unknown sources fail closed); temporal validation (finite
  confidence/timestamps, `valid_until` after `valid_from`, sane invalidation
  times); corrupt/newer `semantic.json` is a typed DEGRADED/newer result and is
  never silently reset (state writes are atomic temp+fsync+rename, 0600);
  replay re-runs validation/authority; default status never contacts Ollama.
- **Rejection #2 repairs (2026-09-05):** (a) External-content boundary — raw
  web/tool/model/observed text is never stored, journaled, FTS-indexed, or
  returned; it is stored only as a bounded non-recoverable reference
  `[external:<source>:<sha256[:20]>:<label?>]`, so private page/tool/model text
  cannot appear in journals, logs, prompts, diagnostics, recall/explain/CLI
  output, or exported reports. User-owned (source=user) memory stays raw;
  import defaults to external-origin unless an explicit
  `trusted_user_import=True` restore opts in. Replay reproduces the stored
  reference exactly (integrity never claimed for content intentionally not
  kept). (b) State-config validation — `load_semantic_state`/`write_semantic_state`
  validate every value (boolean `enabled`, known provider, finite
  `0 < timeout_s <= 60`, integer `0 < top_k <= 50`, valid model id, valid http(s)
  url, no unsupported fields, supported schema); malformed values are a typed
  DEGRADED/config-invalid result and are never coerced; CLI refuses mutation
  without `--confirm`. (c) Durability — state writes surface file-fsync, rename,
  and directory-fsync failures as typed `SemanticStateError` (directory fsync is
  no longer swallowed), with the temp file cleaned up on pre-rename failure.
- **Rejection #3 repairs (2026-09-05):** (a) no remaining leak path — `correct()`
  errors never echo caller-supplied text (store.correct no longer includes the
  old text and the adapter raises a non-recovering message when a target is
  absent); belief `entity`/`fact_type` are validated as bounded single-token
  identifiers (`[A-Za-z0-9._-]`, <= 32 chars) so raw prose cannot be smuggled
  through them; sentinel tests scan every persisted file and CLI/printed output
  in the state directory. There is no MemoryStore-to-semantic record-copy
  migration; SemanticMemory wraps the same authoritative store and
  external-origin text is only ever a bounded reference. (b) The embedding
  provider URL is loopback-only per the documented policy (plain http to
  `127.0.0.1`/`::1`/`localhost`); https and non-loopback hosts are rejected on
  both state load and adapter `set_provider`.
- **Rejection #4 repairs (2026-09-05):** parent-directory creation failures
  (mkdir PermissionError, read-only filesystem, invalid path, disk-full) are
  typed `SemanticStateError`; directory-fsync failure after `os.replace()` is
  reported explicitly as durability-UNCERTAIN (`SemanticStateError.
  durability_uncertain=True` + `state_path`, CLI `durability_uncertain` +
  `recovery` fields, exit 2) with a recovery/rollback contract and the previous
  state never claimed preserved in that case; unknown fields are rejected by
  `write_semantic_state()` (same policy as load).
- **Runtime selection** lives in `<state_dir>/memory/semantic.json` (state,
  not the live `voice.yaml`); `memory enable|disable|set-provider` are dry-run
  unless `--confirm`.
- **Provenance:** GENOME pinned at `ea76cf924b2382499a158b8c7ad8a815913c962f`
  (Apache-2.0) — see `docs/UPSTREAM_REUSE.md` R23 and
  `THIRD_PARTY_NOTICES.md`.

Tests: `tests/test_packet8m.py` (focused) + affected 5B/5C/portable regressions.
Rollback: `git revert` of the 8M commit; `memory disable`; remove
`semantic.json`/journal; the store remains fully functional without the columns.
The hotkey fix is purely in-process and reversible by restarting the service.
