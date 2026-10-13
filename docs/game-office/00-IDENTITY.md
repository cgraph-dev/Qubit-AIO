# 00 — Identity

**Working name:** `Qubit Corp` (internal dir `docs/game-office/`, module namespace `office/`).
Name is a placeholder — it must not ship as a Tripo/StarNet/pixel-agents derivative and must not
carry any upstream brand (see `docs/BRAND.md`).

## What this is

A real company, staffed by real agents, rendered as a game.

The operator (Tricky) is the **owner**. He is not the player. He sits outside the org chart the
way a shareholder does: he supplies capital, absorbs legal and compliance, pays the fixed fees,
and takes the profit. Everything else — what businesses to start, who to hire, what to build,
what to sell, when to kill a product — is decided inside the org by agents.

The UI is a top-down isometric view of a corporate tower. A **floor** is a building level holding
two to seven **departments** (each department is a business with its own P&L). A **room** is a team.
A **desk** is a seat. Three reserved floors hold org-level function instead of businesses: the
executive floor (the CEO and the boardroom), the director floor, and the intelligence floor where
intake happens (D30). The layout is not a skin over the data: **the layout is the organizational
structure**, and the organizational structure is the product.

## Product law

1. **The tower never asserts state it cannot prove.** Every agent position, every status
   bubble, every dollar on the HUD traces to a real row in a real store. No decorative
   animation that implies work is happening when it is not. (Borrowed from StarNet's stated
   core law — it is the correct law.)
2. **A desk is a seat, a room is a team, a department is a business, a floor is a building level.**
   Placing, moving, or removing one is an org mutation, not a cosmetic one: a room's objects are the
   team's capability grants (`75-EDITOR.md`), and a seat carries a real supervisor and a real budget
   envelope (`11-ORG-CHARTER.md`).
3. **Money is the survival signal, and it is real money.** Not an in-world score. Real dollars
   earned by real products sold under the owner's name. Real dollars burned on real inference.
4. **Nothing an agent does outbound is unattributable.** Every external action (post, publish,
   file, invoice, send) lands in an append-only ledger with agent id, cost, and the artifact it
   produced — before and independently of whether it made money.
5. **The org must be able to lose.** An agent that burns its balance without earning is
   terminated. The death condition is explicit and checkable, not a mood.
6. **The owner keeps the off-switch.** Emergency stop is the single mutation gate, the same one
   the CLI/voice/dashboard already share. Agents do not get to resume themselves.

## What it is not

- Not a simulation of a company. The simulation fantasy is explicitly rejected: a fake P&L
  would make the whole thing a game about a game.
- Not a fork of any referenced repo. All seven references are **design sources only** — read for
  structure, unlicensed for code reuse unless license permits it (`80-RISKS.md`).
- Not a second dashboard. It is a Qubit dashboard tab, served by the existing authenticated
  bridge, sharing the existing stores, ops broker, confirmation journal, and e-stop.

## Success, stated so it can be checked

The tower is working when, without the owner touching anything for a week:

- the org paid its own inference bill from money it earned;
- every dollar of that bill is traceable to a specific agent, run, and artifact;
- at least one business unit was killed by its own manager for losing money, and the ledger
  shows exactly why;
- the owner can open the tower and, in under ten seconds, see which floor is carrying the
  company and which floor is a cost center;
- and the owner's legal exposure is a bounded, enumerable list — not "whatever they did";
- and a third party can pick any single dollar in the ledger and walk it to the seat, the turn, and
  the artifact that produced it, using only records the org cannot edit (`17-AUDIT.md`).

Everything after this file exists to make those six sentences true.
