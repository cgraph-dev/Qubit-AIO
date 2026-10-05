# Voice Front-Door — Design (2026-10-03)

Owner ask: voice must be the front door to everything — explicit routing to
the full /chat worker, Perplexity Pro as the web brain, VS Code edits by
voice, spoken reports, proactive briefings — all talking back. Voice feels
pointless today not from missing capability but from routing: voice answers
directly and reaches the full agent only when the shortcut fails.

Status: DESIGN FOR REVIEW. Nothing below is built. Each slice is proven
live before the next begins.

## 1. Current truth (measured, not claimed)

- `/qubit-chat turn()` classifies into `answer_local / web_search /
  local_action / registry / agent_task / clarify / refuse`
  (`voicebridge/chat_transport.py`). The `agent_task` branch runs the
  headless /chat worker (`ORC.launch_task`) with full tools and reports
  back — **/qubit-chat → /chat routing already exists**.
- Voice `ask()` answers directly (live facts, skills, brain on
  `chat_model=brain4`) and dispatches to the full turn only on brain
  failure (Decision 26, Slice 1). The tool world sits behind a fallback.
- Voice coverage: 82.22% real intents, 0 dead backends
  (`docs/generated/voice_coverage.json`). Coverage ≠ usefulness.
- Web branch order today: DDG first (~1.2 s, links only, rate-flaky —
  2nd rapid query failed live 2026-10-03), Perplexity API only on DDG
  miss, browser-Pro last. `intent.web_paid_first` unset (default false).
- Perplexity API measured 2026-10-03: `sonar` 2.5 s, `sonar-pro` 1.2 s,
  both direct answer + citations. Browser-Pro path: CDP DOWN, script
  timeouts 60–120 s — unusable for live voice rhythm.
- VS Code: voice opens projects/docs (`code <path>`); edits only via the
  agent worker behind the classifier. Reports: `generate_report` working.
- Briefings/proactive voice: nothing exists.
- Live models (measured 2026-10-03, both 100% GPU): brain4 (Qwen3-4B)
  exact answer 8.0 s; spark17 (1.7B) 11.9 s + narrated the instruction.
  Memory (name/identity) is per-turn `USER.md` injection, model-agnostic.

## 2. Misconfigurations to fix (with the slices, not before)

1. `intent.local_llm.model`, `intent.ollama_model`, `intent.runner.alias`
   still `qubit-spark17` — the intent classifier runs on the narrating
   model. → brain4 (with Slice 2).
2. `intent.web_paid_first` unset → DDG-first contradicts the design.
   → `true` (with Slice 2).
3. CDP down → browser-Pro path dead. Health-gate + supervisor (Slice 6);
   until then the order must not depend on it.

## 3. Slices

### Slice 1 — Route words + talk-back + barge-in
- Explicit prefixes bypass the classifier: **"in chat: …"** → headless
  /chat worker (existing `agent_task` branch); **"quick: …"** → direct
  answer (today's behavior).
- Progress talk-back: immediate acknowledgment ("on it, searching now"),
  never silent waiting; spoken summary + full text in History.
- Barge-in: 150–300 ms confident speech on the echo-cancelled mic while
  TTS plays stops the reply and yields (research-backed params; our
  500 ms end-silence already in band — unchanged).
- Confirm-gated writes keep the existing voice-"yes" settle.
- Accept: one "in chat" build end to end by voice; interrupt-mid-reply
  proven; no double-execute; classifier untouched for unprefixed turns.

### Slice 2 — Web order + hardening
- New voice web order: `sonar-pro` API → browser-Pro (CDP-healthy only,
  25 s bound, "still reading…" talk-back) → DDG last resort. Loud
  degrade at each layer, never silent.
- API caller: retry-with-backoff + `sonar-pro` → `sonar` degrade (we hit
  a live 429 proving the need). 10-minute identical-query cache (kills
  the rapid-follow-up problem for all three paths).
- CDP health in voice pre-flight: one honest "browser search
  unavailable, using API" instead of failing mid-turn.
- Accept: one web question per path live; 429 simulated → degrade proven.

### Slice 3 — VS Code edits by voice
- Shape (default): "in chat, in repo X, change …" → worker opens VS Code
  on the file (`code -g path:line`), applies the change, reports file +
  lines changed. Dictated line-by-line edits are out (error-prone).
- Upgrade path (same slice if smooth): the official VS Code MCP
  integration (native MCP server: live editor state, workspace edits,
  tasks, diagnostics) with Qubit as MCP client — `code --add-mcp` to
  install. Falls back to CLI-open + worker-edit when the extension is
  absent. Reads back the diff summary, never the whole file.
- Accept: voice edit lands in a real repo, VS Code open on the change,
  spoken diff summary.

### Slice 4 — Proactive briefings (needs owner answers below)
- "While you were away" digest (voice + History): completions, cron
  output, important mail/social — need-to-know first, max 2–3 items.
- "Brief me" morning briefing: weather, calendar, unread, today's cron.
- Rules: sleep mode instead of quiet hours (owner decision §5 —
  no fixed schedule, so "I'm going to sleep" silences unprompted
  speech until the next "hey qubit"); batched non-urgent items, short
  cancelable readouts, one idea per block.
- Accept: digest + briefing by voice; quiet-hours suppression proven
  (no speech inside the window).

### Slice 5 — PipeWire AEC mic source (the real echo fix)
- `module-echo-cancel` (WebRTC AEC) echo-cancelled source feeds the mic
  chain; ASR/KWS consume it instead of raw mic. Replaces the 5 s
  journal-skip hack (kept as belt-and-braces until AEC is proven).
  Host verified PipeWire, no echo-cancel loaded — recipe applies.
- Accept: owner-eared test — own replies no longer transcribe; "hey
  qubit" during speaker playback fires correctly.

### Slice 6 — CDP supervisor
- Watchdog keeps Chromium alive with the debug port, pins
  Perplexity/Gmail tabs, exposes health the Slice-2 pre-flight reads.
- Accept: kill Chromium → supervisor restores → voice web works, no
  manual relaunch dance.

### Slice 7 — STT eval spike (background, throwaway)
- Benchmark NVIDIA Parakeet TDT 0.6B v3 (~23× Whisper throughput,
  6.3% WER, ~2 GB) against our faster-whisper-small on owner speech.
  Adopt only if strictly better on accuracy AND latency. Output is a
  recommendation, not code kept.

### Slice 8 — KWS eval spike (background, throwaway)
- Train an OpenWakeWord "hey qubit" model (open-source, ONNX, Linux CPU;
  rated best DIY custom-phrase spotter) against our current spotter
  (3 hits ever — acoustically deaf). If it hears reliably, the whole
  ASR-fallback scaffolding (§47–§75) can shrink to a backstop.
  Output is a recommendation, not code kept.

### Slice 9 — Realtime watch (no commitment)
- Kyutai Voice of Reason (open-weight speech-to-speech) and
  Qwen-Audio-3.1-Realtime (full-duplex, interrupt/yield) stay on watch;
  Gemini/ElevenLabs/Grok items are cloud-first, not us. Revisit when a
  spike is cheap. Qwen3-8B evaluated and REJECTED (no VRAM headroom
  beside the speech stack); brain4 stays the voice tier.

## 4. Build order

1 → 2 → 3 (each independently useful, each proven live before the next),
then 4 (needs answers), 5 and 6 in any order, 7–8 as background spikes,
9 on watch. Slices 1–3, 5–6 are bounded work on existing flows; Slice 4
gets its mini-spec once the questions below are answered.

## 5. Owner decisions (2026-10-03 — all four answered)

1. **No quiet hours — manual sleep mode instead.** Owner has no fixed
   sleep schedule, so time-based silence can't work. Design: saying
   "I'm going to sleep" (or "going to sleep") puts voice into sleep
   mode — no unprompted speech at all. The next directed "hey qubit"
   wakes it and clears the mode. Explicit beats clever.
2. **Briefings: (a) + (b).** On-request ("brief me") AND unprompted
   spoken completions ("report's ready"). Sleep mode gates (b), never (a).
3. **Slice 3 default ACCEPTED.** Open + apply + report; no line-by-line
   dictation.
4. **Spikes APPROVED.** Try Parakeet (Slice 7) and OpenWakeWord
   (Slice 8); ship slices 1–3 first, spikes run as background work.

## 6. Research log (Perplexity Pro, 2026-10-03)

- Voice-AI releases late-Sep/early-Oct 2026: Kyutai Voice of Reason,
  Qwen-Audio-3.1-Realtime, Gemini 3.8 Live/Flash TTS, Grok Transcribe
  2.0, ElevenLabs v4 — only the first two matter to self-hosted.
- Proactive-voice best practice: short/scheduled/cancelable, batch
  non-urgent, quiet hours by default, urgent-only escalation.
- Small models 1–8B: Qwen3-4B still competitive (tool use); 8B better
  but rejected on VRAM math.
- Barge-in recipe: PipeWire echo-cancel + 150–300 ms trigger; VAD
  10–30 ms frames; matches our 500 ms end-silence.
- Local STT: Parakeet TDT 0.6B v3 speed/accuracy leader → Slice 7.
- Custom wake word: OpenWakeWord best open DIY → Slice 8.
- VS Code agent control: official native MCP integration → Slice 3.
- Our own 429 on `sonar-pro` → Slice 2 backoff requirement.
