# Change report — Packet 7C remediation

Generated: 2026-08-27 02:54 EEST
Repository: $HOME/Projects/Repos/openclaw-voice-bridge

## Files changed
```
M .gitignore
 M README.md
 M config/voice.example.yaml
 M config/voice.local.yaml
 M config/voice.yaml
 M docs/AUDIO_SETUP.md
 M docs/MIGRATIONS.md
 M docs/OPERATIONS_7C.md
 M docs/STEAL_LIST.md
 M docs/VOICE_BRIDGE_SYSTEM.md
 M frontend/index.html
 M pyproject.toml
 M scripts/browser_auto.py
 M scripts/browser_inbox.py
 M scripts/generate_report.py
 M scripts/install.sh
 M scripts/review_gmail.py
 M scripts/review_tiktok.py
 M scripts/web_search.py
 M systemd/openclaw-voice-bridge.service
 M tests/test_live_stress.py
 M tests/test_new_features.py
 M tests/test_packet7c.py
 M tests/test_router_extensions.py
 M tests/test_stress.py
 M tests/test_units.py
 M tests/test_voice_id.py
 M tests/test_webui.py
 M voicebridge/asr/sherpa.py
 M voicebridge/audio/mic.py
 M voicebridge/audio/playback.py
 M voicebridge/cli.py
 M voicebridge/config.py
 M voicebridge/distribution.py
 M voicebridge/hermes/backend.py
 M voicebridge/openclaw/cli_backend.py
 M voicebridge/openclaw/discovery.py
 M voicebridge/openclaw/gateway_ws.py
 M voicebridge/orchestrator.py
 M voicebridge/runner_contract.py
 M voicebridge/safety/policy.py
 M voicebridge/terminal.py
?? THIRD_PARTY_NOTICES.md
?? benchmarks/corpus/
?? docs/AGENT_PROMPT_PACK.md
?? docs/ALIGN_4D.json
?? docs/ALIGN_4D_winner.json
?? docs/AUDIO_STABILITY_4D.json
?? docs/BAKEOFF_4D.json
?? docs/BAKEOFF_4D.md
?? docs/BENCHMARKS.md
?? docs/CANARY_5A.md
?? docs/CONTEXT_5C.md
?? docs/EVIDENCE_5A.md
?? docs/LESSONS_6A.md
?? docs/MEMORY_5B.md
?? docs/PACKET_0A_MISMATCH_REPORT.md
?? docs/QUBIT_UNIFIED_AGENT_PLAN.md
?? docs/UNIFIED_AGENT_PLAN.md
?? docs/UPSTREAM_REUSE.md
?? docs/VOICE_BRIDGE_COMPLETE_GUIDE.md
?? docs/router_4c_report.json
?? frontend/approvals.html
?? frontend/token-setup.html
?? scripts/align_4d.py
?? scripts/audio_stability_4d.py
?? scripts/bake_brain.py
?? scripts/bakeoff_4d.py
?? scripts/benchmark_transitions.py
?? scripts/canary_run_4d.py
?? scripts/check_clean_imports.py
?? scripts/check_dashboard_consumer.mjs
?? scripts/install_systemd_unit.sh
?? scripts/router_eval_4c.py
?? tests/conftest.py
?? tests/test_dashboard_stress.py
?? tests/test_events_agui_conformance.py
?? tests/test_mic_health.py
?? tests/test_mic_resolve.py
?? tests/test_packet0b.py
?? tests/test_packet1a.py
?? tests/test_packet1b.py
?? tests/test_packet2a.py
?? tests/test_packet2b.py
?? tests/test_packet3a.py
?? tests/test_packet3b.py
?? tests/test_packet4b.py
?? tests/test_packet4c.py
?? tests/test_packet4d.py
?? tests/test_packet4d_mitigation.py
?? tests/test_packet5a.py
?? tests/test_packet5b.py
?? tests/test_packet5c.py
?? tests/test_packet5c_integration.py
?? tests/test_packet6a.py
?? tests/test_packet7c_audio.py
?? tests/test_release_smoke.py
?? tests/test_steal_list.py
?? tests/test_steal_list_strict.py
?? tests/test_think_leak.py
?? voicebridge/approvals.py
?? voicebridge/audio/settings.py
?? voicebridge/broker.py
?? voicebridge/canary.py
?? voicebridge/context/
?? voicebridge/context_compiler.py
?? voicebridge/dispatch_guard.py
?? voicebridge/events/
?? voicebridge/evidence/
?? voicebridge/isolated.py
?? voicebridge/planner.py
?? voicebridge/router/
?? voicebridge/sandbox.py
```

## Diff summary
```
.gitignore                            |   4 +
 README.md                             |  38 +-
 config/voice.example.yaml             |  40 +-
 config/voice.local.yaml               |   6 +-
 config/voice.yaml                     |  37 +-
 docs/AUDIO_SETUP.md                   |   7 +-
 docs/MIGRATIONS.md                    |  43 ++
 docs/OPERATIONS_7C.md                 | 101 ++++-
 docs/STEAL_LIST.md                    |  52 ++-
 docs/VOICE_BRIDGE_SYSTEM.md           | 106 ++++-
 frontend/index.html                   | 239 ++++++++++-
 pyproject.toml                        |   3 +-
 scripts/browser_auto.py               |   4 -
 scripts/browser_inbox.py              |   1 -
 scripts/generate_report.py            |   2 +-
 scripts/install.sh                    |  20 +-
 scripts/review_gmail.py               |   2 -
 scripts/review_tiktok.py              |   2 +-
 scripts/web_search.py                 |   3 -
 systemd/openclaw-voice-bridge.service |   9 +
 tests/test_live_stress.py             |   2 +-
 tests/test_new_features.py            |   2 +-
 tests/test_packet7c.py                |  17 +-
 tests/test_router_extensions.py       |   2 +-
 tests/test_stress.py                  |  76 ++--
 tests/test_units.py                   |   4 +-
 tests/test_voice_id.py                |   1 -
 tests/test_webui.py                   |   3 -
 voicebridge/asr/sherpa.py             |  41 +-
 voicebridge/audio/mic.py              |  98 ++++-
 voicebridge/audio/playback.py         |  76 ++--
 voicebridge/cli.py                    |  94 ++++-
 voicebridge/config.py                 |   1 +
 voicebridge/distribution.py           | 117 +++++-
 voicebridge/hermes/backend.py         |   2 +-
 voicebridge/openclaw/cli_backend.py   |   2 +-
 voicebridge/openclaw/discovery.py     |   3 +-
 voicebridge/openclaw/gateway_ws.py    |   1 -
 voicebridge/orchestrator.py           | 719 ++++++++++++++++++++++++----------
 voicebridge/runner_contract.py        |  27 +-
 voicebridge/safety/policy.py          |   4 +-
 voicebridge/terminal.py               |   1 -
 42 files changed, 1590 insertions(+), 422 deletions(-)
```

## Tests
test_packet7c (30) + test_packet7c_audio (30) + full suite 762 passed 0 failed 0 skipped + clean_venv_check PASS + check_deployment 0 fail

## Benchmarks / measurements
model digest verified: qubit-brain4=7bbd02b8c294b0ab... via /api/tags fallback; audio backend resolution ALSA; distributable manifest complete

## Risks
audio status reports HyperX MISSING when the mic is unplugged (correct, no silent fallback); model digest requires Ollama reachable (fails closed otherwise)

## Rollback
revert remediation commit; audio .bak + pre-restore backups preserve prior state
