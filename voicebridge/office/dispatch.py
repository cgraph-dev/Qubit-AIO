"""The dispatcher: hands out work, runs the turns, and routes it for review.

This is the loop that makes nine seats worth more than one. It does four things
per task, in this order, and stops at the first refusal:

```
pick an open task  →  claim it for a seat  →  spend one turn of the budget
                   →  run the turn  →  check the exit condition  →  review
```

Three properties that matter more than throughput:

* **A task is only `done` if its `exit_check` passes.** The check is a shell
  command supplied by whoever created the task, run against real state; the
  model's own reply is never the verdict (C17). If the check fails, the task
  stays open and the trail records it — no silent success.
* **Every turn is bounded twice.** By the seat's daily turn budget (the honest
  bound on a flat-rate provider, M5) and by the ledger's admission path. A seat
  that has spent its turns stops, and the dispatcher moves on rather than
  spinning.
* **A reviewer is a real turn with a machine-readable verdict.** The reply must
  carry `VERDICT: accept|reject` and, on reject, `NOTE: <what to change>`.
  A reply without a parseable verdict leaves the task in `review` and records
  that the reviewer did not decide — never an assumed accept.

`executor` is the same seam the runner uses: production passes nothing and the
seat's real turn runs against the provider; a test injects a stub so the whole
loop is provable without spending anything.
"""
from __future__ import annotations

import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional

from . import store
from . import work as W
from .errors import BAD_INPUT_CODE, BAD_STATE_CODE, NOT_FOUND_CODE, OfficeError

EXIT_CHECK_TIMEOUT_S = 120
VERDICT_RE = re.compile(r"^\s*VERDICT:\s*(accept|reject)\s*$", re.I | re.M)
NOTE_RE = re.compile(r"^\s*NOTE:\s*(.+?)\s*$", re.I | re.M)


def shell_checker(command: str, *, cwd: Optional[str] = None,
                  timeout: int = EXIT_CHECK_TIMEOUT_S) -> Callable[[Dict[str, Any]], bool]:
    """Turn a task's `exit_check` into the runner's checker (C17).

    The command is the *creator's* — the owner writes it when opening the task —
    so this runs what the owner asked to be checked, not what the agent claims.
    """
    def _check(_result: Dict[str, Any]) -> bool:
        try:
            done = subprocess.run(command, shell=True, cwd=cwd, timeout=timeout,
                                  capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            return False
        except Exception:                                  # noqa: BLE001
            return False
        return done.returncode == 0
    return _check


def _load(conn) -> Dict[int, int]:
    """How many open tasks each seat is already carrying, so work spreads."""
    rows = conn.execute(
        "SELECT assignee_id, COUNT(*) n FROM office_task"
        " WHERE status IN ('running','fixing','review') AND assignee_id IS NOT NULL"
        " GROUP BY assignee_id")
    return {int(r["assignee_id"]): int(r["n"]) for r in rows}


def pick_worker(cfg: Any, *, task: Dict[str, Any],
                pool: Optional[List[int]] = None,
                extra_load: Optional[Dict[int, int]] = None) -> Optional[int]:
    """The least-loaded seat that may take this task.

    A `fixing` task goes back to the seat that has it: the fix instruction is
    addressed to whoever wrote the thing, and handing it to a fresh seat throws
    away the context that made the instruction meaningful.
    """
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        # Continuity: a task already owned by a seat goes back to that seat. A
        # fix instruction is addressed to whoever wrote the thing, and handing
        # in-progress work to a fresh seat throws away the context that made the
        # instruction meaningful.
        if task["status"] in ("fixing", "running") and \
                task["assignee_id"] is not None:
            return int(task["assignee_id"])
        load = _load(conn)
        if extra_load:
            # a preview must simulate the same spread a real run produces, or it
            # shows every task landing on the same seat and teaches the wrong
            # thing about the queue
            for key, value in extra_load.items():
                load[int(key)] = load.get(int(key), 0) + int(value)
        if pool:
            candidates = [int(a) for a in pool]
        else:
            # Leaf seats only. A seat that supervises somebody is a reviewer, and
            # a manager that also does the work is a manager reviewing its own
            # work — the one thing separation of duties exists to prevent (C31).
            dept = task["department_id"]
            sql = ("SELECT id FROM office_agent WHERE status='active'"
                   " AND seat_type IN ('worker','analyst','researcher','support',"
                   " 'scraper')"
                   " AND id NOT IN (SELECT supervisor_id FROM office_agent"
                   "                WHERE status='active'"
                   "                  AND supervisor_id IS NOT NULL)")
            args: List[Any] = []
            if dept is not None:
                sql += " AND (department_id=? OR department_id IS NULL)"
                args.append(int(dept))
            candidates = [int(r["id"]) for r in conn.execute(sql + " ORDER BY id",
                                                             args)]
        if not candidates:
            return None
        # stable: lowest load, then lowest id, so a run is reproducible
        candidates.sort(key=lambda a: (load.get(a, 0), a))
        return candidates[0]
    finally:
        conn.close()


def dispatch(cfg: Any, *, rounds: int = 1, pool: Optional[List[int]] = None,
             executor: Optional[Callable] = None,
             cwd: Optional[str] = None, model: Optional[str] = None,
             unmetered: bool = True, dry_run: bool = False,
             confirmed: bool = False,
             requested_by: str = "cli:owner") -> Dict[str, Any]:
    """Run up to `rounds` worker turns across the open queue.

    `unmetered=True` by default: on a subscription-backed gateway a turn costs no
    marginal dollar, so recording it as one would manufacture phantom spend that
    later denies real work (D16). The dollar caps still apply if it is set false.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office dispatch requires --confirm")
    from . import runner as R

    rounds = max(1, min(int(rounds), 200))
    results: List[Dict[str, Any]] = []
    # A task once handled in this run is not picked again, even if its status did
    # not move. Without this a preview loops on task #1 `rounds` times and a task
    # that refuses to leave `open` starves every task behind it.
    seen: List[int] = []
    sim_load: Dict[int, int] = {}   # dry-run only: who a preview would consume

    for _ in range(rounds):
        conn = store.connect(cfg)
        try:
            store.migrate(conn)
            # `running` is included on purpose: a task whose turn was refused or
            # whose exit check failed is still in progress, and leaving it out
            # strands it forever — nothing would ever pick it up again.
            sql = ("SELECT * FROM office_task"
                   " WHERE status IN ('open','fixing','running')")
            if seen:            # `NOT IN (NULL)` is NULL, so it excludes EVERY
                                # row — the clause must be absent, not empty
                sql += " AND id NOT IN (%s)" % ",".join("?" * len(seen))
            row = conn.execute(sql + " ORDER BY id LIMIT 1", tuple(seen)).fetchone()
        finally:
            conn.close()
        if row is None:
            break
        task = dict(row)
        seen.append(int(task["id"]))

        worker = pick_worker(cfg, task=task, pool=pool, extra_load=sim_load)
        if worker is None:
            results.append({"task": task["id"], "state": "NO_SEAT",
                            "detail": "no active seat may take this task"})
            break

        # ---- verify before spending ------------------------------------------
        # Check the exit condition BEFORE the turn budget is touched. If the work
        # is already done — a previous attempt got there, or the owner finished it
        # by hand — then paying a model (or a turn) to rediscover that is the most
        # expensive possible way to read a file. This is the one place a task can
        # complete without spending anything, and it is the honest kind of free:
        # a check that passed, not a charge that was skipped.
        pre = shell_checker(task["exit_check"], cwd=cwd)
        if not dry_run and pre({}):
            try:
                W.claim(cfg, task["id"], agent_id=worker, confirmed=True,
                        requested_by=requested_by)
            except OfficeError as err:
                W._trail(cfg, task["id"], actor="office:dispatch",
                         kind="claim_failed",
                         body=f"{err.code}: {err.detail[:200]}")
                results.append({"task": task["id"], "state": "REFUSED",
                                "worker": worker, "code": err.code,
                                "detail": err.detail[:200]})
                continue
            W.record_turn(cfg, task["id"], outcome="satisfied",
                          payload={"prechecked": True, "turns_spent": 0})
            results.append({"task": task["id"], "state": "OK", "worker": worker,
                            "satisfied": True, "cents": 0, "turns_spent": 0,
                            "detail": ("the exit check already passed, so no turn "
                                       "was spent; the task went straight to "
                                       "review")})
            continue

        # The budget is checked *before* the claim, so a seat out of turns never
        # ends up holding work it cannot do — and a dry run only *reads* the
        # budget. A preview that consumed turns would make the preview the most
        # expensive way to find out what a run would do.
        # The budget is READ here and CONSUMED after a successful claim. Consuming
        # it first was a real bug: a claim that failed still cost the seat a turn,
        # so a seat could spend its whole day on tasks it never got.
        peek = W.turn_budget(cfg, agent_id=worker)
        exhausted = (peek["turns_cap"] is not None and
                     int(peek["turns_used"]) >= int(peek["turns_cap"]))
        if dry_run:
            if exhausted:
                results.append({"task": task["id"], "state": "BUDGET_EXHAUSTED",
                                "code": "OFFICE_TURN_BUDGET_EXHAUSTED",
                                "detail": (f"seat #{worker} would be refused: "
                                           f"{peek['turns_used']}/"
                                           f"{peek['turns_cap']} turns used")})
                break
            sim_load[int(worker)] = sim_load.get(int(worker), 0) + 1
            results.append({"task": task["id"], "state": "preview",
                            "worker": worker, "title": task["title"],
                            "exit_check": task["exit_check"],
                            "turns_used": peek["turns_used"],
                            "turns_cap": peek["turns_cap"]})
            continue
        if exhausted:
            results.append({"task": task["id"], "state": "BUDGET_EXHAUSTED",
                            "code": "OFFICE_TURN_BUDGET_EXHAUSTED",
                            "detail": (f"seat #{worker} has used "
                                       f"{peek['turns_used']}/{peek['turns_cap']} "
                                       f"turns today")})
            break


        try:
            W.claim(cfg, task["id"], agent_id=worker, confirmed=True,
                    requested_by=requested_by)
        except OfficeError as err:
            # A task that cannot be claimed must not abort the run: the queue
            # behind it still deserves its turn. Record it and move on.
            W._trail(cfg, task["id"], actor="office:dispatch", kind="claim_failed",
                     body=f"{err.code}: {err.detail[:200]}")
            results.append({"task": task["id"], "state": "REFUSED",
                            "worker": worker, "code": err.code,
                            "detail": err.detail[:200]})
            continue

        budget = W.spend_turn(cfg, agent_id=worker)
        if not budget["allowed"]:            # cannot happen after the peek
            results.append({"task": task["id"], "state": "BUDGET_EXHAUSTED",
                            "code": budget["code"], "detail": budget["detail"]})
            break

        brief = (f"{task['title']}\n\n{task['brief']}\n\n"
                 f"Completion is checked by running:\n  {task['exit_check']}\n"
                 f"The work is not finished until that command succeeds.")
        if task["status"] == "fixing" and task["review_note"]:
            brief += ("\n\nThe reviewer sent this back with:\n"
                      f"  {task['review_note']}\nAddress exactly that.")

        checker = shell_checker(task["exit_check"], cwd=cwd)
        try:
            run = R.run_turn(cfg, worker, brief, executor=executor,
                             checker=checker, model=model,
                             department_id=task["department_id"],
                             unmetered=unmetered)
        except OfficeError as err:
            # The turn did not happen, so the task is not in progress: put it
            # back in the queue rather than leaving it claimed by a seat that
            # never ran it.
            W.record_turn(cfg, task["id"], outcome=f"refused:{err.code}")
            W.requeue(cfg, task["id"], reason=f"{err.code}: {err.detail[:200]}")
            results.append({"task": task["id"], "state": "REFUSED",
                            "worker": worker, "code": err.code,
                            "detail": err.detail[:200]})
            continue

        satisfied = bool(run.get("done"))
        W.record_turn(cfg, task["id"], run_id=run.get("run_id", ""),
                      outcome="satisfied" if satisfied else "not_satisfied",
                      payload={"cents": run.get("cents"),
                               "state": run.get("state"),
                               "turns_in_run": 1})
        results.append({
            "task": task["id"], "state": "OK" if satisfied else "INCOMPLETE",
            "worker": worker, "run_id": run.get("run_id"),
            "run_state": run.get("state"), "satisfied": satisfied,
            "cents": run.get("cents"),
            "detail": ("the exit check passed" if satisfied else
                       "the exit check did NOT pass, so the task is not done"),
        })
    return {"state": "OK", "rounds": rounds, "results": results,
            "ran": len([r for r in results if r.get("state") == "OK"]),
            "unmetered": bool(unmetered)}


def adjudicate(cfg: Any, *, rounds: int = 1, executor: Optional[Callable] = None,
               model: Optional[str] = None, unmetered: bool = True,
               dry_run: bool = False, confirmed: bool = False,
               requested_by: str = "cli:owner") -> Dict[str, Any]:
    """Give each task in `review` to its reviewer as one real turn.

    The reviewer must answer with a parseable verdict. An unparseable reply is
    recorded as *no decision* and the task stays in review — the alternative,
    treating silence as acceptance, is how nine agents produce a green board and
    nothing that works.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office adjudicate requires --confirm")
    from . import runner as R

    rounds = max(1, min(int(rounds), 200))
    results: List[Dict[str, Any]] = []
    # `seen` prevents the bug this loop already had once: the query returns the
    # OLDEST task in `review`, so a task that cannot be judged (its reviewer is
    # unfunded, or gave no verdict) is picked again every round and the run never
    # reaches the tasks behind it. `dispatch` grew this list for the same reason.
    # A task that does not leave `review` this round is not retried in this run.
    seen: List[int] = []
    for _ in range(rounds):
        conn = store.connect(cfg)
        try:
            store.migrate(conn)
            sql = "SELECT * FROM office_task WHERE status='review'"
            if seen:            # `NOT IN (NULL)` is NULL, so it excludes EVERY row
                sql += " AND id NOT IN (%s)" % ",".join("?" * len(seen))
            row = conn.execute(sql + " ORDER BY id LIMIT 1",
                               tuple(seen)).fetchone()
        finally:
            conn.close()
        if row is None:
            break
        task = dict(row)
        seen.append(int(task["id"]))
        reviewer = task["reviewer_id"]
        if reviewer is None:
            results.append({"task": task["id"], "state": "NO_REVIEWER"})
            break

        budget = W.spend_turn(cfg, agent_id=int(reviewer))
        if not budget["allowed"]:
            results.append({"task": task["id"], "state": "BUDGET_EXHAUSTED",
                            "detail": budget["detail"]})
            break
        if dry_run:
            results.append({"task": task["id"], "state": "preview",
                            "reviewer": reviewer})
            continue

        brief = (
            f"You are reviewing task #{task['id']}: {task['title']}\n\n"
            f"The work claims to satisfy this exit condition:\n"
            f"  {task['exit_check']}\n\n"
            f"Its artifact: {task['artifact'] or '(none recorded)'}\n\n"
            f"Reply with exactly two lines:\n"
            f"  VERDICT: accept    (or)    VERDICT: reject\n"
            f"  NOTE: <if reject, the specific change required; if accept, why it "
            f"meets the condition>")
        # A review turn is a real turn and goes through the SAME admission gate as
        # a work turn, so it can be refused for the same reasons (an unfunded
        # reviewer, D1). Without this catch the refusal escaped `adjudicate` as a
        # raw OfficeError and aborted every remaining round -- the first
        # unfunded reviewer would take the whole adjudication down with it,
        # including tasks whose reviewers were perfectly solvent. `dispatch`
        # already handles this at its own `run_turn` call; this matches it.
        #
        # The task is deliberately LEFT IN `review` (not requeued): a review that
        # could not run has not been rejected, and moving it back to `open` would
        # throw away the work waiting to be judged. It stays until its reviewer
        # can afford to judge it.
        try:
            run = R.run_turn(cfg, int(reviewer), brief, executor=executor,
                             model=model, unmetered=unmetered)
        except OfficeError as err:
            W._trail(cfg, task["id"], actor=f"seat:{reviewer}",
                     kind="review_refused",
                     body=f"{err.code}: {err.detail[:200]}")
            results.append({"task": task["id"], "state": "REFUSED",
                            "reviewer": int(reviewer), "code": err.code,
                            "detail": err.detail[:200]})
            continue
        reply = str(run.get("reply") or "")
        verdict = VERDICT_RE.search(reply)
        note = NOTE_RE.search(reply)
        if verdict is None:
            W._trail(cfg, task["id"], actor=f"seat:{reviewer}",
                     kind="no_verdict",
                     body="the reviewer gave no parseable verdict; the task "
                          "stays in review")
            results.append({"task": task["id"], "state": "NO_VERDICT",
                            "reviewer": int(reviewer),
                            "detail": "the reply carried no `VERDICT:` line, so "
                                      "nothing was accepted"})
            continue
        decision = verdict.group(1).lower()
        W.review(cfg, task["id"],
                 verdict="accept" if decision == "accept" else "reject",
                 note=(note.group(1) if note else ""), by_agent=int(reviewer),
                 confirmed=True, requested_by=requested_by)
        results.append({"task": task["id"], "state": "OK", "verdict": decision,
                        "reviewer": int(reviewer),
                        "note": note.group(1) if note else ""})
    return {"state": "OK", "rounds": rounds, "results": results}
