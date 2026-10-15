# Office animation reachability — why a full ledger can render an empty, still scene

**Scope.** The relationship between `voicebridge/office/tower.py`'s `_ANIMATION`
map (the event-kind → pose table the renderer folds over) and the kinds that
production code actually writes to `office_event`. Two questions, answered with
evidence, both directions:

1. **Is any mapped pose unreachable** — a pose the renderer can draw that nothing
   in the office can emit?
2. **Is any emitted kind invisible** — work that writes an event row and has no
   pose, so the tower cannot show it?

Pinned by `tests/test_office_animation_reachability.py` (the RELATIONSHIP, not the
current list — adding a kind does not break it; see §6).

---

## 1. The two sides

### The mapped side — `tower._ANIMATION` (`tower.py:46-70`)

15 kinds → pose:

| kind | pose | |
|---|---|---|
| `run_started` | working | runner's own events |
| `run_settled` | settling | |
| `run_unpriced` | paused | |
| `run_failed` | stopped | |
| `task_done` | done | |
| `task_unverified` | working | a ran-but-unverified turn still WORKED |
| `consent_denied` | blocked | |
| `tool_call` | walking | |
| `note_written` | carrying | `50-INTEGRATION.md` capability map |
| `memory_written` | glow | |
| `skill_used` | working | |
| `mcp_call` | walking | |
| `ledger_write` | settling | |
| `escalation` | walking_to_manager | |
| `meeting` | meeting | **UNREACHABLE — see §3** |

`_animation_for` (`tower.py:231`) reads this map and defaults an **unknown** kind
to `working` — an event that happened stays visible, but a kind nobody mapped is
silently drawn as generic `working` rather than as nothing.

### The emitted side — the production write sites

Every `office_event` row goes through one seam: `runner._event` (`runner.py:59`),
which `agent_memory.emit_event` (`agent_memory.py:62`) reuses. Emission sites, as
of `17da98198`:

| kind | emit site | how the kind is named |
|---|---|---|
| `run_started` | `runner.py:216` | literal |
| `run_settled` | `runner.py:290`, `runner.py:351` | literal |
| `run_failed` | `runner.py:246` | literal |
| `run_unpriced` | `runner.py:323` | literal |
| `consent_denied` | `runner.py:203` | literal |
| `tool_call` | `runner.py:235` | literal |
| `task_done` / `task_unverified` | `runner.py:295`, `runner.py:355` | conditional `"task_done" if done else "task_unverified"` |
| `note_written` | `runner.py:120` | literal (also `agent_memory.NOTE_KIND_EVENT`) |
| `ledger_write` | `receipts.py:144` | literal |
| `escalation` | `escalation.py:412` | literal |
| `memory_written` | `agent_memory.py:221` | **variable** — `event = NOTE_KIND_EVENT if mkind == "note" else MEMORY_KIND_EVENT` (`:218`) |
| `skill_used` | `agent_memory.py:457` | **variable** — `kind = classify_tool(name)` (`agent_memory.py:435`) |
| `mcp_call` | `agent_memory.py:457` | **variable** — `classify_tool` (`agent_memory.py:433`) |

The three **variable-kind** rows are the ones a naive `grep '"kind"'` misses:
`remember()` (`agent_memory.py:164`) emits `memory_written` through a local named
`event`, and `tool_events()` (`agent_memory.py:439`) emits whatever
`classify_tool()` returned. `classify_tool` returns `"mcp_call"` for the `mcp__`
wire prefix, `"skill_used"` for the vendored skill tools, and `""` (no event) for
anything else — a plain file/shell tool is never promoted into a walk.

---

## 2. The two-direction diff (the finding)

Derived by AST over `voicebridge/office/*.py`, resolving literals, module
constants, local-name dataflow, and classifier returns (see §6 for the
collector). Reproduce with:

```sh
.venv/bin/python -m pytest tests/test_office_animation_reachability.py -q
```

### Direction A — mapped but not emitted

```
UNREACHABLE: 'meeting'  -> pose 'meeting'   emitted_by = NOTHING
```

**One** unreachable pose, and it is the declared exception: `meeting` maps to a
boardroom pose and the boardroom is not wired (`docs/game-office/57-BOARDROOM.md`
defines the room kinds; nothing writes a `meeting` row yet). Emitting it now would
draw motion for a room that does not exist, so it stays mapped AND unemitted **on
purpose**. The other 14 mapped kinds each have a real emitter (table in §1).

### Direction B — emitted but not mapped

```
UNMAPPED: (none)
```

Every kind production can write to `office_event` has a `_ANIMATION` pose. No work
is invisible. This is the direction that would otherwise rot silently: an unmapped
kind does not crash — `_animation_for` folds it to `working` — so a new emitted
kind without a pose would show generic motion and nobody would notice the intent
was never encoded.

### What the live store actually contains

The live office store (`~/.local/state/qubit-voicebridge/office/office.db`) held,
at the time of this audit:

```
run_started       3 rows   newest = 1791821997 (age ≈ 225664 s / ≈ 2.6 days)
tool_call         1 row
task_unverified   1 row
run_unpriced      1 row
run_settled       1 row
----- 7 rows total, newest far outside tower.ACTIVE_WINDOW_S = 120
```

Six of the 14 emittable kinds (`memory_written`, `note_written`, `skill_used`,
`mcp_call`, `ledger_write`, `escalation`) have **never been written** in this
store — the work that would fire them (a turn that used a skill/MCP, an artifact,
a money posting, a memory write, an escalation) has not run. `tower.state()` on
this store returns `empty: True`, `characters: []`, so the live page's
`0 working / 0 need you` and every agent's `Waiting for instructions` are the
**honest** render of a store whose newest event is 2.6 days old.

> Note: the page header's `Connected 10 agents` is the SSE agent-roster count, not
> animation state. Ten seats exist and are `active` in `office_agent`; none has a
> fresh `office_event` row, so all ten fold to `idle`.

---

## 3. Unreachable pose — exact fix site (NOT applied)

**Finding:** `meeting` is mapped (`tower.py:69`, `"meeting": "meeting"`) and
nothing emits it.

**This is the declared, deliberate exception, not a defect to fix blindly.** There
is no boardroom, so emitting `meeting` would be a pose for a room that does not
exist — the opposite of the "no event, no motion" contract.

**When the boardroom is built, the fix is to EMIT at the honest call site:**

- **Emit site:** the boardroom/review path that seats participants in a
  `boardroom` / `review` room (`docs/game-office/57-BOARDROOM.md`), the moment a
  decision session actually convenes — i.e. a call
  `agent_memory.emit_event(cfg, "meeting", agent_id=<participant>, ...)` alongside
  whatever room/participant state that path sets. Do **not** add a synthetic
  `meeting` row anywhere; it must correspond to a real session.
- **Mapping entry:** already present (`tower.py:69`) — no change needed.
- **Then:** remove `"meeting"` from `DECLARED_UNREACHABLE` in the test so the
  forward rule covers it again, and the new emitter is asserted like the rest.

Until then the honest state is: mapped, unreachable, declared with a reason, and
verified to be *really* unemitted (§6) so the declaration cannot hide a live pose.

**No invisible kind to fix** — Direction B is empty.

---

## 4. The autonomous driver — OFF by default, and gated

The office's own report is not a bug: **nothing is driving it.** The driver is
`voicebridge/office/autowork.py`, and its shape is deliberate:

- **OFF by default.** `CONFIG_KEY = ("office", "autowork", "enabled")`
  (`autowork.py:112`); `_flag()` (`autowork.py:172`) returns `False` unless config
  says otherwise. The module docstring states it (`autowork.py:33`).
- **Gated, in order** (`tick`, `autowork.py:623`):
  1. `enabled?` — no → `return {skipped: "disabled"}` **without even opening the
     store**;
  2. e-stop latched → `{state: "HALTED"}` (a stop is not a crash);
  3. time due? (persisted `last_tick_at` vs `every_seconds`, default
     `DEFAULT_EVERY_S = 900`) → `{skipped: "not_due"}`;
  4. then, per seat: frozen posture → skip; no turns left → `turn_budget`; at
     proposal cap → `proposal_cap`; then `task_proposal.propose` → approver, and
     one real `runner.run_turn` per seat per tick.
- **Not a thread, not a loop, not a second budget.** It is a cron-shaped job that
  runs and returns; it spends through the *existing* gates (e-stop → consent →
  admission/caps → turn budget → envelope) and invents no counter. A tick that
  does nothing writes nothing (C7/D14).
- **The flag is read from one place.** `enable()` (`autowork.py:782`) persists to
  `autowork.json` beside the store; `_flag()` reads that persisted value first,
  then the config key — so `enable` and the tick consult the SAME source (the
  "silent no-op `enable`" class is ruled out by design).

### Live state — the driver has never run

```sh
$ .venv/bin/python -m voicebridge office autowork
{
  "state": "OK",
  "enabled": false,
  "config_key": "office.autowork.enabled",
  "every_seconds": 900,
  "last_tick_at": null,      # never ticked
  "last_tick_date": null,
  ...
}
```

- No `~/.local/state/qubit-voicebridge/office/autowork.json` exists (the persisted
  state file is absent).
- The live config (`~/.config/qubit-voicebridge/voice.yaml`) has **no** `office:`
  section, so the config default (`False`) governs.
- The live cron store (`~/.local/state/qubit-voicebridge/schedules.db`, 5 jobs)
  contains **no** job referencing office/autowork. Nothing is scheduling the tick.

**Conclusion (the legitimate finding):** the office looks dead because nothing has
driven it. The animation map is **reachable** (14/15 kinds emittable), the renderer
is **correct**, and the scene is **genuinely idle** — the last real work is ~2.6
days stale. Turning the driver on
(`qubit office autowork --set on`, then schedule the tick on the existing cron
surface) is the operator action that would produce motion; it is opt-in because it
spends real inference money.

---

## 5. Falsifiability (both rules proven able to fail)

Per the skill rule *"prove a repair's test can fail"*, both directions were
exercised on the real tree, then restored (sha256 `ad373a7b…` before and after):

| mutation | expected | observed |
|---|---|---|
| add `"phantom_pose_no_emitter": "working"` to `_ANIMATION` | Direction A red, naming the pose | `AssertionError: these _ANIMATION poses are unreachable …: ['phantom_pose_no_emitter']` (5 of 6 tests red) |
| remove `"ledger_write": "settling"` from `_ANIMATION` (still emitted by `receipts.py:144`) | Direction B red, naming the site | `AssertionError: these kinds are written to office_event but have no _ANIMATION pose — the work is invisible in the tower: ['ledger_write']  Evidence: ledger_write <- ['receipts.py:144']` |

After restore: `6 passed`. The existing
`tests/test_office_event_animation_linkage.py` still passes (`20 passed` together).

---

## 6. The relationship test — what it pins and why it is not the current list

`tests/test_office_animation_reachability.py`.

The older `test_office_event_animation_linkage.py` is correct but hard-codes
`WIRED = {...}` and `already_emitted = {...}` and asserts
`mapped <= WIRED | already_emitted | DELIBERATELY_UNWIRED`. Adding one honest kind
turns it red — and the red gets blamed on the new kind. Its own docstring names
this as the weathering rule; the missing half is that the *assertion* is on
membership.

This file pins the RELATIONSHIP instead. **Neither side is a literal today:**

- the **mapped** side is read out of the live `tower._ANIMATION`;
- the **emitted** side is derived from `voicebridge/office/*.py` by AST —
  string literals and conditionals at the call site, module constants, a
  local-name dataflow pass (`event = …` / `kind = classify_tool(name)`), and the
  return literals of a classifier function.

Two rules:

1. **`test_every_mapped_pose_has_an_emitter_or_is_a_named_exception`** — every
   mapped kind is emittable OR in `DECLARED_UNREACHABLE` (with a reason). A new
   `_ANIMATION` entry with no emitter fails here, naming the pose.
2. **`test_every_emitted_kind_is_mapped_or_a_declared_non_animator`** — the
   reverse. A new emitted kind with no pose fails here, naming the emit site.

Plus the escape hatches are themselves asserted to be true
(`test_the_declared_unreachable_pose_is_really_unemitted`,
`test_the_declared_non_animating_kind_is_really_emitted`) so a declaration cannot
rot into a hiding place, and `test_the_relationship_can_fail` mutates a scratch
copy and proves Rule 1 goes red.

**Adding a kind does not break this test.** A new mapped kind with a real emitter
passes Rule 1; a new emitter with a mapping passes Rule 2; a new kind that is
deliberately one or the other is added to the matching DECLARED table **with a
reason**. The only red is a genuine reachability gap.

---

## 7. Reproduce the evidence

```sh
# the mapped side
sed -n '/_ANIMATION = {/,/^}/p' voicebridge/office/tower.py

# the emitted side (the three variable-kind emits are why a plain grep is not enough)
grep -rn '_event(\|emit_event(\|_emit(' voicebridge/office/*.py

# the diff, both directions, as the test computes it
.venv/bin/python -m pytest tests/test_office_animation_reachability.py -v

# the live store
sqlite3 ~/.local/state/qubit-voicebridge/office/office.db \
  "SELECT kind, COUNT(*), MAX(ts) FROM office_event GROUP BY kind;"
sqlite3 ~/.local/state/qubit-voicebridge/office/office.db \
  "SELECT status, COUNT(*) FROM office_agent GROUP BY status;"

# the driver's default + gate + last tick
.venv/bin/python -m voicebridge office autowork
ls ~/.local/state/qubit-voicebridge/office/autowork.json   # absent = never ran
```
