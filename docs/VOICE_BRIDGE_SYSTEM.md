# Voice Bridge — System, Learnings & Roadmap

> Living doc: everything the bridge does, what we cherry-picked from open-source
> projects, what we still want to build, and how to keep it smart/fast.
> Keep in sync after each big change. Refresh with `graft build` if you use the
> code graph.

---

## 1. What this system IS (current)

A **local, low-resource, English-only voice assistant** bridging your mic to
agents/browsers/tools. Runs on the RTX 3070 Ti 8 GB + Ryzen 5700X.

### The pipeline (each turn)
```
mic (HyperX USB mono) --> 16k WAV
   -> ASR sherpa parakeet (fallback moonshine) (0.5s, 0 VRAM)
   -> [if empty: cached fallback]
   -> classifier (heuristic + optional LLM)
   -> route (local | openclaw | hermes | browser | coding | conversation)
   -> local model qubit-brain (3.6B, resident) answers chat (~0.5s)
   -> TTS piper en_US-amy (22050Hz) -> resample -> default sink (HyperX IEC958)
```

### Key files
| File | Role |
|---|---|
| `voicebridge/local_llm.py` | local chat model + EN-only guard (retries non-English) |
| `voicebridge/intent/classifier.py` | deterministic + LLM router |
| `voicebridge/orchestrator.py` | main loops, task queue, interrupt, heartbeat |
| `voicebridge/learning.py` | **CLHF** — learns from feedback (no retraining) |
| `voicebridge/audio/mic.py` | capture, best-channel mono, VAD |
| `voicebridge/audio/playback.py` | pinned-output TTS playback + resample |
| `voicebridge/asr/sherpa.py` | parakeet/moonshine ASR |
| `voicebridge/webui.py` | dashboard server + REST API |
| `scripts/browser_auto.py` | read/scrape/open Chromium tabs (CDP) |
| `scripts/generate_report.py` | deterministic rich reports |
| `Modelfile.qubit` → `qubit-brain` | the baked identity/behavior/model |

### Health / watching it
- `qubit-dev` terminal: `/status`, `/health` (status + recent errors + timing),
  `/watch` (live activity tail), `/logs`, `/activity`
- `events.jsonl` = source of truth; dashboard Activity/Reports/Logs read it.

---

## 2. Language & mic (current decisions)
- **English only.** All Romanian removed (TTS voice, ASR multilingual tier, RO
  detector, dialogs, baked rule). A reply-guard retries if the model drifts out
  of English.
- **Mic = HyperX Cloud III (USB, mono).** `mic.channels: 1` (it's mono; the
  unplugged Scarlett needed 4). `mic.gain: 3.0` + a peak-normalizer (clip to
  ~0.35 before ASR) so a quiet/variable mic transcribes consistently.
- **Output = HyperX USB digital (iec958)** — the same sink YouTube/browser use,
  so the agent is audible there too. The bridge now follows the DEFAULT sink
  (unreliable raw-ALSA device indices/churn caused 'can't hear the agent').
  `speak_web_locally: false` -> the browser plays audio_b64 once = single speech
  (was double-speaking on web). Playback has a watchdog (a wedged device can't
  freeze a request) and falls back to the default sink on device-open failure.
- **USB port speed matters**: audio USB devices on a slow USB1.1 (12M) port
  freeze the moment a stream opens. `scripts/diagnose_audio_usb.py` detects this
  (we hit it with the HyperX until it was moved to a fast port). `Audio` device
  churn (indices shift on each reconnect) is why we resolve BY NAME.

---

## 3. What we TOOK from open-source (and built)

> **See `STEAL_LIST.md`** for the focused speed/smarter/minimal-VRAM steal-list
> with the specific techniques to implement next.

### CopilotKit — Self-Learning (CLHF) ✅ SHIPPED
- Realized: agents improve in-context from feedback, no fine-tuning.
- Built: `LearningLedger` (state_dir/learning.jsonl, fsynced) + `POST /api/feedback`
  + 👍/👎 buttons on every history turn. Lessons injected as
  `<lessons-what-the-user-taught-me>` (always-true, override old/wrong answers).
- Diagram ref: CopilotKit's **Generative UI + Human-in-the-loop** (we use decision
  buttons for follow-ups, e.g. windowed-tab vs background).

### AirLLM — keep large models resident + quantized ✅ VALIDATES OFFICE
- Realized: offload/stream layers; only quantize weights to keep accuracy.
- We already do the relevant part: `keep_alive -1` (model always resident),
  Q4_K_M quantization, cached recognizers. No need to rewrite — but it informs
  the "bigger model tier" idea below.

### OpenSandbox — credential vault + isolated execution 🔜 PARTIAL
- Realized: keep secrets in a vault, run untrusted code isolated.
- We keep secrets in `~/.hermes/.env` (never committed/baked). Full sandboxing is
  not critical because scripts are whitelisted, but see roadmap.

---

## 4. What we DIDN'T take (and why)
- **CopilotKit full framework**: huge React/state layer — overkill; our dashboard
  is a lean static page. We cherry-picked the *learning loop* idea only.
- **AirLLM runtime**: our 3B is tiny + resident; layer-streaming is for running
  huge models slowly. Wrong trade for low-latency voice.
- **OpenSandbox Docker/K8 sandboxes**: heavy VM overhead per call; our tool calls
  are local + whitelisted.

---

## 5. Roadmap — what to do NEXT (priority order)

### High value, low risk
1. **Live task streaming** (CopilotKit Generative UI): push agent/task steps to the
   dashboard progressively (SSE or incremental `/api/activity`) so long tasks show
   real-time progress, not just a spinner.
2. **Feedback → auto-preference**: on `feedback: good/bad` also auto-extract a
   short preference and add it (so repeated good answers become rules).
3. **Self-tuning mic**: at boot run mic-test, measure peak, and auto-adjust
   `mic.gain` to hit a target RMS (~0.1-0.2) so room/headset changes don't break ASR.
4. **CLHF vs poisoning**: on repeated "bad" for the same question, prune the
   conflicting rows (avoid oscillating lessons).

### Medium
5. **Bigger model quality tier** (AirLLM idea): when GPU is free and the task is
   hard, `normal/turbo` tiers could use a larger resident model (e.g. Qwen3-8B
   Q4) that AirLLM-style compression fits in 8GB — better answers, still fast.
6. **Inbox/report enhancements**: richer per-account tab reads (multi inbox),
   auto-route "check my {platform}" to the right review script with the handle.
7. **Sandboxed script execution** (OpenSandbox idea): run generated code in a
   container/subprocess sandbox for safety on risky tools.

### Lower
8. **Multi-channel** (CopilotKit Channels): expose the same agent on
   Discord/Telegram (already have accounts) — reuse the routing, new UI surface.
9. **Report "share/copy" buttons** in the dashboard; export to PDF/MD.
10. **Voice-ID hardening**: more enrollment samples; voice barge gates.

---

## 6. Config reference (live, tuned)
Provided in `~/.config/openclaw-voice-bridge/voice.yaml` + defaults in
`config/voice.yaml` + `voicebridge/config.py` (read the file for exact values).

---

## 7. Tests
`tests/test_stress.py` — run with `.venv/bin/python -m pytest tests/test_stress.py -q`.
Covers: orb rapid-press freeze (node), CLHF ledger, playback resample, routing
success ≥95%, rich reports, mic-gain guard, EN-only language guard.
`scripts/test_orb_frozen.mjs` + `scripts/test_orb_stress.mjs` — atomic frontend
state-machine proofs.

---

---
## 8. The one "bigger" idea we keep circling
**A smarter local model without losing speed.** The 3B is fast (~0.5s) but not
deep. AirLLM shows you can run a **7-8B Q4 on 8GB**. Plan:
- Load `Qwen3-8B` (Q4) resident on the GPU for `hard/pro` tier and hard questions.
- Keep the 3B as the fast default chit-chat model.
- Route by complexity (heuristic + feedback), keep `keep_alive -1`.
- Validate on your box: measure tokens/s + VRAM before switching the default.
This is the highest-leverage "make it much smarter" improvement available.
