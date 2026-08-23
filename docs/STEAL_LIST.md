# Steal-List: Speed, Smarter, Minimal VRAM

> FOCUS: make the local agent **faster + smarter** while using **as little VRAM
> as possible** (RTX 3070 Ti 8GB, shared with ollama + occasionally whisper).
> All three sources are permissive-licensed (CopilotKit = MIT, AirLLM & OpenSandbox
> = Apache-2.0) so we can copy/adapt any of it legally, even proprietary.
>
> Rule: WE DO NOT drop in whole frameworks. We extract the *specific technique*
> that fits our Python bridge and implement it natively (lean, no bloat, no rewrite).

---

## 0. The hard constraint
8 GB VRAM total. Currently ~2.6 GB = qubit-brain (3.6B, Q4_K_M, resident).
Headroom ≈ 5 GB. Whisper-large is removed (EN-only). So we have room for
**one more, larger, resident model** IF it's heavily quantized AND we stream
responsibly. Anything that doubles memory per call is out.

---

## 1. From AirLLM — run a BIGGER SMARTER model on the same GPU (the big win)

### The idea AirLLM proves
- **Only keep what's needed in VRAM.** AirLLM keeps ONE layer on GPU at a time
  (layer-shard streaming) — so a 70B fits on 4GB. For US: we don't want the disk
  latency of streaming per layer, but the **quantization** half is directly usable.
- **Block-wise / BNB quantization of WEIGHTS only** → ~3x speedup, near-zero loss,
  because the bottleneck is memory-not-compute for small models. AirLLM: `compression='4bit'/'8bit'`.

### What we can do (highest leverage)
- Add a **`hard`/`pro` model tier** that loads a smarter model, quantized to fit 8GB:
  - `Qwen3-8B` (Q4_K_M ≈ 5.3GB) for complex/logical questions.
  - Keep `qubit-brain` 3.6B as the fast default for chit-chat + routing.
  - Switch by complexity: heuristic + a cheap "is this hard?" check + user's
    `pro/hard/turbo` command + feedback signal (bad answers -> escalate to 8B).
- Store BOTH resident (`keep_alive -1`) so switching is instant (no reload).
  Monitor: if VRAM can't hold both, evict the 8B when idle and reload on demand
  (measure first — see validation below).
- **Prefetching overlap** (AirLLM): overlap model-load with compute. For us that
  means: warm the 8B in the background *after* the 3B answers, so the next hard
  question is instantly handled. Cheap thread, no user-visible latency.

### Validation before committing (run on this box)
```
# pull 8B Q4
ollama pull qwen3:8b --quantize q4_K_M  (or the huggingface equivalent)
# measure: tokens/s + peak VRAM for 2 resident models
ollama create hard-brain -f Modelfile.hard   # baked EN-only like qubit-brain
```
Accept it only if: 8B + 3B both stay resident under 8GB **and** 8B answers in
<1.5s on your hardware. If not, keep 8B on-demand (not resident) — AirLLM-style
"load only when needed, evict after" — slight reload cost, zero idle VRAM.

---

## 2. From CopilotKit — faster interaction + smarter UI (web/streaming)

### Techniques we can lift
1. **Streaming (SSE/ad-hoc events)** — CopilotKit streams agent text/actions.
   For us: **incremental `/api/activity`** (or an SSE endpoint) so long tasks show
   step-by-step progress vs a spinner. Perceived speed ↑ (not actual, but UX).
2. **Agent Skills / tool schema** — CopilotKit teaches agents to use tools via
   structured definitions. We already have local actions + templates; formalize a
   tiny **tool schema** so the LLM classifications are more precise (better routing
   -> fewer wasted turns -> faster + less model calls).
3. **Human-in-the-loop** — we have this (decision buttons). Extend: **inline edit**
   so you can type a correction into the same turn (feeds CLHF instantly).
4. **Self-learning via CLHF` already shipped** — keep it; add **auto-preference**
   extraction from `good` feedback so repeated good answers become permanent rules.

### Speed from the UI side
- **Debounce + reuse**: dashboard already polls every 2s; lower to 1.5-2s and
  only fetch deltas (etag/since) so we don't re-render (fewer bytes, faster paint).
- **Preload TTS**: synthesize the reply while the model streams (pipeline overlap)
  — shaves ~0.3-0.5s per turn.

---

## 3. From OpenSandbox — cleaner tools + vault (resource-light parts)

### Techniques we can lift
1. **Credential vault pattern** → we keep secrets in `~/.hermes/.env` (uncommitted).
   Improve: one helper `get_secret(name)` + a warning if a required key is missing,
   and never echo secrets into logs/events.
2. **Sandbox lifecycle API shape** (provision/run/terminate) → apply the *same shape*
   to our tool calls: each `local_script`/scraper gets an **explicit timeout +
   bounded output + cleanup**, so a hung tool can't wedge the loop (frees resources).
3. **Streamed logs/metrics** → we already emit `events.jsonl`; keep the contract so
   `qubit-dev /watch` and the dashboard Logs stay the single source of truth.

### What NOT to take
- Docker/K8 sandboxes per call: heavy VM overhead, not needed for whitelisted tools.

---

## 4. Other speed/VRAM wins already in our stack (keep, don't regress)
- `keep_alive: -1` on ALL local model calls (never eject -> no cold reload).
- **Cached** ASR recognizers (fallback + main) — never reload per utterance.
- `num_ctx: 4096`, `num_predict` capped (bounded output = faster + less memory).
- `top_p: 0.9`, `temperature 0.4` (deterministic-ish, fewer wasted tokens).
- Playlist/summary via the deterministic `generate_report.py` (no model reasoning).
- Mic `gain: 3.0` + Air mode (clean capture -> fewer ASR retries -> faster turns).
- Cached beeps/WAVs; one thread for hum (less thread fan-out).

---

## 5. Concrete roadmap (ordered, minimal-risk first)
1. **[DO] Bigger-model `pro` tier** (AirLLM) — highest "smarter" ROI. Validate 8B
   fits + resident, else on-demand-with-evict. Bake EN-only.
2. **[DO] Auto-preference from feedback** (CopilotKit CLHF) — small, immediate.
3. **[DO] Tool schema for the classifier** (CopilotKit) — better routing, fewer calls.
4. **[DO] Inline correction in same turn** (CopilotKit HITL) — faster fixes, feeds CLHF.
5. **[DO] Delta polling + incremental activity streaming** (CopilotKit) — perceived speed.
6. **[MAYBE] Preload TTS while model streams** — real 0.3-0.5s win.
7. **[MAYBE] Sandbox-with-timeout helper** for scripts/scrapers (OpenSandbox shape).
8. **[SKIP] Whole frameworks / Docker sandboxes** — bloat, no benefit here.

---

## 6. What we will NOT do (and why)
- **Rewrite dashboard in React** for CopilotKit — our static page is lean + works.
- **Adopt AirLLM runtime** for the 3B — layer-streaming is SLOWER for small models.
- **Docker-per-tool** — unnecessary overhead for whitelisted local tools.

Because everything we actually ship is a *thin native implementation of a proven
idea*, it stays fast, low-VRAM, and maintainable — and we can legally build on all
three sources forever.
