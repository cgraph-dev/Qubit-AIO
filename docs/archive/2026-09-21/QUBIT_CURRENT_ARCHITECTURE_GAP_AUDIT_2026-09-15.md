# Current Architecture Gap Audit — 2026-09-15

Status: **SOURCE-LEVEL FINDINGS — implementation remains open**

This audit records current code/configuration patterns that explain why the
product can appear configurable or working in a narrow test while remaining
incomplete as a unified Qubit system. It does not change any ledger state.

| ID | Confirmed finding | Evidence | Required repair |
|---|---|---|---|
| PV-01 | The component registry has only `brain`, `stt`, `tts`, `voice`, and `audio` kinds; no canonical API-provider, routing-policy, realtime-voice, or session profile. | `voicebridge/profiles.py:41-60` | Extend one registry/profile schema; do not create a parallel config store. |
| PV-02 | Brain4 is embedded as the default in profile/config/transport result objects. | `voicebridge/profiles.py:54-59,157-166`; `voicebridge/config.py:343-360,428-431`; `voicebridge/chat_transport.py:41-42,361-381`; `voicebridge/providers/contracts.py:66-73` | Resolve all defaults from the canonical profile resolver and preserve a shipped default only as registry data. |
| PV-03 | The normalized provider gateway exists, but important active paths still call local Ollama directly. | `voicebridge/providers/gateway.py:49-73`; `voicebridge/orchestrator.py:2439-2467,2487-2514` | Route chat, planner, intent, summary, vision, and voice through the same provider gateway/profile resolver. Add a test that rejects direct production bypasses. |
| PV-04 | Dashboard chat accepts only the configured/default local model; it cannot select a registered API/profile model for a session. | `voicebridge/chat_transport.py:368-381` | Add session model-profile selection, capabilities/egress validation, persistence, receipts, dashboard and CLI controls. |
| PV-05 | Realtime provider configuration is a provider-specific block rather than a versioned Qubit realtime profile. | `voicebridge/config.py:271-277` | Normalize realtime adapters into the profile registry with capability, consent, device, egress, health, rollback, and cancellation metadata. |
| PV-06 | TTS uses provider-specific config and a hard-coded style string. | `voicebridge/config.py:312-322` | Move voice/style/output/provider choices into user-visible speech-output profiles; preserve compatibility migration and remove unchosen personality defaults from global product config. |
| PV-07 | The legacy direct-tool rollback is explicitly unsecured if `tool_broker` is disabled. | `voicebridge/config.py:422-426` | Remove/deprecate the bypass. Power/autonomous mode must pre-authorize scopes through the broker, never turn the broker off. |
| PV-08 | Current docs/config describe a single English/HyperX/Piper machine profile as if it were broader product policy. | `docs/VOICE_BRIDGE_SYSTEM.md:10-75`; `config/voice*.yaml` | Retain it as the default machine profile while implementing profile-driven languages/devices/providers and visible qualification. |
| PV-09 | The registry defaults and the provider gateway are separate authority candidates. | `voicebridge/profiles.py`; `voicebridge/providers/contracts.py`; `voicebridge/providers/gateway.py` | Establish one resolver and make the old objects adapters or migrate them atomically. |
| PV-10 | Existing test/ledger closure does not prove every UI control, because the current plan had no mandatory selector-to-store evidence map. | historical reports plus `docs/QUBIT_PLAN_INTEGRITY_AUDIT_2026-09-15.md` | Build and enforce a control matrix: selector, request, operation, store, rendered result, recovery, and production-listener test. |

## Required first implementation slice

Before adding another model/provider button or voice alias, implement a single
profile-resolution operation that accepts a profile ID and session/task role,
returns typed effective provider/model/speech settings and egress/budget policy,
and is used by one real chat path end to end. Then migrate callers in bounded
families with tri-surface and restart/rollback tests. This prevents yet another
parallel router or configuration source.

## Relation to upstream parity

The pinned Hermes/OpenClaw audit must identify every local source model,
provider, audio, session, and autonomy workflow. This source-level audit lists
Qubit integration gaps only; it is not an upstream completeness claim.
