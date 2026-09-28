# Qubit Voice Actionability Contract

**Date:** 2026-09-21

**Status:** AUTHORITATIVE QUBIT CONTRACT — normative and binding.
This document defines what it means for a capability to be actionable through
voice, the record that must exist for every such capability, and the rules that
bind voice to the same canonical runtime as every other surface. It is not a
status report, not evidence of completion, and not a substitute for the ledgers,
manifests, and registries it names. Where this contract and a status artifact
disagree, this contract governs how the disagreement is read; the artifact
remains the source of the measured values.

---

## 1. Purpose and scope

Qubit must make every supported Qubit, Hermes, and OpenClaw capability the user
can perform actionable through voice. Voice is a first-class interface to the
same product, not a reduced or parallel product.

This contract binds:

- the voice surface of Qubit (`voicebridge/`), including the orchestrator and
  `voicebridge/voice_commands.py`;
- the per-capability voice support record that must exist for every capability
  in the Qubit surface, whether it originates in Hermes, OpenClaw, or Qubit-native
  work;
- the model and provider routing that voice uses for chat and escalation;
- the pluggable speech roles (ASR, STT, TTS, realtime profiles);
- the mechanism by which future skills and plugins become voice-actionable.

It consumes the parity definition, comparison targets, and dispositions in
[QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md](QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md),
and the upgrade procedure in
[QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md](QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md).
It does not restate their contents.

---

## 2. Dispositions

Dispositions are exactly four, and are the same dispositions defined in the
capability parity contract. No other value is valid for a capability or for a
voice support record.

| Disposition | Meaning for voice |
| --- | --- |
| IMPLEMENTED | The capability is reachable through voice and executes through the canonical operation via the full behavioral chain. |
| SAFE_EQUIVALENT | Voice reaches a working allowed counterpart and explicitly refuses the unsafe upstream variant with a typed refusal. The refusal does not stand in for a missing allowed counterpart. |
| OPERATOR_ONLY | Voice is wired to the canonical operation, but a genuine live prerequisite (credentials, hardware, accounts, OAuth, CAPTCHA/2FA) prevents end-to-end execution in the current environment. The row must carry a runbook and surface a typed unavailable state. |
| OPEN | Anything not terminal. OPEN is the fail-closed default. |

A capability is not voice-actionable because a route returns HTTP 200, because a
voice entry exists in a registry, or because a typed refusal exists. Presence
without exercised behavior is OPEN.

Nothing in this document asserts that any capability is VERIFIED. Disposition
values describe code-side and operator-side state; they are not a claim that an
independent verification pass has run. Where a value is not recorded, it is
UNKNOWN / NOT VERIFIED.

---

## 3. The single-authority rule (no shadow backend)

Voice MUST NOT implement a separate shadow backend. Voice, the CLI, the
dashboard, `/chat`, and `/qubit-chat` MUST call the SAME canonical operations and
the SAME single runtime, session, and store authority.

The rules are:

1. A voice command resolves to exactly one canonical operation. That operation is
   the operation the CLI and dashboard also invoke. Voice does not own a
   divergent copy of the behavior.
2. Voice does not open a private runtime, session store, or state database that
   differs from the shared authority.
3. Where a canonical operation has a side effect, voice obtains the same
   policy, confirmation, e-stop, and audit mediation as every other caller.
   Voice must not bypass, weaken, or re-implement those checks.
4. Where a canonical operation is unavailable to voice for a structural reason
   (for example, it requires hardware voice must never activate), voice returns
   typed guidance naming the exact canonical command rather than fabricating an
   execution.

The single session and store authority is `QubitSessionStore` over the vendored
`qubit_state.SessionDB` (SQLite `state.db`), at
`voicebridge/qubit_session_store.py:1-19,43`. The chat turn path reaches it
through `voicebridge/chat_transport.py:369-415`, and the compatibility layer
fails closed on drift at `voicebridge/qubit_compat.py:502-533`.

A capability whose voice path diverges from the canonical operation, or whose
voice path writes state through a separate authority, is OPEN regardless of how
it behaves in isolation.

---

## 4. Voice pipeline and utterance-to-operation mapping

The voice pipeline stages are:

```
mic -> ASR -> classify -> route -> worker -> speak
```

This chain is the documented pipeline at `voicebridge/orchestrator.py:1`.

Speech input is selected through `voice.input_engine`:

- `browser` — supported Chrome SpeechRecognition/Web Speech API;
- `local` — Qubit’s configured local STT adapter;
- `auto` — browser first, local fallback with an explicit visible reason.

Right-Ctrl starts and stops the selected input engine. Browser permission,
availability, privacy/network status, interim/final transcript state, local
fallback, and errors must be represented in `/qubit-chat`. Regardless of the
selected input engine, the transcript enters the same classify, route, broker,
policy, audit, confirmation, e-stop, session, and worker path.

An utterance maps to a canonical operation as follows:

1. **Capture (mic).** Audio is captured. Voice never activates the microphone on
   behalf of a capability that is declared CLI-only or hardware-only; such a
   request returns typed guidance.
2. **ASR.** Speech is transcribed. The default STT is sherpa/parakeet with a
   moonshine fallback (`voicebridge/config.py:256-266`); whisper is an optional
   adapter (`voicebridge/asr/whisper.py`).
3. **Classify.** The transcript is classified into a voice intent kind.
4. **Route / resolve.** The utterance is resolved against the canonical voice
   registry (`voicebridge/voice_commands.py:465`, `resolve`). Resolution returns
   a match, an unknown, a clarification request, or an ambiguity with candidates.
   Only a `kind == "match"` resolves to a canonical operation. A non-match is a
   fail-closed outcome, never a manufactured execution.
5. **Confirm.** Operations whose tier is mutating or destructive, or that are
   financial, require confirmation
   (`voicebridge/capabilities.py:69-96`, `needs_confirmation`). Confirmation is
   issued and consumed by the confirmation store
   (`voicebridge/capabilities.py:141-242`).
6. **Worker / execute.** The canonical operation executes through
   `voicebridge/voice_commands.py:9461`, `execute`. Policy and e-stop are
   checked before side effects
   (`voicebridge/capabilities.py:271-330`, `PolicyEngine`).
7. **Speak.** The result is spoken. The default TTS is piper
   (`voicebridge/config.py:312-313`); adapters include piper, hybrid, kokoro,
   edge, and openai_tts.

A realtime or duplex lane is not part of the default pipeline. An OpenAI
Realtime profile remains resolvable and is logged, but the orchestrator starts
no realtime adapter (`voicebridge/orchestrator.py:659-669`).

Every capability's voice support record (section 5) must state where it enters
this chain and which of these stages it uses.

---

## 5. Per-capability voice support record

Every supported Qubit, Hermes, and OpenClaw capability must have a voice support
record. Each record must populate all sixteen fields below, or explicitly mark a
field UNKNOWN / NOT VERIFIED with the reason it could not be determined. A
record with an omitted field is non-conforming. A capability with no record is
OPEN by default.

The sixteen required fields:

| # | Field | Required content |
| --- | --- | --- |
| 1 | **canonical operation** | The one canonical Qubit operation the capability resolves to, with its owner. Not a voice-specific duplicate. |
| 2 | **voice intent name** | The voice intent identifier the classifier/routing layer emits for this capability. |
| 3 | **aliases and natural-language examples** | The declared aliases and representative utterances (for example, natural-language phrasings) that must resolve to this capability. |
| 4 | **required slots/entities** | Every argument the operation needs, which are required versus optional, their types, and how each is extracted or clarified when missing. |
| 5 | **confirmation requirements** | Whether confirmation is required, at what tier, and the confirmation lifecycle (issue, TTL, consume, replay, expiry). |
| 6 | **authorization and ownership** | The identity and authorization required, the owning principal, and the ownership boundary of the operation. |
| 7 | **policy and e-stop checks** | The policy decisions, broker mediation, and emergency-stop enforcement that gate the side effect. |
| 8 | **execution path** | Where the operation enters the pipeline and what it calls, from resolution through the worker to the real backend. |
| 9 | **success response** | The result the user hears/speaks on success, and how it is derived from the real result. |
| 10 | **refusal response** | The typed refusal returned when the request is unsupported, unauthorized, malformed, or dangerous. |
| 11 | **retry behavior** | Whether the operation is retryable, under what conditions, and with what idempotency guarantees. |
| 12 | **cancellation behavior** | How an in-flight or pending request is cancelled and what cancellation guarantees. |
| 13 | **recovery behavior** | How a partial failure is detected, reported, and recovered, including revalidation from the journal where applicable. |
| 14 | **audit record** | The audit event(s) emitted, including actor, session, operation, decision, and redaction applied. |
| 15 | **dashboard/CLI equivalent** | The dashboard control and CLI invocation that reach the same canonical operation, proving there is no shadow path. |
| 16 | **model/provider routing behavior** | Which model and provider serve the operation, whether local or escalated, and how session and audit continuity are preserved across any switch. |

### 5.1 Record template

```
capability:        <id>
source:            <Qubit|Hermes|OpenClaw>
disposition:       <IMPLEMENTED|SAFE_EQUIVALENT|OPERATOR_ONLY|OPEN>
canonical_operation: <operation id + owner>
voice_intent:      <intent name>
aliases:           [<alias>, ...]
examples:          [<natural language utterance>, ...]
slots:             [<name: type (required|optional), extract/clarify rule>, ...]
confirmation:      <none|required: tier, ttl_s, consume, replay, expiry>
authorization:     <principal, scopes, ownership boundary>
policy_estop:      <checks performed before side effect>
execution_path:    <mic->ASR->classify->route->worker->speak entry point + calls>
success_response:  <spoken/typed success result>
refusal_response:  <typed refusal code(s)>
retry:             <none|retryable: conditions, idempotency>
cancellation:      <pending/in-flight cancel semantics>
recovery:          <partial-failure detection + recovery, journal revalidation>
audit_record:      <event(s), fields, redaction>
cli_dashboard:     <CLI op + dashboard control, same canonical operation>
routing:           <model/provider, local|escalated, continuity guarantee>
notes:             <UNKNOWN / NOT VERIFIED items and reasons>
```

---

## 6. Capability coverage: Qubit, Hermes, OpenClaw

The record in section 5 applies to every capability in the Qubit surface,
regardless of origin:

- **Qubit-native capabilities** — the operation is owned by Qubit and the record
  documents the Qubit path.
- **Hermes-derived capabilities** — the record documents the Qubit adapter that
  realizes the upstream behavior, against the contractual Hermes revision named
  in the capability parity contract.
- **OpenClaw-derived capabilities** — the record documents the Qubit adapter that
  realizes the upstream behavior, against the contractual OpenClaw revision named
  in the capability parity contract.

Derived capabilities are subject to the same no-shadow rule: their voice path
must reach the same canonical Qubit operation as their CLI and dashboard paths,
not a separate re-implementation of the upstream behavior.

The count of capabilities that currently have a conforming voice support record
is UNKNOWN / NOT VERIFIED in this document. This contract does not assert a
count, and no count may be inferred from the existence of the voice registry
(`voicebridge/voice_commands.py`) alone.

---

## 7. Chat, model, and provider routing for voice

### 7.1 `/qubit-chat`

`/qubit-chat` uses fast local Brain4 voice by default. The default local chat
model is `qubit-brain4` (`voicebridge/chat_transport.py:42`;
`voicebridge/providers/resolver.py:46,216-226`), provider `ollama`, with
`egress_consent` deny.

The `/qubit-chat` backend paths are:

- streaming: `POST /api/qubit/chat/stream` -> `h_chat_stream`
  (`voicebridge/qubit_compat.py:3054-3068`) -> `stream_turn` in
  `voicebridge/chat_transport.py`;
- non-streaming: `POST /api/qubit/chat` -> `h_chat_turn`
  (`voicebridge/qubit_compat.py:2622-2646`).

### 7.2 Difficulty-based escalation

Difficult requests MAY escalate to configured API models. Escalation is opt-in
and fail-closed:

- Cloud escalation is disabled by default: `frontier.enabled=False`,
  `route="local"` (`voicebridge/config.py:392-394`).
- `"auto"` is opt-in escalation and never escalates on untrusted text
  (`voicebridge/providers/gateway.py:1-6`).
- An explicit frontier request fails closed.

Escalation is a routing decision, not a new execution authority. The escalated
model serves the same canonical operation and the same session and store.

### 7.3 Session and audit continuity across switches

Model/provider switching MUST preserve session and audit continuity. Switching
the model or provider for a turn must not fork the session, must not start a new
audit lineage, and must not route the turn to a different session or store
authority than the one defined in section 3. A switch that breaks session or
audit continuity is non-conforming.

---

## 8. Replaceable speech roles

Speech recognition, STT, TTS, and realtime profiles are replaceable roles
(pluggable roles). The default configuration and available adapters are:

| Role | Default | Adapters |
| --- | --- | --- |
| STT | sherpa/parakeet (fallback moonshine), `voicebridge/config.py:256-266` | whisper optional adapter, `voicebridge/asr/whisper.py` |
| TTS | piper, `voicebridge/config.py:312-313` | piper, hybrid, kokoro, edge, openai_tts |
| Realtime | none active by default, `voicebridge/orchestrator.py:659-669` | OpenAI Realtime profile resolvable/logged; no adapter started |

Replacing a role must not change which canonical operation a resolved intent
invokes, must not change confirmation, policy, e-stop, or audit behavior, and
must not create a separate session or store. The role is a transport concern;
the operation authority is unchanged.

---

## 9. Future skills and plugins

Future skills and plugins must automatically become voice-actionable through
declared capability metadata or an adapter. The mechanism is:

1. A skill or plugin declares its capability metadata. Capability declarations
   are validated against the known capability set; unknown identifiers are
   dropped with a warning as the fail-closed choice, so a plugin can never
   surface a capability the build cannot grant.
2. A declared capability is registered as a `CapabilitySpec`
   (`voicebridge/capabilities.py:69-96`) with its tier, summary, scopes, timeout,
   and financial flag, which determine confirmation requirements
   (`voicebridge/capabilities.py:94-96`).
3. The capability becomes reachable through the same voice resolution path as
   built-in capabilities, subject to the same confirmation, policy, e-stop, and
   audit mediation.
4. Where a plugin cannot be represented directly as metadata, an adapter maps it
   to a canonical operation; the adapter is subject to the same no-shadow rule.

A plugin capability that declares metadata but has no execution path, or whose
execution path diverges from the canonical runtime, remains OPEN.

---

## 10. Typed refusals

Unsupported or dangerous requests must receive typed refusals and must never
execute silently.

- A request that does not resolve to a canonical operation returns a typed
  non-match outcome (unknown, clarify, or ambiguous with candidates), not a
  fabricated execution.
- A request denied by authorization, policy, or e-stop returns the typed denial
  for that condition.
- A request that requires a live prerequisite the environment lacks returns a
  typed unavailable state, consistent with OPERATOR_ONLY, including the exact
  prerequisite and a canonical command where one applies.
- Upstream WebSocket dialects `/api/pty`, `/api/console`, `/api/ws`,
  `/api/events`, `/api/pub`, and `/api/audio/speak-stream` are typed-gated or
  refused (`voicebridge/webui.py:926-948`).

A refusal counts only as SAFE_EQUIVALENT when a working allowed counterpart
exists and is reachable. A refusal standing alone is OPEN.

---

## 11. Voice surfaces and their canonical backends

| Surface | Backend | Notes |
| --- | --- | --- |
| `/qubit-chat` | `POST /api/qubit/chat/stream` -> `h_chat_stream` (`voicebridge/qubit_compat.py:3054-3068`) -> `stream_turn`; non-streaming `POST /api/qubit/chat` -> `h_chat_turn` (`voicebridge/qubit_compat.py:2622-2646`) | Fast local Brain4 voice by default. |
| `/chat` | One WebSocket `/agent/api/pty` (`voicebridge/pty_ws.py`; `frontend/qubit-web/src/pages/TerminalChatPage.tsx`) | Renders the vendored TUI terminal; ticket via `POST /agent/api/auth/ws-ticket`. |
| Voice | mic -> ASR -> classify -> route -> worker -> speak (`voicebridge/orchestrator.py:1`) | Resolves via `voicebridge/voice_commands.py`. |
| CLI | Canonical operations invoked directly. | Must match the voice and dashboard canonical operation. |
| Dashboard | Canonical operations invoked through controls. | Must match the voice and CLI canonical operation. |

These surfaces are different transports over one authority. They must not
diverge in the canonical operation they invoke, the session and store they use,
or the policy and audit they apply.

---

## 12. Unknowns and not-verified items

The following are UNKNOWN / NOT VERIFIED in this document and must not be read as
established:

- The count of capabilities with a conforming voice support record, and the
  count by disposition. No count is asserted here.
- Per-capability field values not recorded in a ledger, manifest, or registry.
  A missing value is UNKNOWN / NOT VERIFIED, not an implicit pass.
- Any claim that voice actionability has been independently verified. This
  document is a contract, not a verification result; independent verification is
  governed by
  [QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md](QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md).
- Any latest-revision coverage statement for Hermes or OpenClaw beyond what the
  capability parity contract records.

---

## 13. Normative references

Active sibling documents:

- [QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md)
- [QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md](QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md)
- [QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md](QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md)
- [QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md](QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md)
- [QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md](QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md)
- [QUBIT_DOCUMENTATION_INDEX_2026-09-21.md](QUBIT_DOCUMENTATION_INDEX_2026-09-21.md)

Frozen contracts and owner records physically stored at
`docs/archive/2026-09-21/` that remain authoritative by content:

- [QUBIT_OWNER_AMENDMENT_2026-09-15_PROVIDER_AND_VOICE_PLUGGABILITY.md](archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-15_PROVIDER_AND_VOICE_PLUGGABILITY.md)
- [QUBIT_VOICE_TO_TEXT_WORKING_PROFILE_2026-09-16.md](archive/2026-09-21/QUBIT_VOICE_TO_TEXT_WORKING_PROFILE_2026-09-16.md)
- [QUBIT_PROVIDER_VOICE_AUDIT_2026-09-15.md](archive/2026-09-21/QUBIT_PROVIDER_VOICE_AUDIT_2026-09-15.md)
