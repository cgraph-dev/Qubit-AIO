# Sherpa-onnx ASR tuning — decoding methods & VAD endpointing

Quick reference for the sherpa-onnx backends used by the voice bridge. All values
verified against sherpa-onnx **1.13.6** (the version installed in the local venv).

## Decoding methods by backend

| Engine | Recognizer factory | Supported `decoding_method` values | Notes |
|---|---|---|---|
| moonshine | `from_moonshine_v2` | `greedy_search` (default) | No beam search; Moonshine decodes in one pass. `max_active_paths` is ignored. |
| parakeet (NeMo transducer) | `from_transducer` | `greedy_search`, **`modified_beam_search`** | `"beam_search"` and `"modified_beam"` are **rejected** → crash at load time. Use `"modified_beam_search"` for WER improvement on Parakeet. |

### Beam search on Parakeet (the WER-cut play)
```python
# voicebridge/asr/sherpa.py, inside from_transducer(...)
decoding_method="modified_beam_search",
max_active_paths=4,    # 1 = greedy-equivalent; 4–8 = best WER / latency tradeoff
```
- Parakeet-TDT 0.6B-v3 int8 decode is <1 s on CPU even with beam search → stay fast.
- `max_active_paths` controls the beam width; values 4–8 are the sweet spot.

## VAD endpointing tunables (Silero, in `voice.yaml`)

The goal: catch quick speech starts without dropping leading phonemes, and don't
cut off mid-thought pauses.

| Parameter | Default | Tuned | Why |
|---|---|---|---|
| `vad.speech_start_ms` | 180 | **280** | Silero has a pre-roll buffer that backs the leading edge; 280 ms avoids false-triggering on consonants while still catching fast starts. |
| `vad.silence_timeout_s` | 0.8 | **1.2** | Research shows 0.7–0.9 s is the lower bound for natural pauses; 0.8s cuts mid-thought. 1.2 s gives breathing room without dragging turn latency. |
| `vad.max_utterance_s` | 30.0 | 30.0 | Hard cap; unchanged. |

### Config file locations
The bridge reads config from `$VOICE_BRIDGE_CONFIG` (set by the systemd service
to `%h/.config/openclaw-voice-bridge/voice.yaml`). The repo source of truth is
`config/voice.local.yaml` — **both must be updated** when tuning VAD or ASR config:

1. `config/voice.local.yaml` (repo — commit this)
2. `~/.config/openclaw-voice-bridge/voice.yaml` (runtime — the systemd service reads this)

The voice-bridge service unit sets:
```
Environment=VOICE_BRIDGE_CONFIG=%h/.config/openclaw-voice-bridge/voice.yaml
```
So edits to the repo copy alone will NOT take effect until the runtime copy is
synced and the service is restarted.

## Restart + verify pattern

After any config or code change to the bridge:

```bash
# 1. restart the user service
systemctl --user restart openclaw-voice-bridge.service

# 2. verify it's actually running (not in a crash loop)
systemctl --user status openclaw-voice-bridge.service | head -10

# 3. verify the web dashboard port is listening
ss -tlnp 2>/dev/null | grep 8890

# 4. HTTP sanity check
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8890

# 5. check the journal for ASR load confirmation (or crash errors)
journalctl --user -u qubit-voice-bridge -n 10 --no-pager | grep -E 'asr\.load|Unsupported'
```

If the service is in a crash loop (restart counter climbing, port 8890 never
opens), check the journal for `Unsupported decoding method` — that's the
sherpa-onnx beam-search string mismatch pitfall (#8 in pitfall-catalog.md).