# 14 — The R&D Floor (your idea lab)

Where **you** put material in and the org turns it into candidate businesses.

The `intelligence` floor (`13-PIPELINE.md`) is the **firehose**: scrapers hunt the open web all day
and 200 signals a day go into the funnel, most of them killed. This floor is the opposite machine —
**one thing at a time, chosen by you, read deeply.** Same destination, different intake.

A floor, not a department: it does not sell anything. It feeds the businesses. That is exactly what
the reserved floors are for (D30).

---

## The room map — a task room is a unit of work, not a department

This floor is organised like `BitmapAsset/pepeclaw` organises its office: **eight task rooms that
map abstract agent work to visible spaces** ("Genome Lab", "Dream Chamber", "War Room", "Red Team
Arena", …). A room there is a *kind of work*, and an agent walks to the room its current task
belongs to.

That is the correct model here, and it is worth stating because it is subtly different from how
`30-DATA-MODEL.md` describes rooms elsewhere (a room is a team, inside a department). On this floor
the four rooms are **stages of one pipeline**, and an item moves between them:

```
   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
   │ 1. INTAKE    │──►│ 2. DIGEST    │──►│ 3. WAR ROOM  │──►│ 4. ARCHIVE   │
   │ you drop it  │   │ 3-phase read │   │ 3 seats argue│   │ the library  │
   └──────────────┘   └──────────────┘   └──────────────┘   └──────┬───────┘
          ▲                                                         │
          │  you can also just say it out loud (voice / dashboard)   │
          └─────────────────────────────────────────────────────────┘
                     the survivor becomes an `office_idea`
                        and enters the funnel like any other
```

| Room | What happens there | Named after |
|---|---|---|
| **Intake** | a drop zone that is *yours*. A folder on disk, a link pasted anywhere, a voice note, a URL from the dashboard | pepeclaw's "Identity Vault" — provenance is kept here |
| **Digest** | the deep read. Video → transcript → structured playbook. An article → the same. **Never a summary** | pepeclaw's "Dream Chamber" (offline synthesis) |
| **War Room** | three seats take positions on the playbook and the disagreement is recorded | pepeclaw's "War Room" |
| **Red Team Arena** | a fourth seat attacks the playbook's assumptions before it advances | pepeclaw's "Red Team Arena", adopted as-is |
| **Archive** | every digested playbook, forever. The ones that were killed keep their kill reason | — |

**Five rooms, and that is the budget.** pepeclaw fixes eight for its whole office; nine to five per
floor is the right order of magnitude, and `60-FLOORPLAN.md` enforces it.

---

## Intake — two ways in, one shape

| Way in | How | For |
|---|---|---|
| **The folder** | drop a file, or a `.txt`/`.md` holding one URL per line, into `~/Documents/qubit-ideas/` | video links, articles, PDFs, your own notes |
| **Voice / chat** | say it: *"put this in the lab: <link> — the angle is X"* | when you are thinking out loud and want the *thought* captured, not just the link |

Both land in the same table, so there is exactly one intake in the org:

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | REAL | |
| `source_kind` | TEXT | `url_video` \| `url_article` \| `file` \| `voice` \| `note` |
| `source_ref` | TEXT | the URL, the file path, or the transcript of what you said |
| `owner_note` | TEXT | **your** words about why it caught your eye — optional, and the single most valuable field on the row |
| `status` | TEXT | `new` \| `digesting` \| `digested` \| `triaged_out` \| `failed` |
| `priority` | INTEGER | 0 default; you can raise one item and it jumps the queue |
| `via` | TEXT | `folder_watch` \| `voice` \| `dashboard` |

**The owner's note matters more than the link.** A link is a fact; *why it caught your eye* is the
hypothesis, and it is the thing a researcher cannot recover from the material alone. If a drop has a
note, the digest must address the note explicitly.

## The no-backlog rule — the failure mode this floor exists to avoid

**An inbox is a debt, and an unread inbox is a lie.** The moment this floor holds 40 unread links,
it has stopped being a lab and become a graveyard — and that is the exact shape the plan elsewhere
calls *"the org accumulates work nobody owns."*

So the rule is blunt:

| Rule | Detail |
|---|---|
| **capacity** | the floor holds **at most 12 open items.** Past that, intake refuses new drops and says so |
| **a digest has a deadline** | 72 hours. An item that misses it is `triaged_out` with a written reason, and you are told which ones and why |
| **kill is a real outcome** | `triaged_out` is not a failure. A researcher that kills 8 of 12 has done its job — the rate is a *rated metric* for this floor (`19-REVIEWS.md`) |
| **one at a time is fine** | there is no volume target here. That is the whole difference from the intelligence floor |
| **decay** | an item you dropped and never returned to loses priority after 14 days and is archived undigested. It is still there if you come back |

## Digest — how the read actually works

This is the part worth borrowing properly, and it comes from the **Generative Agents** architecture
(the paper `a16z-infra/ai-town` is built on). Its reflection mechanism is the reusable bit, and its
shape is **three phases, not one summarisation**:

| Phase | What the agent does | Why |
|---|---|---|
| **1. Identify what needs consolidating** | generate **three specific questions** about this material — not "summarise it" | question-driven reflection "prevents bland summarization" — the source material gets *interrogated* |
| **2. Gather evidence** | answer each question **with citations back into the material** | an insight with no citation pointer is an opinion |
| **3. Synthesise with pointers** | produce the playbook, each claim carrying where in the material it came from | the playbook is checkable against the video you actually watched |

For this floor the three questions are fixed, because we know what a business needs:

1. **What is the mechanism?** — what would we actually do, and why does that make money?
2. **What would have to be true?** — what must hold for this to work, and what would falsify it?
3. **What does it cost to start, and what kills it?** — capital, first dollar, and the kill criterion.

Those three map exactly onto `office_idea`'s mandatory fields (`mechanism`, evidence, kill criteria),
so **the digest writes the idea row directly.** There is no "playbook format" separate from the
pipeline's format — a second format would be a second store, which C3 forbids.

**The digest states its own confidence, and says when it could not read the material.** A paywalled
article, a video with no captions, a link that 404s: the item goes to `failed` with the reason, never
to a fabricated playbook.

### What the digest produces

```
PLAYBOOK — <title>
source:      <url / file>   (dropped <ts>, digested <ts>)
your note:   "<owner_note>"          ← addressed explicitly, or the playbook says it could not be
mechanism:   one paragraph. what we do, why it pays.
questions:   the three above, answered, each claim citing where in the source it came from
numbers:     price, cost, margin, first-dollar path — with sources, or marked ESTIMATE
would need:  what would have to be true
kill if:     the falsifier
confidence:  high | medium | low + what would raise it
cannot-see:  anything the material did not answer
```

## War Room — three seats, positions, disagreement recorded

The panel is **question-driven, not a free-form debate**. Each seat answers the same three questions
from its own angle and **must take a position**, because a position can be wrong and an observation
cannot (`19-REVIEWS.md` makes the same argument for functional directors).

| Seat | Argues | Must answer |
|---|---|---|
| `analyst` — demand | is there anyone who wants this? | who buys, at what price, and how we would know |
| `analyst` — build | can we actually produce it? | what we can build in a week, and what we cannot |
| `director_functional` — Finance | does it survive the fees? | net margin after platform/payment costs, not gross |

Then the **Red Team Arena**: a fourth turn attacks the *strongest* argument, not the weakest. A
playbook that survives its own best case being attacked is worth funding.

**The disagreement is the product.** The panel's output is not a verdict, it is a recorded argument:
each seat's position, each objection, and the one thing they could not agree on. If all three agree
immediately, that is recorded too — and it is a *flag*, because three independent angles converging
on the first pass usually means the playbook is obvious, and obvious ideas are usually already done.

Adopted from `rsanandres/aphae`'s phase rule — *"let information move between agents … it creates
information asymmetry"*: the seats see the **digest** and each other's positions, but the digest is
written first and independently, so the panel cannot steer the read.

---

## What this floor may not do

| It may not | Why |
|---|---|
| **fund** anything | funding is the director/CEO path (D37). This floor has no envelope |
| **hold a gate** | same rule as the scraper (D31): its incentive is interesting ideas, so it must not decide which ones survive |
| **skip the funnel** | a playbook from *your* material enters at the **same** gate 0 as a scraper's signal. Your material is a strong hint, not a mandate — the org may kill it, and must say why |
| **overwrite an idea** | a re-digest of the same source is a **new row** with `supersedes_id` set. The ledger rule (C1) applied to ideas |
| **be the only way in** | everything here produces `office_idea` rows; nothing here is a parallel store (C3) |

**The rule that keeps it honest:** *a playbook you supplied is treated as authoritative about the
material and irrelevant about the business.* The org must read what you gave it faithfully, and must
still be free to say the business is bad.

---

## Seats

| Seat | Count | Tier | Reports to | Job |
|---|---|---|---|---|
| `researcher` | 1–2 | best | Director of Intelligence | digest; owns the playbook |
| `analyst` (demand) | 1 | best | Director of Intelligence | the panel, demand position |
| `analyst` (build) | 1 | best | Director of Intelligence | the panel, build position |
| `director_functional` (Finance) | shared | best | CEO | the panel, money position |
| `auditor` | shared | best | owner (dotted) | checks a digested playbook's citations resolve |

**Intake costs nothing to hold** — it is a folder and a table. The floor is 2–4 seats at the smallest
useful size, and it can run at **zero seats** (drops queue as `new`, undigested) without breaking
anything. That is deliberate: you should be able to drop a video at 2am and have nothing happen.

---

## Where it lives

| Concern | Path |
|---|---|
| Floor role | `office_floor.role = 'rnd'` |
| Drop folder | `~/Documents/qubit-ideas/` (watch + one-file-archive, like the existing disk-watcher precedent) |
| Rooms | `intake`, `digest`, `war_room`, `red_team_arena`, `archive` — five, inside the floor's budget |
| Tables | `office_intake_item` (new), plus rows in `office_idea` with `source_channel='owner_intake'` |
| Assets | none new. Text-first; no art dependency (M4's rule) |

## Provenance

| Adopted | From | Shape here |
|---|---|---|
| Task rooms map abstract agent work to visible rooms | `BitmapAsset/pepeclaw` (8 rooms) | the five rooms below are stages of one item's journey, and the agent walks with the item |
| "Dream Chamber" — offline synthesis of memory | `pepeclaw` | the Digest room |
| "War Room" / "Red Team Arena" | `pepeclaw` | the panel and the assumption-attack, adopted by name and by function |
| Provenance kept with the item, verification surface | `pepeclaw` ("Identity Vault") | the Intake room keeps source + your note forever |
| Reflection = 3 phases (questions → evidence → synthesised insights with citation pointers) | **Generative Agents** (the architecture `a16z-infra/ai-town` implements) | the Digest, verbatim in shape |
| Trigger-based consolidation (fires on cumulative importance, not a schedule) | same paper / `ai-town` | the no-backlog rule and the 72h deadline, instead of a fixed digest queue |
| Conversation summarised into a first-person memory with an importance score, retrieved later | `ai-town` (`rememberConversation`) | the Archive: every playbook scored, and past playbooks retrieved when a new drop overlaps one |
| Agents see each other's positions; information asymmetry matters | `rsanandres/aphae` (rumour propagation) | the panel sees the digest and each other, never the source read-in-progress |
| Work is grouped into labelled workstreams with dependencies, so a stage cannot fake progress | `aphae`'s PLAN.md structure (phase → item → needs) | the War Room's mandatory positions + the Red Team attack as an explicit `needs`-style gate |
| One doc as the living record, amended rather than rewritten, with a rule that a stale doc is worse than none | `aphae` PLAN.md, `starnet` docs/INDEX.md | this directory's `05-AMENDMENT-LOG.md` already follows it; the R&D floor logs every digest and kill there |
| Integrate many agent sources through one typed boundary | `pixel-agents` `HookProvider` | intake accepts five source kinds through one `office_intake_item` shape |
| Live state shown as-is, with a demo fallback that is clearly labelled | `pepeclaw` demo-mode | if the floor is empty the tower says **"no material dropped"** — it never invents a busy lab (C7) |
