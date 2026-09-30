# Audit Technique: Multi-Document Project Plan Review

## File discovery one-liner

```bash
find <repo-root> -type f \( -iname "*.md" -o -iname "*.mdx" -o -iname "*.rst" \
  -o -iname "*.txt" -o -iname "README*" -o -iname "CLAUDE*" -o -iname "AGENTS*" \
  -o -iname "PLAN*" -o -iname "SPEC*" -o -iname "*.toml" -o -iname "*.yaml" \
  -o -iname "*.yml" -o -iname "*.json" \) | sort
```

This catches planning docs, configs, and schemas in one pass. Pipe to `sort` for deterministic ordering.

## Reading order protocol

1. AGENTS.md (or CLAUDE.md) — non-negotiable rules
2. README.md — scope, non-goals, authority statement
3. IMPLEMENTATION_ROADMAP.md — phase order, gates, immediate work queue
4. SLM_FROM_SCRATCH_PLAN.md — technical design
5. Companion docs (SKILL_ARCHITECTURE, COGNITIVE_SELF_IMPROVEMENT, etc.)
6. PLAN_AUDIT_*.md — findings

## Phase detection heuristics

| Pattern in text | Phase meaning |
|---|---|
| "Status: Candidate" + "no training or production activation" | R0 planning-only |
| "Current phase: R0 — repository and governance baseline" | Explicit R0 |
| No `.git` metadata in checkout | R0 blocker |
| "Immediate work queue" with numbered packets | Active R0 tasks |
| "Advancement is evidence-gated, not calendar-gated" | Gates > dates |
| "No X is activated by these documents alone" | Everything is candidate |

## Conflict resolution

If Document A says "X" and Document B says "Y":
1. Check the README's authority hierarchy
2. The higher-authority doc wins
3. Note the conflict and the resolution in the summary
4. Companion docs are non-normative unless the roadmap explicitly activates them

## Session example: Qubit-The-Big-G audit flow

- Found 13 doc files via `find` across `docs/` and root
- AGENTS.md established 18 non-negotiable rules
- README.md defined authority: AGENTS > ROADMAP > SLM_PLAN > companions > audit
- IMPLEMENTATION_ROADMAP.md defined R0–R9 phases with evidence gates
- PLAN_AUDIT_2026-08-24.md resolved 23 conflicts (A-01 through A-23)
- Current status: R0, blockers = missing git metadata, owner review pending
- Nothing authorized to download/train/install until R0–R2 gates pass
