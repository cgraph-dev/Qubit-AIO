"""The autonomous driver: the cron-shaped tick that lets the org notice work.

Every turn the office has ever run was a human typing ``qubit office run`` or
``qubit office dispatch``. The proposal layer (``task_proposal.propose``) had
**no consumer at all** in production — its only call site is a CLI verb — so the
org could execute anything asked of it and notice nothing on its own. That is
the difference between a diorama and a game, and it is the one thing this module
adds.

What it is **not**, and why each of these is a hard rule rather than a taste:

* **Not a loop, and not a thread.** The tick is a *job* — it runs, does one
  bounded pass, and returns (``grep 'while True|threading.Thread|
  asyncio.create_task' voicebridge/office/*.py`` stays empty). The cadence comes
  from the repo's existing cron scheduler, exactly as ``survival.daily_tick``
  already runs; that is what makes it survive a restart and be pausable from the
  cron surface (O3).

* **Not a second budget.** A tick spends through the *existing* gates, in the
  existing order: the e-stop first (``runner.py:173``), then consent
  (``runner.py:180``), then admission and the caps (``receipts.admit``), then the
  per-seat daily turn budget (``work.turn_budget`` / ``work.spend_turn``), then
  the envelope (``envelope.check_spend``/``spend`` inside the turn). This module
  invents no counter of its own. Its only new dial is *how often a seat is
  woken*, not *what it may spend*.

* **Not a second source of motion.** The tick emits no animation of its own. It
  calls the same ``runner.run_turn`` a human command calls, so every
  ``run_started`` / ``tool_call`` / ``run_settled`` / ``task_done`` row is
  written by the existing emitter. No event, no motion (C7/D14): a tick that
  does nothing writes nothing.

* **Off by default.** ``office.autowork.enabled`` is ``False`` in config. A
  disabled tick is a genuine no-op — it does not even open the store.

## The tick, in order

```
enabled?  ──no──▶  return {skipped: "disabled"}   (nothing was read or written)
   │yes
estop?    ──latched─▶ return {halted}             (a stop is not a crash)
   │clear
time due? ──no──▶  return {skipped: "not_due"}    (persisted last_tick_at)
   │due
adjudicate(rounds=1)   review a finished task, if there is one
approve_pending()      each pending proposal its level above is willing to take
per seat:
   frozen posture        -> skip, never woken
   no turns left         -> skipped/turn_budget
   proposals at its cap  -> skipped/proposal_cap
   file a proposal from its own context (goal + memo + org workload)
   > left to its approver, OR the kitty cannot cover a turn -> skipped/'envelope'
   < turns left          -> spend one turn, run one real attributed turn
```

**At most one turn per seat per tick** (O3) falls out of the structure: the loop
runs a seat once and moves on. The daily turn budget is what bounds the day; the
``every_seconds`` cadence is what bounds the tick. Neither is a new invention.

## Who approves the org's own proposals

A seat may never approve its own idea; the approver is exactly one level above
(``task_proposal.approve_level``, C31). So the tick drives the *existing* approve
path:

* a **leaf's** proposal is approved by its manager — the level directly above it;
* a **manager's / director's** proposal is approved by the CEO seat, which is
  above it — and the task then reaches the leaf pool through the existing
  ``dispatch.pick_worker``;
* a **CEO's** proposal has no seat above it (the owner is not an agent, D2). The
  tick does **not** invent a seat approver for it: the proposal stays pending,
  visibly, for the owner. That is the honest outcome, not a silent auto-approve.

An approver that cannot afford the work is left in place too — a refusal in the
approval path is recorded on the proposal, never retried in a loop.

## What each seat proposes

There is **no second strategy engine** here. The "reason" a seat cites is read
off state the office already keeps:

* its **goal** — the id of the task it is already assigned to, if any;
* its **memo** — the reason its last proposal was rejected (``office_task_proposal.reason``);
* the **org's backlog** — how many tasks are already open.

A seat with a standing goal proposes the next step for it; a seat whose last idea
was sent back proposes its correction; otherwise it proposes a bounded one-turn
tick of self-directed work. The grounding travels in the proposal's ``rationale``
so the level above can judge *why*, which is the whole point of the field.

## Memory (A5 seam — frozen)

The turn is composed with the seat's memories when ``agent_memory`` exists
(``recall(cfg, agent_id)``), and the turn's outcome is written back when both its
functions exist (``remember(...)``). When it is absent the tick degrades to an
empty context — it never fabricates a memory and never opens a store of its own
(C3: one memory authority). A4 calls that API; A5 owns it.
"""
from __future__ import annotations

import importlib
import json
import time
from typing import Any, Callable, Dict, List, Optional

from . import store
from . import work as W
from .errors import OfficeError

#: The config flag that turns the whole thing on. OFF by default (O3): autonomy
#: spends real inference money, so it is opt-in and a single flag governs it.
CONFIG_KEY = ("office", "autowork", "enabled")

#: The default cadence, in seconds. Fifteen minutes (O3) — deliberately short of
#: a human's attention span and long enough that a seat's turn has settled before
#: it is woken again.
DEFAULT_EVERY_S = 900

#: The most a single tick may propose/approve. One, like the turn, so a tick is
#: bounded work regardless of how large the queue has grown.
DEFAULT_MAX_APPROVALS = 1

#: Per-seat outcomes that are *not* a refusal — the tick simply did not use this
#: seat this pass, and says exactly why. Distinguishing these from a failure is
#: the difference between a quiet org and one that looks broken.
SKIP_FROZEN = "frozen_posture"
SKIP_TURN_BUDGET = "turn_budget"
SKIP_PROPOSAL_CAP = "proposal_cap"
SKIP_ORGAN_ASSIGNED = "assigned_to_the_org"
SKIP_TURN_LIMIT = "seat_already_used_this_tick"

#: The postures that may act unattended (C5, runner.py:180). ``supervised``
#: stages a confirmation and ``frozen`` refuses — neither is a wake.
ACTING_POSTURES = ("unattended_full", "unattended_readonly")

#: The deterministic fallback directives, keyed by seat type. They are the
#: honest default in the absence of a seat-specific directive from memory (L2):
#: they say what the role is for, not what to do in any particular turn.
SEAT_DIRECTIVES: Dict[str, str] = {
    "ceo": "the org's standing objective: earn its next dollar and keep every "
           "seat solvent. Review the open queue, the survival picture and the "
           "envelope, and take the single highest-impact action available to "
           "you — propose it if it needs the owner, do it if it does not.",
    "director_portfolio": "your portfolio's next dollar: the highest-value move "
                          "inside it, or the decision to sunset a business that "
                          "is bleeding.",
    "director_functional": "the function you own: the one change this turn that "
                           "most improves how the org operates.",
    "manager": "your team's throughput: unblock the work already in flight "
               "before starting anything new.",
    "worker": "the highest-impact task for your role. Do real work now; do not "
              "stop at a status update.",
    "analyst": "the highest-value question in the data you can actually reach. "
               "Answer it and say what you found.",
    "researcher": "the open question that most changes a decision the org is "
                  "about to make. Answer part of it with evidence.",
    "scraper": "the collection that feeds a live business. Run it and record "
               "what came back.",
    "support": "the open issue hurting a customer or blocking revenue. Move it "
               "forward one concrete step.",
    "auditor": "the control most likely to be failing right now. Sample it and "
               "record the finding, or state that it held.",
}

#: The one-line job kind the tick's outcome is memoized under (A5 seam).
MEMO_KIND = "autowork_tick"

_last_tick_memo: Dict[str, Any] = {"at": None, "date": None, "result": None}


# --------------------------------------------------------------- configuration
def _flag(cfg: Any) -> bool:
    """Is the driver enabled? **Off unless something says otherwise** (O3).

    The config key is the deployment default; a value persisted by
    :func:`enable` (the owner's runtime flip, from the CLI or the cron surface)
    wins over it. Both are read here, so the gate a tick consults is the same
    gate ``status`` and ``enable`` report — otherwise ``enable`` would silently
    do nothing, which is exactly the class of bug this module refuses elsewhere.
    """
    persisted = _load_state(cfg).get("enabled")
    if persisted is not None:
        return bool(persisted)
    try:
        return bool(cfg.get(*CONFIG_KEY, default=False))
    except Exception:                                   # noqa: BLE001
        return False


def _every_seconds(cfg: Any) -> int:
    """The tick cadence in seconds, from config, floored at one minute.

    The floor exists so a mistyped ``every_seconds: 1`` cannot turn a cron job
    into a busy loop. The flag is the dial; this is a guard rail, not a budget.
    """
    try:
        raw = cfg.get("office", "autowork", "every_seconds",
                      default=DEFAULT_EVERY_S)
        return max(60, int(raw))
    except Exception:                                   # noqa: BLE001
        return DEFAULT_EVERY_S


def _state_path(cfg: Any) -> Any:
    """Where the tick's own ``last_tick_at`` lives — beside the office store.

    One small JSON file in the office's own state directory. It is *cadence
    state*, not business state: no money, no work, no event. Keeping it out of
    the store is deliberate — the store is append-only and event-shaped, and a
    high-frequency timestamp churning in it would pollute the very table the
    tower animates from.
    """
    return store.db_path(cfg).parent / "autowork.json"


def _load_state(cfg: Any) -> Dict[str, Any]:
    path = _state_path(cfg)
    try:
        if path.exists():
            data = json.loads(path.read_text() or "{}")
            return data if isinstance(data, dict) else {}
    except Exception:                                   # noqa: BLE001
        pass
    return {}


def _save_state(cfg: Any, state: Dict[str, Any]) -> None:
    path = _state_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, sort_keys=True))
    except Exception:                                   # noqa: BLE001
        pass


def _due(state: Dict[str, Any], *, now: float, every_s: int) -> bool:
    """Has ``every_s`` elapsed since the persisted tick? Missing stamp = due."""
    last = state.get("last_tick_at")
    if last is None:
        return True
    try:
        return (now - float(last)) >= float(every_s)
    except (TypeError, ValueError):
        return True


# ------------------------------------------------------------------ ownership
def _installed(name: str) -> bool:
    """Is ``voicebridge.office.<name>`` importable? Used for the A5 seam."""
    try:
        importlib.import_module(f"{__package__}.{name}")
        return True
    except Exception:                                   # noqa: BLE001
        return False


def _recall(cfg: Any, agent_id: int) -> List[Dict[str, Any]]:
    """The seat's memories, through A5's API, or an empty list.

    A4 must not create a second memory store (C3). If ``agent_memory`` is not
    installed yet — A5 lands before A4 consumes it — the tick composes an empty
    context and carries on. An empty context is honest; an invented one is not.
    """
    if not _installed("agent_memory"):
        return []
    try:
        mod = importlib.import_module(f"{__package__}.agent_memory")
        out = mod.recall(cfg, int(agent_id))
        if isinstance(out, dict):
            items = out.get("memories") or out.get("items") or []
            return [m for m in items if isinstance(m, dict)][:5]
        if isinstance(out, list):
            return [m for m in out if isinstance(m, dict)][:5]
    except Exception:                                   # noqa: BLE001
        return []
    return []


def _remember(cfg: Any, agent_id: int, *, text: str,
              meta: Optional[Dict[str, Any]] = None) -> bool:
    """Write the turn's outcome back through A5's API, if it exists."""
    if not _installed("agent_memory"):
        return False
    try:
        mod = importlib.import_module(f"{__package__}.agent_memory")
        mod.remember(cfg, int(agent_id), kind=MEMO_KIND, text=str(text)[:1000],
                     meta=meta or {})
        return True
    except Exception:                                   # noqa: BLE001
        return False


# ------------------------------------------------------------------- the seats
def _live_seats(cfg: Any) -> List[Dict[str, Any]]:
    """Every seat that may be considered, oldest first, CEO last.

    Terminated seats are excluded here and nowhere else — the loop's single
    filter for "is there anyone to wake".
    """
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT id, slug, seat_type, autonomy, supervisor_id, department_id"
            " FROM office_agent WHERE status != 'terminated'"
            " ORDER BY CASE seat_type WHEN 'ceo' THEN 1 ELSE 0 END, id")]
    finally:
        conn.close()
    return rows


def _assigned_task(cfg: Any, *, agent_id: int) -> Optional[Dict[str, Any]]:
    """The seat's own in-flight task, if it has one — its standing goal."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute(
            "SELECT id, title FROM office_task WHERE assignee_id=?"
            " AND status IN ('open','running','fixing','review')"
            " ORDER BY id LIMIT 1", (int(agent_id),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def _rejected_reason(cfg: Any, *, agent_id: int) -> str:
    """Why this seat's most recent proposal was sent back, if it was.

    This is the seat's memo — the correction the level above asked for — and it
    is exactly the "learn from the refusal" loop the proposal layer exists for.
    """
    if not _installed("task_proposal"):
        return ""
    try:
        mod = importlib.import_module(f"{__package__}.task_proposal")
        out = mod.proposals(cfg, status="rejected", proposer_id=int(agent_id))
        rows = out.get("proposals") or []
    except Exception:                                   # noqa: BLE001
        return ""
    if not rows:
        return ""
    return str(rows[-1].get("reason") or "").strip()


def _pending_count(cfg: Any, *, agent_id: int) -> int:
    if not _installed("task_proposal"):
        return 0
    try:
        mod = importlib.import_module(f"{__package__}.task_proposal")
        return int(mod.pending(cfg, int(agent_id))["seat_pending"])
    except Exception:                                   # noqa: BLE001
        return 0


def _open_task_count(cfg: Any) -> int:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        return int(conn.execute(
            "SELECT COUNT(*) n FROM office_task WHERE status IN"
            " ('open','running','fixing','review')").fetchone()["n"])
    finally:
        conn.close()


def _is_leaf(cfg: Any, *, agent_id: int) -> bool:
    """Does this seat supervise nobody? The leaf pool ``dispatch`` assigns to.

    ``dispatch.pick_worker`` draws its pool with ``id NOT IN (SELECT supervisor_id
    ...)`` (dispatch.py:107-112), so a manager who is itself assigned work would
    be a manager reviewing below it while doing the work — the separation-of-duties
    failure the chain exists to prevent (C31). The tick uses the *same* test.
    """
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute(
            "SELECT COUNT(*) n FROM office_agent WHERE status='active'"
            " AND supervisor_id=?", (int(agent_id),)).fetchone()
        return int(row["n"]) == 0
    finally:
        conn.close()


def _envelope_covers(cfg: Any, *, agent_id: int,
                     cents: int = 1) -> Optional[bool]:
    """Can the kitty cover a one-cent turn? ``None`` = no envelope is set.

    ``None`` is not ``True``: the caller treats "no envelope" as the uncapped
    case and proceeds (the caps and balance still gate the actual turn). This is
    the driver's own check for the *tiered* manager-director case, where the turn
    runs inside the manager's own post outside ``dispatch`` — so the envelope
    must be consulted here rather than assumed to be checked downstream.
    """
    try:
        mod = importlib.import_module(f"{__package__}.envelope")
        out = mod.check_spend(cfg, agent_id=int(agent_id), cents=int(cents))
        return bool(out["allowed"])
    except Exception:                                   # noqa: BLE001
        return None


def _seat_context(cfg: Any, seat: Dict[str, Any]) -> Dict[str, Any]:
    """The seat's own context: goal, memo, org pressure, memories.

    Pure reads. The returned ``brief`` is what the seat's turn will be asked to
    act on; ``rationale`` is what its proposal will cite. Grounding travels with
    the proposal so the level above can judge *why* (L1's whole point).
    """
    agent_id = int(seat["id"])
    task = _assigned_task(cfg, agent_id=agent_id)
    memo = _rejected_reason(cfg, agent_id=agent_id)
    open_tasks = _open_task_count(cfg)
    memories = _recall(cfg, agent_id)

    directive = SEAT_DIRECTIVES.get(str(seat["seat_type"]),
                                    SEAT_DIRECTIVES["worker"])
    parts = [f"You are {seat['slug']} ({seat['seat_type']}).",
             f"Your standing directive: {directive}"]
    if task is not None:
        parts.append(f"Your standing goal is task #{task['id']}: {task['title']}.")
        reason = f"continue task #{task['id']} toward completion"
    else:
        parts.append("You have no task assigned right now.")
        reason = "no task is assigned; take the highest-impact action for your role"

    if memo:
        parts.append(f"Your last proposal was sent back with: {memo}")
        reason = f"correct what was sent back: {memo[:160]}"
    if open_tasks:
        parts.append(f"The org already has {open_tasks} open task(s) in flight.")
    if memories:
        lines = "; ".join(str(m.get("text") or m.get("summary") or "")[:200]
                          for m in memories if m)
        if lines:
            parts.append(f"What you remember: {lines}")
    parts.append("Act now. Do real work; do not stop at a status update.")

    return {"agent_id": agent_id, "slug": seat["slug"],
            "seat_type": seat["seat_type"], "autonomy": seat.get("autonomy"),
            "supervisor_id": seat.get("supervisor_id"),
            "department_id": seat.get("department_id"),
            "task": task, "memo": memo, "open_tasks": open_tasks,
            "memories": memories, "directive": directive,
            "brief": "\n".join(parts), "reason": reason}


def _one_line(value: Any) -> str:
    """First line of a reply, squeezed to a title-sized string."""
    first = str(value or "").strip().splitlines()
    return (first[0] if first else "").strip()[:120]


def _title_for(ctx: Dict[str, Any]) -> str:
    task = ctx.get("task")
    if task is not None:
        return f"Advance task #{task['id']}: {_one_line(task['title'])}"
    return f"Self-directed: {ctx['slug']}"


# ---------------------------------------------------------------- assessments
def assess(cfg: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Read-only: would a tick act, and for whom? No writes, no spend.

    This is the preview surface the owner reaches for before enabling the flag —
    it answers "what would the org do on its own" without doing any of it.
    """
    now = store.now() if now is None else float(now)
    state = _load_state(cfg)
    seats = _live_seats(cfg)
    would_act, skipped = [], []
    for seat in seats:
        agent_id = int(seat["id"])
        posture = seat.get("autonomy") or "unattended_full"
        if str(posture) not in ACTING_POSTURES:
            skipped.append({"agent_id": agent_id, "slug": seat["slug"],
                            "outcome": SKIP_FROZEN, "posture": posture})
            continue
        budget = W.turn_budget(cfg, agent_id=agent_id)
        if budget["turns_cap"] is not None and \
                int(budget["turns_used"]) >= int(budget["turns_cap"]):
            skipped.append({"agent_id": agent_id, "slug": seat["slug"],
                            "outcome": SKIP_TURN_BUDGET,
                            "detail": (f"{budget['turns_used']}/"
                                       f"{budget['turns_cap']} turns used")})
            continue
        would_act.append({"agent_id": agent_id, "slug": seat["slug"],
                          "seat_type": seat["seat_type"],
                          "directive": SEAT_DIRECTIVES.get(
                              str(seat["seat_type"]), SEAT_DIRECTIVES["worker"])})
    return {"state": "OK", "enabled": _flag(cfg),
            "every_seconds": _every_seconds(cfg),
            "due": _due(state, now=now, every_s=_every_seconds(cfg)),
            "last_tick_at": state.get("last_tick_at"), "now": now,
            "seats": len(seats), "would_act": would_act,
            "skipped": skipped}


# ------------------------------------------------------------------- approval
def _approve_pending(cfg: Any, *, limit: int,
                     requested_by: str = "office:autowork") -> Dict[str, Any]:
    """Drive the *existing* approve path for pending proposals.

    For a leaf or manager proposal, the level directly above approves it, and the
    task lands in the ordinary queue. For the CEO's proposal there is no seat
    above it (D2), so it is left pending for the owner — named, never
    silently dropped and never auto-approved.
    """
    if not _installed("task_proposal"):
        return {"approved": [], "left": [], "declined": []}
    mod = importlib.import_module(f"{__package__}.task_proposal")
    try:
        rows = mod.proposals(cfg, status="proposed").get("proposals") or []
    except Exception:                                   # noqa: BLE001
        return {"approved": [], "left": [], "declined": []}

    approved: List[Dict[str, Any]] = []
    left: List[Dict[str, Any]] = []
    declined: List[Dict[str, Any]] = []
    for row in rows:
        if len(approved) >= int(limit):
            break
        pid = int(row["id"])
        proposer_id = int(row["proposer_id"])
        try:
            level = mod.approve_level(cfg, proposer_id)
        except OfficeError as err:
            declined.append({"proposal_id": pid, "code": err.code,
                             "detail": err.detail[:200]})
            continue
        approver_id = level.get("approver_id")
        if approver_id is None:
            # the CEO's idea: the owner is the only approver and is not a seat.
            left.append({"proposal_id": pid, "proposer_id": proposer_id,
                         "outcome": "awaits_the_owner",
                         "reason": "the seat above the CEO is the owner, who is "
                                   "not an agent (D2)"})
            continue
        # the approver must be able to afford the work one level down, or the
        # approval would mint a task its pool cannot action.
        covers = _envelope_covers(cfg, agent_id=int(approver_id), cents=1)
        if covers is False:
            left.append({"proposal_id": pid, "approver_id": int(approver_id),
                         "outcome": SKIP_ORGAN_ASSIGNED,
                         "reason": "the approver's envelope cannot cover a turn"})
            continue
        try:
            out = mod.approve(cfg, pid, approver_id=int(approver_id),
                              confirmed=True, requested_by=requested_by)
            approved.append({"proposal_id": pid, "task_id": out["task_id"],
                             "approver_id": int(approver_id)})
        except OfficeError as err:
            declined.append({"proposal_id": pid, "code": err.code,
                             "detail": err.detail[:200]})
    return {"approved": approved, "left": left, "declined": declined}


def _adjudicate(cfg: Any, *, executor: Optional[Callable],
                model: Optional[str], unmetered: bool) -> Dict[str, Any]:
    """One review turn, if a task is waiting for one. Refusals are reported."""
    if not _installed("dispatch"):
        return {"state": "SKIPPED", "reason": "dispatch is not installed"}
    from . import dispatch as D
    try:
        return D.adjudicate(cfg, rounds=1, executor=executor, model=model,
                            unmetered=unmetered, confirmed=True,
                            requested_by="office:autowork")
    except OfficeError as err:
        return {"state": "REFUSED", "code": err.code, "detail": err.detail[:200]}


def _propose(cfg: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """File the seat's proposal through the existing layer. Raises on refusal."""
    mod = importlib.import_module(f"{__package__}.task_proposal")
    out = mod.propose(
        cfg, ctx["agent_id"], _title_for(ctx),
        brief=f"{ctx['brief']}\n\nRationale: {ctx['reason']}",
        exit_check=("true  # a one-turn self-directed tick completed on the "
                    "record; the level above decides whether it earned a "
                    "longer task"),
        rationale=(f"{ctx['reason']}. Seat {ctx['slug']} "
                   f"({ctx['seat_type']}) with {ctx['open_tasks']} open task(s) "
                   f"org-wide"
                   + (f"; last review said: {ctx['memo'][:160]}"
                      if ctx.get("memo") else "")),
        confirmed=True, requested_by="office:autowork")
    out["grounding"] = {"task_id": (ctx["task"] or {}).get("id"),
                        "memo": ctx.get("memo", ""),
                        "open_tasks": ctx["open_tasks"],
                        "memories": len(ctx.get("memories") or [])}
    return out


def _run_seat_turn(cfg: Any, ctx: Dict[str, Any], *, executor: Optional[Callable],
                   model: Optional[str], unmetered: bool,
                   department_id: Optional[int]) -> Dict[str, Any]:
    """Spend one turn and run one real attributed turn for this seat.

    The budget is **consumed only after** every refusal that precedes a claim,
    exactly as ``dispatch`` does: a turn that never runs must not be charged.
    """
    from . import runner as R

    budget = W.spend_turn(cfg, agent_id=ctx["agent_id"])
    if not budget["allowed"]:
        return {"outcome": SKIP_TURN_BUDGET, "code": budget.get("code"),
                "detail": budget.get("detail", "")}
    try:
        return R.run_turn(cfg, ctx["agent_id"], ctx["brief"], executor=executor,
                          model=model, unmetered=unmetered,
                          department_id=department_id)
    except OfficeError as err:
        # The turn did not happen — e.g. the envelope refused it
        # (OFFICE_ENVELOPE_EXCEEDED) or admission did. Report it; the budget
        # charge stands only if the turn actually began, and run_turn raises
        # before the first model call in every envelope/admission case, so the
        # charge is returned below.
        return {"outcome": "refused", "code": err.code,
                "detail": err.detail[:200]}


# ------------------------------------------------------------------- the tick
def tick(cfg: Any, *, executor: Optional[Callable] = None,
         model: Optional[str] = None, unmetered: bool = True,
         now: Optional[float] = None, enabled: Optional[bool] = None,
         max_approvals: int = DEFAULT_MAX_APPROVALS,
         every_s: Optional[int] = None,
         requested_by: str = "office:autowork") -> Dict[str, Any]:
    """One cron-shaped pass. Runs, does bounded work, returns. Never loops.

    ``enabled`` overrides the config flag for one call (the CLI's manual
    trigger); when it is ``False`` the tick is a genuine no-op — nothing is read
    from the store and nothing is written. ``now`` freezes the clock so the
    cadence is provable without a wall-clock wait.
    """
    now = store.now() if now is None else float(now)
    is_enabled = _flag(cfg) if enabled is None else bool(enabled)
    if not is_enabled:
        return {"state": "OK", "enabled": False, "ran": [], "proposed": [],
                "approved": [], "skipped_seats": [], "skipped_proposals": [],
                "declined": [], "at": now,
                "meaning": ("the autonomous driver is off by default "
                            "(office.autowork.enabled); nothing was read or "
                            "written")}

    # 1 — the e-stop is the first gate, as in run_turn (runner.py:173).
    if store.estop_engaged(cfg):
        return {"state": "HALTED", "enabled": True, "ran": [], "proposed": [],
                "approved": [], "skipped_seats": [], "skipped_proposals": [],
                "declined": [], "at": now,
                "meaning": "the emergency stop is latched; no seat was woken"}

    # 2 — cadence. The last tick is persisted, so a restart cannot replay a tick.
    every = int(every_s if every_s is not None else _every_seconds(cfg))
    state = _load_state(cfg)
    if not _due(state, now=now, every_s=every):
        return {"state": "OK", "enabled": True, "skipped": "not_due",
                "every_seconds": every, "last_tick_at": state.get("last_tick_at"),
                "at": now, "ran": [], "proposed": [], "approved": [],
                "skipped_seats": [], "skipped_proposals": [], "declined": [],
                "meaning": (f"the cadence is {every}s and the last tick was "
                            f"{state.get('last_tick_at')}")}

    # Stamp the tick up-front: the cadence is a fact about the tick, and a tick
    # that acts partially must still not re-fire a second later.
    state["last_tick_at"] = now
    state["last_tick_date"] = time.strftime("%Y-%m-%d", time.localtime(now))
    _save_state(cfg, state)

    ran: List[Dict[str, Any]] = []
    proposed: List[Dict[str, Any]] = []
    skipped_seats: List[Dict[str, Any]] = []
    declined: List[Dict[str, Any]] = []

    # 3 — review any finished task, then take pending proposals one level up.
    # The approval pass runs AFTER the seat loop (below), so a proposal floated
    # by *this* tick can be taken by its level above in the same pass — the
    # per-tick approval cap keeps that bounded.
    adjudication = _adjudicate(cfg, executor=executor, model=model,
                               unmetered=unmetered)

    # 4 — one pass over the seats. AT MOST ONE turn per seat (O3).
    for seat in _live_seats(cfg):
        agent_id = int(seat["id"])
        posture = str(seat.get("autonomy") or "unattended_full")
        if posture not in ACTING_POSTURES:
            # A frozen seat is never woken; a supervised one stages a
            # confirmation rather than acting. Both are skips, not failures.
            skipped_seats.append({"agent_id": agent_id, "slug": seat["slug"],
                                  "outcome": SKIP_FROZEN, "posture": posture})
            continue

        ctx = _seat_context(cfg, seat)

        # 4a — the ratchet: the seat files an idea from its own context.
        proposal: Optional[Dict[str, Any]] = None
        try:
            proposal = _propose(cfg, ctx)
            proposed.append({"agent_id": agent_id, "slug": seat["slug"],
                             "proposal_id": proposal["id"],
                             "grounding": proposal["grounding"]})
        except OfficeError as err:
            declined.append({"agent_id": agent_id, "slug": seat["slug"],
                             "stage": "propose", "code": err.code,
                             "detail": err.detail[:200]})

        # 4b — whose turn is it? A *leaf* seat (one that supervises nobody) is
        # left to the org: `dispatch.pick_worker` assigns leaf seats only, so it
        # is the org that owns a manager's idea. A manager/director/CEO seat
        # supervises somebody, so its own self-directed work runs here, once.
        if _installed("org") and _is_leaf(cfg, agent_id=agent_id):
            skipped_seats.append({"agent_id": agent_id, "slug": seat["slug"],
                                  "outcome": SKIP_ORGAN_ASSIGNED,
                                  "reason": "the org owns a leaf's idea: "
                                            "dispatch assigns it"})
            continue

        # 4c — budget, read before anything is charged (O3: <=1 turn/seat/tick).
        budget = W.turn_budget(cfg, agent_id=agent_id)
        if budget["turns_cap"] is not None and \
                int(budget["turns_used"]) >= int(budget["turns_cap"]):
            skipped_seats.append({"agent_id": agent_id, "slug": seat["slug"],
                                  "outcome": SKIP_TURN_BUDGET,
                                  "detail": (f"{budget['turns_used']}/"
                                             f"{budget['turns_cap']} turns used")})
            continue

        # 4d — the kitty. A seat with no money proposes (free) but does not
        # spend; that is the honest refusal, and it is reported as such.
        covers = _envelope_covers(cfg, agent_id=agent_id, cents=1)
        if covers is False:
            skipped_seats.append({"agent_id": agent_id, "slug": seat["slug"],
                                  "outcome": SKIP_ORGAN_ASSIGNED,
                                  "detail": "the envelope cannot cover a turn"})
            continue

        # 4e — one real attributed turn, through the existing runner.
        result = _run_seat_turn(cfg, ctx, executor=executor, model=model,
                                unmetered=unmetered,
                                department_id=seat.get("department_id"))
        outcome = result.get("outcome") or result.get("state")
        ran.append({"agent_id": agent_id, "slug": seat["slug"],
                    "outcome": outcome, "run_id": result.get("run_id"),
                    "done": result.get("done"), "cents": result.get("cents"),
                    "code": result.get("code"),
                    "detail": result.get("detail")})
        _remember(cfg, agent_id,
                  text=(f"tick {time.strftime('%Y-%m-%dT%H:%M', time.localtime(now))}: "
                        f"ran a self-directed turn ({outcome}); "
                        f"{result.get('detail') or result.get('reply') or ''}"
                        )[:800],
                  meta={"run_id": result.get("run_id"), "outcome": str(outcome),
                        "state": result.get("state")})

    _last_tick_memo.update({"at": now, "result": None})

    # 5 — the ratchet, one level up. Run after the seat loop so this tick's own
    # proposals are visible to their approvers; bounded by `max_approvals`.
    approvals = _approve_pending(cfg, limit=int(max_approvals),
                                 requested_by=requested_by)

    summary = {
        "state": "OK", "enabled": True, "at": now, "every_seconds": every,
        "seats": len(_live_seats(cfg)),
        "adjudication": adjudication,
        "approvals": {"approved": approvals["approved"],
                      "left": approvals["left"],
                      "declined": approvals["declined"]},
        "proposed": proposed, "ran": ran,
        "skipped_seats": skipped_seats, "declined": declined,
        "meaning": ("a tick that did nothing wrote nothing: every row above is "
                    "an outcome, and an empty list is an idle org, not a "
                    "missing one"),
    }
    # A quiet org says WHY it was quiet, but only when it is quiet — otherwise a
    # note every 15 minutes would be noise the office already has enough of.
    if not proposed and not ran and not approvals["approved"]:
        summary["quiet"] = True
    return summary


def enable(cfg: Any, *, enabled: bool = True, requested_by: str = "cli:owner"
           ) -> Dict[str, Any]:
    """Persist the single flag that turns the driver on or off.

    Status, not creation: this is the flag the merge's corrected spec
    (``90-CORRECTIONS.md`` §C7 / O3) says must exist and default to ``false``.
    """
    data = _load_state(cfg)
    data["enabled"] = bool(enabled)
    _save_state(cfg, data)
    store.audit("autowork.set", requested_by, {"enabled": bool(enabled)})
    return {"state": "OK", "enabled": bool(enabled),
            "config_key": ".".join(CONFIG_KEY),
            "every_seconds": _every_seconds(cfg),
            "meaning": ("the autonomous driver will run on the next cron tick"
                        if enabled else
                        "the autonomous driver is off; no seat will be woken")}


def status(cfg: Any) -> Dict[str, Any]:
    """The driver's config, cadence and last tick — one read, no writes."""
    state = _load_state(cfg)
    every = _every_seconds(cfg)
    return {"state": "OK", "enabled": _flag(cfg),
            "config_key": ".".join(CONFIG_KEY),
            "every_seconds": every,
            "last_tick_at": state.get("last_tick_at"),
            "last_tick_date": state.get("last_tick_date"),
            "due_in": (None if state.get("last_tick_at") is None else
                       max(0.0, every - (store.now()
                                         - float(state["last_tick_at"])))),
            "postures": list(ACTING_POSTURES),
            "max_approvals_per_tick": DEFAULT_MAX_APPROVALS}


def run_now(cfg: Any, *, executor: Optional[Callable] = None,
            model: Optional[str] = None, unmetered: bool = True,
            now: Optional[float] = None,
            requested_by: str = "cli:owner") -> Dict[str, Any]:
    """Run one tick immediately, regardless of the flag or the cadence.

    This is the owner's manual trigger and the test's entry point: it is the
    *same* tick, with the gate held open for one call. It still honours the
    e-stop — holding the gate open is not the same as disarming the stop.
    """
    return tick(cfg, executor=executor, model=model, unmetered=unmetered,
                now=now, enabled=True, every_s=0, requested_by=requested_by)


__all__ = [
    "CONFIG_KEY", "DEFAULT_EVERY_S", "ACTING_POSTURES", "SEAT_DIRECTIVES",
    "SKIP_FROZEN", "SKIP_TURN_BUDGET", "SKIP_PROPOSAL_CAP",
    "SKIP_ORGAN_ASSIGNED", "SKIP_TURN_LIMIT",
    "tick", "run_now", "assess", "enable", "status",
]
