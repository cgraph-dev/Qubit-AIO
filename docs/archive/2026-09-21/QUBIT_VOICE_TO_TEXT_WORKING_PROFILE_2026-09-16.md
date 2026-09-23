# Qubit voice-to-text working profile

Verified on 2026-09-16 from the live user configuration, source wiring, installed model files, and the working dashboard screenshot.

This is a record-only reference. It does not change configuration or source code. Do not replace these values because a generic example configuration looks different.

## Important clarification

Qubit currently has two speech-to-text paths:

1. **Dashboard `/chat` Voice button:** browser-native Web Speech API (`SpeechRecognition` or `webkitSpeechRecognition`). The browser produces the transcript. Qubit then sends that transcript to `POST /api/qubit/chat`, which uses the Qubit chat backend and the local `qubit-brain4` model.
2. **Desktop VoiceBridge / Right-Ctrl PTT:** local `sherpa-onnx` ASR using the Parakeet-TDT model on CPU. This path captures audio locally, runs VAD and normalization, writes a temporary 16 kHz mono WAV, and transcribes it locally.

Therefore, the screenshot’s excellent `/chat` transcription is not evidence of Qubit ASR. It is browser speech recognition followed by a Qubit-Brain4 response. The assistant sentence “we are using the Qubit agent” is stale/generated response text; it is not the active transport or model authority. The visible response metadata identifies `model=qubit-brain4`.

## Canonical product decision

Chrome/Chromium browser recognition is the preferred online voice-input
provider for Qubit because it is the currently working, fast, high-accuracy
path. Preserve the existing browser settings and route the final transcript
through Qubit’s canonical backend. Do not replace it with Qubit speech input,
pretend Chrome’s private engine is embedded locally, or silently change the
provider. Local Sherpa Parakeet remains the explicit offline/private fallback.

## Active dashboard transcription path

Source: `frontend/qubit-app/plugins/qubit-chat/qubit-chat.js`

The Voice button configures the browser recognizer as follows:

```text
API: window.SpeechRecognition || window.webkitSpeechRecognition
language: en-US
interimResults: true
maxAlternatives: 1
watchdog: 60 seconds, sliding and reset on every result
```

Flow:

```text
Voice button
  → browser microphone permission
  → browser SpeechRecognition
  → interim/final transcript in the chat input
  → final transcript submitted to /api/qubit/chat
  → voicebridge.agent.turn
  → Qubit-Brain4/local chat response
  → persistent Qubit chat session
```

Final transcripts are sent through exactly the same `sendTurn()` path as typed messages. The dashboard does not execute a separate Qubit voice backend. It reports typed states for unsupported browser recognition, denied microphone permission, no speech, timeout, and recognition errors.

The dashboard voice path is browser-dependent. Chrome/Chromium availability and browser microphone permission affect recognition quality; the local ASR settings below do not change the browser’s recognition engine.

## Active desktop VoiceBridge ASR

Effective configuration loaded at runtime:

```yaml
config_path: ~/.config/qubit-voicebridge/voice.yaml

asr:
  provider: sherpa
  engine: parakeet
  provider_ep: cpu
  num_threads: 8
  language: en
  hotwords:
    - Tricky
    - CGraph
    - OpenClaw
    - Hermes
    - Qubit
    - RankValut
    - Bucharest
    - piper
    - Vercel
    - Elixir
    - Phoenix
    - Tauri
  hotwords_score: 1.5
  min_confidence: 0.02
```

Effective runtime object, independently resolved from the loaded configuration:

```text
provider class: SherpaASR
provider name: sherpa
engine: parakeet
execution provider: cpu
threads: 8
language: en
```

Implementation:

- `voicebridge/orchestrator.py::_make_asr()` selects the effective speech-in profile and constructs `SherpaASR`.
- `voicebridge/asr/sherpa.py` loads the Parakeet-TDT NeMo transducer with `sherpa_onnx`.
- Parakeet uses modified beam search, `max_active_paths=4`, `model_type=nemo_transducer`, BPE tokenization, and a model-derived feature dimension.
- Hotwords are materialized only for the Parakeet path at `/tmp/qubit-voicebridge/asr-hotwords.txt`.
- The recognizer is loaded once and reused; the fallback recognizer is cached rather than reloaded for every utterance.

Installed runtime versions observed:

```text
sherpa_onnx: 1.13.7
sounddevice: 0.5.6
```

## Active ASR model artifacts

Model directory:

```text
~/.local/share/qubit-voicebridge/models
```

Active Parakeet model directory:

```text
sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8
```

Observed files and SHA-256 values:

```text
decoder.int8.onnx  11,845,314 bytes
  a590b4ed63132fc372f69c5f76ede4bb177ec365744c645a3944ea3bce1a389c
encoder.int8.onnx  652,184,281 bytes
  acfc2b4456377e15d04f0243af540b7fe7c992f8d898d751cf134c3a55fd2247
joiner.int8.onnx  6,355,316 bytes
  a4f952ad58e49ab2c3cebf60145ec54cce75054dd2e90c48cc41289c07aedd79
tokens.txt         93,939 bytes
  d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d
```

Silero VAD model used by the active configuration:

```text
~/.local/share/qubit-voicebridge/models/silero_vad_v5.onnx
sha256: 6b99cbfd39246b6706f98ec13c7c50c6b299181f2474fa05cbc8046acc274396
size: 2,313,101 bytes
```

Other installed ASR models are fallback/alternative artifacts. They are not the currently selected ASR engine:

- Moonshine quantized Sherpa model
- Moonshine int8 Sherpa model
- Parakeet-TDT v2 int8 model
- faster-whisper base/small cache entries

## Active microphone and signal path

Hardware profile:

```text
microphone: Audio-Technica AT2020 XLR condenser
interface: Focusrite Scarlett 2i2 4th Gen USB
input: Scarlett input 1
phantom power: 48V required on the Scarlett hardware
headphones/output: HyperX headset, with output following the OS default sink
```

Effective capture settings:

```yaml
mic:
  device: null
  device_name: "Scarlett 2i2 4th Gen: USB Audio"
  sample_rate: 48000
  channels: 1
  blocksize: 480
  gain: 3.0
  min_rms: 0.0005
  normalize_target: 0.35
  max_capture_s: 30.0
```

Why these values must remain stable:

- `device: null` avoids persisting a transient PortAudio/ALSA index.
- `device_name` is the stable microphone identity. The bridge re-pins the Pulse/PipeWire default source by name before capture and never chooses a random replacement when the name is absent.
- 48 kHz is the interface capture rate.
- One channel is correct for the AT2020 on Scarlett input 1.
- 480 frames is approximately 10 ms at 48 kHz.
- Hardware gain and 48V produce the signal; digital gain `3.0` is only a modest pre-amplifier.
- Captured audio is converted to mono, clipped after digital gain, and linearly resampled from 48 kHz to the ASR’s 16 kHz rate.
- The recorded ASR WAV is 16-bit PCM, mono, 16 kHz.
- The captured clip is peak-normalized to `0.35` before ASR when its peak is below clipping level.
- The capture watchdog stops a wedged stream after 30 seconds and retries stream opening up to three times.

Output settings are intentionally not pinned:

```yaml
audio:
  output_device: ""
  output_device_name: ""
  output_volume: 100
```

An empty output device means follow the current OS default sink. Do not replace this with a numeric device index; indices change after USB reconnects and can route audio to a silent device.

## Active VAD and endpointing

```yaml
vad:
  enabled: true
  backend: silero
  silero_model: ""       # resolves to silero_vad_v5.onnx
  aggressiveness: 1
  speech_start_ms: 280
  silence_timeout_s: 1.2
  max_utterance_s: 30.0
  rms_gate: 0.0015
  barge_in: true
```

Implementation details:

- VAD receives 16 kHz PCM and operates on the Silero model’s native window.
- The endpoint state machine requires approximately 280 ms of voiced audio to enter speech.
- Approximately 1.2 seconds of trailing silence ends a hands-free/toggle utterance.
- Hold-to-talk release remains the practical end signal for Right-Ctrl PTT.
- The RMS floor is combined with the VAD decision so hiss does not trigger speech.
- Barge-in is enabled so speech can interrupt TTS.

## PTT and speaker verification

```yaml
hotkey:
  hold_key: ctrl_r
  tap_ms: 300
  toggle_no_speech_s: 5.0
  interrupt_on_ptt: true
  tap_cancel: esc

speaker_verify:
  enabled: true
  name: user
  threshold: 0.65
  window_s: 2.0
  enroll_seconds: 5

feedback:
  beeps: true
```

Right Ctrl is the working desktop push-to-talk key in the current X11 session. A short tap toggles listening; Esc cancels. Speaker verification is used for barge-in authorization when an enrollment exists.

## Qubit model/backend after transcription

This is separate from speech-to-text:

```yaml
intent:
  conversation_backend: local
  local_llm:
    enabled: true
    model: qubit-brain4
    url: http://127.0.0.1:11434
    timeout_s: 8.0
    max_tokens: 100
    num_ctx: 4096
```

The dashboard’s `/api/qubit/chat` path and the desktop VoiceBridge path both send resolved text into Qubit’s agent/backend path. The current dashboard response metadata showing `model=qubit-brain4` is the reliable runtime evidence; natural-language claims inside a model response about “Qubit” are not routing telemetry.

## Do not change these working invariants

1. Keep the active desktop ASR as Sherpa Parakeet v3 int8 on CPU with 8 threads unless a new profile is benchmarked and explicitly selected.
2. Keep the AT2020 on Scarlett input 1, 48V enabled, and the stable device-name pin.
3. Keep capture at 48 kHz mono with blocksize 480 and gain 3.0.
4. Keep resampling to 16 kHz and peak normalization target 0.35.
5. Keep Silero VAD, 280 ms speech start, 1.2 s silence timeout, 30 s maximum, and RMS gate 0.0015.
6. Keep dashboard recognition settings `en-US`, interim results enabled, and one alternative.
7. Keep browser Voice and desktop PTT documented as separate ASR paths.
8. Never store API keys, dashboard tokens, personal paths, or secret values in this document.
9. Do not infer the active ASR from branding text, a model-generated reply, a route name, or an upstream compatibility filename.

## Verification commands

Read-only checks for this profile:

```bash
voicebridge audio status
voicebridge audio check
systemctl --user is-active qubit-voicebridge.service
python3 scripts/diagnose_audio_usb.py
journalctl --user -u qubit-voicebridge --since "5 min ago" | grep -Ei 'asr|mic|capture|vad|hotkey'
```

For a future change, record the new configuration hash, ASR/VAD artifact hashes, model/provider identity, WER/latency evidence, device self-check result, and dashboard/desktop path separately before replacing this profile.

## Source references

- `config/voice.yaml`
- `~/.config/qubit-voicebridge/voice.yaml` (effective machine configuration)
- `voicebridge/orchestrator.py`
- `voicebridge/asr/sherpa.py`
- `voicebridge/audio/mic.py`
- `voicebridge/audio/vad.py`
- `voicebridge/audio/settings.py`
- `frontend/qubit-app/plugins/qubit-chat/qubit-chat.js`
- `docs/AUDIO_SETUP.md`
- `docs/AUDIO_STABILITY_4D.json`
