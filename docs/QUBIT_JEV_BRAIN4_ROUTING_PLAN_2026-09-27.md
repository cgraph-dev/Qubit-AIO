# Qubit × Jev optional routing layer for `/qubit-chat` — Implementation Plan (rev 3, verified ×2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optionally integrate Jev (TypeSafe AI System One typed-decision model) as a *disabled-by-default* decision/routing advisor for `/qubit-chat` and the voice path, so the existing interpretation layer can ask Jev "local or API, which toolset/skill/plugin/MCP, confirm or refuse, clarify, or not enough context" — while Brain4 remains Qubit's local conversational model and Qubit's one authority (broker / policy / audit / confirmation / e-stop) stays above every decision.

**Architecture:** Jev is a stateless HTTP classifier, never an executor and never a model that "trains" Brain4. A new `voicebridge/jev/` overlay package turns a bounded, redacted `TurnIntent` (built from the *existing* `voicebridge/intent/classifier.py` / `voicebridge/context_policy.py` signals) into an OpenRouter-Decisions-style request, validates the typed reply against a versioned schema, and returns an advisory `JevDecision`. It is consulted only when the deterministic interpretation flags uncertainty, and its output can only *narrow* (deny, clarify, recommend escalation, add advisory catalog hints); it can never widen privilege or bypass confirmation/e-stop. Tool/skill/plugin/MCP selection stays with Brain4 and the canonical broker.

**Tech Stack:** Python 3.12 (`.venv`), stdlib `urllib` + `json` (matching the in-tree precedent `qubit_runtime/evals/compaction/jev_arm.py`), `voicebridge/config.py`, `voicebridge/providers/resolver.py`, `voicebridge/broker.py`, `voicebridge/logging_setup.log_event`, `qubit_runtime/agent/redact.py::redact_sensitive_text(force=True)`, pytest via `scripts/run_tests.sh`, Vite/`tsc` + Vitest for the dashboard.

**Spec:** This document is spec + plan. It is grounded in the live code at the exact symbols cited below, plus `docs/EXECUTION_PLAN.md`, `docs/PARITY.md`, `docs/DECISIONS.md`, `docs/AGENTS.md` (repo rubric), and the in-tree precedent `qubit_runtime/evals/compaction/jev_arm.py`. Jev product facts are from `https://jevtypesafe.org/` and `https://jevtypesafe.org/faq/`.

---

## Revision note — what re-verification changed (v1 defects, now fixed)

This plan was adversarially re-checked against the code. The following v1 errors were found and are corrected throughout this revision:

1. **Tool routing was unreachable from the chat turn.** `chat_transport.turn()` → `agent.turn()` calls `providers.gateway.run_role(...)` exactly once and **executes no tools**; multi-step tool work runs through `agent.run_session()` (called only from the orchestrator task dispatch). v1 implied the chat turn had a tool catalog Jev could restrict. **Fixed:** routing is split into a *conversational path* (`CT.turn`/`stream_turn`) and a *task path* (`run_session` dispatch); `select_*` are advisory hints, and only `request_confirmation` is enforceable everywhere (via the broker).
2. **No toolset parameter exists** on `voicebridge/agent.py::turn` (`cfg, text, *, session, timeout_s, estop_engaged, model, role, profile_id, reasoning`) or `run_session` (`…, execute_tool, max_steps, …`). "Restrict the offered toolset" is therefore not implementable without changing the runtime/planner. **Fixed:** advisory by default; enforcement is an explicit, separately-approved change, not assumed.
3. **Escalation target semantics were wrong.** `context_policy.escalation_target()` returns `None` (there is **no** "resolver default") unless `agent.context.escalation_model` is pinned, and `_escalation_route()` is only reached when `decision.requires_escalation` is true (window overflow). Jev's `route_api` is a **new** trigger. **Fixed:** `route_api` calls the canonical resolver explicitly; `{}` target removed; no target ⇒ typed refusal.
4. **Missing call sites and a double-evaluation bug.** Escalation handling exists in `turn` (`:820`), `retry_last` (`:1076`), `stream_turn` (`:1154`) and `stream_preflight` (`:1237`); v1 omitted `retry_last` and missed that `stream_turn` calls `stream_preflight` (so a naive Jev hook would call Jev twice per streamed turn). **Fixed:** single evaluation per turn, cached and threaded.
5. **Wrong "initial interpretation" seam.** The real cheap interpretation is `voicebridge/intent/classifier.py::Classifier.classify() → Intent` (voice) and `voicebridge/context_policy.decide_context()` (chat). v1 invented a new signal set. **Fixed:** reuse the existing `Intent`/`ContextDecision`.
6. **Voice path misidentified.** Voice conversation runs in `orchestrator._handle_conversation` (`:2361`, decision at `:2434`): local reply first, then perplexity/deepseek fallback gated by `orchestrator._cloud_egress_allowed` (`FrontierPolicy`) — not `chat_transport.turn`. **Fixed:** integrate at that decision and respect the frontier egress gate.
7. **Benchmark location was wrong.** Root `benchmarks/` exists with a `voicebridge.eval-corpus/1` corpus (`benchmarks/corpus/{acceptance,heldout}_cases.json`) and baselines (`benchmarks/eval_baseline_6b.json`); `qubit_runtime/evals/` is vendored and clobbered by a rebase. **Fixed:** extend the existing corpus schema (rev 3 further moved the runner into `voicebridge/benchmarking/`; see item 19).
8. **Layering violation.** v1 planned to reuse `estimate_tokens`/`fake_asker` from vendored `qubit_runtime/evals/compaction/jev_arm.py` in production. **Fixed:** production reuses `voicebridge/context_policy.CHARS_PER_TOKEN` for a text estimator (the `estimate_tokens(messages)` helper takes a message list); fakes live in `voicebridge/benchmarking/`/`tests/`.
9. **Legacy-brand leak in the precedent.** `jev_arm.py:437` sends `HTTP-Referer: https://github.com/NousResearch/hermes-agent`. **Fixed:** never copy it; use a Qubit-owned/absent referer and add a branding test.
10. **Triple-negative config.** v1 defaulted `privacy_mode=true` on top of `enabled=false`, making enablement confusing. **Fixed:** one off switch (`enabled=false`); `privacy_mode=false` default; `qubit jev enable` validates endpoint + key and reports what is missing.
11. **Redundant gates.** `local_only` vs `api_escalation_allowed` duplicated. **Fixed:** `api_escalation_allowed=false` is the global owner gate; per-session "local only" maps to the existing `model_profile_local_only`.
12. **Raw-key validation invented.** **Fixed:** reuse `providers/resolver.py:94 is_secret_ref()` and `:98 looks_like_raw_secret()`.
13. **Scope leakage.** `CT.turn` is also called by `dashboard_ops.py:1693` (`role="coding"`), `session_companion.py:96`, and `gateway/openclaw_handlers/sessions.py`. **Fixed:** `agent.jev.roles=["chat"]` default allowlist.
14. **Cancellation omitted.** **Fixed:** the Jev call honors the turn's `cancel` callable and the e-stop latch.
15. **Startup/doctor network risk.** **Fixed:** lazy construction; `doctor` reports config only (live probe only with `--probe`).
16. **Test ownership surfaces omitted.** **Fixed:** route-ownership, voice-inventory coverage, config-default, branding and determinism tests added.
17. **Per-session override invented.** **Fixed:** reuse `resolver.set_session_profile`/`session_overrides` patterns.
18. **No per-phase exit criteria.** **Fixed:** each phase now ends with explicit acceptance evidence.

Second adversarial pass (rev 2 → rev 3) found these additional errors:

19. **Benchmark harness mislocated.** The real harness is `voicebridge/benchmarking/` (`harness.py` with `percentile`/`write_run`/`OutputLock`/`TransportRecorder`, `corpus.py` with `Case`/`load_corpus`) and the data lives in `benchmarks/`. **Fixed:** extend `voicebridge/benchmarking/` and reuse its utilities; do not invent a parallel harness.
20. **`run_session` is a bounded *one-tool* session, not multi-step.** It plans once via `voicebridge/planner.py::plan`, executes at most one tool, then summarizes. **Fixed:** wording everywhere.
21. **Wrong task-path integration point.** The task path is wrapped in `self._worker_guard.run("agent.session", lambda: _agent_run_session(...))` (`orchestrator.py:1799`), not the bare `_agent_run_session` call. **Fixed:** integrate at the wrapper.
22. **`request_confirmation` semantics over-claimed.** `orchestrator._agent_tool` (`:2941`) dispatches with `source="model"` and already refuses MUTATING/DESTRUCTIVE tools with `DENY_CONFIRM_SOURCE`. Jev cannot lower this; its only real effect is raising a *reversible* call to require a user confirmation. **Fixed:** severity semantics state this.
23. **Voice `Intent` is not passed into `_handle_conversation`.** The dispatcher (`orchestrator.py:1454`/`:3151`/`:3290`) holds the `Intent` and calls `_handle_conversation(text)`. **Fixed:** route selection integrates at the dispatcher; the signature is extended to carry `Intent`.
24. **Voice hot-path latency.** A synchronous Jev call (up to `timeout_ms`) before the sub-second local reply would double voice latency. **Fixed:** voice consults Jev only for task-class utterances (`kind ∈ agent/coding/local`) with a short timeout, never on the fast-conversation path.
25. **Tool-enforcement hook is real but unimplemented.** `planner.plan(text, *, cfg, url, model, timeout_s)` and `run_session(...)` have no hint parameter. **Fixed:** advisory default; the optional enforcement hook is a named new `tool_hint` kwarg, owner-gated.
26. **Clarification/refusal wording.** Jev returns `noul`/`choice` decisions, not user-facing prose. **Fixed:** Qubit renders a deterministic template (or lets Brain4 ask); Jev text is never shown to the user.
27. **Reason codes and prompt-cache safety.** **Fixed:** reason codes are validated against an allowlist, and Jev advisories append only to the transient user turn — never the cached system/persona prefix.
28. **Text token estimator corrected.** `context_policy.estimate_tokens(messages)` takes a message list, not text. **Fixed:** reuse `context_policy.CHARS_PER_TOKEN` for a text estimator.

Kept from v1: the typed decision envelope, the privacy/minimization model, the four-arm benchmark, the eight-phase rollout, the rollback, and the explicit "Brain4 works without Jev" guarantee.

---

## Activation gate — this plan does NOT start now

This is a **separate plan that begins only after Qubit reaches 100%** — after **M4 `VERIFIED`** (`docs/PLAN.md`), full gate matrix + independent read-only verifier.

Hard preconditions (all true before Phase 0):

- [ ] M4 `VERIFIED` and independently reproduced.
- [ ] Vendor pin frozen to a single writer (no mid-flight rebase; `docs/QUBIT_1TO1_ROOT_CAUSES.md`).
- [ ] `/chat` terminal parity behaviorally proven; **Jev touches the `/qubit-chat` conversational path, the voice path, and (optionally) the task path only — never the `/chat` PTY.**
- [ ] Owner has signed the endpoint/data-egress decision.
- [ ] No `M1`/`M2`/`VERIFIED`/`CODE_READY` is claimed by any task here.

If any precondition is false: **BLOCKED — JEV PLAN GATED ON M4**.

---

## Executive summary

Jev is an online **typed-decision** model (System One): it returns `choice`, `score`, or `noul` (yes/no probability) answers in one call, in the millisecond band, instead of free text. It is **not** a language model, not a chatbot, not a Brain4 replacement, and has **no downloadable weights** (API-only).

Qubit already has every seam Jev needs:

- **An existing interpretation layer:** `voicebridge/intent/classifier.py::Classifier.classify(text) → Intent(kind, confidence, task_text, params, reason, source, backend)` with `resolve_model_tier()`; `Intent.kind ∈ {system, confirm, cancel, conversation, coding, clarify, local, agent, status}`. Plus `voicebridge/context_policy.py` (`_looks_complex`, `decide_context`, `ContextDecision`, `TIER_FAST/EXPANDED/ESCALATED`).
- **An opt-in, fail-closed escalation seam:** `chat_transport.py:714 _escalation_route()` + `context_policy.py:320 escalation_target()`.
- **One canonical model resolver:** `voicebridge/providers/resolver.py` (`resolve_model`, `ModelProfile`, `EffectiveProfile`, `is_secret_ref`, `looks_like_raw_secret`, `set_session_profile`, `session_overrides`).
- **One execution authority:** `voicebridge/broker.py` (`CapabilityBroker.execute`, confirmation store, e-stop) + `voicebridge/power.py`.
- **A voice cloud-egress gate:** `orchestrator._cloud_egress_allowed` (`FrontierPolicy`).
- **A proven Jev transport precedent in-tree:** `qubit_runtime/evals/compaction/jev_arm.py` (`openrouter_asker`, `JevOptions`, `JevUsage`, `fake_asker`) — borrow the transport *shape*, not its code (see defect #8/#9).

**Recommended design:** *conditional post-interpretation*. The existing interpretation (`Classifier.classify` for voice; `decide_context` + role + catalog state for chat) is the initial reading; Jev is consulted **only when that reading is uncertain**. Simple turns never call Jev. Jev output is advisory and monotonic-cautious.

**Brain4 remains Qubit's local conversational model.** Jev changes *routing and containment around* a turn, never the model's weights. If Jev is disabled, unreachable, times out, or rejects, `/qubit-chat` and voice behave exactly as today.

---

## What Jev is and is not

**Is:** TypeSafe AI's System One decision model (`Choice`, `Score`, `Noul`); best at routing, classification, scoring, moderation, tool-call risk gating, context compaction, model routing; HTTP-accessed. The in-tree precedent calls OpenRouter's Decisions API (`POST https://openrouter.ai/api/alpha/decisions`, model `~typesafe/jev-latest`). The referenced resource advertises an **unofficial community host** (`POST https://jevtypesafe.org/api/v1/decide`, prepaid `jv_live_` keys, not affiliated with TypeSafe AI); official TypeSafe signup is described as waitlisted. Vendor-published speed/cost (≈70–500 ms band; ≈$0.042/MTok input, free output) is **unverified in this repo**.

**Is not:** not local (no weights); not a Brain4 replacement/upgrade; not an executor (never calls tools, shell, files, or messages); not an authority (never overrides confirmation, e-stop, policy, budgets, allowlists, credentials); not a catalog source of truth.

**Critical framing:** Jev may only **lower** privilege (refuse, clarify, restrict a hint, recommend local over API). It may never **raise** it. If a Jev answer would allow something Qubit policy would not, Qubit policy wins and the answer is discarded.

---

## Current Qubit architecture (verified, with file:line)

| Concern | Where it lives | Verified behavior |
|---|---|---|
| `/qubit-chat` conversational turn | `voicebridge/chat_transport.py:771 turn()`, `:1186 stream_turn()` | e-stop → agent-enabled → reasoning validation → `resolve_route` → context → escalation → persist user row → `_generate` |
| Brain4 single turn | `chat_transport.py:947 _generate()` → `voicebridge/agent.py:229 turn()` → `providers.gateway.run_role(...)` | **one provider call, no tool execution** |
| Bounded one-tool task path | `voicebridge/agent.py:314 run_session(..., execute_tool, max_steps=4, ...)`; planner `voicebridge/planner.py:134 plan(text, *, cfg, …) → TypedProposal` | plans once, executes **at most one** tool via the caller's broker, summarizes; wrapped in `orchestrator._worker_guard.run("agent.session", …)` (`:1799`) |
| Task tool authority | `orchestrator._agent_tool` (`:2941`) | dispatches via `broker.execute(..., source="model")`; MUTATING/DESTRUCTIVE ⇒ `DENY_CONFIRM_SOURCE` until a user confirmation exists |
| Other `CT.turn` callers | `dashboard_ops.py:1693` (`role="coding"`), `session_companion.py:96`, `gateway/openclaw_handlers/sessions.py:357,898` | shared chat path; must be scoped by role |
| Interpretation (voice) | `voicebridge/intent/classifier.py:186 Classifier.classify → Intent` | dispatcher holds `Intent` (`orchestrator.py:1454`/`:3151`/`:3290`) and calls `_handle_conversation(text)`; kinds incl. `conversation`/`clarify`/`agent`; `llm_classifier` default **true** |
| Interpretation (chat) | `voicebridge/context_policy.py` `_looks_complex`, `decide_context` | complexity/size tier; `requires_escalation` = window overflow only |
| Model/profile registry | `voicebridge/providers/resolver.py` | `resolve_model`, `ModelProfile`, `is_secret_ref`, `looks_like_raw_secret`, `set_session_profile` |
| Escalation | `chat_transport.py:714 _escalation_route`; `context_policy.py:320 escalation_target` | opt-in via `agent.context.escalation_model`; no pinned target ⇒ typed `CONTEXT_ESCALATION_UNAVAILABLE` |
| Escalation call sites | `turn:820`, `retry_last:1076`, `stream_turn:1154`, `stream_preflight:1237` | `stream_turn` calls `stream_preflight` (double-resolution already exists) |
| Voice conversation | `voicebridge/orchestrator.py:2361 _handle_conversation` (decision `:2434`) → `_chat_local_reply`; fallbacks `_chat_perplexity`/`_chat_deepseek*`/`_chat_ollama` | cloud gated by `:2586 _cloud_egress_allowed` (`FrontierPolicy`) |
| Authority | `voicebridge/broker.py` `CapabilityBroker.execute`, `confirmation_store_for`, `estop_engaged`; `voicebridge/power.py`; `voicebridge/safety/destructive_screen.py`; `orchestrator._worker_guard` | ONE path; destructive screen; power latch; audit |
| Audit | `voicebridge/logging_setup.py:77 log_event` → `events.jsonl` | receipts |
| Redaction | `voicebridge/redaction.py` `redact_text`; `qubit_runtime/agent/redact.py:616 redact_sensitive_text(force=True)` | forced at egress boundaries |
| Session store | `chat_transport.ChatSessionStore`; `voicebridge/qubit_session_store.py` | one runtime `state.db` authority |
| Config | `voicebridge/config.py` defaults + `Config.load`; installed `~/.config/qubit-voicebridge/voice.yaml`; repo `config/voice.example.yaml` | `cfg.get("agent","context",key,default=…)` accessor (`Config.get(*path, default)`, `:686`) |
| Benchmark harness | `voicebridge/benchmarking/{harness,corpus,unit_mode}.py` (`Case`, `load_corpus`, `percentile`, `write_run`, `OutputLock`); data `benchmarks/corpus/*_cases.json` (`voicebridge.eval-corpus/1`), baselines `benchmarks/eval_baseline_6b.json` | Qubit overlay; survives rebase |
| Existing Jev precedent | `qubit_runtime/evals/compaction/jev_arm.py` (+ `scripts/jev_cycles.py`, tests) | **vendored type; borrow shape only** |

**Two paths, not one.** The chat turn is conversational (`AG.turn`, no tools). Tool work is the bounded one-tool task path (`AG.run_session` + `orchestrator._worker_guard`/`_agent_tool`). Any plan that claims Jev restricts the chat turn's tools is wrong. `select_*` are advisory hints to whichever path is running. **`request_confirmation` can only raise caution:** the task broker already fails closed on MUTATING/DESTRUCTIVE model-originated calls (`DENY_CONFIRM_SOURCE`), so Jev's genuinely additive effect is raising a *reversible* call to require a user confirmation, never clearing one. `reject_unsafe_action` is enforced by the caller.

**Hard invariants (must hold in every phase):**

1. `router.decide` never imports the broker/agent and never executes; it returns data.
2. A Jev result can never change a dispatch `source` (always `"model"` at the tool broker) and can never fabricate or consume a confirmation id.
3. Jev advisories are appended only to the **transient user turn**; they must never mutate the cached system/persona prefix (prompt-caching is sacred per `AGENTS.md`).
4. The combined decision is always the **more cautious** of Jev and Qubit policy.

### Where Jev can actually help (honest value assessment)

| Use | Fit | Why |
|---|---|---|
| Local-vs-API escalation (chat) | **Strong** | today escalation fires only on window overflow; Jev adds a semantic trigger through the existing resolver |
| Voice route classification (`kind`) | **Strong** | the dispatcher already computes `Intent`; Jev can disambiguate weak/multi-intent utterances |
| Risk gating a **reversible** tool | **Moderate** | broker already fails closed for mutating/destructive; Jev can add caution where policy currently allows silently |
| Toolset/skill/plugin/MCP *selection* | **Weak (advisory)** | no enforcement seam exists; only a hint can be passed to `planner.plan` via a new kwarg |
| Context compaction | Already solved | `qubit_runtime/evals/compaction/jev_arm.py` is an eval precedent; not needed for routing |

If the benchmark (Phase 7) shows the weak rows add no measurable value, the plan keeps them advisory and disabled rather than building enforcement.

### Concrete `TurnIntent` (frozen before code)

```json
{
  "schema": "qubit.jev.intent/1",
  "surface": "chat | voice | task",
  "role": "chat | coding | companion | gateway",
  "kind": "conversation | agent | coding | local | clarify | system | confirm | cancel",
  "kind_confidence": 0.0,
  "text_chars": 0,
  "est_tokens": 0,
  "history_messages": 0,
  "tool_candidate": false,
  "web_hint": false,
  "risk_hint": "",
  "ambiguous": false,
  "session_id": "hex",
  "trace_id": "hex"
}
```

`uncertain(TurnIntent) → bool` is a single pure predicate over these fields (e.g. `tool_candidate or web_hint or ambiguous or est_tokens > fast_window or kind_confidence < 0.6 or role in {"coding"}`), documented and unit-tested; no network, no model call.

---

## Recommended Qubit architecture

### Placement decision

Three options:

1. **Before interpretation (pre-router on raw text).** Sees no reading; adds a hop to every turn; over-escalates. Rejected.
2. **After interpretation, every turn.** Best input, but adds third-party latency/cost/dependency to the common case. Rejected.
3. **After interpretation, conditionally on uncertainty.** ✅ **Recommended.**

**Tradeoffs:** the simple-turn fast path stays byte-for-byte unchanged (no network, no latency); Jev runs only where deterministic rules are weak. The cost is dependence on the uncertainty gate: under-flagging means Jev is simply not consulted and today's deterministic behavior holds (safe failure); over-flagging costs latency/cost, bounded by budget/timeout/circuit. This is the only design satisfying "no silent network calls" and "no startup effect if unavailable".

**"Initial interpretation" is concrete, not a new Brain4 call.** For voice, reuse the already-computed `Intent` from `Classifier.classify`. For chat, reuse `decide_context` plus cheap lexical/task signals (`_task_hint_score`-style) with **no extra LLM call** (the LLM classifier is never invoked just to decide whether to ask Jev). "After Brain4's interpretation" means after the same lightweight reading Qubit already performs — never an added model round-trip.

### Pipeline

```
User text or voice
  → existing interpretation:
        voice: Intent = Classifier.classify(text)      (already computed)
        chat : ContextDecision = decide_context(...)   (+ cheap lexical signals)
  → TurnIntent (deterministic; no network)
  → IF uncertain AND enabled AND not privacy AND role allowed AND not e-stop AND not cancelled:
        → Jev decision (bounded, redacted; typed; validated)
        → advisory; combine by taking the MORE CAUTIOUS of Jev and policy
     ELSE: deterministic fallback (today's behavior)
  → conversational path (AG.turn) | task path (AG.run_session + broker:
        confirm/e-stop/destructive-screen/audit) | clarification | API escalation
  → reply
```

### Component boundaries (new `voicebridge/jev/` overlay)

- `classifier.py` — `build_intent(...) → TurnIntent` (from `Intent`/`ContextDecision`); `uncertain(TurnIntent) → bool`.
- `privacy.py` — minimize + `redact_sensitive_text(force=True)`; enforce `max_request_chars`/`max_state_tokens`.
- `schema.py` — typed decision dataclasses + envelope validation + severity ordering.
- `transport.py` — pluggable `asker(state, questions)`; stdlib HTTP; timeout; circuit breaker; lazy; no-op when disabled.
- `router.py` — `decide(cfg, intent, catalog) → JevDecision` + deterministic fallback; the ONLY module the turn paths call.
- `config.py` — normalized `agent.jev` config with bounds; reuses `is_secret_ref`.
- `observability.py` — redacted receipt + `log_event`.

`router` returns data; it never imports the broker or executes. The turn paths remain the only actors.

---

## Sequence diagram

```mermaid
sequenceDiagram
  autonumber
  participant U as User (text/voice)
  participant IV as existing interpretation
  participant CT as chat_transport / orchestrator
  participant JV as voicebridge.jev (advisory)
  participant AG as agent.turn / agent.run_session
  participant BR as broker (confirm/e-stop/audit)
  participant API as API profile (opt-in)

  U->>IV: message
  IV-->>CT: Intent (voice) | ContextDecision + lexical signals (chat)
  CT->>CT: e-stop? enabled? role allowed? cancelled?
  alt uncertain AND enabled AND !privacy AND role allowed
    CT->>JV: decide(TurnIntent, catalog-names)  [redacted, bounded, timeout]
    JV-->>CT: JevDecision | JEV_UNAVAILABLE
    CT->>CT: combine = more_cautious(jev, policy)
  else
    CT->>CT: deterministic fallback (no network)
  end
  alt request_clarification
    CT-->>U: ask (no execution, no content persisted in receipt)
  else reject_unsafe_action
    CT-->>U: typed refusal (no execution)
  else route_api
    CT->>CT: resolver.resolve_model(escalation profile)
    alt no profile / egress denied / budget
      CT-->>U: typed refusal (never silent local)
    else allowed
      CT->>API: remote turn (egress/allowlist/budget re-checked)
    end
  else conversational
    CT->>AG: agent.turn (provider call; advisory hints only)
  else task
    CT->>AG: worker_guard.run(agent.session) -> run_session(execute_tool=_agent_tool)
    AG->>BR: source="model" / confirm / destructive-screen / e-stop / audit / execute
  end
  AG-->>CT: reply / authorization outcome
  CT-->>U: final response + routing receipt
```

Text fallback: same ordering as the component boundaries and the failure/fallback table.

---

## Typed Jev decision schemas

One versioned envelope, validated on every response. Unknown type or missing field ⇒ `JEV_INVALID_DECISION`, discarded (fallback) — never coerced.

### Envelope

```json
{
  "schema": "qubit.jev.decision/1",
  "decision_type": "route_local",
  "confidence": 0.0,
  "reason_code": "STRING_ENUM",
  "target": {},
  "confirmation_state": "not_required",
  "expires_at": 0,
  "timeout_ms": 0,
  "trace_id": "hex",
  "fallback": "local",
  "source": "jev",
  "latency_ms": 0,
  "model": "~typesafe/jev-latest"
}
```

Required: `schema`, `decision_type`, `confidence` (0.0–1.0 clamped), `reason_code`, `target` (`{}` if N/A), `confirmation_state`, `expires_at` (epoch ms), `timeout_ms`, `trace_id`, `fallback`.

- `confirmation_state ∈ {not_required, required, already_satisfied, forbidden}`. Jev may only move this **toward more caution**; it can never cancel a confirmation Qubit already requires, and it can never supply or consume a confirmation id.
- `expires_at`/`timeout_ms`: single-turn; consumed before expiry; expired ⇒ fallback; clamped to `agent.jev.max_timeout_ms`.
- `fallback ∈ {local, refuse, clarify, deterministic}`.
- `reason_code` is validated against a **closed allowlist** (extended only by PR): `SIMPLE_LOCAL`, `COMPLEX_ESCALATE`, `DESTRUCTIVE_INTENT_DETECTED`, `AMBIGUOUS_REQUEST`, `CONTEXT_MISSING`, `UNSAFE_ACTION`, `TOOLSET_HINT`, `UNAVAILABLE`. Unknown code ⇒ `JEV_INVALID_DECISION`.
- **Jev-authored text is never user-facing.** Jev returns decisions (`noul`/`choice`/`score`), not prose; any user-visible clarification/refusal is a Qubit-rendered deterministic template (or Brain4 asks).

### Decision types (corrected target semantics)

| `decision_type` | `target` | Advisory effect |
|---|---|---|
| `route_local` | `{"model":"qubit-brain4"}` | keep local / suppress escalation |
| `route_api` | `{"profile_id":"<registered>"}` | **requires** the owner-named `agent.jev.escalation_profile` (Jev may not name an arbitrary profile) **and** `api_escalation_allowed=true`; resolved via `resolver.resolve_model`; absent/denied ⇒ typed refusal |
| `select_toolset` | `{"toolset":"<name>"}` | **advisory hint only** by default (no enforce path exists); validated against the live catalog; unknown ⇒ ignored. Optional enforcement (owner-gated) = new `tool_hint` kwarg on `planner.plan`/`run_session` |
| `select_skill` | `{"skill":"<name>"}` | advisory hint (same caveat); never auto-run |
| `select_plugin` | `{"plugin":"<name>"}` | advisory hint (same caveat) |
| `select_mcp` | `{"server":"…","tool":"…"}` | advisory hint (same caveat) |
| `request_confirmation` | `{"capability":"…"}` | **raises caution only**: routed through the canonical confirmation store; the task broker already fails closed for MUTATING/DESTRUCTIVE model-originated calls (`DENY_CONFIRM_SOURCE`), so the additive case is a *reversible* call |
| `request_clarification` | `{"question_chars":"<=280"}` | Qubit asks with a deterministic template (Jev decides *whether*); no execution; question text not persisted in receipts |
| `reject_unsafe_action` | `{"screen_code":"…"}` | typed refusal (Qubit template); never executes |
| `context_insufficient` | `{"missing":["…"]}` | fail closed to deterministic policy |
| `jev_unavailable_fallback_local` | `{}` | identical to disabled |

### Severity (monotonic; Jev cannot raise)

`reject_unsafe_action` > `request_confirmation` > `request_clarification` > `context_insufficient` > (`route_api`/`route_local`/`select_*`) > `jev_unavailable_fallback_local`.

Qubit combines Jev's advisory with its own policy by taking the **more cautious**; a Jev answer can never move the combined decision toward less caution.

### Worked example (noul → envelope), corrected `route_api`

Request (minimized, redacted, names only):

```json
{
  "model": "~typesafe/jev-latest",
  "state": {
    "context": "Route one Qubit /qubit-chat turn. Answer only the typed questions.",
    "intent": {"kind": "agent", "ambiguous": true, "tool_candidate": true, "risk_hint": "delete"},
    "vectors": {"toolsets": ["terminal","files","web","browser"], "skills": ["qubit-voice-mode"], "mcps": ["cgraph-graft"]},
    "budget": {"max_state_tokens": 1500}
  },
  "questions": {
    "escalate": {"type":"noul","instructions":"This request must run on an API profile, not Brain4."},
    "dangerous": {"type":"noul","instructions":"Executing without confirmation would be unsafe."},
    "clarify": {"type":"noul","instructions":"Critical information is missing and must be asked first."},
    "toolset": {"type":"choice","options":["terminal","files","web","browser","none"]}
  }
}
```

Response → decision:

```json
{
  "schema": "qubit.jev.decision/1",
  "decision_type": "request_confirmation",
  "confidence": 0.86,
  "reason_code": "DESTRUCTIVE_INTENT_DETECTED",
  "target": {"capability": "local.files_delete"},
  "confirmation_state": "required",
  "expires_at": 1893456000000,
  "timeout_ms": 800,
  "trace_id": "9f2c…",
  "fallback": "refuse",
  "source": "jev",
  "latency_ms": 143,
  "model": "~typesafe/jev-latest"
}
```

The caller routes this through `voicebridge/broker.py`; for a `DESTRUCTIVE`-tier capability the existing two-step confirmation is already mandatory, so Jev only re-asserts it.

---

## Privacy and security model

**Boundary rule:** nothing leaves Qubit to Jev except the minimum structured task state; the full transcript, files, credentials, and chat history never do.

1. **Redact before serialization:** every outbound string passes `qubit_runtime/agent/redact.py::redact_sensitive_text(text, force=True)` plus vault-value scrubbing. Test: no outbound field matches a secret shape when secrets are present.
2. **Minimize:** `state` = fixed route instruction + bounded `intent` flags + **names only** for toolset/skill/plugin/mcp + token budget. No message bodies, no tool outputs. `goal` (if any) is at most the `goal_from_messages`-style ≤3-prompt truncated form, redacted.
3. **Never send:** API keys, cookies, tokens, `.env` values, private files, raw credentials, full history, tool outputs.
4. **Caps:** `agent.jev.max_request_chars` (default `6000`) and `agent.jev.max_state_tokens` (default `1500`); exceed ⇒ deterministic truncate or fall back — never silently send more.
5. **Privacy mode:** `agent.jev.privacy_mode: true` (default **false**, since `enabled` is already false) disables all calls; togglable by voice ("private mode on") and per session; visible in status.
6. **Retention:** receipts record *that* Jev was consulted + decision metadata, never request contents; bounded by `agent.jev.receipt_retention_days` (default `7`).
7. **Fail closed for dangerous actions:** a `dangerous` result forces confirmation or refusal; Jev can never clear `voicebridge/safety/destructive_screen.py` or the e-stop latch (`broker._power_denied`).
8. **Authority stays above:** every influenced action passes `CapabilityBroker.execute` → power/e-stop → destructive screen → autonomy/confirmation → audit.
9. **Network hygiene:** HTTPS only; configured endpoint only; no cross-host redirects; explicit timeout; never log credentials.
10. **Third-party egress is explicit and owner-gated:** calling any Jev host sends data off-device. Off by default; status says `Jev: enabled (endpoint: …)` before any call. A `hermes`/`nous`/`openclaw`-branded endpoint is refused (legacy-URL guard, mirroring `update_channel.py`).

---

## Model-routing policy

- **Brain4 handles simple/local turns** unchanged. Jev is never consulted for a simple turn.
- **Jev may recommend `route_api`;** it is resolved through `providers/resolver.resolve_model` and is subject to every existing check (egress, allowlist, budget, credential). No registered profile/target ⇒ typed `CONTEXT_ESCALATION_UNAVAILABLE` (fails closed, never silent local).
- **Jev never executes tools.** `select_*` are advisory hints; the canonical agent chooses and the broker authorizes/confirms/e-stops/audits/executes.
- **Jev cannot bypass policy.** Monotonic-cautious combination (above).
- **If Jev is unavailable,** Brain4 continues locally / deterministic fallback — identical to disabled.
- **User override where safe.** Per-session "local only" maps onto the existing `model_profile_local_only`; a user may always disable Jev for a session.

---

## Configuration design

All keys under `agent.jev` in `voicebridge/config.py` defaults + installed `~/.config/qubit-voicebridge/voice.yaml`; documented in `config/voice.example.yaml`. **All defaults off/least-privilege. One off switch: `enabled=false`.**

| Key | Default | Bounds / behavior |
|---|---|---|
| `enabled` | `false` | master; false ⇒ lazy no-op, zero network, zero behavior change |
| `endpoint` | `""` | explicit `https://`; legacy-branded hosts refused |
| `model` | `""` | e.g. `~typesafe/jev-latest`; never a secret |
| `key_ref` | `""` | credential **reference only** (`env:…`/`file:…`); validated with `resolver.is_secret_ref`; a raw key is refused via `looks_like_raw_secret` |
| `roles` | `["chat"]` | only these `CT.turn` roles consult Jev (excludes `coding`/companion) |
| `timeout_ms` | `800` | clamped `(0, max_timeout_ms]` |
| `max_timeout_ms` | `2000` | hard ceiling |
| `min_confidence` | `0.60` | below ⇒ fallback |
| `escalation_threshold` | `0.75` | `route_api` honored only above this |
| `max_request_chars` | `6000` | outbound cap |
| `max_state_tokens` | `1500` | state cap |
| `privacy_mode` | `false` | true ⇒ never call |
| `api_escalation_allowed` | `false` | owner opt-in; ANDs with existing escalation config |
| `escalation_profile` | `""` | the **only** profile `route_api` may target (owner-named); empty ⇒ `route_api` refused |
| `tool_hint_enabled` | `false` | optional owner-gated enforcement (new `tool_hint` kwarg); false ⇒ `select_*` advisory only |
| `voice_task_only` | `true` | voice consults Jev only for task-class utterances, never the fast-conversation path |
| `per_session_override` | `true` | session may disable (enable always subject to global gate) |
| `receipt_retention_days` | `7` | receipts |
| `daily_usd` | `0.0` | `0.0` ⇒ no paid calls |
| `circuit_fail_threshold` | `3` | open the breaker after N failures (process-wide, lock-guarded) |
| `circuit_cooldown_s` | `300` | breaker cooldown |

**Enablement sequence** (a single `qubit jev enable` validates all): `enabled=true` **and** `privacy_mode=false` **and** `endpoint` set **and** `key_ref` resolves. Status reports exactly which is missing. No silent partial enablement.

**User controls:** dashboard "Jev routing" group on `SystemPage` + `/qubit-chat` side-panel badge; CLI `qubit jev status|enable|disable|privacy on|off|explain [--session]|config-get|config-set`; voice status/enable/disable/privacy + a visible routing-explanation line. **Secrets never returned** — only `is_set: true/false` and `key_ref` kind (mirrors `voicebridge/env_store.py`).

---

## Failure / fallback behavior

| Condition | Behavior |
|---|---|
| `enabled=false` / `privacy_mode=true` | lazy no-op; deterministic path |
| role not in `agent.jev.roles` | deterministic path |
| Endpoint/timeout/connection error | `jev_unavailable_fallback_local`; deterministic |
| Malformed/unknown decision | `JEV_INVALID_DECISION`; discard; deterministic |
| Confidence below `min_confidence` | discard; deterministic |
| Decision expired | discard; deterministic |
| Budget exhausted / circuit open | skip; deterministic |
| e-stop latched / turn cancelled | Jev not consulted (or abandoned); typed refusal/cancel |
| `reject_unsafe_action` | typed refusal; nothing executes |
| `route_api` but no registered profile / egress denied | typed escalation refusal; never silent local |
| Jev less cautious than policy | ignored; policy wins |
| `select_*` names an unavailable target | ignored for that vector; catalog unchanged |

Failure is never a regression and never a silent widening.

---

## Observability

Each turn emits a **privacy-safe routing receipt** via `logging_setup.log_event` (`events.jsonl`):

| Field | Meaning |
|---|---|
| `route` | `local` \| `api` \| `task` |
| `jev_consulted` | bool |
| `decision_type` / `reason_code` | typed decision metadata |
| `confidence` | 0.0–1.0 |
| `latency_ms` | Jev round-trip when called |
| `fallback_reason` | `disabled`\|`role`\|`privacy`\|`timeout`\|`invalid`\|`low_confidence`\|`expired`\|`budget`\|`circuit`\|`estop`\|`cancelled`\|`none` |
| `success` | bool |
| `tokens`/`cost_usd_est` | from Jev `usage` when available |
| `trace_id` | shared with the turn receipt |
| `explanation` | short code-derived text, no content |

- Records **that** Jev was consulted, never request/question text.
- The existing turn receipt gains a nested `jev` block; `/qubit-chat` renders a one-line explanation from these code fields.
- Secret/key/cookie/token may never appear in any receipt, log, or UI field (enforced by tests).

---

## Benchmark design (before implementation)

Location: extend **`voicebridge/benchmarking/`** (the real harness: `harness.py` `percentile`/`write_run`/`OutputLock`/`TransportRecorder`, `corpus.py` `Case`/`load_corpus`, `unit_mode.py`) + the data in **`benchmarks/`** (`corpus/*_cases.json`, `voicebridge.eval-corpus/1`; baselines `eval_baseline_6b.json`). Add `voicebridge/benchmarking/jev_arm.py` + a runner; do **not** fork the harness and do **not** add anything to vendored `qubit_runtime/evals/`.

**Four arms:** A = Brain4 alone; B = Brain4 + deterministic routing; C = Brain4 + Jev; D = Brain4 + Jev + API escalation. Arm D needs a real API credential, so offline runs substitute a stub API profile (a local profile that returns canned text) and the live D run is owner-gated.

**Corpus cases** (extend `benchmarks/corpus/`, synthetic only; add `expected.jev_decision` + a `jev_routing` category alongside the existing `expected.kinds`/`route_class`): simple conversation; planning; browser; filesystem; toolset/skill/plugin/MCP selection; ambiguous request; dangerous action; long-context; voice request; API-escalation-requiring request.

**Metrics:** task success; correct tool selection; unsafe-action refusal; escalation accuracy; unnecessary API escalations; latency p50/p95; cost USD; voice-turn responsiveness; session continuity; failure behavior when Jev unavailable; user correction rate.

**Controls:** a deterministic fake asker (in `voicebridge/benchmarking/` or `tests/`, never imported by production `voicebridge/jev/`), plus a random-Jev control and an adversarial permissive-Jev control to prove the safety floor. No network in default test runs; a live run is opt-in and owner-gated.

**Output:** deterministic artifact `docs/generated/QUBIT_JEV_ROUTING_BENCHMARK_<date>.json` (byte-identical ×2).

**Decision rule:** ship enablement only if C/D beats B on success/unsafe-refusal without material latency/cost/unnecessary-escalation regression; otherwise keep implemented-but-disabled.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Network dependency | off by default; no call on simple turns; timeout + circuit breaker; fallback identical to today |
| Latency | uncertain turns only; `timeout_ms`; voice budget measured |
| API cost | `daily_usd`/caps; `0.0` ⇒ no paid calls |
| Privacy / third-party egress | forced redaction; minimization; privacy mode; owner-gated endpoint |
| Incorrect routing | advisory; monotonic-cautious combine; benchmark gates enablement |
| Over-escalation | `escalation_threshold`; `api_escalation_allowed=false`; metric |
| Under-escalation | deterministic window-overflow escalation still runs; Jev additive |
| Prompt injection | Jev sees bounded structured state, never raw untrusted text as instructions; `voicebridge/learning.py` injection shapes; output schema-validated and narrowing-only |
| Decision spoofing | typed schema + monotonic severity + policy re-check; permissive answers cannot raise privilege |
| Stale tool catalogs | `select_*` validated against live catalog; unknown ignored; TTL cache |
| Jev outage | `jev_unavailable_fallback_local` |
| API-key failure | `key_ref` only; missing ⇒ typed degraded status |
| Local-only users | fully supported; no key/endpoint/network required |
| Future Jev API changes | versioned envelope; asker seam; schema rejects drift |
| **Vendor/routing alias drift** | `~typesafe/jev-latest` is a moving alias on a third-party gateway; pin to a concrete model id when the owner approves; record the resolved model in every receipt |
| **Community-host ToS / availability / pricing** | owner-gated endpoint; `daily_usd`; treat all Jev output as untrusted input |
| **Rebase fragility** | new code stays in the `voicebridge/`/`benchmarks/`/`tests/` overlay; never in vendored `qubit_runtime/` |
| **Prompt-cache invalidation** | Jev advisories append only to the transient user turn; never mutate the cached system/persona prefix (test asserts the prefix is byte-stable across a session with Jev on) |
| **Benchmark harness mismatch** | the existing harness records Ollama transport/residency; the Jev arm reuses its utilities (`percentile`/`write_run`/`OutputLock`/`load_corpus`) rather than pretending to be a transport-recording run |
| **Circuit-breaker concurrency** | the breaker is process-wide and lock-guarded; concurrent chat turns must not race the counter |
| **Tool source laundering** | Jev can never set/override `source` (always `"model"`) or inject a confirmation id; invariant test |
| **Legacy-brand leak on egress** | never copy `jev_arm.py`'s `HTTP-Referer`; Qubit-owned/absent referer; branding test |
| Conflict with one authority | Jev is a data-returning advisor; all execution stays on the broker path |
| Conflict with `/chat` parity | scoped to `/qubit-chat` + voice + task dispatch; `/chat` PTY untouched by construction and test |
| Scope leakage to other callers | `roles` allowlist default `["chat"]` |

---

## Implementation phases

Each phase is a checkpoint with acceptance evidence and a rollback to `enabled=false`. No phase touches the `/chat` PTY path, the frozen archive, `docs/differential/INTERFACE.md`, or `~/.hermes`.

### Phase 0 — Research and API validation
- Confirm the real request/response contract for the chosen endpoint (OpenRouter Decisions API vs community `/api/v1/decide`), including `noul`/`choice`/`score` and `usage`.
- No live key in repo/tests; validation uses the documented contract + `jev_arm.py` precedent, or a one-off owner-run probe outside the suite.
- Deliverable: `docs/QUBIT_JEV_ENDPOINT_AND_DATA_EGRESS.md` (endpoint, auth, data sent, retention, owner sign-off block).
- **Exit:** owner signs egress; else **BLOCKED — JEV PLAN GATED ON OWNER EGRESS DECISION**.

### Phase 1 — Interface and typed schema
- New `voicebridge/jev/schema.py`, `voicebridge/jev/config.py`.
- Tests: every type validates; unknown/missing ⇒ `JEV_INVALID_DECISION`; confidence clamped; severity monotonic; `is_secret_ref`/`looks_like_raw_secret` enforced.
- **Exit:** schema + config tests green; no product behavior change.

### Phase 2 — Offline fake/Jev-compatible backend
- New `voicebridge/jev/transport.py` with asker injection; fakes (`fake`, `random`, `adversarial-permissive`) in `voicebridge/benchmarking/` + `tests/` stubs (never imported by production `voicebridge/jev/`).
- Tests: deterministic offline; **no socket opened** when `enabled=false`/`privacy_mode=true`/role excluded (monkeypatched opener asserted uncalled).
- **Exit:** no-network guarantees proven.

### Phase 3 — Conversational routing/classification (`CT.turn` + `stream_turn`)
- New `voicebridge/jev/{classifier,router,privacy,observability}.py`.
- Modify `chat_transport.py`: build `TurnIntent` from `context_policy` + cheap lexical/task signals (**no LLM classifier call**); consult `router.decide` once per turn, **after** `_estop`/`_agent_enabled`/reasoning checks and **before** persistence; honor `cancel`; thread the decision into `_generate` (advisory only). For streaming, evaluate once (in `stream_preflight` and return it, or cache on the turn) so `stream_turn` does not double-call.
- Apply only when `role ∈ agent.jev.roles` (default `["chat"]`), so `dashboard_ops` coding turns, `session_companion`, and gateway session handlers are untouched.
- Tests: simple turn = zero Jev calls; uncertain turn = exactly one (incl. streamed); redaction; no orphan user row on refusal; role filter excludes `coding`; cancellation; system/persona prefix byte-stable with Jev on.
- **Exit:** conversational path behavior-identical when disabled; advisory proven.

### Phase 4 — Bounded one-tool task path
- Integrate at the `self._worker_guard.run("agent.session", lambda: _agent_run_session(...))` wrapper (`orchestrator.py:1799`; same pattern at `:1937`/`:3025`), consulting `router.decide` once before dispatch.
- Deliver `request_confirmation`/`reject_unsafe_action` into `orchestrator._agent_tool` (`:2941`). Note the broker already dispatches as `source="model"` and refuses MUTATING/DESTRUCTIVE with `DENY_CONFIRM_SOURCE`, so Jev only **raises** caution for **reversible** tools; it can never clear a confirmation.
- `select_*` = advisory by default. Optional enforcement (owner-gated, `tool_hint_enabled=true`): add a `tool_hint` kwarg to `voicebridge/planner.py::plan` and thread it through `run_session`. Jev still never executes.
- Tests: confirmation directive honored; broker still blocks restricted capabilities; stale/unknown names ignored; no privilege gain; adversarial permissive Jev cannot bypass; Jev cannot set `source` or inject a confirmation id.
- **Exit:** task path safe; advisory-only selection stated in code + docs.

### Phase 5 — Local/API escalation
- Wire `route_api` into the four escalation sites (`turn`, `retry_last`, `stream_turn`, `stream_preflight`) via a single helper; require `agent.jev.escalation_profile` (owner-named) registered **and** `api_escalation_allowed=true`; resolve via `resolver.resolve_model`; preserve atomic ordering (no orphan user row); respect egress/budget.
- Tests: no profile/allowed=false ⇒ typed refusal; configured profile ⇒ real resolver path; egress/budget deny; e-stop denies; Jev cannot name an arbitrary profile.
- **Exit:** escalation fail-closed proven on all four sites.

### Phase 6 — Voice and Right-Ctrl
- Integrate at the dispatcher (`orchestrator.py:1454`/`:3151`/`:3290`), which already holds `Intent`: consult `router.decide` for route selection and extend `_handle_conversation` to accept the `Intent` (signature change) rather than reclassifying.
- Per `voice_task_only=true`, voice consults Jev only for task-class utterances (`kind ∈ agent/coding/local`) with a short timeout; the fast-conversation path is never blocked. Cloud only if `_cloud_egress_allowed` (`:2586`) allows.
- Add voice intents in `voicebridge/voice_commands.py`.
- Tests: simple voice turn unchanged and never blocks; task-class voice turn consults once; Right-Ctrl path unaffected; refusal/clarification spoken; latency within the voice budget.
- **Exit:** voice routing proven; egress gate respected; hot path untouched.

### Phase 7 — Benchmark comparison
- Extend `voicebridge/benchmarking/` (reuse `percentile`/`write_run`/`OutputLock`/`load_corpus`) and `benchmarks/corpus/`; run the four arms (D stubbed offline); emit deterministic `docs/generated/QUBIT_JEV_ROUTING_BENCHMARK_<date>.json`.
- **Exit:** owner reviews benefit; no milestone claimed.

### Phase 8 — Guarded production rollout
- Pilot enablement for one session; observe receipts/metrics; keep the kill-switch (`enabled=false`) documented. Rollout opt-in and reversible.
- **Exit:** pilot metrics reviewed; default remains disabled.

---

## Exact files likely to change

**New:**

- `voicebridge/jev/__init__.py`, `schema.py`, `config.py`, `privacy.py`, `classifier.py`, `transport.py`, `router.py`, `observability.py`
- `voicebridge/benchmarking/jev_arm.py` (+ runner); `benchmarks/corpus/jev_*.json` (synthetic cases, `voicebridge.eval-corpus/1`)
- `tests/test_jev_schema.py`, `test_jev_privacy.py`, `test_jev_transport.py`, `test_jev_routing.py`, `test_jev_fallback.py`, `test_jev_task_path.py`, `test_jev_voice.py`, `test_jev_dashboard.py`, `test_jev_brand.py`
- `docs/QUBIT_JEV_ENDPOINT_AND_DATA_EGRESS.md`, `docs/QUBIT_JEV_BRAIN4_ROUTING.md` (operator guide)

**Modified:**

- `voicebridge/chat_transport.py` — single-evaluation consult in `turn`/`retry_last`/`stream_turn`/`stream_preflight`; receipt.
- `voicebridge/orchestrator.py` — dispatcher + `_handle_conversation(Intent)` voice consult; `_worker_guard`/`_agent_tool` task consult; egress gate respected.
- `voicebridge/planner.py` — **optional only** (`tool_hint_enabled`): a `tool_hint` kwarg on `plan`.
- `voicebridge/agent.py` — only if enforcement is approved (advisory needs no change).
- `voicebridge/context_policy.py` — expose uncertainty signals (no behavior change when disabled).
- `voicebridge/config.py` + `config/voice.example.yaml` — `agent.jev` defaults.
- `voicebridge/voice_commands.py` — intent entries + registry.
- `voicebridge/cli.py` — `qubit jev …`.
- `voicebridge/qubit_compat.py` — status/settings routes.
- `voicebridge/distribution.py` — `doctor()` config-only check.
- `frontend/qubit-web/src/pages/ChatPage.tsx`, `SystemPage.tsx`, `lib/api.ts`.
- `docs/STATUS.md`, `docs/EXECUTION_PLAN.md`, `docs/CHECKLIST.md`.

**Configuration migration:** none. `agent.jev.*` are additive with safe defaults; `Config` merges defaults under the installed `voice.yaml`, so an operator sees `enabled=false` and no change. No key is renamed or re-typed; a future schema bump is handled by the versioned envelope.

**Interfaces to freeze before code:** `qubit.jev.decision/1`, `TurnIntent`, and `asker(state, questions) -> response`.

**Not touched:** `/chat` PTY path; `voicebridge/providers/gateway.py` (the provider gateway is unchanged); frozen records and `docs/differential/INTERFACE.md`; `~/.hermes`; legal attribution.

---

## Test plan

- Invariant tests only (behavior contracts, never snapshots), via `scripts/run_tests.sh`.
- Schema: all types; unknown ⇒ typed discard; confidence clamp; severity monotonic.
- Privacy (highest scrutiny): planted secrets absent from outbound; caps enforced; no transcript; key absent from responses/receipts/logs.
- No-network: `enabled=false`, `privacy_mode=true`, excluded role ⇒ opener asserted uncalled.
- Fallback: timeout/malformed/low-confidence/expired/budget/circuit/disabled all identical to today; e-stop/cancel short-circuit.
- Safety: adversarial permissive fake cannot lower caution; `reject_unsafe_action` refuses; `route_api` without profile fails closed.
- Routing: deterministic fake yields expected local/escalate/clarify/reject without executing.
- Task path: broker confirmation/destructive-screen still enforced; no privilege gain; Jev cannot set `source` (always `"model"`) or inject a confirmation id.
- Prompt-cache: with Jev on, the system/persona prefix is byte-stable across a session; advisories appear only in the transient user turn.
- Planner hook (if enabled): `tool_hint` constrains the proposal; unknown hint ignored; disabled ⇒ byte-identical planner behavior.
- **Ownership surfaces:** route-ownership matrix (new `qubit_compat` routes), voice-inventory coverage (new intents resolve to real backends), config-default consistency (installed vs example), branding scan (no legacy token in new code/docs/referer), generated-artifact determinism (benchmark byte-identical ×2).
- Voice: simple turn unchanged and never blocked; task-class turn calls once; Right-Ctrl unaffected; `voice_task_only` honored.
- Dashboard: settings round-trip; `is_set` never returns the secret.

---

## Rollback plan

1. **Immediate:** `agent.jev.enabled=false` (or `qubit jev disable`) — reverts to today with zero code change; an env-derived internal kill-switch forces off regardless of the file.
2. **Per-session:** disable for the session; deterministic path holds.
3. **Full:** remove `voicebridge/jev/`, the `chat_transport`/orchestrator call sites, and the routes; Jev is stateless/advisory, so **no stored schema or state migration** is required. Existing receipts remain (marked `source: jev`, retention-bounded); no session content was ever written by Jev.
4. **No data to unwind.**

---

## Cost and latency considerations

- Jev is called only on *uncertain* turns; simple turns pay nothing.
- Cost bounded by `daily_usd`, `max_request_chars`, `max_state_tokens`; `daily_usd=0.0` ⇒ no paid calls.
- A network round trip (vendor band ≈ tens–hundreds of ms) is added only to flagged turns, with a hard `timeout_ms` ceiling + circuit breaker; on breach it degrades to local.
- Voice is the most latency-sensitive surface; the uncertainty gate + timeout protect it; the benchmark measures voice responsiveness.
- All vendor speed/cost figures are **unverified here**; the benchmark is the only in-repo evidence.

---

## Unresolved owner decisions

1. **Endpoint/vendor:** official TypeSafe (waitlisted) vs the unofficial community host `jevtypesafe.org` vs OpenRouter Decisions API (in-tree precedent) — who receives Qubit data.
2. **Data egress consent:** whether any `/qubit-chat`/voice content may leave the device at all (default: strict minimization + off).
3. **Model pinning:** accept the moving `~typesafe/jev-latest` alias, or pin a concrete model id.
4. **Enforcement vs advisory for `select_*`:** advisory-only (recommended) vs the owner-gated `tool_hint` kwarg on `voicebridge/planner.py::plan`/`run_session`.
5. **Escalation target naming:** which single profile `agent.jev.escalation_profile` may name.
6. **Escalation default:** keep `api_escalation_allowed=false` until the benchmark proves benefit?
7. **Receipt retention** and whether receipts are user-visible.
8. **Budget/payer** and cap.
9. **Benchmark ownership** (task-suite curator, benefit verdict signer).
10. **Voice disclosure** (spoken vs on-screen only) and whether `voice_task_only` stays default-true.
11. **Pilot scope** (which sessions/profiles enable Jev first).

---

## Explicit statement: Brain4 remains usable without Jev

Brain4 is Qubit's local conversational model and the default for `/qubit-chat` and voice. Jev is an optional, disabled-by-default advisor that at most changes *routing and containment around* a turn. **If Jev is disabled, unreachable, times out, returns a malformed or low-confidence answer, exceeds budget, or is blocked by privacy mode or role scope, Brain4 answers exactly as it does today, with no network call and no behavior change.** Jev never trains or upgrades Brain4, never executes tools, and never sits above Qubit's confirmation, policy, audit, or e-stop authority.
