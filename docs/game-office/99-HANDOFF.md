# 99 — Handoff

For any agent (or human) picking this up. Read this **before** the rest of the directory.

## Read order

1. This file.
2. `95-CONTRACT.md` — the invariants you may not break. Non-negotiable.
3. `10-DECISIONS.md` — the rulings. Binding on implementation; only the owner changes them.
4. `90-MILESTONES.md` — find your milestone and its **behavioral proof**. That proof is your
   definition of done.
5. The specific spec file for your area.

Do not read the whole directory and start building. Pick a milestone.

## Which milestone can start now

| Milestone | Can start? | Why |
|---|---|---|
| **M0** — accounting spine | **Accounting half DONE 2026-10-12** | All four behavioral proofs pass (see `90-MILESTONES.md`). `voicebridge/office/` + `qubit office` + 57 tests. **Carried into M1:** `consent.py`, which has no consumer until a real turn can be denied. |
| **M1** — one agent, one real turn | **Built 2026-10-12** | The runner, the consent ladder, artifacts, events, and `qubit office trace` all land. Proven at zero cost via an injected executor. **Only the real paid call is outstanding** (~$0.05). Freezes the `office_event` schema — which unblocks M4. |
| **M2** — org + escalation | After M1 | Needs real turns to escalate about. **Also the first milestone that needs D34–D41**: the supervisor edge, the span limit, the envelopes, and the auditor seat all land here (`11-ORG-CHARTER.md`). |
| **M3** — first business, real money | **Blocked** | Needs three owner decisions — see below. |
| **M4** — the tower ("Qubit Office" page) | After M1 (schema only) | Can start in parallel with M2/M5 once `office_event` is frozen. **No art dependency** — phase-1 agents are procedural. |
| **M5** — the ratchet | After M1 | Needs attributed artifacts to score against. |
| **M6** — Tripo characters | After M4 | A renderer swap at existing tile coordinates. The owner exports the assets himself (D26), so no Tripo key is needed by the org. |
| **M7** — floorplan editor | After M4 | Object=capability, so this is an admin console for real grants, not decoration. Staged writes + undo (`75-EDITOR.md`). |
| **M8** — the R&D floor | After M1 (the pipeline must exist) | The owner's own intake: drop a video or an article, get a digested playbook that enters the funnel at gate 0. Runs at **zero seats** without breaking (`14-RND-FLOOR.md`). |

**M3 is blocked on the owner.** Do not guess these:
1. First business vertical — the owner picks from the **scraper/analyst/researcher** proposals
   (`13-PIPELINE.md`).
2. Payment rail / processor, and whose account it settles to.
3. Legal entity on the sale.

**Not an M3 blocker:** Tripo Studio vs API. D26 has the owner exporting assets himself, and M6
(M7's neighbour) is the only milestone it touches. The four M3 questions the plan used to carry are
now org work that ends in a review or a request (`05-AMENDMENT-LOG.md` § Known open items).

Start M0. It needs none of them.

## The eight ways to get this wrong

These are the failures the spec exists to prevent. Each one looks reasonable while you are doing it.

1. **Building the tower first.** A beautiful tower over a fake economy is the exact failure every
   referenced project has. The ledger is M0. The visuals are M4. Do not reorder.
2. **Adding a second store / loop / memory.** It is always tempting — "the office needs its own
   event bus." It doesn't (C3). Read/write split-brain is this repo's most expensive recurring
   bug class, and it has already been paid for in cron, skills, pairing, channels, and sessions.
3. **Making the UI optimistic.** "Show the move immediately, reconcile later." That is the tower
   lying, which breaks the one product law (C4, C7, D14).
4. **Writing a money total by hand.** Ever. All totals derive from the ledger (C2).
5. **Letting an agent edit its own baseline.** The daily reflection writes to the *ratchet*, never
   to the baseline (D5). An agent that rewrites its own instructions cannot be measured against
   anything, and "getting smarter" becomes unfalsifiable.
6. **Treating unknown spend as zero.** A crash mid-turn drops a real charge; if the ledger reads
   healthy anyway, an agent survives on money it already burned (D15/C14). Admission must refuse
   while any run is unsettled.
7. **Counting an unmetered run as dollars.** A subscription-backed turn reported as spend becomes a
   phantom cost line that eventually *denies real work* with a cap-hit naming money nobody spent
   (D16/C15).
8. **Letting a run mark itself done.** StarNet burned a whole night on a loop whose only
   completion signal was the model saying "verified the files exist". Done is machine-checkable or
   it is not done (C17).
9. **Building the hierarchy without the supervisor edge.** Nine directors and a manager per business
   looks right in a diagram; without `supervisor_id` nothing in the schema can answer *who rates
   this seat*, and the reviews become prose about agents nobody can attribute (C30/D34).
10. **Letting the CEO gate every spend.** The envelope exists so one desk is not the org's
    throughput (D37). A CEO that approves every $20 is the approval queue D19 deleted, wearing a
    different name.
11. **Treating the auditor as optional.** It is one seat and roughly nine turns a month. Without it,
    the plan's central claim — the numbers are real — rests on the agents choosing to be accurate
    (`17-AUDIT.md`).

## How to claim done

A milestone is done when **its proof command runs and you quote the real output**. Not "tests
pass". Not "implemented". Not "should work".

```
M0 proof  → the ledger rejects UPDATE/DELETE; pnl arithmetic is exact to the cent
M1 proof  → qubit office trace <agent> shows the turn, real tokens/cents, artifacts, events;
             and an over-budget turn is REFUSED (typed), not run
M2 proof  → an escalation appears on the approvals surface, is approved from voice, agent resumes;
             and a below-floor runway escalates instead of silently dying
M3 proof  → a business earns its first real dollar, row names the agent + artifact
M4 proof  → the Qubit Office page renders real seats; HUD burn/runway match qubit office pnl to
             the cent; kill a session mid-turn and the character stops animating; an org with no
             rows renders the empty state (never a demo office)
M6 proof  → a rigged GLB renders at the same position/state as the procedural sprite with no
             change to office/store/event code; the license gate refuses a non-commercial entry
M5 proof  → a ratchet entry with artifact-backed score changes in the predicted direction;
             a zero-score entry deactivates with a recorded reason
M7 proof  → editing the floor changes a real capability grant, staged and undoable, with the
             grant resolved fresh on the next turn (C12)
```

**On the corporate layer (D34–D41):** it is not a milestone of its own, and it is not decoration to
bolt on later. The supervisor edge, the span limit, and the review records land in **M2**; the
envelopes land in **M2–M3**; the auditor and the reconciliation checks land in **M3**, because M3 is
the first milestone where real money exists to be audited.

Claims of `VERIFIED`, `COMPLETE`, or `1:1` without that output are refused — the same rule this
repo already applies to release claims (`AGENTS.md`).

## What you may and may not change

From `95-CONTRACT.md`, the short version:

- **May:** add columns/tables, add seat types, add event kinds, add clocks, add businesses and
  ratchet entries, improve the asset pipeline.
- **May not:** touch a ledger constraint or the append-only triggers, relax a capability grant,
  remove an event kind the renderer reads, change the reflection cadence or its one-output cap,
  change survival math, the money gate, or the e-stop boundary, or ship a non-commercial asset.

Touching a "may not" is not a proposal — it is a ruling change, and it goes to the owner.

## Filing a proposal against a ruling

Any agent may. Format:

```
PROPOSAL — D<n>
Alternative I want: <the change>
Why: <evidence: file:line, a measured number, or a reproduced failure>
What it costs: <money, latency, complexity, or a thing it makes impossible>
```

Evidence, not preference. "This would be cleaner" is not a proposal.

## Environment facts you will need

- Office state: `<QUBIT_HOME>/runtime/office/office.db`
- Tower plugin: `frontend/qubit-app/plugins/qubit-office/` (overlay — never edit a vendored file)
- Static assets: overlay plugins serve only `.js` / `.json` / `.css` today — **`.glb` and tile PNGs
  need the suffix allowlist extended** in `voicebridge/webui.py::_serve_qubit_plugin`
  (keep the existing path-confinement check)
- New SPA route: add to `SPA_ROUTE_PATHS` in `voicebridge/webui.py` (~line 868)
- Assets land at `frontend/qubit-app/plugins/qubit-office/assets/characters/<slug>.glb`
- Models/keys: the org spends the owner's money. Read `45-ECONOMY.md` before wiring any provider call.

## Asset intake (owner is supplying characters)

Per character, from Tripo:

- **Format: GLB** (only format carrying mesh + PBR texture + skeleton + clips together)
- **Texture: 1K** — characters render ~64–96px tall; 4K is 16× the bytes for no visible gain
- **Must be rigged** (Auto Rig before export) — a static mesh has no walk cycle
- **Clips, named exactly:** `idle`, `walk`, `work`, `talk`
- **Consistent scale + humanoid silhouette** across the whole cast, or the isometric floor breaks

Drop at `assets/characters/<slug>.glb`; a manifest entry per file, schema in `70-ASSETS.md`.
The license gate (C9) records the tripo account, plan tier, and generation date per asset, and
refuses a non-commercial-tier asset at build time.
