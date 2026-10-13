# 50 — Capability Wiring

The org does not get new memory, a new tool registry, or a new skill format (D9). It gets a
**seat map**: which existing Qubit capability each seat type is granted, and how what the agent
does with it comes back as a renderable event.

## The four systems, and where the org plugs in

| System | Existing seam in this repo | What an org agent does with it |
|---|---|---|
| **Obsidian** | `qubit_runtime/skills/note-taking/obsidian` (vault read/search/create/edit) | Writes deliverable notes, reads the owner's vault for context, keeps a per-business note as its working memory |
| **OpenViking** | `voicebridge/context_store.py` (local root `<state>/context`) + `viking_*` tools | Writes durable facts (decisions, theses, kill reasons), searches past context before proposing a business |
| **Skills** | `qubit_runtime/skills/**` + `skill_manage` | Uses installed skills as tools; the ratchet can *author* a new skill, which lands in the real skills tree |
| **MCP + tools** | `voicebridge/mcp_servers.py`, `tools/registry.py`, `voicebridge/dashboard_ops.py` | Calls MCP servers and registry ops; mutation ops go through the shared confirmation journal |

**Rule:** if an org agent needs a capability that exists, it uses the real one. If it needs one
that does not exist, it is a plugin or a skill — never a special case wired into the office
module.

## Seat capability grants

Capability is a property of the **seat type**, resolved at hire from a table in the store. This is
the org's permissions model, and it is deliberately boring: a fixed table, reviewed by a human at
founding time, not dynamically granted by agents.

| Seat | Models | Research | Capital request | Publish (mandatory review) | Hire/fire | Author skills |
|---|---|---|---|---|---|---|
| `ceo` | best | Perplexity + registry | raises to owner | yes | yes (org-wide) | yes |
| `director_portfolio` | best | Perplexity + registry | raises to CEO | yes | yes (own portfolio) | yes |
| `director_functional` | best | Perplexity + registry | advisory note only | yes (own lane) | no | yes |
| `manager` | best | Perplexity + registry | raises to director | yes | yes (own department) | yes |
| `scraper` | cheap | browser fetch + crawl | **no** | **no** | no | yes (search patterns only) |
| `analyst` | best | Perplexity + registry | no | no | no | yes |
| `researcher` | best | Perplexity (primary) | no | no | no | yes |
| `worker` | cheap | registry only | no | no | no | yes |
| `support` | cheap | registry only | no | no | no | no |
| `auditor` | best | full read: ledger, reports, artifacts, ideas | **no** (it reports findings, never spends) | no | no | no |
| `researcher` (R&D floor) | best | transcript fetch (`yt-dlp`), article fetch, OpenViking | no | **no** | no | yes (digest templates only) |

Three seat rules, in addition to the two below:
- **A `scraper` has no judgment authority.** It finds and documents; it never ranks business value
  or votes on an idea. Its incentive is volume, so it must not hold a gate (`13-PIPELINE.md`).
- **A `director_functional` cannot block.** It writes an advisory note in its lane; the portfolio
  director decides and must address the objection. Silence is neither consent nor a veto.
- **A `director_portfolio` owns its businesses' P&L.** Its picks that die are its record.
- **The R&D floor holds no gate and no budget (D42).** A digest is material, not a business case; it
  enters the funnel at gate 0 like everything else, and the panel's argument is recorded, not obeyed.
- **The `auditor` cannot spend and cannot act** (D40). It reads everything and writes findings;
  anything else would make it a second CEO with less accountability.

Two hard rules on top of the table:

- **A `worker` cannot sell.** Selling is a **mandatory review** (D22) and always goes to the owner,
  and only `manager`/`ceo` seats may raise one. A worker that believes something should be sold
  raises it to its manager. This keeps the publish surface small.
- **A `scraper` cannot execute.** Scrapers find and document; they never touch a tool with side
  effects, and they never rank business value (D31). Their entire output is a cited signal.

## Research path (D8)

Default research tool for `scraper`/`analyst`/`researcher` and `manager`/`director`/`ceo` seats is the **Perplexity Pro browser
session** (`skills/research/perplexity-web-search` — logged-in Chromium via CDP, zero API cost).
The plain search tool stays the fallback for cheap lookups, and a researcher that uses the fallback
must say so in its proposal (it is lower quality evidence).

An idea is refused at **gate 0** without all of (D31):
1. the business thesis in one paragraph,
2. at least three independent citations,
3. an estimated unit price and cost,
4. `kill_criteria`,
5. the regulatory/legal exposure field (owner-facing, see `45-ECONOMY.md`),
6. `permitted_use_checked` on every source (D29 item 6).

That list is enforced at write time in the template, not by review taste (C27).

## What comes back as an event

Every capability use writes an `office_event` row (`30-DATA-MODEL.md`) so the tower can animate
it without the renderer knowing anything about the underlying tool:

| Agent action | `office_event.kind` | Tower rendering |
|---|---|---|
| Reads the vault | `tool_call` | walks to the vault, reads |
| Writes an Obsidian note | `note_written` | carries a document to the shelf |
| Writes OpenViking memory | `memory_written` | brief glow at the desk |
| Uses a skill | `skill_used` | tool swing at the workbench |
| Calls an MCP server | `mcp_call` | walks to the comms array |
| Spends on inference | `ledger_write` | coin/energy drain animation at the desk |
| Earns revenue | `ledger_write` | deposit at the vault, floor plate flash |
| Raises an escalation | `escalation` | walks to the manager's room, clock icon |
| Staged action awaiting the owner | `escalation` | red pending marker until settled |

**Anti-lie rule (D14):** the renderer animates from these rows only. No row → no animation. There
is no "looks busy" state; if the agent's turn has produced no event yet, it sits still.

## Escalation to the owner

An agent that needs the owner (approval, legal question, a decision above its grade) does not
just stall. It creates a staged entry with a **one-question brief and options** (the Task Brief
pattern from StarNet, which is the right pattern) and it is visible:
- in the tower as a red marker on the desk and the floor plate,
- in the existing approvals surface,
- read aloud on request through the voice dashboard.

Escalations have a TTL. An expired escalation is recorded as `unanswered`, which is a signal the
manager sees — an agent whose escalations go unanswered is being ignored, and that is an
organizational problem worth surfacing rather than hiding.
