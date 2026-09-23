# Owner Amendment — Provider and Voice Pluggability

Status: **OWNER-APPROVED — 2026-09-15**

This amendment clarifies and extends the frozen Qubit acceptance contract. It
does not weaken the Hermes-foundation, OpenClaw-integration, parity, rebrand,
evidence, or independent-verification requirements. It replaces **per-action
confirmation** with owner-created pre-authorization only for operations that
fall wholly inside an active scoped-autonomy profile, as defined below.

## Decision

Qubit is the product and control plane. The complete pinned Hermes source is
the local runtime foundation; OpenClaw capability implementations are added to
that foundation; Qubit VoiceBridge is the shared natural-language control
surface. Neither an upstream product nor a particular model is Qubit's product
identity.

`qubit-brain4` is the current default local reasoning-model profile. It is a
separate, replaceable model artifact—not a hard-wired runtime requirement,
policy authority, credential store, or product identity. It may be evaluated,
edited, upgraded, disabled, replaced, or removed using Qubit's versioned model
lifecycle, without removing Qubit's Hermes/OpenClaw functionality.

## Required configurable-provider architecture

Qubit must expose versioned, user-controlled profiles for these independent
roles:

1. **Language/reasoning provider and model** — a local Ollama-compatible
   profile, an approved OpenAI-compatible or other documented API profile, and
   future local or remote adapters.
2. **Task routing profile** — the user chooses local-only, an explicitly named
   provider/model, or an allowed policy-controlled routing chain for simple,
   hard, coding, tool, summarization, and vision tasks.
3. **Speech-input profile** — ASR engine/model, language, device, VAD, wake or
   push-to-talk behavior, and quality/resource settings.
4. **Speech-output profile** — TTS engine/model/voice, output device, language,
   style, latency/quality setting, and fallback policy.
5. **Realtime voice profile** — where an approved external realtime provider
   combines conversation, ASR, and/or TTS, it is a selectable adapter profile;
   it must not bypass the Qubit session, broker, audit, e-stop, consent, or
   redaction path.

Language-model selection and speech selection are deliberately separate. A
user may use Qubit-Brain4 with local speech, Qubit-Brain4 with an approved
external TTS provider, an API language model with local ASR/TTS, or an approved
realtime voice provider. An external provider is never selected silently and
never receives data outside the user's configured egress and privacy policy.

## Mandatory behavior

- Profiles are named, versioned, persisted in the canonical Qubit state tree,
  exportable without credentials, and available from the `qubit` CLI, Qubit
  dashboard, and voice where the request has unambiguous non-secret arguments.
- Each profile supports inspect, validate/test, activate, deactivate, clone,
  edit, rollback, and remove. Removing an active profile first requires a safe
  replacement or typed recovery state; it must not corrupt sessions.
- Credentials are references to the Qubit secret store/environment, never raw
  profile values, logs, reports, prompts, URLs, browser state, or voice
  arguments. Voice must guide a user to the safe dashboard/CLI secret flow
  instead of accepting or repeating a secret.
- Provider capabilities (streaming, tools, structured output, vision, audio,
  context limit, cost/budget, rate limits, locality, data residency) are
  explicit and validated before a task runs.
- Fallback is visible in the receipt and UI. It is disabled by default across
  locality boundaries; an API model may not silently replace local Brain4, and
  a local model may not pretend to be an API result.
- All model and speech adapters share exactly one Qubit session identity,
  operation registry, broker, policy, audit trail, cancellation path,
  emergency-stop behavior, resource budget, and persisted conversation/memory
  contract. “One voice path” means one authoritative control path, not one
  immutable vendor or model.
- Qubit-Brain4 training/evaluation is a separate model-lifecycle project.
  Candidate artifacts use separate aliases and provenance; promotion requires
  held-out quality, safety, privacy, latency/resource, rollback, and license
  evidence. No automatic raw-log training, self-modifying model swap, or
  safety-removal workflow is permitted.

## Owner-directed advanced local-model research

The owner authorizes advanced experimentation on locally owned Qubit-Brain4
candidate artifacts, including Modelfile changes, LoRA/adapter training,
distillation, preference optimization, and weight-level research such as
abliteration. This authorization applies to **separately named experimental
artifacts only**. It does not replace the production alias in place, change
the Qubit broker/policy, expose credentials, or silently promote a candidate.

Training data may include owner-selected local Qubit logs and feedback. The
implementation must make the dataset selection, raw-data inclusion, retention,
export, license, and experiment result visible to the owner. It must support
redacted and raw-local modes, default new jobs to redacted data, keep raw data
on the local machine unless the owner separately enables egress, and never use
unreviewed records to modify a production model automatically. A candidate is
promoted only through a named, reversible profile change after the owner has
reviewed the evaluation receipt.

An experimental model may have less restrictive response behavior, but it
still cannot directly bypass Qubit's capability broker, scope policy, audit,
secret isolation, cancellation, resource limits, or emergency stop. Model
alignment is not the authorization system.

## Owner-directed autonomy policy

The owner authorizes a configurable **Power User / Autonomous** policy mode.
In that mode, Qubit may execute operations without per-operation confirmation
when the owner has pre-authorized the exact capability scopes, targets, time
window, concurrency, network/provider policy, and resource budget in an
autonomy profile. This includes scheduled and unattended work.

Autonomy profiles must be visible, exportable without secrets, pausable,
expiring, revocable, and bound to an owner/session/device. They must record
every operation and external side effect in the existing audit/receipt system.
The global emergency stop, cancellation, resource ceilings, authenticated
owner control, and secret/cookie redaction remain non-bypassable. A model,
webpage, skill, plugin, or received message cannot grant or widen its own
autonomy scope.

## Parity and completion requirements

The final Hermes/OpenClaw local-product parity inventory must include all
upstream model/provider, model-switching, model-health, credentials-reference,
voice/audio, streaming, session, and provider-failure workflows that are in
the pinned local source. Each applicable workflow needs one real backend and
equivalent Qubit CLI, dashboard, and VoiceBridge surface with the same outcome,
typed failure, persistence, cancellation, recovery, redaction, and audit
semantics.

The dashboard Models & Providers workspace and Setup/Voice workspace must be
fully functional—not status cards. They must visibly show profile selection,
capabilities, locality/egress disclosure, health, budgets, credential-reference
state, test results, current session choice, recovery, and rollback.

No completion claim is permitted merely because a model alias exists, an API
is reachable, a UI selector renders, or a provider test is mocked. Completion
requires the acceptance tests described in
`QUBIT_PROVIDER_VOICE_PARITY_PLAN_2026-09-15.md` and independent reproduction.

## Scope boundary

This amendment covers the local Qubit product and its user-configured providers.
A future hosted Qubit account/portal, billing service, model marketplace, or
remote connector infrastructure is separate product work and may not be
fabricated as a local implementation. Required legal attribution, provenance,
and source comparison records retain upstream names only where necessary;
normal Qubit product surfaces remain Qubit-branded.
