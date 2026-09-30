# Voice Mode Troubleshooting on Linux

Session-specific reference for debugging Qubit voice mode (push-to-talk) when `Ctrl+B` appears to do nothing or produces empty transcripts.

## Quick Diagnostic Flow

1. **Check audio environment detection**
   ```python
   from tools.voice_mode import detect_audio_environment
   import json
   print(json.dumps(detect_audio_environment(), indent=2))
   ```
   - `available: true` + empty `warnings` = libraries and devices are OK
   - If warnings mention PortAudio, install system libs first

2. **Check if recording actually captures audio**
   ```python
   from tools.voice_mode import AudioRecorder
   import time
   rec = AudioRecorder()
   rec.start()
   time.sleep(3)
   print("Has spoken:", rec._has_spoken)
   print("Peak RMS:", rec._peak_rms)
   print("Frames:", len(rec._frames))
   path = rec.stop()
   print("Path:", path)
   rec.shutdown()
   ```
   - `Path: None` + `Peak RMS < 200` = **silence threshold too high** (see Pitfall #7 in SKILL.md)
   - `Path: None` + `Frames: 0` = stream not capturing at all (device/driver issue)
   - `Path: /tmp/hermes_voice/...` = recording works, check transcription next

3. **Test transcription directly**
   ```python
   from tools.voice_mode import transcribe_recording
   result = transcribe_recording('/tmp/hermes_voice/recording_xxx.wav')
   print(result)
   ```
   - `filtered: True` + empty transcript = whisper filtered it (too quiet or no speech)
   - CUDA errors are normal if no GPU; it falls back to CPU automatically

## Device Selection Notes

PipeWire/Pulse `default` device (usually index 10) is preferred over raw ALSA hardware devices because:
- Raw ALSA devices like `hw:1,0` (Scarlett 2i2) may reject 16kHz sample rate with `PaErrorCode -9997`
- PipeWire resamples transparently

List devices:
```python
import sounddevice as sd
print(sd.query_devices())
```

## Known Hardware Patterns

| Hardware | Typical RMS | Recommended Threshold |
|----------|-------------|----------------------|
| Built-in laptop mic | 500–3000 | 200 (default) |
| USB headset | 300–2000 | 200 (default) |
| Focusrite Scarlett (low gain) | 1–10 | 10–20 |
| Focusrite Scarlett (high gain) | 50–300 | 20–50 |

## Config Values

```bash
# Very sensitive (quiet rooms, dynamic mics)
qubit config set voice.silence_threshold 10

# Sensitive (professional interfaces, close-mic'd)
qubit config set voice.silence_threshold 20

# Default (built-in mics, headsets)
qubit config set voice.silence_threshold 200

# Disable auto-stop (manual Ctrl+B only)
qubit config set voice.silence_duration 999
```

## Related Files

- `tools/voice_mode.py` — `AudioRecorder.stop()` line ~761: `if self._peak_rms < SILENCE_RMS_THRESHOLD`
- `cli.py` — `handle_voice_record` line ~12526: prompt_toolkit keybinding
- `hermes_cli/voice.py` — `normalize_voice_record_key_for_prompt_toolkit`
