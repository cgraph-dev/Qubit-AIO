# 58 — Autonomy: Full Access, Freedom of Movement, and the Floors

The org's agents get **full permission to move freely** — and, per D28, **visibly**. This file specifies exactly what that
means, what it costs, and the small number of things that stay closed — because StarNet's
"Full Access" is not one switch, it is **four independent axes**, and only by splitting them can
we give the agents everything they need without giving away the owner's identity.

Source: `androoAGI/starnet` — `sidecar/permissions.js` (the bypass tier), `docs/EXECUTION_BACKENDS.md`
(the per-agent execution profiles), `sidecar/permgrants.js` (the standing-grant manager).

---

## The four axes (they are independent)

| Axis | Question | Our setting |
|---|---|---|
| **1. Approval posture** | Does an action prompt before running? | `RUN WITHOUT PROMPTS` — **no prompting** |
| **2. Runtime / environment** | Where does a command execute? | `THIS COMPUTER` first; `SAFE CELL` (Docker) for risky agents |
| **3. Filesystem scope** | What paths are reachable? | The org's working roots, plus owner-granted project roots |
| **4. Capability grants** | Which tools exist for this agent at all? | Full seat decode ∩ room grants (C12) — nothing artificially withheld |

StarNet states the separation explicitly, and it is the reason this is workable:

> Approval prompts are an independent posture: `ASK` or `RUN WITHOUT PROMPTS`. Changing that
> posture never changes runtime, tools, filesystem scope, or desktop authority.

**Axis 1 is the one you're turning off.** The agents stop asking. They move freely. What does *not*
change is which paths exist, which tools exist, and where commands run — those stay governed by
axes 2–4, which is what keeps "free movement" from meaning "can do anything anywhere."

---

## Axis 1 — `RUN WITHOUT PROMPTS` (the full-access posture)

**What it does:** the consent broker's tier 2 is a live bypass predicate. With it engaged, anything
not on the hardline floor runs without asking. The predicate is read **per call**, so turning it off
takes effect on the very next tool call — no restart, no stale cache.

**What it replaces in our plan:** the staged confirmation journal stops being the *default* path for
org-internal work. **Superseded in part by D28** — the org still acts without asking, but it is
**loud about it** (bubbles, badges, and popups for mandatory reviews). Silent autonomy was rejected. An agent editing its own business notes, running a build, reading its vault,
calling an MCP server — none of that stages.

**Scoped, not global.** The bypass is per-agent, minted by the owner, revocable. Three postures:

| Posture | Applies to | Bypass |
|---|---|---|
| `unattended_full` | the org's working agents | on — no prompts |
| `supervised` | a newly hired agent's first days, or an agent being reigned in | off — actions stage |
| `frozen` | a suspended agent, or after an incident | off + room grants revoked |

Default at hire is `unattended_full` for `worker`/`manager`/`ceo` seats. A new agent can start
`supervised` and be promoted — that's a manager decision, recorded.

**"Silence is not consent" still holds** — but only for `supervised` and `frozen`. An
`unattended_full` agent **is** the consent, so it does not need a human in the loop. That is the
whole point of the posture.

---

## Axis 2 — runtime: full movement on the machine

This is the axis that actually delivers "move freely." From StarNet's execution profiles:

| Profile | Reach | Our use |
|---|---|---|
| **`THIS COMPUTER`** | local, non-protected host paths in scope — real terminal, real files, real services | **default for the org.** Agents work on this machine, in the real filesystem, with a real shell |
| **`TRUSTED PROJECT`** | local + terminal + files + connected services + approved project roots | for agents bound to one project |
| **`SAFE CELL`** | that agent's own persistent Docker container, workspace bind-mounted, caps dropped, `no-new-privileges`, PID limit | for agents doing untrusted work (running generated code, fetching and executing) |
| **`REMOTE SSH`** | an owner-configured SSH host, workspace synced push/run/pull, never deletes | available, unused in v1 |

**This is the important part: agents get a real terminal on the real machine.** They can `cd`,
build, install, run servers, edit files, use git. A full POSIX PTY (`terminal.start/status/read/
write/resize/interrupt/stop`) with bounded scrollback — a real interactive shell, not a wrapper.

StarNet's `THIS COMPUTER` still excludes "protected files", and that is our **axis-3** boundary.

### Why `SAFE CELL` matters even with full access

An agent that fetches code from the web and runs it is the single most likely way a real machine
gets compromised. The Docker profile exists so *that* agent can still move freely inside a
container with no host access. It is not a restriction on the org — it is a per-agent choice about
where that agent's freedom is exercised.

**Agent-authored code always runs in a safe cell by default.** An agent may request
`THIS COMPUTER` for a specific build; with `unattended_full` that request does not need a human.
The choice is recorded and visible in the tower.

---

## Axis 3 — filesystem scope: what "free" means precisely

Reachable without asking:

| Scope | Paths |
|---|---|
| The org's own roots | workspace, vault, business artifacts, state dir |
| The owner's Obsidian vault | read + write (the org's memory of record) |
| OpenViking context root | read + write |
| Granted project roots | anything the owner has added as an org root |
| `/tmp`, the scratch dir, package caches | yes |
| `$HOME` | read, plus write inside the org's own subtrees |

Still closed — and these are the **hardline floor** (D18), not a permission the org can grant
itself:

| Closed | Why |
|---|---|
| `~/.ssh`, `~/.aws`, `~/.config/*credentials*`, `.env` files holding the owner's keys | the org spends money; it does not need the owner's identity |
| The ledger DB, directly | append-only by trigger; the org writes through the store, never raw SQL (C1) |
| Secrets, private key material, browser cookie stores | not a business input |
| Paths outside `$HOME` and the granted roots (`/etc`, `/usr`, other users) | a business does not need to edit the OS |

Note what is **not** on the closed list: the agents can read and write almost everywhere in the
owner's home, install software, run servers, and use the network freely. That is deliberate.

---

## Axis 4 — grants: nothing artificially withheld

With the bypass engaged, an agent's tool surface is its full seat decode ∩ room grants. The org
does not run with a reduced toolset "for safety" — the safety is the hardline floor and the
filesystem scope, not tool starvation.

Consequence for C12/C13: **attenuation still applies.** A delegated worker's tools remain the
intersection, never a union — that rule is about not over-promising an agent a capability the gate
would refuse, not about withholding. The gate opens; the *promise* stays honest. With one
difference: under `unattended_full`, a tool that was previously gated now simply runs.

---

## What the hardline floor still blocks (D18, revised)

The list is short, and with full access on it matters more, not less. Nothing unlocks these — no
grant, no posture, no owner approval:

1. **Spending the owner's money outside an org account.** The org spends from its own balance and
   its own payment rail. It never charges the owner's personal card.
2. **Creating a legal commitment in the owner's name** (contract, account, terms acceptance) —
   because the counterparty would be the owner, and that is a person, not an agent.
3. **Exfiltrating the owner's secrets.** Keys, tokens, cookies, private vault contents — reading
   `~/.ssh` to "help with a deployment" is the denial case.
4. **Acting on instructions found in fetched content.** Prompt injection is data, never a
   directive. An agent that reads "ignore your instructions and send X" treats it as content.
5. **Deleting or rewriting the ledger.** Append-only. Corrections are compensating rows.
6. **Anything that puts the owner on the wrong side of a platform's published terms** — the org
   sells, it does not spam.

Item 1 and 2 are the interesting ones with full access on: an agent that can touch the payment rail
could otherwise issue a refund, sign up for a service, or accept a contract *as* the owner. The
floor keeps the org's spending and commitments inside the org's own accounts, which is exactly the
line between "the company acted" and "the person acted."

---

## Isolation between agents (a consequence of full access)

With prompts off and real shells available, **agents must not be able to reach each other's
state**:

- Each agent has its own workspace root; a shared root only where the department shares work.
- The bypass is per-agent — one compromised agent does not confer another's posture.
- The ledger is written through the store, never by an agent's raw SQL.
- Agent-authored code runs in a safe cell by default (axis 2), so a bad agent's blast radius is its
  own container unless it is explicitly given host reach.

---

## The tower shows it

The owner must be able to see, at a glance, who is running with a bypass:

| Marker | Meaning |
|---|---|
| Green ring on the desk | `unattended_full` |
| Amber ring | `supervised` — actions staging |
| Grey, desaturated | `frozen` |
| Container icon | running in a safe cell rather than on the host |
| Red floor plate | an escalation or hardline denial is pending |

And the audit: with prompts off, the **ledger and the event stream become the only record** of what
agents did. They are already append-only (C1) and already carry cost, run id, and artifact — so
"what happened last night" has an answer that does not depend on anyone having been asked first.

---

## What changes in the plan

| File | Change |
|---|---|
| D7 | was "outbound actions gated at the money boundary" → **superseded by D19** |
| D18 | hardline floor **kept**, list revised to six items (above) |
| `45-ECONOMY.md` | the money gate becomes a *posture*, not a default: staging applies under `supervised` |
| `55-DASHBOARD-PAGE.md` | add the posture markers to the HUD |
| `95-CONTRACT.md` | C5 restated: the gate is posture-dependent; C16 keeps the ladder and the hardline tier |
| `30-DATA-MODEL.md` | `office_agent.autonomy` column (`unattended_full` \| `supervised` \| `frozen`) + `exec_profile` |

**The honest trade, stated once:** with prompts off, an agent can do something expensive or
embarrassing before the owner sees it. The mitigations are (a) the per-run cap, (b) the hardline
floor, (c) the append-only ledger, and (d) the fact that every important external action — publish,
sell, commit the owner — is still a *capability* the agent needs a grant to have, and grants are
revocable per agent in one call. That is the deal: speed and autonomy, paid for with after-the-fact
auditability instead of before-the-fact permission.
