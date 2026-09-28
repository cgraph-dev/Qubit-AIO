# Qubit Provider, Model, and Voice Parity Plan

Status: **ACTIVE OWNER-DIRECTED PLAN — 2026-09-15**

Authority order: the frozen acceptance contract, its dated owner amendments,
`QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`, and then this implementation plan.
This plan adds completion work; it never converts an unimplemented workflow
into a status tile, a mock, or a safe-equivalent label without the required
real behavior and evidence.

## Outcome

Deliver one local Qubit product built on the pinned Hermes foundation with
OpenClaw capabilities and one Qubit control plane. Qubit-Brain4 is the default
local model profile, but every supported model, API provider, ASR provider,
TTS provider, realtime voice provider, device, and routing policy is
user-configurable, replaceable, testable, reversible, and governed by the same
Qubit safety/operation system.

## Non-negotiable architecture

```text
CLI / Dashboard / Voice / API
              |
      Qubit profile resolver
              |
  model router + speech adapters
              |
 one session / broker / policy / audit / e-stop / memory / scheduler
              |
Hermes foundation + OpenClaw capabilities + Qubit native adapters
```

There is one authoritative operation/session/policy path. Multiple selectable
providers are adapters behind it, not duplicate chat servers, secret stores,
memory stores, routers, or voice listeners.

## Workstream P0 — evidence-led inventory and misconfiguration audit

Before changing behavior, generate a machine-readable inventory of every:

- Hermes model/provider/audio/voice workflow in the pinned local source;
- OpenClaw model/runtime/provider/audio workflow in the pinned local source;
- Qubit model, router, ASR, TTS, realtime, vision, embedding, summarizer, and
  provider caller;
- hard-coded model alias, URL, API key variable, direct HTTP call, direct
  Ollama call, fallback branch, duplicated config key, stale model alias, and
  upstream-branded normal route.

For each row record source symbol, current caller, expected backend operation,
CLI/dashboard/voice route, test, persistence location, security/egress policy,
and disposition. Rows with a static page, dead control, incompatible store,
or success not verified through the production caller remain open.

Deliver `docs/QUBIT_PROVIDER_VOICE_AUDIT_2026-09-15.{md,json}` plus regression
tests that fail when a new direct provider path bypasses the gateway. Never
put credential values, machine-private paths, cookies, or audio in the audit.

## Workstream P1 — canonical contracts and registry

Implement/complete typed contracts and a canonical persisted registry for:

- `LanguageModelProvider`: chat, stream, capabilities, health, usage, cancel;
- `ModelProfile`: provider, model, role routing, locality, egress consent,
  context/tool/vision/audio requirements, budgets, fallback chain, and
  rollback target;
- `SpeechInputProfile`: provider, model, device, VAD, language, PTT/wake;
- `SpeechOutputProfile`: provider, model/voice, output, language, style;
- `RealtimeVoiceProfile`: provider, model, duplex capabilities, consent,
  audio/data egress, fallback; and
- `ProviderProfile`: endpoint, capability declaration, credential reference,
  health/probe policy, provenance, quota and cost metadata.

Do not create a second registry beside the existing profiles/config/state
plane. Migrate old configuration atomically, preserve a backup, recognize
legacy aliases, and make failed validation leave the selected profile unchanged.

## Workstream P2 — provider adapters and routing

1. Make the local Ollama-compatible adapter profile-driven; Brain4 is only the
   shipped default profile.
2. Make the existing approved OpenAI-compatible/frontier provider implementation
   profile-driven. Generalize only from documented provider contracts; add a
   new provider only with provenance, capability declaration, egress, secret,
   timeout, retry, budget, cancellation, and redaction behavior.
3. Add realtime-voice adapter support as an optional profile boundary. A
   provider that combines speech and language still passes all utterances,
   tool calls, confirmations, and output through Qubit policy/session/audit.
4. Route a hard question to an API only when the selected profile/route allows
   it. The receipt must show exact provider/model, reason, egress decision,
   usage/cost where available, and any fallback.
5. Make local-only mode technically enforce no external provider call—not just
   hide a UI selector. API failure must be typed and recoverable; never return
   fabricated answers or silently substitute an external/local model.

## Workstream P3 — real controls on all Qubit surfaces

### CLI (`qubit`)

Provide documented, JSON-capable commands for profile list/show/create/edit/
clone/test/activate/deactivate/remove/rollback; task/model route selection;
provider health/capabilities; voice input/output/realtime profile selection;
and session-level override/reset. Secret values must be supplied through the
existing protected secret/reference workflow, never command history.

### Dashboard

Build fully working Models & Providers and Voice & Audio workspaces from the
preserved Hermes information architecture. Controls must include profile
selection, capability/health, local-vs-external disclosure, budgets, test
action, active-session override, retry/recovery, rollback, device selection,
and typed unavailable states. Every control must call the same operation as
CLI; test every form, menu, modal, keyboard route, error state, and restart.

### VoiceBridge

Support spoken non-secret intents such as “use my local brain,” “use my
research model for this session,” “switch speech to my local voice,” “what
model and voice are active?”, “test my voice profile,” and “reset this session
to defaults.” Natural language maps to a profile ID/alias after ambiguity
resolution. Risky/effective changes use the shared approval journal. Voice
never accepts/repeats API keys, raw endpoint secrets, or bulk imported config;
it gives the exact safe CLI/dashboard handoff instead.

## Workstream P4 — Hermes/OpenClaw functional parity and rebrand audit

Use the pinned source as an oracle, not a marketing claim. Audit every local
page, route, command, model/provider setting, chat session control, audio/
voice control, tool panel, plugin, board, achievement, dialog, and background
workflow. For each one prove a real Qubit production call, not merely a route
that returns 200. Reconcile the UI with source screenshots and DOM/control
inventories at desktop, tablet, and narrow mobile widths. Fix layout drift,
dead controls, wrong text, stale aliases, duplicate stores, placeholder data,
and branding leaks.

Normal product paths, service names, URLs, browser titles, CLI help, logs,
generated artifacts, and UI text must use Qubit. Preserve Hermes/OpenClaw
names only in legal notices, license/provenance records, isolated source
fixtures, or explicitly documented migration compatibility layers.

## Workstream P5 — Qubit-Brain4 lifecycle

Keep model development separate from product functionality:

- candidate models/adapters receive new immutable IDs and provenance;
- evaluation uses consented, redacted data and held-out test cases;
- promotion is a confirmed profile change with a rollback target;
- deleting a model artifact is an explicit lifecycle operation and cannot
  delete sessions, memory, reports, or fallback profiles;
- no unreviewed raw conversation/audio collection, automated nightly training,
  weight-safety removal, or self-promotion is allowed.

## Workstream P6 — verification and release

Batch implementation by workstream. During a workstream use static audit,
imports, compilation, and focused tests; run the complete suite only once the
implementation batch is ready. Before handoff, run all required final gates:

- per-provider contract, egress, secret/redaction, cancellation, retry,
  fallback, rate-limit, budget, persistence/restart, migration, and rollback;
- CLI/dashboard/voice tri-surface tests through the production operation;
- live local Brain4 and local speech qualification when hardware is available;
- an optional approved API and realtime voice canary with a test credential,
  never an unbounded production account;
- every dashboard route/control/form/modal/stream, screenshot/DOM comparison,
  keyboard/a11y/responsive checks;
- full hermetic test suite with warnings treated as errors; package, clean
  install, upgrade, rollback, uninstall, service, secret, provenance, source
  inventory, dead-code, branding, and independent-verifier gates.

## Definition of done

This work is not done until all model/provider/voice rows in the current
Hermes/OpenClaw/Qubit inventory are real Qubit workflows or formally terminal
only where the owner amendment permits it; all normal routes are Qubit-branded;
every visible control works through the shared backend; Brain4 can be replaced
without loss of service; configured API/realtime adapters work only with clear
consent; and an independent verifier reproduces the evidence.

## Canonical browser voice-input decision — 2026-09-16

Authoritative settings record: `docs/QUBIT_VOICE_TO_TEXT_WORKING_PROFILE_2026-09-16.md`.

The preferred online speech-input provider is the working Chrome/Chromium
`SpeechRecognition` path used by Qubit Chat. Preserve `en-US`, interim results,
and one alternative, then send the final transcript through the same Qubit
backend used by typed chat, CLI, and voice. Do not substitute Hermes ASR or
claim that Chrome’s private recognition engine is embedded in Qubit. Keep
local Sherpa Parakeet as the explicit offline/private fallback. Full provider
parity must test both paths and disclose browser permission/network behavior.
