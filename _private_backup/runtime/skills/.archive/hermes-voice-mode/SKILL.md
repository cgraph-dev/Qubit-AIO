---
name: hermes-voice-mode
description: Setup, configure, and troubleshoot Qubit voice mode (STT + TTS), push-to-talk, microphone issues, and audio provider quirks.
title: Qubit Voice Mode Setup & Troubleshooting
trigger:
  - user asks about voice mode, voice-to-voice, /voice, or push-to-talk
  - Ctrl+B or voice record key not working
  - TTS not playing, STT not transcribing, or microphone issues in Qubit
  - setting up speech-to-text or text-to-speech
  - configuring audio or voice providers like openai, edge, elevenlabs
  - faster-whisper, sounddevice, pyaudio, or PortAudio errors
---

# Qubit Voice Mode Setup & Troubleshooting

## 1. System Dependencies

Voice mode requires PortAudio at the system level and Python audio libraries in the **Qubit venv**.

### Install system libraries
```bash
sudo apt-get install -y libportaudio2 libportaudiocpp0 portaudio19-dev
```

> **Pitfall:** If `dpkg` was interrupted (e.g., by a previous upgrade), apt will fail with:
> `E: dpkg was interrupted, you must manually run 'sudo dpkg --configure -a'`
> Fix this **first** before installing anything else.

### Install Python libraries (in Qubit venv)
```bash
$QUBIT_HOME/hermes-agent/venv/bin/pip install sounddevice pyaudio SpeechRecognition
```

> **Pitfall:** Always use the **Qubit venv Python** (`$QUBIT_HOME/hermes-agent/venv/bin/python3`), not system Python. Voice imports will fail from system Python even if the packages appear installed.

### Verify audio environment
```bash
$QUBIT_HOME/hermes-agent/venv/bin/python3 -c "
from tools.voice_mode import detect_audio_environment
import json
print(json.dumps(detect_audio_environment(), indent=2))
"
```
Expected: `{"available": true, "warnings": [], "notices": []}`

## 2. Configuration

### Enable voice mode
```bash
qubit config set stt.enabled true
qubit config set stt.provider local          # faster-whisper, free, offline
qubit config set tts.provider openai         # or edge, elevenlabs, etc.
qubit config set voice.record_key ctrl+b     # or ctrl+space, alt+space, etc.
qubit config set voice.beep_enabled true
```

### OpenAI TTS provider quirks
When using **OpenAI TTS through the Qubit gateway**, the only supported model is:
```yaml
tts:
  provider: openai
  openai:
    model: gpt-4o-mini-tts      # NOT tts-1
    voice: nova                  # alloy, echo, fable, onyx, nova, shimmer
```

> **Pitfall:** Setting `model: tts-1` produces:
> `Unsupported managed OpenAI speech model. Supported models: ['gpt-4o-mini-tts']`
> Always use `gpt-4o-mini-tts` for gateway-routed OpenAI TTS.

### Pro audio interface threshold fix
Professional audio interfaces (Focusrite Scarlett, etc.) output lower software signal levels than built-in mics. The default `silence_threshold: 200` causes all recordings to be discarded as "too quiet."

**Symptom:** Recording starts/stops but returns `None` (no WAV file), or `peak_rms` is < 200.

**Fix:** Lower the threshold dramatically:
```bash
qubit config set voice.silence_threshold 20    # or even 10 for very quiet setups
```

Also ensure hardware gain is adequate:
- Turn the interface gain knob to ~12 o'clock
- Speak 6-12 inches from the mic
- Watch for green halo LED flashes (not solid red = clipping)

## 3. Usage

### Interactive voice-to-voice
```bash
qubit                    # start interactive session
/voice on                 # enable voice-to-voice mode
# Press Ctrl+B -> speak -> press Ctrl+B again (or stay silent 3s)
/voice off                # disable
```

### Voice mode commands
| Command | Effect |
|---------|--------|
| `/voice on` | Voice-to-voice (speak in, spoken reply) |
| `/voice tts` | Text-to-speech only (you type, AI speaks) |
| `/voice off` | Disable voice output |

## 4. Troubleshooting Checklist

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Ctrl+B does nothing | Not in interactive session, or voice mode not on | Run `qubit` (not `qubit chat -q`), then `/voice on` |
| Pressing right Ctrl (bare modifier) does nothing | Bare modifier isn't a valid PTT binding | The toggle requires a CHORD — `voice.record_key` must be modifier+key (`ctrl+b`, `ctrl+space`). `parseVoiceRecordKey` rejects a bare `ctrl`/`alt` (no key) and falls back to the default Ctrl+B; multi-modifier chords (`ctrl+alt+r`) are also rejected. Left/right Ctrl are NOT distinguished — right Ctrl+B works identically. Verify the configured key first: `grep -A5 '^voice:' $QUBIT_HOME/config.yaml` |
| Recording beeps but no file | Threshold too high for pro audio interface | `qubit config set voice.silence_threshold 20` |
| `ImportError: No module named sounddevice` | Not installed in Qubit venv | `venv/bin/pip install sounddevice pyaudio SpeechRecognition` |
| `PortAudioError: Invalid sample rate` | Hardware doesn't support 16kHz directly | Use PipeWire/Pulse default device (index 8/9/10), not raw ALSA hw |
| `paInvalidSampleRate` on Scarlett | Direct ALSA hw device rejects 16kHz | The `default` device (index 10) handles resampling; use that |
| TTS fails with "Unsupported managed OpenAI speech model" | Wrong model name for gateway | Set `tts.openai.model` to `gpt-4o-mini-tts` |
| TTS stays on `piper` after editing repo `config/voice.yaml` | Live bridge reads `~/.config/openclaw-voice-bridge/voice.yaml`, not `~/Projects/Repos/openclaw-voice-bridge/config/voice.yaml` | Patch the live file: `grep ^tts ~/.config/.../voice.yaml`, set `provider: openai` + `openai: {voice: nova, model: gpt-4o-mini-tts, instructions: "...sexy Japanese..."}` then `systemctl --user restart qubit-voice-bridge` |
| Voice slow (~1 s TTS lag) + chattery heartbeat | `grok-voice-bridge` `openai` voice = network TTS (~1.0 s) + heartbeat LLM every 8-15 s | Bump `progress_min_interval_s: 15→22`, hum `uniform(8,15)→(15,30)`, progress LLM cooldown `20→30s`, and use `scripts/generate_report.py` instead of an extra formatting LLM call |
| `Yeah.` / `hello` takes 4.6 s (`intent.llm` with `qwen3-vl:8b`) | Filler is routed through the Ollama classifier before `_handle_conversation` — cheap greetings must never hit the LLM | Add a filler fast-path **before** `Classifier.classify()` in both `_route_intent` (mic) and `_process_web_utterance_inner` (web): `if low in ("hello","yeah","hi"…) → _handle_conversation directly` (logs `routing.filler_fast`, <100 ms). Also switch `intent/llm.py` default `qwen3-vl:8b` → `huihui_ai/llama3.2-abliterate:3b` (resident, ~0.5 s) — filler still bypasses it entirely |
| Voice-to-text empty (`chars 0` on 2 s capture, peak 0.26-0.37 but transcript blank) | `sherpa/parakeet` can return 0 chars on a real wav even when mic heard energy — not just VAD | Keep `asr.provider: sherpa` `engine: parakeet` as primary, add `_transcribe_with_fallback()` (retry once with the *other* engine: `parakeet ↔ moonshine`, 48-172 ms). `moonshine` is fastest fallback (0.05 s), not `faster-whisper` (0.46-1.34 s bench on same wav). Also lower `vad.rms_gate 0.0018→0.0015` for soft syllables; don't paper over it with routing tricks |
| Hybrid TTS never saves money | `tts.provider: openai` bills every short filler | Use `tts.provider: hybrid` (new `voicebridge/tts/hybrid.py`): ≤18 words → local `piper` (free, 0.08 s), >18 words / reports / heartbeat → `openai gpt-4o-mini-tts:nova` (sexy Japanese). Saves ~70 % while keeping the voice for reports |
| `faster-whisper CUDA runtime failed` / `Library libcublas.so.12 is not found` | libcublas.so.12 missing from the linker path — the only copy on this box is Ollama's bundle | `export LD_LIBRARY_PATH=/usr/local/lib/ollama/cuda_v12` BEFORE importing faster_whisper/ctranslate2 (or bootstrap in code). GPU then runs ~180× realtime (whisper base: 0.04 s for 7.2 s audio). Do NOT accept the silent CPU fallback as "harmless" — it's a fixable misconfig |
| No audio over SSH | No PulseAudio/PipeWire forwarding | Export `PULSE_SERVER` or `XDG_RUNTIME_DIR` |

## 5. Quick Diagnostic Script

Run this to test the full voice pipeline:

```bash
$QUBIT_HOME/hermes-agent/venv/bin/python3 -c "
from tools.voice_mode import AudioRecorder, transcribe_recording
import time

rec = AudioRecorder()
print('SPEAK NOW for 3 seconds...')
rec.start()
time.sleep(3)
print('Has spoken:', rec._has_spoken)
print('Peak RMS:', rec._peak_rms)
path = rec.stop()
rec.shutdown()
print('File:', path)

if path:
    result = transcribe_recording(path)
    print('Transcript:', result.get('transcript'))
"
```

Expected: `peak_rms` > threshold, `path` is a WAV file, transcript contains your speech.

## 6. Local ASR/TTS pitfalls on this box (3070 Ti, 8 GB)

- **whisper model size vs VRAM**: `base` (fp16, ~0.5 GB) coexists with a loaded
  Ollama agent model; `small` OOMs while a 22 GB MoE is hot. Default to `base`
  with `device: auto` (cuda → cpu fallback on OOM, logged).
- **whisper confidence is conservative**: `avg_logprob` mapped as `(x+1)/2`
  gives ~0.3 for a perfectly transcribed TTS utterance. Rejection thresholds
  must be LOW (≈0.15) plus a filler-word check ("uh/um/hmm"), never 0.35.
- **piper-tts (pip, v1.7)**: no built-in voice download — fetch `.onnx` +
  `.onnx.json` from
  `https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/<voice>/medium/`.
  `synthesize_wav(text, wav_file)` expects a `wave.Wave_write` object, not a
  binary file handle. **espeak-ng (the phonemizer C lib) is NOT thread-safe**:
  two PiperVoice instances synthesizing concurrently (progress thread + main
  thread, or a second test instance) SEGFAULT in `piper.espeakbridge` — the
  crash trace shows `piper/voice.py phonemize` with no Python frame. Serialize
  ALL synthesis with a module-global `threading.Lock` (a per-instance lock is
  NOT enough — espeak-ng is a process-global C singleton).
- **webrtcvad** needs `pkg_resources`, removed in setuptools 81+ → pin
  `setuptools<81` in the venv. uv venvs have no `pip` binary — use `uv pip
  install` or `python -m ensurepip` first.
- **pynput** needs DISPLAY (X11 session); import fails headless ("Bad display
  name") → degrade to manual Enter-toggle PTT instead of crashing.
- **Sounddevice on this box**: use the PipeWire `default` device (index 10), not
  raw ALSA hw — simultaneous in+out streams via `default` work; raw ALSA paths
  throw `alsa_snd_pcm_mmap_begin` errors. Keeping one always-open barge-in
  InputStream beats opening/closing a stream per utterance.

## References

- `references/provider-quirks.md` - Provider-specific compatibility notes (OpenAI, Edge, ElevenLabs, etc.)
- `references/hardware-notes.md` - Known audio interface quirks and gain settings
- `references/voice-bridge-coexistence.md` - Running Qubit voice alongside the Qubit Voice Bridge on the same box (8 GB coexistence, port 8890, keep-alive)
