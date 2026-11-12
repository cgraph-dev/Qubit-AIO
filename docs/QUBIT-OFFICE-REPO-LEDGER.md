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

## 2. External repos already recorded in the master plan

These were reviewed conceptually in the plan; no code is imported and no commit is verified here.
Each needs a commit/license/security row before reuse:

DAACS, Canopy, OctoOffice, Blank Collar, Agent Office, Outworked, BagIdea, PixelDesk, Veska /
AI Workforce, Oryh, AgenticCRM, Autonomous Business OS, OOSDK, FusionClaw, RevealUI,
Agent Company AI, JSM HQ, HermitShell.

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
