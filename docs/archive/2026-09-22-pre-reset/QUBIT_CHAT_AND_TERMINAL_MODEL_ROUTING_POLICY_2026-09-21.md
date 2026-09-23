# Qubit Chat and Terminal Model-Routing Policy

**Status:** active architecture policy  
**Date:** 2026-09-21

## Purpose

Qubit has two intentionally different user surfaces. They must share the same canonical backend, sessions, tools, policy, audit, confirmations, and e-stop, while remaining independently customizable for their different latency and context requirements.

## `/chat`: terminal surface

`/chat` is the Qubit-branded Hermes-compatible terminal interface.

It must provide the complete terminal experience and supported Hermes capabilities, including commands, tools, skills, plugins, providers, sessions, history, MCP, OpenClaw operations, errors, retries, cancellation, recovery, and audit behavior.

The `/chat` model is selected through the canonical Qubit model/provider registry. It may use:

- OpenCode;
- OpenRouter;
- another configured API provider;
- a local model only when it satisfies the terminal requirements.

The selected `/chat` model must be executable and support at least 65,536 context tokens. The real provider/model metadata must be checked before the terminal starts. If the model cannot satisfy the context or execution requirement, `/chat` must fail closed with a typed error.

`/chat` must never silently fall back to Qubit-Brain4 because Brain4 is intended for the low-latency native voice path and normally has a smaller context window.

## `/qubit-chat`: native Qubit voice surface

`/qubit-chat` is the native Qubit assistant surface.

Its default local model is Qubit-Brain4 because the priority is fast interaction, voice command recognition, routing, and low latency.

The local voice pipeline remains independently configurable:

- browser speech recognition where available;
- local or configured STT;
- local or configured TTS;
- realtime speech profiles;
- voice activity and interruption behavior.

Brain4 may perform the initial intent classification and task-routing decision. The routing decision must be policy-controlled, auditable, and visible in the session metadata.

## Local escalation and API escalation

When a request is too difficult, too long, or unsuitable for Brain4, `/qubit-chat` may route it to:

1. an optional stronger local model, such as the experimental Ternary Bonsai profile;
2. another configured local model;
3. OpenCode, OpenRouter, or another configured API provider.

The escalation target must be selected through the canonical provider/model registry. It must not create a separate chat backend or separate session store.

The following must remain continuous across escalation:

- session ID;
- message history;
- tool calls and results;
- permissions;
- confirmations;
- policy decisions;
- e-stop state;
- audit records;
- model/provider metadata;
- retry and recovery state.

## Experimental Ternary Bonsai profile

Ternary Bonsai 2 27B may be registered as an optional experimental local profile, for example:

```text
qubit-brainv0.1-ternary-experimental
```

It must not replace Brain4 by default until it has been benchmarked on the target machine for:

- first-token latency;
- tokens per second;
- RAM and VRAM use;
- power use;
- voice-command latency;
- tool-call correctness;
- structured JSON reliability;
- long-context behavior;
- 65,536-token execution;
- refusal and safety behavior;
- API fallback behavior.

The model’s custom ternary runtime requirements, context behavior, and license/provenance must be recorded in its model profile. A model card claim or vendor benchmark is not proof of performance on the Qubit host.

The experimental profile may be used for difficult local tasks from `/qubit-chat`, but it must not become the `/chat` default unless it independently passes the terminal context and reliability gates.

## Brain4 versus local escalation-model benchmarking

Qubit-Brain4 and the experimental Bonsai profile must remain separately registered, selectable model profiles. Adding Bonsai must not replace, delete, rename, or hard-code over Brain4.

Before selecting a default local model, compare both models on the same machine, runtime, prompt set, context sizes, and generation settings. Record results in the model-profile evidence and keep both profiles available for future manual selection.

Required comparison measurements:

- time to first token;
- steady-state tokens per second;
- end-to-end voice-command latency;
- speech-to-intent latency;
- RAM and VRAM usage;
- startup and model-load time;
- power usage where available;
- 4k, 8k, 32k, 65,536, and supported longer-context behavior;
- tool-call selection and argument correctness;
- structured JSON reliability;
- command-routing accuracy;
- refusal and confirmation correctness;
- session/history continuity;
- API-escalation accuracy;
- cancellation, timeout, and recovery behavior.

The fastest model is not automatically the default. The selected `/qubit-chat` local default must be the fastest model that also meets the required voice accuracy, tool reliability, policy, audit, and recovery gates. If Brain4 is fastest and sufficiently reliable, keep Brain4 as default. If the new model is demonstrably better without unacceptable voice latency or safety regressions, it may become the local escalation default only after evidence is recorded and the operator can still switch back.

The model registry must support switching without code changes. At minimum, the operator must be able to select:

- Brain4 as the local voice/router model;
- Bonsai as an optional local escalation model;
- another future local model;
- a configured API model for difficult tasks.

Switching must preserve the active session, transcript, tool state, permissions, confirmation state, audit records, and e-stop state. A model profile must declare its provider, model identifier, runtime, context window, locality, capabilities, measured latency, and verification status.

No model may be promoted based solely on vendor claims. The measured Qubit benchmark is the authority for local default selection.

## Voice actionability

Every supported capability exposed by `/chat` must have a corresponding voice path in `/qubit-chat`, unless it is explicitly classified as `OPERATOR_ONLY` or `SAFE_EQUIVALENT` with documented evidence.

Right-Ctrl is part of this same `/qubit-chat` voice surface. It is a control
trigger, not a separate runtime. Press/release handling, debounce, permission
checks, active-listener state, cancellation, failure, e-stop, and event-journal
records must use the same orchestrator, canonical operation resolver, broker,
policy, audit, confirmation, and session backend as typed `/qubit-chat` turns.
The `/qubit-chat` UI must expose the current Right-Ctrl state and any failure or
unavailable reason.

Voice must call the same canonical operations as the CLI and dashboard:

```text
voice input
→ STT
→ Brain4 intent/router or selected model
→ canonical Qubit operation
→ policy/broker/confirmation/e-stop
→ real provider/runtime
→ shared session/state/audit
→ TTS and rendered result
```

Voice must not directly mutate stores, bypass the broker, bypass confirmation, or invent a parallel implementation.

## User controls

The dashboard and CLI must allow the operator to configure independently:

- `/chat` terminal provider and model;
- `/qubit-chat` local voice/router model;
- local escalation model;
- API escalation provider/model;
- STT profile;
- TTS profile;
- realtime speech profile;
- context minimums;
- routing policy;
- privacy and external-egress policy.

Changing one surface’s model must not silently change the other surface’s model.

## Safety and verification requirements

- API keys must use references and must never appear in logs, URLs, browser output, or session text.
- External egress must follow the configured policy and be auditable.
- E-stop must apply to local and API-routed operations.
- Low-context or unavailable models must fail closed.
- Routing must be testable through text, CLI, dashboard, and voice.
- Model switching must preserve session and audit continuity.
- Every profile must declare provider, model, context window, locality, capabilities, and execution status.
- Future Hermes/OpenClaw skills, plugins, tools, providers, and channels must be able to register their voice intents and routing requirements through the adapter system.

## Permanent summary

```text
/chat
  = Qubit-branded terminal
  = API or selected high-context provider/model
  = full Hermes-compatible terminal capabilities
  = no silent Brain4 fallback

/qubit-chat
  = native Qubit voice assistant
  = Brain4 local-first
  = optional Bonsai/other local escalation
  = optional API escalation
  = shared tools, sessions, policy, audit, confirmations, and e-stop
```
