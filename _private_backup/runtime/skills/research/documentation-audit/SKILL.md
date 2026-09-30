---
name: documentation-audit
description: Systematically review multi-document project plan/specification repositories to determine phase status, authority hierarchy, blockers, and next steps without activating implementation.
category: research
---

# Documentation Audit & Project Plan Review

**Purpose:** Systematically review a multi-document project plan or specification repository to determine current phase status, authority hierarchy, blockers, and next steps — without activating any implementation.

**When to use:** A repository has multiple Markdown/Asciidoc/TXT planning docs with stated authority layers (e.g., AGENTS.md > ROADMAP > technical spec > companion plans). The goal is to *audit*, not to implement.

## 1. Discovery pass

Locate all documentation files in one shot using `find` with multiple extensions:

```bash
find <repo-root> -type f \( -iname "*.md" -o -iname "*.mdx" -o -iname "*.rst" \
  -o -iname "*.txt" -o -iname "README*" -o -iname "CLAUDE*" -o -iname "AGENTS*" \
  -o -iname "PLAN*" -o -iname "SPEC*" -o -iname "*.toml" -o -iname "*.yaml" \
  -o -iname "*.yml" -o -iname "*.json" \) | sort
```

> Avoid `cat`/`head` — use `read_file` for content. Avoid `grep`/`rg` — use `search_files`.

## 2. Establish authority hierarchy (first read)

Every planning repo declares an authority order. Read in that order:

1. **AGENTS.md / CLAUDE.md** — non-negotiable repo rules
2. **README.md / PROJECT.md** — overview, scope, plan-authority order
3. **IMPLEMENTATION_ROADMAP.md / ROADMAP.md** — phase order, gates, stop conditions
4. **Technical specification** (e.g., SLM_FROM_SCRATCH_PLAN.md) — design details
5. **Companion / research backlogs** — non-normative unless activated
6. **Audit / review document** — findings & resolved conflicts

> If documents conflict: higher in the list wins. A companion plan cannot activate dependencies, models, training, or production integration on its own.

## 3. Phase status detection

Look for these signals to determine what phase the project is in:

| Signal | Meaning |
|--------|---------|
| "Status: Candidate" / "no training or production activation" | Planning-only, R0 |
| "Current phase: R0 — repository and governance baseline" | Explicit phase |
| Version-control metadata missing or "no .git" | R0 blocker (rollback cannot be demonstrated) |
| "Immediate work queue" with numbered packets | Active R0 task list |
| References to "gates" / "stop conditions" / "acceptance tests" | Evidence-gated, not calendar-gated |
| "No model training or production integration is activated by these documents alone" | Everything is candidate until owner approval |

**Quick test:** Can the project answer, for every active behavior and artifact: (a) provenance, (b) exact data/model/code/config that produced it, (c) independent tests that accepted it, (d) broker permissions, (e) rollback path, (f) whether it improved measured owner tasks? If not — the project is pre-completion.

## 4. Structured summary format

Produce a summary with these sections:

### Document inventory
A 3-column table: File → Role → Status (authoritative / companion / research).

### Authority hierarchy
Quote the declared authority order verbatim. Note any conflicts and which document controls.

### Phase & blockers
State the current phase. List explicit blockers (e.g., "missing git metadata," "owner review pending").

### Key decisions fixed
Extract numbered decisions (e.g., "S0-a 30M–50M; S0-b 80M–150M; 300M is S1").

### Immediate work queue
List the 3–8 next packets in order. Note which gate each must pass.

### Resource policy
Record hardware assumptions, VRAM limits, and any "do not treat as local default" caveats.

### Non-activation caveat
End every summary with: **Nothing is authorized to download models, install dependencies, start services, or train until the relevant gates pass owner review.**

## 5. Pitfall checklist

- **Authority drift:** A companion doc may claim equal authority to the roadmap. Flag and defer to the hierarchy.
- **Calendar-as-gate:** Phrases like "Days 1–14" are estimates, not phase advancement. Only evidence gates advance phases.
- **Model-card inflation:** `4.5B effective` ≠ `fits 8 GB VRAM`. Record both numbers.
- **Implicit dependency:** A plan saying "use Ollama" does not mean Ollama is on the hot path or approved for production.
- **Self-referential audit:** The audit document references itself in hashes. Exclude from hash verification.

## 6. References

- `references/audit-technique.md` — session-specific workflow: file discovery, authority-ordered reading, phase detection heuristics
- `references/authority-hierarchy-template.md` — reusable table template for documenting plan authority
