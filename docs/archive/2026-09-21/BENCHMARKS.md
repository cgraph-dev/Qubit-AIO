# Benchmarks (Packet 4A)

Status: IMPLEMENTED — Packet 4A measurement surface. No model defaults,
aliases, Modelfile, runner options, or production runtime paths were changed.

## What exists

- `benchmarks/corpus/acceptance_cases.json` and `heldout_cases.json` — the
  fixed sanitized evaluation corpus (`voicebridge.eval-corpus/1`). Twelve
  required categories per split: deterministic routing, ambiguous routing,
  contextual confirm/cancel, local-versus-coding safety, malformed speech,
  short chat, personal-fact conflict, tool selection, invalid arguments,
  refusal/fallback, language policy, prompt injection. All content is
  synthetic; a mechanical sanitizer scan (loader-level) rejects real-mailbox
  emails, home paths, credential-shaped strings, and long digit runs.
  The holdout split is disjoint by ID and is never used for tuning.
- `voicebridge/benchmarking/harness.py` — transition benchmark harness.
  It WRAPS the unchanged production callers (`classify_with_ollama`,
  `chat_local_stream`, `summarize_ollama`) via an in-process transport
  recorder, so captured payloads are exactly what production sends.
- `voicebridge/benchmarking/unit_mode.py` — offline L0 route-latency
  evaluation of deterministic corpus categories through the pure
  `Classifier` (no network).
- `scripts/benchmark_transitions.py` — CLI entry point.

## Modes and outputs

Outputs go to separate directories under
`~/.local/state/voicebridge/benchmarks/<mode>/<timestamp>/` (mode `0700`),
each containing `environment.json`, raw unaveraged `transitions.jsonl`, and
`summary.json`.

| Mode | Network | Residency effect | Use |
|---|---|---|---|
| `unit` | none | none | offline corpus/L0 evidence; safe anywhere |
| `diagnostic` | Ollama only | read-only probes; leaves state warm | repeatable warm measurements |
| `live` | Ollama only | one unload → measure → restore cycle | cold/warm transition reproduction |

```bash
.venv/bin/python scripts/benchmark_transitions.py --mode unit
.venv/bin/python scripts/benchmark_transitions.py --mode diagnostic --iterations 5
.venv/bin/python scripts/benchmark_transitions.py --mode live --iterations 3
```

Exit codes: `0` clean, `2` degraded (typed reasons listed in `notes`),
`1` failed/invalid corpus.

## What is captured

Per step: wall time, `load_duration`, `prompt_eval_count/duration`,
`eval_count/duration`, tokens/s, first streamed content, first complete
sentence, synthesis-ready first audio (optional Piper synthesize-only probe;
never plays sound), resident context before the step, warm/cold label from
`GET /api/ps` (never inferred from `keep_alive`), typed errors.

Environment snapshot: model alias/details (family, quantization),
served Modelfile hash, repository `Modelfile.qubit4` SHA-256, Ollama version,
GPU name/driver/VRAM/utilization, MemAvailable, CPU model, CPU busy fraction
across the run.

Reload exposure: consecutive captured payloads are diffed on material runner
options (`num_ctx`, `keep_alive` presence) per pinned Ollama scheduler
semantics (docs/UPSTREAM_REUSE.md R5). The current live sequence reports
option drift events proving the classifier/chat/summary paths still carry
conflicting options (Packet 4B owns the fix).

Honesty rules enforced by code:

- warm and cold samples are reported separately and never averaged;
- raw rows are kept unaveraged in `transitions.jsonl`;
- warm `load_duration` is reported as a distribution; values over 1 s count
  as reload indicators (observed warm reuse overhead is ~150–190 ms);
- production callers that swallow failures return typed-empty results; the
  harness records these as failed steps rather than silent passes;
- skipped model-required categories in unit mode are counted separately.

## Live-mode restoration duty (rule 32)

Live mode records pre-run residency, unloads via the documented empty-prompt
`keep_alive=0` expire path, measures, then restores at the prior context with
`keep_alive=-1` and verifies via `/api/ps`. The restore report is embedded in
`summary.json`; any mismatch exits `DEGRADED — RUNTIME STATE CHANGED`.

## Baseline recorded 2026-08-25 (qubit-brain4, Q4_K_M, Ollama 0.32.14)

- diagnostic warm (5 iterations): chat p50/p95 ≈ 353/568 ms; classifier
  p50/p95 ≈ 598/665 ms; summary p50/p95 ≈ 459/462 ms; zero errors; residency
  ctx 4096 unchanged.
- live cold/warm (3 iterations): first classifier after unload ≈ 3556 ms
  (reproduces the plan's multi-second option-driven transition); restore
  verified at ctx 4096.
- optional first-audio probe (3 iterations): first content ≈ 194–253 ms,
  first sentence ≈ 235–296 ms, synth-ready audio ≈ 321–505 ms.

These are Packet 4A baselines, not acceptance gates; gating happens in later
Phase 4 packets on this corpus.

## Packet 4B re-baseline (sanitized artifact, canonical contract)

Artifact: rebuilt from sanitized `Modelfile.qubit4` (digest `5542f993…`,
backup alias `qubit-brain4-pre4b` = old digest `df22b4d4…`). All callers now
resolve one runner contract (alias, num_ctx=4096, keep_alive=-1).

Post-change diagnostic (10 iterations, warm):

| Step | warm p50/p95 (ms) | loads >1 s | drift events |
|---|---|---|---|
| classifier | 360/381 | 0 | 0 |
| chat | 359/621 | 0 | 0 |
| summary | 452/466 | 0 | 0 |

(Pre-change the same detector reported 14–24 drift events per run and a
~3.55 s option-driven reload on cold alternation.)

Context comparison (Packet 4B task: measure, don't inherit stale comments):
mixing num_ctx 4096 (summary) with 8192 (chat/classifier) reproduced the
reload pathology — every alternation paid a ~3.6 s load, drift=11, VRAM
3.9 GiB at 8192 vs 3.2 GiB at 4096. One context for ALL callers is mandatory;
4096 is selected because the compiled-context budget plus output reserve
fits with headroom (`fits_budget` test) at lower VRAM.
