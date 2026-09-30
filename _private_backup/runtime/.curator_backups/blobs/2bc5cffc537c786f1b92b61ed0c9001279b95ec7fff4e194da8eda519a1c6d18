# Accent & broken-English routing (Aug 2026)

Source session: user repro `route to open claw and create the text document in temporary folder called trickish micky and write text tricky miki...` — strict heuristic router + beam 1 dropped it; cost cap 20-30% extra.

## ASR
- Engine: `sherpa` `parakeet v3 int8` (`voicebridge/config.py: asr.engine`). Do NOT switch to moonshine for accent cases.
- `asr.beam_size`: 1 → **4** (config default + live `~/.config/openclaw-voice-bridge/voice.yaml`). Still <0.3s for ≤7s clips, large accent win.

## LLM tolerance
- `voicebridge/local_llm.py` SYSTEM_PROMPT: `You understand broken English, heavy accents, and typos — infer intended meaning (never mock).`
- `intent.local_llm` stays enabled (hybrid ~0.3s local, DeepSeek for long/web).

## Router
- `voicebridge/intent/classifier.py`:
  - `_BROKEN_MAP`: `root to→route to`, `open claw→openclaw`, `trickish→tricky`, `micky/miki→mickey`.
  - `normalize_broken_english(text) -> lowered, word-boundary replaced` — called at top of `classify()`; downstream heuristics see `norm`, emitted `task_text` is the normalized form so the agent receives clean phrasing.
  - `_FUZZY_TASK_VERBS` regex + `file|document|doc|text|folder|tmp|project|code|html|image|screenshot` co-signal → broken-English file task → `coding` at 0.68.
  - `intent.llm_classifier`: `false → true` in defaults (**voicebridge/config.py:114**) — adds ~0.3-0.6s but fixes ambiguous broken phrasing. Heuristic strong/partial signals still short-circuit before the LLM.

## Config propagation
- Changing `voicebridge/config.py` DEFAULTS alone is not enough: `~/.config/openclaw-voice-bridge/voice.yaml` shadows them. Patch live file on setup or document the need to re-run install.

## Verification
- `python3 -m py_compile voicebridge/intent/classifier.py voicebridge/local_llm.py`
- `voicebridge.intent.classifier.Classifier({}, ...).classify("root to open claw delete trickish micky file").task_text == "route to openclaw delete tricky mickey file"` and kind `coding`
- 8-case torture: `root to open claw and create text document in temp folder called trickish micky`→coding, `creat file test html please`→coding, `qubit write a report`→qubit, `hello how are you`→conversation
