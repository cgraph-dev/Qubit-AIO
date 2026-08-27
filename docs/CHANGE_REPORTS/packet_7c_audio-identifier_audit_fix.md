# Change report — Packet 7C audio-identifier audit fix

Generated: 2026-08-27 03:10 EEST
Repository: /home/trick/Projects/Repos/openclaw-voice-bridge

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
 M tests/test_packet7c_audio.py
 M tests/test_router_extensions.py
 M tests/test_stress.py
 M tests/test_units.py
 M tests/test_voice_id.py
 M tests/test_webui.py
 M voicebridge/asr/sherpa.py
 M voicebridge/audio/mic.py
 M voicebridge/audio/playback.py
 M voicebridge/audio/settings.py
 M voicebridge/cli.py
 M voicebridge/hermes/backend.py
 M voicebridge/openclaw/cli_backend.py
 M voicebridge/openclaw/discovery.py
 M voicebridge/openclaw/gateway_ws.py
 M voicebridge/orchestrator.py
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
?? tests/test_release_smoke.py
?? tests/test_steal_list.py
?? tests/test_steal_list_strict.py
?? tests/test_think_leak.py
?? voicebridge/approvals.py
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
 docs/MIGRATIONS.md                    |  39 ++
 docs/OPERATIONS_7C.md                 |  26 +-
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
 tests/test_packet7c_audio.py          |  68 +++-
 tests/test_router_extensions.py       |   2 +-
 tests/test_stress.py                  |  76 ++--
 tests/test_units.py                   |   4 +-
 tests/test_voice_id.py                |   1 -
 tests/test_webui.py                   |   3 -
 voicebridge/asr/sherpa.py             |  41 +-
 voicebridge/audio/mic.py              |  98 ++++-
 voicebridge/audio/playback.py         |  76 ++--
 voicebridge/audio/settings.py         |  86 +++-
 voicebridge/cli.py                    |  24 +-
 voicebridge/hermes/backend.py         |   2 +-
 voicebridge/openclaw/cli_backend.py   |   2 +-
 voicebridge/openclaw/discovery.py     |   3 +-
 voicebridge/openclaw/gateway_ws.py    |   1 -
 voicebridge/orchestrator.py           | 719 ++++++++++++++++++++++++----------
 voicebridge/safety/policy.py          |   4 +-
 voicebridge/terminal.py               |   1 -
 40 files changed, 1431 insertions(+), 424 deletions(-)
```

## Tests
test_packet7c (30) + test_packet7c_audio (33) + full suite 765 passed 0 failed 0 skipped + clean-venv PASS + check_deployment 0 fail + model digest OK + distributable complete

## Benchmarks / measurements
normalized stable ids: no (hw:) anywhere in stable_id/persisted config/CLI JSON; live select persists clean names (HD-Audio Generic: ALC892 Analog / pulse)

## Risks
HyperX mic currently not enumerated -> MISSING (correct, no guess); model digest requires Ollama (fails closed otherwise)

## Rollback
revert this commit; audio .bak backups preserve prior config
