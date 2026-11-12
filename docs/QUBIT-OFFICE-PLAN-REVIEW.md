# Qubit Office — plan review and MVP simplification

**Status:** review of the plan set. Not implementation evidence, not `VERIFIED`.
**Latest audit:** `QUBIT-OFFICE-PLAN-AUDIT-V10.md` and `QUBIT-OFFICE-COMPLETE-PLAN.md` v11 supersede
this review for implementation order and exact acceptance commands. Risks listed here are retained.
**Owner decision (later):** the MVP recommendation in §4–§7 is **rejected**. Full scope is committed
from the start; the bugs/misconfigurations and risk findings in §2–§3 remain valid. Where this file
says “defer”, read it as “risk to sequence”, not “remove feature”.

## 1. Verdict

The plan set has good ideas but is **too large, too repetitive, and too infrastructure-heavy for
the first usable product**. It reads like a platform company's two-year architecture, while the
first goal is much simpler:

> One department room with 3–5 real agents, real sessions, real chat, memory, capabilities and one
> business workflow.

The repos and assets already give us most of the rendering, office, character, room and agent UI
patterns. The plan should reuse those and stop designing a second operating system.

## 2. Measured plan set

- `QUBIT-OFFICE-MASTER-PLAN.md`: 1,500 lines
- `QUBIT-OFFICE-COMPLETE-PLAN.md`: 407 lines
- `QUBIT-OFFICE-DEPARTMENTS-AND-INTERACTIONS.md`: 559 lines
- `QUBIT-OFFICE-RESEARCH-GAPS.md`: 218 lines
- Additional ledgers/contracts/threat/phase-0: ~604 lines
- Total: ~3,288 lines across 9 plan documents.

That is too much authority for a product with one business, one department and no agent execution.

## 3. Bugs and misconfigurations found

1. **Duplicate phase orders.** Master plan has v3, v4, v6 and v7; the complete plan says v8; Phase 0
   says v7. A single canonical order is required.
2. **“Full world from day one” vs deferred world.** The plan promises a full static world, then
   phases defer the 100-floor renderer/outside world. Distinguish **full data** from **rendered
   world** explicitly.
3. **283 agent records vs 3–5 active agents.** Live/projection occupancy implies a workforce that
   has no sessions, chat or execution. Treat the 283 as seed/demo, not operational.
4. **Temporal before one agent lane.** Temporal/Postgres/prototype before a working session is
   overengineering for a local single-user first slice. Use the existing SQLite Business OS +
   a simple run/task/checkpoint/lease model first; adopt Temporal when multi-process/HA needs it.
5. **SSO/MFA/multi-tenant/multi-entity/DR before one department.** For a personal/local build,
   start with local owner auth and one tenant. Keep the fields, defer the infrastructure.
6. **Huge domain contract before UI.** Thirty-plus entities and fine-grained fields delay the
   first slice. Start with the 12 entities the lane actually needs; add the rest later.
7. **Asset ledger is only a summary.** The plan claims an asset ledger but the per-file ledger
   generator is still pending. Do not imply the ledger is complete.
8. **Repo reuse ambiguity.** Several reference repos have no LICENSE file; agents-office is
   PolyForm Noncommercial with no-bundling/no-renaming. “Copy from repos” must mean **concepts or
   permitted code only**, not unlicensed copying.
9. **Perplexity contradiction.** Perplexity remains a product research button although the owner
   said it was for planning. Remove/defer it from War Room; use Qubit's general capabilities.
10. **Etsy overfocus.** Etsy is a valid first pilot, but the plan reads as an Etsy product. Keep it
    as the first workflow, not the architecture.
11. **“Plan-only” vs live claims.** Some sections say nothing is implemented while others record
    live deployments. Separate design status from live evidence.
12. **Research requirements are treated as immediate.** The 30+ must-haves are valid eventually,
    but deploying all before a single department works is scope paralysis.
13. **Too many parallel authorities.** Master plan, complete plan, design doc, research doc and
    Phase 0 all sound canonical. Designate one execution plan and make the rest references.
14. **No “stop” rule.** The plan has many later phases but no hard rule that only one department is
    built until it passes recovery/chat/memory tests.

## 4. Simplified MVP v1 (canonical first slice)

**One department. One floor. 3–5 agents. One business workflow. No tower, no outside world, no
Character Studio, no mature simulation.**

### MVP-0 — Reuse inventory

- Use the current office image as the department template.
- Use Habblaud for room rendering/seats/character state.
- Use LimeZu assets already on disk for floors/furniture/portraits.
- Use pixelfx for loading/status effects only.
- Reuse the existing Business OS SQLite and Qubit gateway/state store.
- Check licenses; no unlicensed repo code.

### MVP-1 — One department and real agents

- Department, 4–6 rooms, 3–5 agents, real seats.
- Existing office renderer; no 100-floor expansion.
- Agent config: model/provider, tools, skills, MCP, permissions, budget, memory scope.
- Agent session with owner chat.
- Simple task queue and artifact output.
- One checkpoint/restart proof.

### MVP-2 — Capability gateway and chat

- Route existing Qubit tools/toolsets/MCP/skills/browser/files/messaging through one gateway.
- Deny-by-default grants; no wildcard.
- Owner↔agent chat; agent↔agent chat in one room.
- Approvals for sensitive actions; exact payload/hash; no chat-as-approval.
- Stop/revoke interrupts active work.

### MVP-3 — Memory

- One department Obsidian folder and one agent note set.
- One OpenViking scope through an adapter.
- Session summaries and artifacts linked, not duplicated.
- No global memory pool; no Brain model.

### MVP-4 — One real workflow

- Etsy draft listing or another single low-risk workflow.
- Fake/dry-run first, then real OAuth when credentials exist.
- Draft → approval → external call → receipt → artifact → ledger/reconciliation.
- Unknown outcome is visible, not “completed”.

### MVP-5 — Prove it

- Restart after side effect; no duplicate.
- Revoke mid-run; work stops and checkpoints.
- Agent handoff with artifacts.
- Cross-room/permission denial.
- Backup/restore one department.
- Then, and only then, clone the department.

## 5. Deferred until MVP passes

- Temporal/Postgres workflow engine (use existing SQLite model first; adopt Temporal when needed).
- SSO/MFA/IdP, multi-tenant isolation, multi-entity books.
- 100-floor renderer, outside world, Godot.
- Character Studio merger.
- Mature interactions/social simulation.
- Social/CRM/finance/contracts systems beyond the first workflow.
- Broad autonomy, 283 active agents, deployment rings, SBOM/DR drills.
- Full research gap list as mandatory blockers.

## 6. What to reuse from repos/assets

All 18 external repos from master plan §22 are now mapped in `QUBIT-OFFICE-REPO-LEDGER.md` §2. The
MVP uses only a small shortlist; the ERP/CRM/business repos are later references, not MVP code.


- **Habblaud:** room rendering, seats, office UI, character state.
- **LimeZu:** floors, furniture, portraits, UI; local-only paid art.
- **pixelfx:** loading/status/FX.
- **agent-monitor:** session cards/meeting UI patterns.
- **agents-office:** roster/skills/MCP selector concepts only (license forbids bundling).
- **claw-empire:** worktree isolation, departments, encrypted-token patterns (verify license).
- **SkyOffice:** room chat/map commands (MIT).
- **pixel-agents:** agent/lead/teammate state patterns (license verify).
- **Agent Quest/StarNet/ai-town:** UI/business/simulation concepts only until license verified.

## 7. Immediate next 10 tasks

1. Freeze MVP v1 as the only execution order.
2. Mark old phase lists historical.
3. Generate the per-file asset ledger.
4. Verify repo licenses before any code copy.
5. Build one department with 3–5 agents.
6. Add real agent session + owner chat.
7. Wire one existing Qubit tool through a deny-by-default gateway.
8. Add approval + restart/checkpoint proof.
9. Add one Obsidian folder and one OpenViking scope.
10. Run one dry-run business workflow.

**Rule:** no tower, character studio, outside world, mature interactions or new departments until
MVP-5 passes.
