# Packet 4D bake-off (GENERATED from docs/BAKEOFF_4D.json)

Generated: 2026-08-26T022013Z · production alias `qubit-brain4` untouched: **True** (served-sha 2377b5c6409f…)
Restore after deep-tier cycle: **True** in 4362.35 ms.

## Warm transitions (p50/p95 ms) + stability
| Alias | chat | classifier | errors | drift | >1s loads |
|---|---|---|---|---|---|
| qubit-brain4 | 383.155/444.1035 | 443.94500000000005/472.895 | 2 (typed chat-empty after the single bounde…) | 0 | 0 |
| qubit-cand-q4 | 373.745/497.304 | 433.44/447.792 | 2 (typed chat-empty after the single bounde…) | 0 | 0 |
| qubit-cand-q5 | 376.515/423.02299999999997 | 438.05999999999995/448.08 | 2 (typed chat-empty after the single bounde…) | 0 | 0 |

## Quality gates per alias
| Alias | safety verdict | planner ≥0.95 & 0 unsafe | personal-QA | first-audio ≤800ms |
|---|---|---|---|---|
| qubit-brain4 | pass (n=22, unsafe=0) | acc=1.0, unsafe_accept=1 → False | 10/10 → True | 472.2ms → True |
| qubit-cand-q4 | pass (n=22, unsafe=0) | acc=1.0, unsafe_accept=0 → True | 10/10 → True | 773.7ms → True |
| qubit-cand-q5 | pass (n=22, unsafe=0) | acc=1.0, unsafe_accept=0 → True | 10/10 → True | 434.4ms → True |

## Status
NOT APPLIED — VERIFIER ROUND 5: Q5 is currently the ONLY candidate passing all corrected candidate gates; Q4 must not be promoted. LIMITATION: Q5 passes the defined WARM-audio gate only — verifier observed cold-start first audio up to 8608 ms from a completely unloaded state; a strict ≤800 ms cold requirement would fail Q5 (open user decision). Production alias unchanged; promotion requires explicit user approval.

Rollback commands are recorded in the JSON manifest; production was
never modified (fingerprint-verified before/after).
