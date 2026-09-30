# Audio Interface & Microphone Hardware Notes

## Focusrite Scarlett 2i2 (4th Gen)

- **Issue:** Lower software signal levels than built-in mics cause recordings to be discarded at default threshold
- **Default threshold:** `voice.silence_threshold: 200` → recordings discarded
- **Working threshold:** `20` (or `10` for very quiet setups)
- **Hardware gain:** Turn knob to ~12 o'clock for normal speech at 6-12 inches
- **LED indicator:** Green halo flashing = good level; solid red = clipping (turn gain down)
- **Sample rate quirk:** Direct ALSA hw device (`hw:1,0`) rejects 16kHz; PipeWire/Pulse `default` device handles resampling

## General Pro Audio Interface Guidelines

| Interface Type | Typical Threshold | Notes |
|----------------|-------------------|-------|
| Built-in laptop mic | 100-200 | Higher software gain |
| USB headset | 100-200 | Consumer-level output |
| Focusrite Scarlett | 10-30 | Pro line-level, lower software gain |
| MOTU, RME, etc. | 10-30 | Similar to Focusrite |
| XLR condenser + cloudlifter | 10-20 | May need even lower threshold |

## Checking Your Interface's Signal Level

Run this diagnostic and speak normally:

```bash
$QUBIT_HOME/hermes-agent/venv/bin/python3 -c "
from tools.voice_mode import AudioRecorder
import time
rec = AudioRecorder()
rec.start()
time.sleep(3)
print('Peak RMS:', rec._peak_rms)
print('Has spoken:', rec._has_spoken)
rec.stop()
rec.shutdown()
"
```

- **Peak RMS < 50:** Threshold too high → lower to 10-20
- **Peak RMS 50-200:** Lower threshold to ~30
- **Peak RMS > 500:** Normal consumer mic → default 200 should work
- **Peak RMS = 0:** No signal → check hardware gain, cable, phantom power

## PipeWire / PulseAudio Notes

On modern Linux (Ubuntu 22.04+, Fedora, etc.), PipeWire handles audio. The `default` ALSA device (usually index 10) is the safe choice — it resamples and routes correctly. Raw `hw:X,0` devices may reject non-native sample rates.
