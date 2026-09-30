# Authority Hierarchy Template

Use this template when documenting or auditing a multi-document plan repository.
Fill in each row as you encounter the relevant document. The **Authority** column
determines reading order and conflict resolution.

| Read Order | Document file | Role | Authority level | Status (authoritative / companion / research) | Conflicts observed | Resolved by |
|---|---|---|---|---|---|---|
| 1 | AGENTS.md or CLAUDE.md | Repo-wide rules | Highest | authoritative | — | — |
| 2 | README.md | Scope & authority statement | High | authoritative | — | — |
| 3 | IMPLEMENTATION_ROADMAP.md | Phase order, gates, work queue | High | authoritative | — | — |
| 4 | SLM_FROM_SCRATCH_PLAN.md | Foundation-model design | Medium-High | authoritative (if named) | — | — |
| 5 | GENERATED_CODE_GUARDRAILS.md | Code inspection pipeline | Medium | authoritative (if named) | — | — |
| 6 | [Companion 1] | Research backlog | Low | companion | See column 6 | Hierarchy rule |
| 7 | [Companion 2] | Research backlog | Low | companion | — | — |
| 8 | PLAN_AUDIT_*.md | Audit findings | Meta | companion | — | — |

## Conflict resolution rules

1. Higher authority level wins on conflict.
2. A companion plan cannot activate dependencies, models, training, or integration.
3. If two docs disagree, note both statements and the authority-based resolution.
4. The audit document is meta — its findings are proposals, not authority.

## Example: Qubit-The-Big-G

| Read Order | Document | Role | Authority | Status |
|---|---|---|---|---|
| 1 | AGENTS.md | 18 repo rules | Highest | authoritative |
| 2 | README.md | Scope + authority order | High | authoritative |
| 3 | IMPLEMENTATION_ROADMAP.md | R0–R9 phases, gates | High | authoritative |
| 4 | SLM_FROM_SCRATCH_PLAN.md | Model design | Medium-High | authoritative |
| 5 | GENERATED_CODE_GUARDRAILS.md | Code quarantine | Medium | authoritative |
| 6 | PC_NATIVE_CONTINUAL_SLM_PLAN.md | PC-native agent | Low | companion |
| 7 | PERSONAL_MULTIMODAL_MODEL_PLAN.md | Teacher/eval lab | Low | companion |
| 8 | SKILL_ARCHITECTURE_ENHANCEMENT.md | Skill design | Low | companion |
| 9 | COGNITIVE_SELF_IMPROVEMENT_ARCHITECTURE.md | Self-improvement | Low | companion |
| 10 | PLAN_AUDIT_2026-08-24.md | 23 resolved conflicts | Meta | companion |

**Conflict resolved:** A-01 — multiple documents claimed plan authority → README now declares one hierarchy.
