# Qubit-Brain4 Lifecycle and Advanced Local Research

Status: **ACTIVE OWNER-DIRECTED PLAN — 2026-09-15**

Qubit-Brain4 is the current default local language-model profile. It is not the
Qubit product, not the authorization layer, and not a permanent dependency.
Qubit remains usable with another local model, an approved API model, or a
future model profile. This document governs Qubit-Brain4 development and its
integration into the product.

The controlling product requirements are:

- `QUBIT_OWNER_AMENDMENT_2026-09-15_PROVIDER_AND_VOICE_PLUGGABILITY.md`;
- `QUBIT_PROVIDER_VOICE_PARITY_PLAN_2026-09-15.md`; and
- `QUBIT_PRODUCT_PARITY_AND_AUTONOMY_PLAN_2026-09-15.md`.

## What the owner authorized

The owner may experiment locally with a Modelfile/persona, LoRA/adapters,
distillation, preference optimization, and weight-level research including
abliteration. These are **research candidate** workflows, not automatic
production changes.

An experimental model may be intentionally less restrictive in what it says.
It cannot bypass Qubit's capability broker, profile scopes, authenticated owner
control, audit, secret isolation, resource budget, cancellation, or emergency
stop. A language model proposes; Qubit decides what may execute.

## Model roles

Qubit supports independently selected profiles for:

| Role | Default | May be changed to |
|---|---|---|
| Main local reasoning | `qubit-brain4` | another validated local profile |
| Fast/local classification or summary | configured local profile | another local or explicit API profile |
| Difficult reasoning/coding/research | local-only by default | an owner-approved named API profile |
| ASR/TTS/realtime speech | current local profiles | separately selected local or approved external profiles |

The active language model, speech profile, provider locality, egress decision,
fallback, and cost/usage must be visible in the Qubit receipt and session UI.

## Candidate lifecycle

1. Create a new immutable candidate ID; never overwrite `qubit-brain4`.
2. Record base artifact, exact revision/digest, license, template, source,
   training method, data policy, hardware/resource cost, and rollback target.
3. Run functional, held-out quality, policy/tool-boundary, privacy, latency,
   VRAM/RAM, failure, and rollback evaluations.
4. Store an evaluation receipt and make the candidate available only as a
   non-default model profile.
5. The owner reviews the receipt and explicitly activates the candidate through
   the profile lifecycle. Activation is atomic and reversible.
6. Keep the previous active profile available until the replacement has passed
   a real session and voice/CLI/dashboard smoke test.
7. Archive or remove candidate artifacts only with an explicit lifecycle
   action; removal never deletes Qubit sessions, memory, reports, or other
   profiles.

## Training-data policy

Training jobs use owner-selected local datasets. The UI/CLI must expose:

- dataset sources and date range;
- redacted versus raw-local mode (new jobs default to redacted);
- which fields are included/excluded, deduplication, retention, and deletion;
- whether any egress is permitted (off by default);
- license/consent provenance and experiment owner; and
- the resulting candidate ID, digest, metrics, and rollback path.

Raw local logs and audio can be selected by the owner under the advanced
research policy. They are not silently collected, uploaded, used for nightly
automatic retraining, or used to replace the active model. Secrets, browser
cookies, credential values, and hidden system prompts remain excluded.

## Practical experiment lanes

### Persona/template lane

Use a new Modelfile and candidate alias to test behavior, context, temperature,
and tool-format changes. Validate the complete runtime request path as well as
direct local inference; direct `ollama run` behavior is not proof of Qubit
behavior.

### Adapter/LoRA lane

Train an adapter against a reviewed task corpus, then compare it against a
held-out corpus and the existing default. Measure task quality, structured
output/tool accuracy, hallucination rate, latency, VRAM, and regression on
normal conversation. Keep adapter/base compatibility and rollback metadata.

### Distillation/preference lane

Use consented examples and explicit quality criteria. Prefer a reviewable,
versioned dataset over implicit “good/bad” inference from logs. A local model
may learn response style and task procedures, but Qubit's permissions and
policy stay outside the model weights.

### Weight-research lane

Weight surgery, including abliteration, is allowed only in the experimental
lane. Its evaluation must document changed behavior and possible safety/quality
trade-offs. It must not auto-promote, replace the active default, or be
described as authorization control.

## Product integration requirements

The model profile registry—not hard-coded aliases—must supply the model used by
chat, agent, planner, intent routing, summaries, vision, evaluation, CLI,
dashboard, and VoiceBridge. Direct provider calls are open defects unless they
use the canonical resolver and emit the same receipt/audit fields.

Qubit must support local-only operation. A hard question may route to a named
approved API model only under the selected model/autonomy profile and egress
policy. Fallback across locality boundaries is opt-in, typed, visible, and
cancelable. A provider failure never fabricates an answer or silently changes
the model identity.

## Definition of done

Brain4 integration is complete only when the user can list, inspect, test,
clone, activate, deactivate, roll back, and remove model profiles from Qubit
CLI and dashboard; use corresponding non-secret voice commands; run a
candidate safely; choose local/API/realtime routes explicitly; and retain one
session/memory/broker/audit path regardless of model choice.
