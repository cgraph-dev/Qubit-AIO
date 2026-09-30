# Quiet-mic VAD gating (Focusrite Scarlett pattern)

Source session: RCtrl PTT beep-but-no-reply — `mic.utterance.empty peak_rms 0.00648` below threshold.

## Failure mode
`voicebridge/audio/mic.py:MicCapture.record_utterance` discarded the capture because `voiced_s < min_speech_s` (VAD never entered `speech`) even though the audio was clearly audible. The old single gate (`peak_rms < min_rms` or `voiced_s < min_speech_s → cancel`) made VAD the judge; ASR never saw the audio.

## Fix
- Lower thresholds jointly for quiet pro interfaces:
  - `mic.min_rms`: 0.0008 → **0.0005** (`voicebridge/config.py`)
  - `vad.rms_gate`: 0.004 → **0.0018** (`voicebridge/config.py`, `voicebridge/audio/vad.py:Endpointer/BargeInDetector`)
  - `MicCapture.min_speech_s`: 0.25 → **0.15**
- Keep `xinput` backend (do not switch to pynput).
- Add audible bypass in `mic.py` after computing `voiced_s`:
  ```python
  audible = peak_rms >= max(self.min_rms * 3, 0.0015)
  if not buf16 or (ended_by == "cancelled" and peak_rms < self.min_rms) \
          or (voiced_s < self.min_speech_s and not audible):
      return CaptureResult(None, 0.0, 0, ended_by, peak_rms)
  ```
  If `audible` is true, VAD miss is ignored and the payload is handed to ASR.

## Why this shape
- VAD trims silence / drives endpointing; it must not decide transcribability.
- Real Focusrite taps sit at ~0.006 peak after gain — well above noise floor (~0.003) but below the old `rms_gate 0.004` voiced floor.
- Always emit `log_event("mic.utterance.empty", peak_rms=...)` so threshold drift is visible in `events.jsonl`.

## Verification
- `python3 -m py_compile voicebridge/orchestrator.py voicebridge/webui.py voicebridge/hotkeys.py` must pass.
- Existing tests must stay green (`tests/test_units.py` etc. — no threshold bump that breaks them).
- Check `~/.local/state/openclaw-voice-bridge/events.jsonl` for `mic.utterance` vs `mic.utterance.empty` after a tap.
