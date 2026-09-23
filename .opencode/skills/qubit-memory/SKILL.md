---
name: qubit-memory
description: Use the existing Graft project graph for durable Qubit architecture, decisions, blockers, and verification state.
compatibility: opencode
---

# Qubit project memory

Authoritative documentation context is the fresh docs set in `docs/`:
`docs/README.md` (index + review checklist), `docs/STATUS.md` (current truth),
`docs/PLAN.md` (milestones), `docs/DECISIONS.md` (owner rulings in force), and
`docs/ARCHITECTURE.md`. The pre-reset docs are preserved under
`docs/archive/2026-09-22-pre-reset/` (definition of done, amended by
`docs/DECISIONS.md`). Frozen/legal records are under `docs/archive/2026-09-21/`;
do not edit them, and do not edit `docs/differential/INTERFACE.md`.
Graft (v0.12.0) does not index markdown, so doc authority is carried by
`AGENTS.md` and `opencode.json`, not the graph.

Use the existing Graft skill and Graft MCP tools. Read `.claude/skills/graft/SKILL.md`
for the exact command and tool names; do not invent tool names.

Before work:

- Search the graph for the relevant module, route, capability, test, and known blocker.
- Use the graph's file and line spans to narrow source reads.

During work:

- Record only durable architecture decisions, discovered defects, ownership, and verification results.
- Do not store ordinary conversation, repeated tool output, secrets, or full reports.

Before compaction or handoff:

- Preserve the current objective, changed files, unresolved failures, operator gates, and next action.

Never dump the entire graph into the model context.
