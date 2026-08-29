# Packet 6A — Learned preferences: provenance, correction, and removal

Implemented in `voicebridge/learning.py` (the `LearningLedger`) with an
authenticated dashboard surface (`GET /api/lessons`,
`POST /api/lessons/remove`, `POST /api/lessons/promote`) and the small
"learned" panel in `frontend/index.html`. No model weights are trained and no
routing is changed automatically by this packet.

## The three separated signals

| Kind | Source | Lifecycle |
|---|---|---|
| `explicit_user` | user correction (`feedback=correction`) or `add_preference()` | Highest priority, confidence 1.0, no TTL, never silently removed by outcomes |
| `observed_outcome` | repeated consistent `good` feedback (>= 2 on the same normalized pattern) | Promoted only after 2+ goods; confidence, decay, 90-day TTL, removable by user or by sustained `bad` feedback |
| `model_inference` | `stage_model_inference()` | Always staged as a review item, never injected into the prompt, never auto-promoted; promotion requires an explicit user call |

## Store (`state_dir/learning.jsonl`, fsynced)

- **Evidence rows** are append-only observations: `{"ev", "ts", "user",
  "answer", "feedback", "corrected", "kind"}`.
- **Lesson rows** carry `lesson_id`, `key` (normalized pattern),
  `pref`/`corrected`/`text`, `kind`, `source`, `evidence_ids`, `confidence`,
  `created_at`/`updated_at`/`expires_at`, `supersedes`, `tombstoned`,
  `review_needed`, `conflict_with`, and an append-only `history` audit.
- Lesson updates rewrite the file atomically (temp + `os.replace`), keeping
  exactly one live row per lesson; evidence stays append-only.

## Rules enforced

1. **Corrections override stale lessons.** An explicit correction supersedes
   (tombstones) any conflicting lesson for the same pattern and is rendered
   first in the prompt.
2. **One good interaction cannot rewrite behavior.** Promotion needs
   `MIN_PROMOTION_LIKES = 2` consistent goods; a third/4th like only reinforces
   confidence/evidence, never duplicates.
3. **Contradictory evidence creates a review item**, never a silent rewrite.
   A liked answer that conflicts with an existing lesson (or an explicit
   correction) is stored as `review_needed`; the user resolves it via
   remove/keep in the panel.
4. **Confidence, decay, TTL.** Observed-outcome lessons start at 0.6
   confidence (+0.05 per extra like, cap 0.9), decay with a 30-day half-life,
   expire after 90 days, and are excluded from the prompt below the confidence
   floor or after expiry. Explicit lessons do not decay.
5. **Explicit removal.** `remove(lesson_id)` tombstones the lesson and keeps
   the audit history; unrelated lessons and evidence are untouched.
6. **Prompt injection cannot become a preference.** Instruction-escalation and
   secret-shaped content is refused at the write boundary and logged as
   `learning.injection_blocked`.
7. **Audit explanations come from stored provenance.** `explain(lesson_id)`
   returns only stored fields (kind, source, evidence ids/count, confidence,
   timestamps, history); the dashboard renders them directly — the model never
   invents a "why".
8. **Model inference is never auto-promoted** and never appears in the prompt
   until the user explicitly promotes it.

## API

- `GET /api/lessons` → `{lessons: [active with provenance], reviews: [...]}`
  (auth: header token / loopback).
- `POST /api/lessons/remove` `{lesson_id}` → tombstone + audit.
- `POST /api/lessons/promote` `{lesson_id}` → explicit user promotion of a
  review/staged item.

## Tests

`tests/test_packet6a.py` (28 tests) plus the existing ledger suites
(`tests/test_steal_list.py`, `tests/test_steal_list_strict.py`,
`tests/test_stress.py`). No GPU/network/live services required.

## Rollback

Revert `voicebridge/learning.py`, `voicebridge/webui.py` and
`frontend/index.html`; delete `tests/test_packet6a.py`. Existing `learning.jsonl`
rows remain readable (old evidence rows are valid under the new schema).