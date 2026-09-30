# Qubit Voice E2E Coverage — 2026-09-21

**Owner:** voice E2E workstream (sole owner). **Scope:** NEW tests under
`tests/` and NEW docs only. No product code was edited; defects found are
reported here with `file:line` and pinned as explicit `xfail` where the backend
is reachable.

**Status vocabulary:** this document does not claim VERIFIED, 100%, complete, or
1:1. `VERIFIED` remains forbidden without a passing final matrix and an
independent read-only verifier (see
`docs/QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md`). Everything
below is "focused test evidence," produced under the implementation-first
amendment.

---

## 1. Files added

| File | Kind | Purpose |
|---|---|---|
| `tests/test_voice_e2e_hermetic9u.py` | new test | Hermetic E2E voice coverage (Right-Ctrl, `voice.input_engine`, STT/TTS adapter switching, cancel/e-stop/recovery) |
| `docs/QUBIT_VOICE_E2E_2026-09-21.md` | new doc | This report |

No existing test was modified; no product file was touched.

---

## 2. Test command and counts

Focused run of the new file alone (hermetic marker set — the file is not
`live`/`ui` marked, so it runs in the default suite):

```
.venv/bin/python -m pytest tests/test_voice_e2e_hermetic9u.py -q
```

Result: **16 passed, 6 xfailed** (0 failed, 0 xpassed after the fixes below).

Combined with the existing voice/chat transport suites to confirm no
cross-test/env pollution (several tests in this file use module-scoped process
state — the e-stop latch, the speech registry — and must not leak):

```
.venv/bin/python -m pytest \
  tests/test_voice_e2e_hermetic9u.py \
  tests/test_chat_voice_e2e9u.py \
  tests/test_speech_realtime_profiles.py \
  tests/test_qbpv2_chat_transport.py \
  tests/test_voice_canonical8u.py -q
```

Result: **77 passed, 7 xfailed** (0 failed).

The 6 xfails: 3 are browser-only links (Right-Ctrl DOM, engine
selection/fallback rendering, permission-failure rendering), 3 are reported
product gaps/bugs (§5).

---

## 3. What the hermetic coverage proves

### 3.1 Right-Ctrl capture -> canonical backend

- **Shared route table, no shadow backend** —
  `tests/test_voice_e2e_hermetic9u.py::test_right_ctrl_shares_the_canonical_chat_route_table`:
  typed turns (`h_chat_turn`), streamed turns (`h_chat_stream`), and the only
  voice server ops (local ASR `h_audio_transcribe`, local TTS `h_audio_speak`)
  are the canonical handlers; no `right-ctrl`/`voice/control` route exists.
- **Local-engine transcript enters the canonical turn** —
  `test_right_ctrl_local_engine_transcript_enters_canonical_turn`: the
  canonical local ASR (`POST /api/audio/transcribe`) returns a transcript that,
  fed into `h_chat_stream` -> `chat_transport.stream_turn`, persists the
  conversation to the one `QubitSessionStore` (`receipt.path ==
  voicebridge.chat_transport.stream_turn`).
- **Browser DOM press/release/debounce** is an explicit `xfail`
  (`test_right_ctrl_press_release_debounce_reaches_canonical_turn`) naming the
  exact missing link (`ChatPage.tsx:998-1018`; live coverage in
  `tests/test_voice_e2e_phase8.py` under `-m "ui or live"`). No credential is
  required — the missing link is browser automation.

### 3.2 `voice.input_engine` browser | local | auto

- Values/default: `VOICE_INPUT_ENGINES == ("browser","local","auto")`, unknown/
  missing fails to `auto` (`test_input_engine_values_and_default`).
- Served surface: `GET /api/config` serves `voice.input_engine == "auto"` and
  `GET /api/config/schema` describes the field
  (`test_config_default_and_served_surface_expose_voice_input_engine`).
- **Canonical operation unchanged**: identical receipt path/model/runtime across
  all three engines (`test_input_engine_never_changes_the_canonical_operation`).
- Degraded local states are typed, not fabricated:
  `AUDIO_ASR_UNAVAILABLE` (403) and `AUDIO_TTS_UNAVAILABLE` (403)
  (`test_local_engine_*_unavailable_is_typed_not_fabricated`).
- The STT/TTS profile read the UI consumes is `mode: relay`, host-side, and
  leaks no key/path (`test_voice_config_served_read_has_no_credential_and_relay_mode`).
- The rendered engine selector / fallback reason / permission-failure alert are
  browser-only `xfail`s (`test_browser_engine_selection_and_fallback_reason_render_hermetically`,
  `test_microphone_permission_failure_renders_typed_alert_hermetically`),
  pointing at the live links in `test_voice_e2e_phase8.py`.

### 3.3 STT/TTS adapter switching through the single registry

- Defaults align between registry and seams: sherpa/parakeet STT + piper TTS
  (`test_speech_registry_defaults_match_orchestrator_seams`).
- Config-driven provider/engine switches reach both the registry and the
  orchestrator seams: whisper STT, moonshine engine, kokoro/edge/piper TTS
  (`test_stt_provider_switch_config_drives_registry_and_seam`,
  `test_tts_provider_switch_default_and_optional_providers`).
- The moonshine fallback fires once on an empty primary
  (`test_stt_fallback_engine_moonshine_on_empty_primary`).
- **Canonical operation unchanged across role swaps**: swapping STT/TTS adapters
  changes neither the transport path nor the model nor the store
  (`test_canonical_operation_unchanged_across_speech_role_swaps`).

### 3.4 Cancellation / e-stop / recovery

- Cancel an in-flight streamed turn: `chat_transport.cancel()` latches the
  session event, the stream ends `start, delta, cancelled` (no `done`), and no
  assistant reply is persisted; `retry_last` then recovers the turn without
  duplicating the user message
  (`test_stream_cancel_marks_cancelled_and_recovery_does_not_duplicate`).
- A spoken "cancel" denies a pending confirmation and clears the orchestrator's
  confirmation state through the shared journal
  (`test_voice_cancel_utterance_denies_pending_confirmation`).
- The **durable** e-stop latch refuses a turn before the model call
  (`stream_preflight` 403 `EMERGENCY_STOP`, `turn` `EMERGENCY_STOP`, zero model
  invocations) and a reset restores a working canonical turn
  (`test_estop_latch_refuses_voice_turn_and_recovers_after_reset`).

---

## 4. Product bugs / gaps found (reported, NOT fixed)

These are pinned as `xfail` in the test file. They are code-side findings, not
operator prerequisites.

1. **Session/explicit speech profile never reaches the orchestrator seams.**
   `voicebridge/orchestrator.py:82` (`_speech_override`) calls
   `resolve_speech(cfg, kind)` with no `session`/`profile_id`;
   `_make_asr`/`_make_tts` (`orchestrator.py:531,567`) pass neither. Only
   `voicebridge/providers/speech.py:571-593` (`_pick_speech`) reads a session
   selection, so a profile set via `set_session_speech_profile` (or an explicit
   id) resolves in the registry but is ignored by the runtime. "Speech roles are
   replaceable through the single registry" therefore holds for the config
   default only.
   Test: `test_session_selected_speech_profile_reaches_orchestrator_seam`.

2. **Whisper configured model is overwritten by the sherpa engine name.**
   `voicebridge/providers/speech.py:202,218` set the speech-in record's `model`
   from `asr.engine` (default `parakeet`). `_speech_override` returns it, and
   `voicebridge/orchestrator.py:552-554` builds
   `FasterWhisperASR(model="parakeet")` instead of the configured `asr.model`
   (e.g. `small`). The provider switch itself is correct; the model name is
   wrong, so a whisper run would request a non-existent model.
   Test: `test_whisper_configured_model_reaches_the_seam`.

3. **Compat audio ops bypass the speech registry.**
   `voicebridge/qubit_compat.py:3910` (`_asr_from_cfg`) and `:3940`
   (`_tts_from_cfg`) build adapters from raw `asr`/`tts` config only, unlike
   `orchestrator.py:531/567` which call `_speech_override`. A registry-level
   speech profile therefore does not affect
   `POST /api/audio/transcribe` / `POST /api/audio/speak`.
   Test: `test_compat_audio_ops_honor_the_speech_registry_override`.

Not a new finding but re-observed and left untouched: the 2026-09-21 brief
already tracks the `/api/audio/transcribe` empty-transcript gate
(`qubit_compat.py:4097` returns `""`/`ok:true` rather than a typed refusal) as
`test_voice_e2e_phase8.py` context.

---

## 5. Operator prerequisites (real mic / GPU / credential / browser)

Anything below could not be exercised hermetically and needs a real
environment. None of it is a code defect.

| Capability | Prerequisite | Where covered |
|---|---|---|
| Right-Ctrl DOM press/release/debounce and its pointer to the canonical turn | Playwright + built `frontend/qubit-dist` + browser `SpeechRecognition` stub (no credential) | `test_voice_e2e_phase8.py::test_right_ctrl_drives_canonical_chat_stream` (`-m "ui or live"`) |
| Engine selector / `network:` / `privacy:` / fallback reason rendering | Playwright + built dist + `SpeechRecognition` stub | `test_voice_e2e_phase8.py::test_ui_engine_selection_reflects_normalized_config`, `::test_auto_prefers_browser_speech_recognition`, `::test_auto_falls_back_to_local_with_visible_reason` |
| Microphone permission-failure `[VOICE_DENIED]` alert | Playwright + `getUserMedia` reject stub | `test_voice_e2e_phase8.py::test_microphone_permission_failure_renders_typed_error` |
| Real local-ASR transcription quality (sherpa/parakeet/moonshine, whisper) | Real mic capture or a real WAV + downloaded sherpa/whisper models (CPU; whisper CUDA needs the Ollama CUDA 12 libs) | not covered; requires downloaded models |
| Real local-TTS synthesis (piper default, kokoro/edge) | Downloaded piper/kokoro voices (state `XDG_DATA_HOME/qubit-voicebridge/models`); edge needs network + ffmpeg | not covered |
| External realtime / remote TTS | egress consent + enabled frontier policy + allowlist entry + a real provider credential reference (`env:`/`file:`) | partially covered hermetically by `test_speech_realtime_profiles.py` |
| Served `voice.input_engine` from the *running* dashboard | The running server predates the key and/or the proxy-first cutover omits it; needs a code fix/restart (tracked as `test_voice_e2e_phase8.py::test_served_config_exposes_voice_input_engine` xfail) | `test_voice_e2e_phase8.py::test_running_server_exposes_voice_input_engine` |
| Full `/qubit-chat` turn against a real provider | A real `>=65,536`-token `/chat` credential | not covered (no credential) |

---

## 6. Uncertainty and limitations

- **Graft MCP was unavailable.** Every `graft` tool call timed out
  (`MCP error -32001`), including `graft_repo_map` and `graft_find_code`.
  Context was therefore gathered by direct file inspection and `rg`, not from
  the graph. `graft build` / a later re-run should be used to re-validate the
  spans cited here. This is an environment issue, not a repo issue.
- **Browser-only links are `xfail`, not passing.** The rendered-DOM assertions
  (engine selection, fallback reason, permission alert, Right-Ctrl) are not
  proven hermetically; they are proven only by the live Playwright file, which
  was NOT executed in this session (it needs browser automation).
- **`_dummy_orch` seam tests assert constructor selection, not audio.** They
  prove which adapter class/engine the seams build and that the canonical turn
  receipt is unchanged; they do not prove model download, loading, or audio I/O.
- **The fallback test stubs the fallback class.** It proves the fallback wiring
  and one-shot semantics, not a real moonshine model load.
- **xfail uses `strict=False`.** The three product-gap pins will flip to XPASS
  (and become visible) if/when the code is fixed; the browser pins likewise once
  reachable hermetically. No pin silently hides a regression in the covered
  (passing) behavior.
- **Cross-test state.** The e-stop latch and the speech registry are
  process-shared; the tests reset them (e.g. `ctl.reset()` in `finally`) and the
  combined run above confirms no leak. A future parallel-runner (`pytest-xdist`)
  may need these tests pinned to one worker.
- **No full final matrix was run** (per the implementation-first amendment), and
  no independent verification was performed.

---

## 7. Exact commands

```
# focused hermetic voice E2E file
.venv/bin/python -m pytest tests/test_voice_e2e_hermetic9u.py -q

# with the neighbouring voice/chat transport suites (pollution check)
.venv/bin/python -m pytest tests/test_voice_e2e_hermetic9u.py \
  tests/test_chat_voice_e2e9u.py tests/test_speech_realtime_profiles.py \
  tests/test_qbpv2_chat_transport.py tests/test_voice_canonical8u.py -q

# browser links (opt-in; not run in this session)
.venv/bin/python -m pytest tests/test_voice_e2e_phase8.py -m "ui or live" -q
```
