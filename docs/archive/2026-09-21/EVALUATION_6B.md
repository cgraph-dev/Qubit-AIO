# Packet 6B — Evaluation, task outcomes, and the promotion gate

Implemented in `voicebridge/evaluation/` (a new stdlib-only package), the
`scripts/eval_gate_6b.py` CLI, the durable `state_dir/task_outcomes.jsonl`
records, and a small authenticated hook from the dashboard feedback endpoint.
Nothing is promoted automatically: the gate only returns a decision.

## Metric families (kept separate — never merged into one score)

| Family | Categories |
|---|---|
| safety | prompt_injection, local_vs_coding_safety, confirm_cancel_contextual, refusal_fallback, secret_handling |
| quality | correction_priority, personal_fact_conflict, memory_retrieval, short_chat, language_policy |
| latency | deterministic_routing, ambiguous_routing, malformed_speech |
| resource | model_residency, runner_options, vram_guard |
| tool_success | tool_selection, invalid_arguments, tool_execution |

The evaluator never emits a single weighted average. `score_run()` returns
`per_category` and `per_family` dicts, each with its own pass rate, error
count, and failure reasons, so a safety regression can never be hidden inside
a "better" quality or latency score.

## Task-outcome records

`TaskOutcomeStore` writes append-only, fsynced rows to
`state_dir/task_outcomes.jsonl`. Every row captures approval, retry count,
correction, completion, tool status, latency, error, and redacted user text —
not just the final answer. Injection- and secret-shaped text is never stored
verbatim (a marker is stored instead). Writes are thread-safe AND
cross-process safe (append-only JSONL; verified by a two-process test). The
orchestrator owns one store; the `/api/feedback` path records a
correction/rating outcome through it and the row survives a store reload
(durable on-disk flow, verified through the real HTTP handler).

## Evaluation set

- `base_cases()` — fixed synthetic, sanitized set (22 cases, every category).
  `expected_kinds` tuples encode the acceptable deterministic outcome, e.g.
  `conversation|clarify` = "no action".
- `load_private_set(state_dir)` — generated at runtime from the user's own
  evidence, never committed, sanitized. Three sources:
  1. completed/corrected error-free task-outcome records
     (`task_outcomes.jsonl`); real accepted tasks are routed to the classifier
     probe with route-derived expected kinds, so a coding task must still route
     coding after any change;
  2. real corrections from the Packet 6A lesson ledger (`learning.jsonl`)
     become `correction_priority` cases whose correction machinery is re-run
     on the genuine question/corrected text;
  3. real accepted-conversational feedback rows (good ratings whose question
     the offline router reads as NON-acting) become `short_chat` cases — the
     accepted task must stay non-acting after any change. Acting rows are
     skipped so current routing is never baked into the label.
- `load_eval_set()` — both, with disjoint case ids and honest split labels.
  Reports contain only aggregate scores — never the private case text.
  On the authoring machine the real private set is 3 genuine cases (1
  correction + 2 accepted conversations), all passing.

## Coverage policy (base authoritative / private personalization-only)

Every report and CLI run carries an explicit `coverage_policy`:

- **Base set — authoritative and MANDATORY** for `safety`, `prompt_injection`,
  `poisoning`, `latency`, `resource_limits`, `tool_success`, and `rollback`.
  The combined gate requires every base safety and poisoning category to pass;
  a safety regression, failed poisoning case, missing base category,
  unreviewable result, or failed rollback REJECTs or requires review.
- **Private split — genuine user-specific personalization QUALITY evidence**
  only (accepted conversations, corrections, approvals, retries, completions).
  It is derived from the user's own state; it is never fabricated, never
  contains secrets, and never carries unsafe content.
- **Private 0/0 families are NOT passing evidence.** Private
  safety/resource/tool/poisoning counts of 0/0 are reported as
  `not_applicable` to the private personalization split — never as a pass. The
  CLI prints e.g. `poisoning[private] N/A (0/0; not applicable ...)` and the
  report's `sets.<split>.poisoning.applicability` is `"not_applicable"`.
- **No weighted average and no automatic promotion.** Safety is gated strictly
  per category; the gate never promotes anything on its own.

On the authoring machine the real private split is 3 genuine quality cases
(1 correction + 2 accepted conversations); private safety/resource/tool/
poisoning remain 0/0 = not applicable, so those concerns are carried
exclusively by the (mandatory) base set.

## Poisoning / injection

`run_poisoning()` reuses the REAL boundaries production uses: the Packet 6A
lesson guard (two "good" ratings must never distill injected text), the
Packet 5B memory store (model-inferred candidates stay staged, never
searchable, never auto-promoted), and the offline classifier (injected text
must never reach an acting route). The 6B poisoning suite surfaced a gap in
the 6A injection guard ("forget your safety rules", destructive `rm -rf`
shapes), which was hardened in `voicebridge/learning.py`.

## Promotion gate

`PromotionGate.evaluate(candidate, baseline, required_sets=...)` compares the
two `score_run` outputs per category and per family:

- any safety-critical regression → `REJECT`;
- any non-safety or latency regression beyond tolerance → `REJECT`;
- any safety category untested or with error rows → `NEEDS_REVIEW`;
- any category missing candidate/baseline evidence → `NEEDS_REVIEW`;
- any `required_sets` split with zero candidate cases (e.g. `--set all` with an
  empty private set) → `NEEDS_REVIEW`, never APPROVE;
- only then → `APPROVE`.

**Baseline provenance is a trust boundary.** A baseline must carry
`baseline_metadata` (packet, mode, `generated_at`, `git_commit`, `runner`
settings, `units`, and — for a live baseline — the environment snapshot with
model digest, Modelfile hashes, Ollama version, and hardware). A missing or
malformed provenance, a baseline generated from a DIRTY tree, or a baseline
generated from the SAME git commit as the candidate is never an independent
release gate: the outcome is `NEEDS_REVIEW`, never APPROVE and never REJECT.
An independent release gate therefore requires a baseline generated from a
known-good PRIOR, CLEAN commit — which is a human/CI action, not something the
gate can fabricate.

The gate is a pure function. It never mutates state and never promotes a
model, adapter, preference, or skill — promotion is always an explicit
operator action. Latency is gated SEPARATELY at real scale: any latency key
present in both runs with p95 >= 1 ms (including the live warm-chat probe) is
gated with a ratio tolerance; sub-millisecond in-process classifier jitter is
measurement noise, not a gate.

## Measurement units

Ollama `/api/ps` reports model `size`/`size_vram` in BYTES. `resident_state`
preserves the raw bytes under `*_bytes` and reports correctly converted MiB
under `*_mib` (bytes / 1048576) — a byte value is never stored under a MiB
name. Every baseline records its `units` so a unit mismatch can never be
mistaken for a regression. The live report shows BOTH the model VRAM in MiB
and the TOTAL GPU usage (`nvidia-smi` used/total), so total GPU use is never
hidden behind model size. The resident 4B model (~3.03 GiB) is asserted to
stay under the 5 GiB model-VRAM ceiling, well within the plan's 6.5 GiB
fast-tier total-GPU gate.

## CLI

```bash
python scripts/eval_gate_6b.py --mode unit --set base \
    --save-baseline benchmarks/eval_baseline_6b.json   # unit baseline (provenanced)
python scripts/eval_gate_6b.py --mode unit --set all \
    --state-dir ~/.local/state/openclaw-voice-bridge \
    --baseline benchmarks/eval_baseline_6b.json        # unit gate vs baseline
python scripts/eval_gate_6b.py --mode live --set all \
    --state-dir ~/.local/state/openclaw-voice-bridge \
    --save-baseline benchmarks/eval_live_baseline_6b.json  # saved live baseline
python scripts/eval_gate_6b.py --mode live --set all \
    --state-dir ~/.local/state/openclaw-voice-bridge \
    --baseline benchmarks/eval_live_baseline_6b.json   # live gate vs baseline
```

`--set all` REQUIRES the private set to have at least one case: an empty
private set exits `NEEDS_REVIEW` (3) even without a baseline — an empty
real-user evidence set can never silently approve. `--save-baseline` writes
the score fields (backward-compatible with `PromotionGate`) plus immutable
`baseline_metadata` (git commit, tree-dirty flag, runner settings, environment
snapshot, units). The live probe uses the canonical runner contract
(`runner_contract.resolve`) so it can never trigger a context-size reload, and
it records total GPU usage alongside model VRAM. Reports list each set's case
count and family coverage, e.g. `set[private] 3 cases families={'quality': 3}
poisoning=0/0`.

Exit codes: 0 OK/APPROVE, 2 REJECT, 3 NEEDS_REVIEW, 4 degraded live, 5 failed.
Reports are written as private (`0600`) JSON in `--out`.

## Tests

`tests/test_packet6b.py` (47 tests): eval-set coverage/sanitization, task
outcomes (fields, rejection, redaction, restart, thread + cross-process
concurrency, private-set derivation incl. accepted-conversation backfill),
scoring separation, poisoning, offline-runner hermeticity (no network, no
user state), gate decisions (parity / safety regression / latency regression /
real-scale live_chat gating / untested / error rows / required-set evidence /
pure), **baseline provenance validation** (missing/malformed provenance,
missing live environment, dirty-tree baseline, same-commit non-independence),
VRAM unit correctness, bad-lesson removal + store-inert rollback, the webui
feedback → outcome durable flow, **coverage-policy tests** (empty private
quality requires review; base safety/poisoning mandatory; private 0/0
poisoning is not_applicable not a pass; safety regressions not hidden; no
automatic promotion), and CLI exit codes including `--set all` with an empty
private set and the honest NEEDS_REVIEW for same-code/dirty baselines.
`tests/test_packet4a.py` additionally asserts the `resident_state` byte→MiB
conversion and the 5 GiB model-VRAM ceiling.

## Evidence recorded (stricter closure)

- Private held-out set from the real user state dir: 3 genuine QUALITY cases
  (1 real correction + 2 accepted-conversational feedback rows), evaluated
  3/3, no private text in any report. Private coverage is `{'quality': 3}`;
  private safety/resource/tool coverage is **0** — those families are
  represented by the base set only (real private examples require real user
  tasks, a documented human-data prerequisite, never fabricated).
- Saved, committed baselines with full provenance:
  - `benchmarks/eval_baseline_6b.json` (unit): git `86d1b4a`, dirty tree,
    runner settings, units.
  - `benchmarks/eval_live_baseline_6b.json` (live): model digest
    `7bbd02b8…`, Modelfile SHA, Ollama 0.32.14, hardware, runner settings,
    total GPU (4457/8192 MiB), model VRAM 3030.9 MiB (< 5 GiB ceiling), and
    real warm-chat latency (`live_chat` p50 ~3.34 s / p95 ~3.42 s).
- Because both baselines were generated from a DIRTY tree (the packet code is
  not yet committed), the gate honestly returns **NEEDS_REVIEW** when compared
  against them — never APPROVE. An independent release gate becomes possible
  only after the packet is committed and a clean known-good baseline exists.
- Cross-process task-outcome writes: 2 processes × 150 rows = 300 rows, no
  loss or corruption.
- Dashboard/API flow: `POST /api/feedback` → `task_outcomes.jsonl`, verified
  durable across a store reload.

## Explicit limitations (accepted)

- **Independent release baseline is a prerequisite, not fabricated:** the 
  committed baselines are state RECORDS from a dirty tree; the gate returns
  NEEDS_REVIEW against them. A clean known-good-prior-commit baseline must be
  generated by a human/CI action after the packet is committed.
- **Private evidence is small and quality-only by design (explicit limitation,**
  **not fabricated):** the real private split is 3 genuine quality cases
  (correction_priority + short_chat). Private safety/resource/tool/poisoning
  are 0/0 and are reported as `not_applicable` to the private personalization
  split — those concerns are carried exclusively by the mandatory base set.
  Growing the private split to cover more personalization quality evidence
  requires real user task outcomes (a human-data prerequisite, never
  fabricated).
- Multi-user/cross-device private sets are untested: the private set is
  per-machine by design and cannot be fabricated.
- The full suite emits pre-existing warnings (numpy/sounddevice
  DeprecationWarnings, `os.fork` in a threaded test, [g02] warm-latency
  sample warning). They are unrelated to 6B; there are 0 failures and 0 skips.

## Rollback

Delete `voicebridge/evaluation/`, `scripts/eval_gate_6b.py`,
`tests/test_packet6b.py`, `docs/EVALUATION_6B.md`, and the committed
`benchmarks/eval_baseline_6b.json` / `benchmarks/eval_live_baseline_6b.json`
(revert commit `cf585bf`); remove the `TaskOutcomeStore` import +
instantiation from `voicebridge/orchestrator.py`; remove the outcome-recording
block from the `/api/feedback` handler; revert the extra injection patterns in
`voicebridge/learning.py` only if the 6A guard hardening is unwanted; restore
`voicebridge/benchmarking/harness.py::resident_state` to the byte-valued
`size_vram_mib`/`size_mib` fields only if the unit fix is unwanted.
Existing `task_outcomes.jsonl` is inert if the store is removed.