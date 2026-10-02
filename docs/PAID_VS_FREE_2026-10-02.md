# Paid vs free map — 2026-10-02

Owner directive: "I don't want to pay anyone." Nothing Qubit builds may
REQUIRE spending. The owner's own keys stay untouched and working — paid
paths remain available as opt-in, but the default on every surface below
is the free/local path. Every claim here was verified by reading config
defaults and code paths (grep), never by spending.

New flag from the free-first lane: `intent.web_paid_first` (bool,
default `false` = free-first). Read in
`voicebridge/chat_transport.py::_web_paid_first`; absent or unreadable
degrades to `false`. Set `intent.web_paid_first: true` in `voice.yaml`
to restore the paid-first order.

| # | Paid / hosted touchpoint | Free / local path | Current default | Zero-spend owner action |
|---|---|---|---|---|
| 1 | Web search via paid key (Perplexity `sonar-pro` through the frontier gateway; `voicebridge/orchestrator.py::answer_web_query`; key reference `intent.perplexity_env`) | No-key DDG search (`voicebridge/chat_transport.py::_ddg_web_fallback` over the repo `ddg_search` helper) | Free-first: DDG runs first and a hit wins with no paid call; paid runs only when DDG misses AND the frontier policy allowlists the model AND a key is present (frontier ships `enabled: false`, empty allowlist — `voicebridge/config.py` frontier block) | Leave `intent.web_paid_first` unset/`false`; keep paid keys as opt-in only |
| 2 | Chat answers via hosted API models (deepseek / gateway API profiles, API escalation) | Local Ollama tier (brain / spark profiles; `conversation_backend: local`, `local_llm.enabled: true` — `voicebridge/config.py` local_llm block, `config/voice.example.yaml`) | Local default on both chat surfaces | Keep the local default; API profiles only via an explicit escalation choice |
| 3 | Spoken replies via hosted TTS (network TTS module, key-gated, opt-in through `tts.provider: openai`) | Local piper TTS (`tts.provider: piper` default, `piper_backend: sherpa`; every hosted failure falls back to piper — `voicebridge/orchestrator.py` TTS loader) | piper | Keep `tts.provider: piper` |
| 4 | Media generation providers (image/music/video/speech rows keyed by credential reference: `OPENAI_API_KEY`, `FAL_KEY`, `GOOGLE_API_KEY` — `voicebridge/media_gen.py::PROVIDER_CAPS`) | No transport adapter for most pairs, so they answer typed gaps (`MEDIA_*`) instead of spending; speech overlaps the local TTS pipeline above | No provider keys configured, so nothing can spend | Do not set provider media keys |
| 5 | Channel telephony / SMS transports (webhook transport rows, e.g. twilio — `voicebridge/channels_inbound.py`) | No transport injected, so calls answer a typed refusal instead of dialling (`voicebridge/vision.py` adapter) | Transports unconfigured | Leave channel transports unconfigured |
| 6 | Remote plugin index fetches | Curated pinned index with pin-verified install (`voicebridge/plugin_catalog.py::load_pinned_index`) | Pinned / curated only | Install from the pinned index only |
| 7 | OAuth / subscription / provider proxy flows | Typed manual handoff (`OAUTH_MANUAL_HANDOFF` — `voicebridge/gateway/rpc.py`, `voicebridge/qubit_compat.py`) | Manual | Complete OAuth by hand in the provider dashboard |
| 8 | Frontier gateway as a whole (budgets, per-provider policy) | Local-only routing; the egress gate refuses with a typed log and no network call when the policy is off (`voicebridge/orchestrator.py::_web_egress_allowed`) | Disabled by default | Leave the frontier block disabled |

Key-hygiene notes (verified in code, no values read or printed):

- Key files are referenced by path only (`~/.config/qubit-voicebridge/.env`,
  the runtime `.env`); readers return the value straight into the outgoing
  call and the value is never logged (`_web_env_key` docstring: "Values are
  never logged").
- Chat web receipts carry `{route: web, sources, model}` only — the task
  text is redacted and no credential ever enters a receipt
  (`voicebridge/chat_transport.py::_branch_receipt`).
- The web branch audits session/code/counts only (`turn_web`,
  `turn_web_ddg`, `turn_web_unavailable`); there is no key-bearing log line
  on either order.
