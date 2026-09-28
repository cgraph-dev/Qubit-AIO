# Packet 8H — real audio, PTT, and Right Ctrl qualification (operator runbook)

Status: **IMPLEMENTED** — hermetic qualification is safe to run now. LIVE
audio steps are **opt-in** and require `VOICE_BRIDGE_QUALIFY=1`; this packet
never opens a device, swaps providers, loads models, enables services/timers,
records private audio, or changes the working web-dashboard path on its own.
Source contract: `docs/UNIFIED_AGENT_PLAN.md` §4.1, Packet 8H prompt-pack
prompt, and `docs/AUDIO_SETUP.md`.

## 1. What this packet qualifies

It qualifies the documented microphone/source and audio-sink path, web
push-to-talk, Right Ctrl event capture, configured ASR, reasoning model, Qubit
response, and Piper playback — without changing any of them.

| 8H requirement | Where it is proven |
|---|---|
| Session context: X11/Wayland display, XDG_RUNTIME_DIR, Pulse/PipeWire | `--preflight` (`session_context`) |
| Multi-session display: never guess the highest X display | `session_context` (`AMBIGUOUS` typed state) |
| Effective stable source and sink; missing pin = `DEVICE_MISMATCH` | `--preflight` (`effective_devices`) |
| Provider/model identity recorded; fallback distinguished from real | `--preflight` (`provider_identity`) + `--latency` |
| Capture→ASR→reasoning→synthesis→playback latency decomposition | `--latency` (first-audio vs end-to-end) |
| Sample-rate/channel conversion, silence, clipping, noise, echo, VAD boundaries | `--signal` |
| Right Ctrl duplicate-press / stuck-key protection | `--ptt` edge probe (hermetic) + `--ptt --live` |
| Concurrent web PTT + Right Ctrl turn isolation (no cross-turn answer) | `--concurrency` |
| Automated-restart authorization + backoff + circuit breaker | `--restart-guard` |
| Temporary-audio cleanup / no audio leak | `--cleanup` |
| Wrong sink/source, permission denial, ASR/TTS unavailable, timeout | typed stages below |
| Live microphone signal, audible sink, and real Right Ctrl press | `--capture/--playback/--ptt --live` |

## 2. The harness

- `voicebridge/audio/qualify.py` — staged, bounded qualification.
- `scripts/audio_qualify.py` — CLI (`audio-qualify`).
- `tests/test_audio_qualify.py` — hermetic focused tests.
- `docs/OPERATIONS_8H.md` — this runbook.

Output is machine-readable JSON with **typed** results. Live execution is
**impossible by default**: every live stage requires `--live` AND
`VOICE_BRIDGE_QUALIFY=1`, otherwise it fails closed (`LIVE_NOT_AUTHORIZED`)
and no device is opened.

## 3. Preflight (safe, no live access)

```bash
.venv/bin/python scripts/audio_qualify.py --preflight
```

Reads and reports, in one JSON record:

- `session` — `XDG_RUNTIME_DIR`, session type, a **reachable** X11 display (or a
  typed Wayland note), and Pulse/PipeWire reachability. A missing piece is a
  typed failure with recovery guidance; a multi-session ambiguity is
  `AMBIGUOUS` and the harness never guesses "the highest display".
- `devices` — the effective stable source and sink. An unpinned side is
  `system_default` (the documented follow-the-default rule). A **configured
  pin that matches nothing is `DEVICE_MISMATCH`** with the runtime's implicit
  `aggregate_fallback` route exposed and the **exact configured pin plus the
  present device names listed** (see §5) — never a silent pass and never an
  automatic reroute to another mic.
- `providers` — the actual configured ASR provider/engine, TTS provider/voice,
  and reasoning-model alias, plus any known fallback relationship (e.g. openai
  TTS → Piper) so a fallback can never look like the primary provider.

Expected note: the `devices.state` is `degraded` when the live mic pin is not
currently enumerable — that is the 8H audio-fallback risk being surfaced, not
an error to "fix by rerouting". Qualify the real device or repair the pin.

## 4. Hermetic qualification (no device, no live access)

```bash
.venv/bin/python scripts/audio_qualify.py --all
```

Runs every hermetic stage in an **isolated** qualification state dir (default
`<config state>/qualification`; override with `--state-dir`):

1. `signal` — silence rejection, clipping detection, speech-like detection,
   48 kHz→16 kHz sample-rate conversion, and echo resilience using the same
   gates the production path uses;
2. `vad` — VAD boundaries (speech start, no start on pure silence) with a
   webrtc Endpointer (no model);
3. `latency` — the capture→ASR→reasoning→synthesis→playback decomposition with
   first-audio vs end-to-end ms and fallback detection;
4. `concurrency` — interleaved web-PTT + Right-Ctrl (+ cancel) turns proven
   attributable (no cross-turn answer / shared-state contamination);
5. `restart_guard` — restart attempts are refused without authorization and the
   bounded-backoff + circuit-breaker logic is proven;
6. `cleanup` — temporary-audio files are removed (no audio leak).

`--dry-run` runs without persisting the summary. The production state root,
audio configuration, services, timers, models, and the web dashboard are never
touched (`test_run_qualification_isolated_from_production_state`).

## 5. Live qualification (OPT-IN, per device)

Prerequisites:

- A logged-in graphical session with the audio path reachable
  (`--preflight` session/devices states `OK`; fix `DEVICE_MISMATCH` pins first).
- Operator gate:
  ```bash
  VOICE_BRIDGE_QUALIFY=1
  ```

Resolving a `DEVICE_MISMATCH` (required before live qualification): the record
lists the exact configured pin (e.g. `Scarlett 2i2 4th Gen: USB Audio`) and the
currently enumerated device names for that role. The operator must **intentionally
re-pin** to the real present device with `voicebridge audio select mic "<name>"`
(or `select output`), or repair the hardware so the configured pin is present.
The harness never picks a replacement for you — it only reports what exists and
must not silently fall back to another mic.

Run one live stage at a time (do not open the mic while the bridge's barge
stream is capturing — one input stream at a time on the Scarlett):

```bash
VOICE_BRIDGE_QUALIFY=1 .venv/bin/python scripts/audio_qualify.py --capture --live
VOICE_BRIDGE_QUALIFY=1 .venv/bin/python scripts/audio_qualify.py --playback --live
VOICE_BRIDGE_QUALIFY=1 .venv/bin/python scripts/audio_qualify.py --ptt --live
```

- `--capture --live` opens the production mic path (`mic_selfcheck`) for a
  bounded window and reports `UNAVAILABLE` / `SILENT` / `DEGRADED` / `HEALTHY`
  plus the effective device index. Never records audio.
- `--playback --live` resolves the effective sink with the production
  `Playback` path and plays a **synthetic sine tone** (never private audio);
  `audible` is true only when the stream actually became active.
- `--ptt --live` starts the real HotkeyListener and waits (bounded, 10 s) for
  you to **hold and release Right Ctrl**; it reports the delivered
  `ptt_start`/`ptt_end` events. A `TIMEOUT` is a typed failure, not a pass.

Real capture→ASR→reasoning→TTS→playback **end-to-end** latency is measured by
the operator against the running bridge from its own journal (`webui.talk`
`latency_s`, `asr.transcribe`, `tts.synthesize`, `tts.speak`); the harness
`--latency` stage records the decomposition so hardware stages (capture/play)
and model stages (ASR/reasoning/synthesis) are reported separately.

## 6. Wrong-sink/source and fallback checks

- `--preflight` `devices`: a named pin that matches nothing is
  `DEVICE_MISMATCH`/`aggregate_fallback` — this is the documented 8H risk being
  exposed instead of silently routed.
- `--signal`: silence below the energy floor is rejected before ASR; clipping
  is detected; rate/channel conversion preserves the speech classification and
  an echoed signal still classifies as speech (never silence or clipping).
- `--latency`: an unavailable ASR/TTS/reasoning stage marks
  `real_vs_fallback = "fallback"` — a fallback is never reported as the primary
  provider's real response.
- `--restart-guard`: any automated restart is refused without explicit
  authorization and bounded by backoff + a circuit breaker.

## 7. What is stored and where

- Default root: `<config state>/qualification` (override with `--state-dir`).
  Dirs `0700`, files `0600`.
- `qualify/<run_id>/summary.json` — the redacted typed qualification record
  (stages, typed states, provider identity). It never contains private audio,
  transcripts, or machine-only indices/paths.
- `--dry-run` runs without persisting the summary.

## 8. Cleanup and rollback

- Remove the isolated qualification root to delete all artifacts:
  ```bash
  rm -rf "<config state>/qualification"
  ```
- This packet changes **no** production file: no device, provider, model,
  audio config, hotkey behavior, web-dashboard path, service, or timer is
  modified. Rollback = delete the added files
  (`voicebridge/audio/qualify.py`, `scripts/audio_qualify.py`,
  `tests/test_audio_qualify.py`, `docs/OPERATIONS_8H.md`). No production
  behavior reverts because none was changed.

## 9. Limitations (honest)

- The hermetic suite passes cleanly under `-W error`
  (`pytest -q -W error tests/test_audio_qualify.py ...`). webrtcvad 2.0.10's
  deprecated `pkg_resources` import warning is scoped away with a single narrow
  filter (exact message + UserWarning + webrtcvad/pkg_resources modules, in
  `voicebridge/audio/vad.py`); unrelated warnings still error — proven by the
  `-W error` regression tests in `tests/test_audio_qualify.py`.
- Hermetic `--latency` measures the stage decomposition with synthetic
  stages; real device/model latency requires the opt-in live steps and the
  bridge's own journal.
- `--ptt --live` requires a reachable X11 display; on Wayland-only sessions the
  xinput backend cannot attach and the stage reports `UNAVAILABLE` (typed, not
  a pass). Do not run it while the bridge service is also holding a Right Ctrl
  listener on the same session without first checking for event duplication.
- Live `--capture` must run while no other input stream holds the device.
- The harness never loads ASR/TTS/model files itself; it reports identity and
  lets the operator exercise real providers through the running bridge.