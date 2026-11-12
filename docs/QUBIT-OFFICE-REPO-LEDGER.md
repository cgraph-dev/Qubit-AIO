# Qubit Office — repository dependency ledger

**Status:** Phase 0 inventory. No code is imported by this file. Verify commit/license before use.
A repository appearing here is not an approval to copy or bundle it.

## 1. Local references

| Repo | Local path | Revision / evidence | License | Take | Avoid |
|---|---|---|---|---|---|
| agent-monitor | `/home/trick/Projects/Repos/refs/agent-monitor` | local copy; git commit unknown | no LICENSE file found | agent/session cards, meeting UI, session grouping | process visibility is not durable state or authorization |
| agent-quest | `/home/trick/Projects/Repos/refs/agent-quest` | local copy; git commit unknown | no LICENSE file found | session state manager, subagent lifecycle, provider adapter shape | do not infer runtime/persistence it does not prove |
| ai-town | `/home/trick/Projects/Repos/refs/ai-town` | local copy; git commit unknown | no LICENSE file found | social simulation/map inspiration only | do not copy asset/conversation semantics without verification |
| pixel-agents | `/home/trick/Projects/Repos/refs/pixel-agents` | local copy; git commit unknown | no LICENSE file found | lead/teammate identity, session routing, agent state | do not assume multi-agent persistence/security |
| starnet | `/home/trick/Projects/Repos/refs/starnet` | local copy; git commit unknown | no LICENSE file found | panel/business/agent-commerce concepts | no code reuse without written permission/license |
| agents-office | `/tmp/office-refs/agents-office` | `3773c9b6aed6a1d2c3ab2765d113c644261c2019` (2026-10-08) | **PolyForm Noncommercial 1.0.0 + Sahni.ai terms; no renaming/rebranding; not Part of Another Product** | roster/config/skills/MCP selector concepts only | **must not copy, bundle, rename or adapt into Qubit** without written permission |
| claw-empire | `/tmp/office-refs/claw-empire` | `66a24ea7df2435ef897c48c147deb7ec572c01c2` (2026-03-17) | Apache-2.0 LICENSE file present; verify notices | worktree isolation, departments, skills library, encrypted token patterns, kill controls | do not copy database/model assumptions unverified |
| SkyOffice | `/tmp/office-refs/SkyOffice` | `3f66b8bffad889ee9fc2340f9bcad28146299f47` (2025-12-19) | MIT LICENSE file present | room/player/chat commands, bounded room chat | do not use room chat as canonical conversation store |
| habblaud | `/tmp/office-refs/habblaud` | `faa1a012b5fbf632c66dc6801c5383e53a542406` (1.0.3) | MIT LICENSE file present | room rendering, seats, office UI, character state | do not break current behavior; paid art remains local |
| pixelfx | `/tmp/office-refs/pixelfx` | `044e12a45dc9cc312cec50ee606dfc98afe5adc2` (0.3.1) | MIT LICENSE file present | weather/ambient/event FX | verify performance/asset licensing before shipping |

## 2. External reference repos (from master plan §22)

Exact commits and licenses are **not yet locally verified**. `plan-recorded` means the master plan
records that license; it is not approval to copy. No repo code is imported by this ledger.

| Repo | Source / license | Take | Avoid | MVP? |
|---|---|---|---|---|
| DAACS OS | `omnify2026/DAACS`; licence not finalized — concepts only | goal → PM plan → execute → review → verify → approve; approval queue; dynamic sequencer | no code/assets until licence finalized | Concept: approval sequence |
| Canopy | `kinnerkarmanish/mak`; MIT (plan-recorded) | workspace protocol, 5-layer org, workflow DAG, cron/heartbeats, budgets/governance, cost console | 330-agent roster and full org complexity | Concept: workflow DAG + budgets |
| OctoOffice | `Chepko932/OctoOffice`; Apache-2.0 | 7 departments, mission control, YAML workflows, Tiled/Pixi office, SQLite, terminal, meetings | copy whole stack; verify license/notices | Candidate: room/department/mission control |
| Blank Collar | `The-Blank-Collar/blankcollar-agentic-os`; MIT | goal-first orchestration, department abstraction, allow/approve/deny policy, memory, simulation, observability, feedback | Stripe/channels before MVP; full simulation | Candidate: policy engine + memory |
| Agent Office | `AgentSystemLabs/agent-office`; MIT | floor per project, workers at desks, terminal, voice, GitHub issues/PRs, worktrees, agents managing agents, `/lite` | full product copy; voice/GitHub before one department | Candidate: worktree/agent-delegation patterns |
| Outworked | public; licence verify | desktop office, assets, scheduled tasks, Slack/iMessage, browser, MCP, approvals, cost dashboard | copy until license verified | Concept |
| BagIdea Office | public; licence verify | wallpaper office, social simulation, self-extending plugins/proposals, memory/skills, approval center | plugin self-extension before security gates | Concept |
| PixelDesk | public/open-core; verify | humans + agents as colleagues, shared brain, cost transparency, PWA, proximity | “shared brain” without memory authority | Concept |
| Veska / AI Workforce | Apache-2.0 | AI-native ERP/CRM: 52 tools, 12 departments, CRM/support/finance/HR/ops | entire ERP in MVP | Later: business modules |
| Oryh | Apache-2.0 | agent-first ERP/CRM, skills-not-SDK, audit, durable business records | broad ERP before gateway | Later: audit/business records |
| AgenticCRM | MIT | WhatsApp CRM, tickets/SLA, invoices/payments, 24/7 keepalive, multi-agent workflows | live WhatsApp/payments before approvals | Later: support/CRM reference |
| Autonomous Business OS | MIT | lead → qualify → onboard → deliver → finance → knowledge; HITL; integrations | all stages at once | Later: business workflow map |
| OOSDK | MIT | ontology/policy-as-code, deterministic decisions, caged LLM advisors, order-to-cash | ontology scope explosion | Concept: policy-as-code |
| FusionClaw | MIT | business OS over MCP, CRM/ops/finance, scoped keys, confirmation gates, audit | full business OS in MVP | Later: scoped keys/confirmations |
| RevealUI | MIT | auth/content/offers/payments/agents runtime, one permission model | payments/auth before identity foundation | Later: permission model |
| Agent Company AI | MIT | 9 roles, Stripe/Gumroad, invoices, spend caps, autonomous CEO loop | autonomous CEO/spend in MVP | Later: spend-cap/role patterns |
| JSM HQ | MIT | lead/worker office, per-agent repo clone + Docker sandbox, MCP coordination, PR completion | Docker for every agent before one lane | Candidate: per-agent sandbox/worktree |
| HermitShell | MIT | per-agent Docker isolation, Telegram, scheduler, dashboard | per-agent isolation before MVP | Later: isolation patterns |

## 2.1 MVP repo shortlist

Only these directly inform MVP v1:

1. **Habblaud** — room/office renderer, seats, character state.
2. **SkyOffice** — room/player/chat commands (MIT).
3. **pixelfx** — loading/status FX (MIT).
4. **claw-empire** — worktree isolation, departments, encrypted token patterns (Apache-2.0; verify notices).
5. **agent-monitor** — session/meeting UI concepts only (no LICENSE file).
6. **agents-office** — roster/skills/MCP selector concepts only (PolyForm; no copy/bundle).
7. **Agent Office / JSM HQ** — per-agent worktree/sandbox concepts (MIT; verify commits).

Everything else is a **later business/ERP/CRM reference**, not MVP code.

## 3. Rules

1. No repository code enters Qubit without a row here: URL, commit, license, files, local changes.
2. `no LICENSE file` = **do not copy code**; concepts/patterns only.
3. PolyForm/noncommercial/no-bundling repositories are **concepts only**, never bundled.
4. Paid art and generator binaries stay local and outside the public tree.
5. Record rejected code and rejected patterns; “reviewed” is not an implementation dependency.
6. Add the exact commit and security review result before porting.
7. Attribution and required notices travel with any permitted adaptation.

## 4. Status labels

- **concept** — read for ideas; no code.
- **candidate** — license permits reuse; security review not done.
- **approved** — commit/license/security reviewed; explicit owner sign-off.
- **imported** — code present with provenance and tests.
- **rejected** — do not use.

Current status: Habblaud/pixelfx are existing vendored components; claw-empire/SkyOffice are
candidates pending review; agents-office and all no-license repos are concept-only.
