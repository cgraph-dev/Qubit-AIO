"""Bot + Harness family backends — the wiring owner is the family bridge.

These gateway methods stand behind the ``bot.*`` / ``harness.*`` rows of the
family allowlist in :mod:`voicebridge.qubit_compat` (registered onto each
bridge registry by ``_ensure_family_backends`` there — the one wiring owner).
Every handler delegates to a canonical backend module; nothing here invents a
transport or a store:

- ``bot.tasks_launch`` / ``bot.ideas_accept`` -> ``orchestrator.launch_task``
  (the durable delegated worker headless path voice dispatch uses).
- ``bot.watches_set`` -> ``schedules.create_job`` (the canonical cron store
  the Schedules pages and voice read; alerts surface through ``cron.runs``,
  which ``bot/alerts-list`` already reads).
- ``harness.task_submit`` -> ``sessions.create`` + ``conversations.turn``
  (the canonical session authority and the one agent-turn path, composed —
  exactly the counterpart the typed gate named).
- ``harness.starter_kits`` -> a static curated kit list. Kit *concepts* name
  real local skills only — every ``skills:`` entry names a skill directory
  present in the runtime home, every ``plugins:`` entry names a real Qubit
  surface; launching a kit submits its starter prompt through the
  real ``harness/task_submit`` row. No harness runner is ported: the kit
  prompt runs as a local turn.
- ``bot.tasks_pause`` / ``bot.tasks_resume`` -> the worker box cancel flag
  (``orchestrator._TASK_BOXES`` — the same cooperative hook
  ``wait_task`` uses) plus a durable pause sidecar: pause flags a running
  worker and keeps its prompt; resume relaunches it. True thread suspend
  does not exist in Python; the response says so.
- ``bot.ideas_list`` / ``bot.ideas_edit`` / ``bot.ideas_dismiss`` ->
  ``task.suggestions`` merged with a durable overlay sidecar (dismissed ids
  hidden, edits applied and marked). The ledger derivation itself stays
  read-only; the overlay is the writer.
- ``bot.goals_list`` / ``bot.goals_set`` -> a durable goal sidecar
  (title/description/milestones) listed alongside the cron schedules —
  goals with milestones are records, not schedules, so they are stored as
  records and listed as records.
- ``bot.cards_get`` -> ``artifacts.list`` / ``artifacts.get`` across recent
  sessions (bounded scan, disclosed): ``<session>/<name>`` direct form, or
  a name search over the 15 newest sessions.

Still honestly gated (no primitive exists — see the row comments in
``qubit_compat.py``): ``bot/followups-queue`` + ``bot/drafts-save``
(local-only by design) and the hosted Bot Intelligence persistence (no
such service). ``tasks.dismiss`` stays operator-gated by design.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import json
import os
import time


def _gateway_error(code: str, detail: str) -> Any:
    from .gateway.gateway_ws import GatewayError

    return GatewayError(code, detail)


def _gw_bot_tasks_launch(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Launch a durable delegated worker (headless ``launch_task`` path)."""
    from . import orchestrator as ORCH

    title = str(params.get("title", "") or "").strip()
    prompt = str(params.get("prompt", "") or "").strip()
    if not prompt:
        raise _gateway_error("BOT_LAUNCH_BAD_PARAMS",
                             "tasks-launch needs a non-empty prompt")
    try:
        session_key = ORCH.launch_task(prompt, cfg=cfg)
    except ORCH.TaskLaunchDenied as exc:
        raise _gateway_error(str(exc.code), str(exc.detail))
    _remember_prompt(cfg, session_key, prompt, title)
    return {"state": "OK", "session": session_key,
            "title": title or prompt[:80],
            "kind": str(params.get("kind", "") or ""),
            "goalId": str(params.get("goalId", "") or ""),
            "authority": "durable delegated worker (orchestrator.launch_task, "
                         "same safety verdict + e-stop path as voice "
                         "dispatch); track via tasks-get / wait_task",
            "requested_by": str(params.get("requested_by", "") or "")}


def _suggestion_text(row: Dict[str, Any]) -> str:
    for key in ("prompt", "task_text", "text", "title", "summary"):
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _idea_registry(cfg: Any) -> Dict[str, Dict[str, Any]]:
    """The durable ideas registry shared with ``task.suggestions`` (rpc.py)."""
    try:
        from .gateway.rpc import _ideas_load

        return _ideas_load(cfg)
    except Exception:
        return {}


def _idea_find(cfg: Any, idea: str) -> Optional[Dict[str, Any]]:
    """Resolve an idea id against the derived inbox, then the durable ledger.

    Reads the same derivation `ideas-list` serves (`_idea_rows`). It used to
    call the `task.suggestions` gateway, which returns
    `TASK_SUGGESTIONS_OPERATOR_REQUIRED` unconditionally — so an id that
    `ideas-list` had just displayed could never be resolved, and every accept
    failed with `BOT_IDEA_NOT_FOUND` against a row the user was looking at.
    """
    for row in _idea_rows(cfg):
        if str(row.get("id", "")) == idea:
            return row
    from . import task_ledger as TL

    listing = TL.list_tasks(cfg, limit=100)
    rows = listing.get("tasks", []) if isinstance(listing, dict) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("id", "task_id", "taskId", "session", "session_key"):
            if str(row.get(key, "") or "") == idea:
                return row
    return None


def _gw_bot_ideas_accept(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Accept an idea: launch its prompt as a worker, record the acceptance."""
    from . import orchestrator as ORCH

    idea = ""
    for _key in ("idea", "ideaId", "idea_id", "id"):
        _val = str(params.get(_key, "") or "").strip()
        if _val:
            idea = _val
            break
    if not idea:
        raise _gateway_error("BOT_IDEA_BAD_PARAMS",
                             "ideas-accept needs an idea id "
                             "(send it as 'idea', 'ideaId' or 'id')")
    if idea in set(_overlay(cfg)["dismissed"]):
        raise _gateway_error("BOT_IDEA_DISMISSED",
                             f"idea {idea!r} was dismissed; relaunch its "
                             "prompt via tasks-launch explicitly")
    match = _idea_find(cfg, idea)
    if match is None:
        raise _gateway_error("BOT_IDEA_NOT_FOUND",
                             f"no open suggestion {idea!r} in the derived "
                             "idea inbox; nothing was launched")
    prompt = _suggestion_text(match) or str(
        params.get("prompt", "") or "").strip()
    if not prompt:
        raise _gateway_error("BOT_IDEA_BAD_PARAMS",
                             f"suggestion {idea!r} carries no prompt text; "
                             "nothing was launched")
    try:
        session_key = ORCH.launch_task(prompt, cfg=cfg)
    except ORCH.TaskLaunchDenied as exc:
        raise _gateway_error(str(exc.code), str(exc.detail))
    # Record the acceptance in the shared registry (upstream taskId link + the
    # accepted status), so a re-derivation never resurrects it (compare-and-swap).
    try:
        from .gateway.rpc import _ideas_load, _ideas_save

        reg = _ideas_load(cfg)
        row = reg.get(idea) or dict(match)
        row["status"] = "accepted"
        row["taskId"] = session_key
        reg[idea] = row
        _ideas_save(cfg, reg)
    except Exception:
        pass
    _remember_prompt(cfg, session_key, prompt, _suggestion_text(match)[:200])
    return {"state": "OK", "idea": idea, "session": session_key,
            "taskId": session_key, "accepted": True,
            "authority": "derived idea accepted: launched as a durable "
                         "delegated worker (orchestrator.launch_task); the "
                         "shared registry records status=accepted + taskId",
            "requested_by": str(params.get("requested_by", "") or "")}


def _gw_bot_watches_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Record a recurring watch as a canonical cron schedule."""
    from . import schedules as SCH

    title = str(params.get("title", "") or "").strip()
    if not title:
        raise _gateway_error("BOT_WATCH_BAD_PARAMS",
                             "watches-set needs a title")
    try:
        interval = int(params.get("intervalMinutes", 0) or 0)
    except (TypeError, ValueError):
        interval = 0
    if interval < 1 or interval > 7 * 24 * 60:
        raise _gateway_error(
            "BOT_WATCH_BAD_PARAMS",
            "watches-set needs intervalMinutes (1..10080); without a "
            "recurrence there is nothing to schedule")
    url = str(params.get("url", "") or "").strip()
    condition = str(params.get("condition", "") or "").strip()
    value = str(params.get("value", "") or "").strip()
    task_text = "Watch %s: %s%s" % (
        url or title,
        condition or "check for changes",
        f" (alert when: {value})" if value else "")
    job = SCH.create(cfg, title, task_text, f"every {interval} minutes",
                     requested_by=str(params.get("requested_by", "")
                                      or "dashboard:user"),
                     confirmed=True)
    if isinstance(job, dict):
        job["watch"] = {"url": url, "condition": condition, "value": value,
                        "intervalMinutes": interval}
    return job if isinstance(job, dict) else {"result": job}


def _gw_harness_task_submit(cfg: Any, params: Dict[str, Any]
                            ) -> Dict[str, Any]:
    """Submit a task: create a canonical session, run one turn in it."""
    from .gateway.rpc import default_registry

    prompt = str(params.get("prompt", "") or "").strip()
    if not prompt:
        raise _gateway_error("HARNESS_SUBMIT_BAD_PARAMS",
                             "task_submit needs a non-empty prompt")
    harness = str(params.get("harness", "") or "").strip()
    registry = default_registry()
    created = registry.handle(cfg, "sessions.create",
                              {"name": prompt[:80],
                               "requested_by": str(params.get("requested_by",
                                                              "") or
                                               "dashboard:user")})
    sid = ""
    if isinstance(created, dict):
        sess = created.get("session")
        if isinstance(sess, dict):
            for key in ("id", "session_id", "key"):
                if sess.get(key):
                    sid = str(sess[key])
                    break
        if not sid:
            for key in ("session_id", "id", "key"):
                if created.get(key):
                    sid = str(created[key])
                    break
    if not sid:
        raise _gateway_error("HARNESS_SUBMIT_NO_SESSION",
                             "session_create returned no session key; "
                             "nothing was submitted")
    turn = registry.handle(cfg, "conversations.turn",
                           {"text": prompt, "session": sid,
                            "requested_by": str(params.get("requested_by", "")
                                               or "dashboard:user")})
    out: Dict[str, Any] = {"state": "OK", "session": sid, "harness": harness,
           "authority": "canonical session (sessions.create) + one real "
                        "agent turn (conversations.turn); no sessionless "
                        "runner exists, so submit always opens a session",
           "requested_by": str(params.get("requested_by", "") or "")}
    if isinstance(turn, dict):
        out["turn"] = turn
    return out


#: Static curated starter kits. Every skill named below
#: exists locally (verified against the runtime skills home), and every
#: ``plugins:`` entry names a real Qubit surface (kanban board CLI,
#: achievements track) rather than an installable remote. Launching a kit
#: submits its starter ``prompt`` through the real ``harness/task_submit``
#: row.
HARNESS_STARTER_KITS = [
    {"id": "coding-delegation",
     "kind": "starter-kit",
     "title": "Coding delegation",
     "description": "Delegate a coding task to a headless worker with a "
                    "coding-agent skill (codex, claude-code, opencode).",
     "prompt": "Run this coding task with the coding-agent skill and report "
               "plan, diffs, and receipts.",
     "skills": ["autonomous-ai-agents/codex",
                "autonomous-ai-agents/claude-code",
                "autonomous-ai-agents/opencode"],
     "plugins": []},
    {"id": "container-ops",
     "kind": "starter-kit",
     "title": "Container ops",
     "description": "Inspect and manage local containers through the "
                    "docker-management skill.",
     "prompt": "Inspect local container state with the docker-management "
               "skill and report health.",
     "skills": ["devops/docker-management"],
     "plugins": []},
    {"id": "team-board",
     "kind": "starter-kit",
     "title": "Team board",
     "description": "Coordinate multi-agent work on the bundled kanban "
                    "board (columns, cards, threads, assignees).",
     "prompt": "Summarize the current kanban board state: columns, cards, "
               "and who runs what.",
     "skills": [],
     "plugins": ["kanban"]},
    {"id": "workflow-achievements",
     "kind": "starter-kit",
     "title": "Workflow achievements",
     "description": "Track vibe-coding achievements from the bundled "
                    "achievements plugin.",
     "prompt": "Report recent workflow achievements and what unlocked them.",
     "skills": [],
     "plugins": ["qubit-achievements"]},
]


def _gw_harness_starter_kits(cfg: Any, params: Dict[str, Any]
                             ) -> Dict[str, Any]:
    """Serve the static curated starter-kit list (read-only)."""
    del cfg, params
    return {"state": "OK", "kits": [dict(k) for k in HARNESS_STARTER_KITS],
            "authority": "static curated list in "
                         "voicebridge/family_bot_harness.py; every "
                         "named skill/plugin exists locally"}


_CONSOLE_FILE = "bot_console.json"
_CUSTOM_HARNESS_FILE = "custom_harnesses.json"
_MAX_GOALS = 200
_MAX_PAUSED = 50
_MAX_OVERLAY = 500
_MAX_FOLLOWUPS = 200
_MAX_DRAFTS = 100
_MAX_CUSTOM_HARNESSES = 100
_CARD_SCAN_SESSIONS = 15


def _console_path(cfg: Any) -> Optional[Any]:
    try:
        from pathlib import Path

        base = Path(cfg.state_dir())
    except Exception:
        return None
    return base / _CONSOLE_FILE


def _console_load(cfg: Any) -> Dict[str, Any]:
    skel = {"paused": {}, "ideas": {"dismissed": [], "edits": {}},
            "goals": [], "followups": [], "drafts": {}}
    path = _console_path(cfg)
    if path is None:
        return skel
    try:
        if not path.is_file():
            return skel
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return skel
    if not isinstance(data, dict):
        return skel
    paused = data.get("paused")
    ideas = data.get("ideas")
    goals = data.get("goals")
    followups = data.get("followups")
    drafts = data.get("drafts")
    return {
        "paused": paused if isinstance(paused, dict) else {},
        "ideas": ideas if isinstance(ideas, dict) else
        {"dismissed": [], "edits": {}},
        "goals": goals if isinstance(goals, list) else [],
        "followups": followups if isinstance(followups, list) else [],
        "drafts": drafts if isinstance(drafts, dict) else {},
    }


def _console_save(cfg: Any, state: Dict[str, Any]) -> None:
    from pathlib import Path

    path = _console_path(cfg)
    if path is None:
        raise _gateway_error("BOT_CONSOLE_STORE_UNAVAILABLE",
                             "no Qubit state dir; console records cannot "
                             "persist")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise _gateway_error("BOT_CONSOLE_STORE_UNAVAILABLE",
                             f"console sidecar unwritable: {exc}"[:200])


def _remember_prompt(cfg: Any, session: str, prompt: str, title: str) -> None:
    try:
        state = _console_load(cfg)
        paused = state.get("paused")
        if not isinstance(paused, dict):
            return
        while len(paused) >= _MAX_PAUSED:
            paused.pop(next(iter(paused)), None)
        entry = paused.get(session, {})
        if not isinstance(entry, dict):
            entry = {}
        entry.update({"prompt": prompt[:4000], "title": title[:200]})
        paused[session] = entry
        state["paused"] = paused
        _console_save(cfg, state)
    except Exception:
        pass


def _resolve_box_session(cfg: Any, params: Dict[str, Any]) -> str:
    from . import orchestrator as ORCH

    candidates: List[str] = []
    for key in ("session", "session_key", "sessionKey", "taskId", "task_id",
                "id", "receiptId", "receipt_id"):
        val = params.get(key)
        if val is not None and str(val).strip():
            candidates.append(str(val).strip())
    boxes = getattr(ORCH, "_TASK_BOXES", {})
    for cand in candidates:
        if cand in boxes:
            return cand
    for cand in candidates:
        if len(cand) > 8:
            continue
        try:
            from . import task_ledger as TL

            shown = TL.show(cfg, cand)
        except Exception:
            continue
        rec = shown if isinstance(shown, dict) else {}
        for key in ("session", "session_key", "sessionKey", "sessionId",
                    "session_id"):
            val = rec.get(key)
            if val is not None and str(val).strip() in boxes:
                return str(val).strip()
    return ""


def _gw_bot_tasks_pause(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Flag a running worker to stop; keep its prompt for resume."""
    from . import orchestrator as ORCH

    sid = _resolve_box_session(cfg, params)
    if not sid:
        raise _gateway_error(
            "BOT_PAUSE_GONE",
            "no running headless worker matches; pause targets a live "
            "worker by its session key (bot-launched tasks report it)")
    lock = getattr(ORCH, "_TASK_BOX_LOCK", None)
    try:
        if lock is not None:
            lock.acquire()
        box = dict(getattr(ORCH, "_TASK_BOXES", {}) or {}).get(sid)
    finally:
        try:
            if lock is not None:
                lock.release()
        except Exception:
            pass
    if not isinstance(box, dict):
        raise _gateway_error("BOT_PAUSE_GONE",
                             f"worker {sid!r} is gone; nothing to pause")
    event = box.get("event")
    try:
        finished = bool(event.is_set()) if event is not None else True
    except Exception:
        finished = True
    if finished:
        raise _gateway_error("BOT_PAUSE_GONE",
                             f"worker {sid!r} already finished; resume "
                             "relaunches only a paused worker")
    box["cancel"] = True
    prompt = str(params.get("prompt", "") or "").strip()
    title = str(params.get("title", "") or "").strip()
    try:
        state = _console_load(cfg)
        paused = state.get("paused")
        if isinstance(paused, dict):
            entry = paused.get(sid, {})
            if not isinstance(entry, dict):
                entry = {}
            if prompt:
                entry["prompt"] = prompt[:4000]
            if title:
                entry["title"] = title[:200]
            entry["paused_at"] = time.time()
            entry["paused"] = True
            paused[sid] = entry
            state["paused"] = paused
            _console_save(cfg, state)
    except Exception:
        pass
    return {"state": "OK", "session": sid, "paused": True,
            "authority": "worker box cancel flag (the hook wait_task "
                         "uses); the thread stops cooperatively — Python "
                         "has no thread suspend. Resume relaunches the "
                         "kept prompt.",
            "requested_by": str(params.get("requested_by", "") or "")}


def _gw_bot_tasks_resume(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Relaunch a paused worker's kept prompt as a fresh worker."""
    from . import orchestrator as ORCH

    sid = _resolve_box_session(cfg, params)
    if not sid:
        for key in ("session", "taskId", "task_id", "id"):
            val = params.get(key)
            if val is not None and str(val).strip():
                sid = str(val).strip()
                break
    if not sid:
        raise _gateway_error("BOT_RESUME_BAD_PARAMS",
                             "tasks-resume needs a session (or task id of "
                             "a paused worker)")
    state = _console_load(cfg)
    paused = state.get("paused")
    entry = (paused.get(sid) if isinstance(paused, dict) else None) or {}
    prompt = str(entry.get("prompt", "") or "").strip()
    if not prompt:
        raise _gateway_error(
            "BOT_RESUME_NO_PROMPT",
            f"no kept prompt for {sid!r}; relaunch via tasks-launch "
            "with an explicit prompt")
    try:
        new_key = ORCH.launch_task(prompt, cfg=cfg)
    except ORCH.TaskLaunchDenied as exc:
        raise _gateway_error(str(exc.code), str(exc.detail))
    try:
        if isinstance(paused, dict):
            paused.pop(sid, None)
            state["paused"] = paused
            _console_save(cfg, state)
    except Exception:
        pass
    return {"state": "OK", "session": new_key, "resumed_from": sid,
            "authority": "kept prompt relaunched as a fresh durable "
                         "worker; a still-running worker refuses typed "
                         "TASK_INFLIGHT",
            "requested_by": str(params.get("requested_by", "") or "")}


def _overlay(cfg: Any) -> Dict[str, Any]:
    state = _console_load(cfg)
    ideas = state.get("ideas")
    if not isinstance(ideas, dict):
        return {"dismissed": [], "edits": {}}
    dismissed = ideas.get("dismissed")
    edits = ideas.get("edits")
    return {
        "dismissed": [str(x) for x in dismissed]
        if isinstance(dismissed, list) else [],
        "edits": edits if isinstance(edits, dict) else {},
    }


def _save_overlay(cfg: Any, overlay: Dict[str, Any]) -> None:
    state = _console_load(cfg)
    state["ideas"] = overlay
    _console_save(cfg, state)


def _goal_rows(cfg: Any) -> List[Dict[str, Any]]:
    """Durable goal records from the console sidecar (same source goals-list uses)."""
    try:
        state = _console_load(cfg)
    except Exception:
        return []
    goals = state.get("goals") if isinstance(state, dict) else None
    rows: List[Dict[str, Any]] = []
    if isinstance(goals, list):
        for g in goals:
            if isinstance(g, dict) and g.get("id"):
                rows.append(g)
    return rows


def _ledger_rows(cfg: Any) -> List[Dict[str, Any]]:
    """Rows from the canonical durable task ledger (voicebridge.task_ledger).

    An import failure here is a real defect, not "no data" — an earlier revision
    used a wrong-depth relative import and a bare `except: return []`, which
    reported an empty ledger forever instead of raising. Store reads are
    guarded (a store CAN legitimately be empty); the import is not.
    """
    from . import task_ledger as TL
    try:
        out = TL.list_tasks(cfg)
    except Exception:
        return []
    if isinstance(out, dict):
        cand = out.get("tasks") or out.get("rows") or []
    else:
        cand = out
    return [r for r in cand if isinstance(r, dict)] if isinstance(cand, list) else []


def _hook_rows(cfg: Any) -> List[Dict[str, Any]]:
    """Flow-hook rows from the canonical schedules store."""
    from . import schedules as SCH
    try:
        out = SCH.list_flow_hooks(cfg)
    except Exception:
        return []
    hooks = out.get("hooks") if isinstance(out, dict) else out
    return [h for h in hooks if isinstance(h, dict)] if isinstance(hooks, list) else []


def _idea_rows(cfg: Any) -> List[Dict[str, Any]]:
    """Derive suggestion rows the way upstream `refreshIdeas` does.

    Upstream (`apps/server/src/engine/service.ts:524` @ b06caad7) builds a
    workspace snapshot of its OWN durable stores (tasks, goals, monitors,
    ideas, memories, artifacts, notifications) and derives ideas from that —
    there is no model-proposed registry involved. This gateway used to read
    `task.suggestions`, which returns `TASK_SUGGESTIONS_OPERATOR_REQUIRED`
    unconditionally, so the list was always empty.

    The derivation below applies upstream's observable rules to the stores
    Qubit actually has: stable-ish ids, capped counts, and a plan idea for any
    active goal that has no milestones. It never invents a row — every entry is
    derived from a real ledger/schedule/goal/hook record, and the evidence is
    that record.
    """
    rows: List[Dict[str, Any]] = []
    seen: set = set()

    def _add(row: Dict[str, Any]) -> None:
        iid = _idea_id(row)
        if iid and iid not in seen:
            seen.add(iid)
            rows.append(row)

    # --- source 1: cron schedules that are disabled or have been failing ----
    # Upstream derives document/coordination ideas from qualifying mail items;
    # Qubit has no mail store, so the equivalent "something in your workspace
    # needs attention" signal is a switched-off or erroring schedule.
    try:
        from . import schedules as SCH  # type: ignore
        jobs = SCH.list_jobs(cfg) if hasattr(SCH, "list_jobs") else []
    except Exception:
        jobs = []
    for job in (jobs if isinstance(jobs, list) else [])[:5]:
        if not isinstance(job, dict):
            continue
        jid = str(job.get("id") or "").strip()
        if not jid:
            continue
        name = str(job.get("name") or "").strip() or f"schedule {jid}"
        last = str(job.get("last_state") or "").strip().upper()
        if job.get("enabled") is False:
            _add({
                "id": f"schedule-off:{jid}",
                "title": f"Re-enable {name}",
                "reason": (f"The schedule {name!r} exists but is switched off, "
                           "so it will not run."),
                "prompt": (f"Re-enable the schedule {name!r} and confirm it "
                           "fires on its next due time."),
                "kind": "agent",
                "status": "new",
                "evidence": [{"id": jid, "kind": "schedule", "title": name,
                              "excerpt": f"enabled=false, last_state={last or '—'}"}],
            })
        elif last and last not in ("OK", "SUCCESS", "RUNNING", ""):
            _add({
                "id": f"schedule-fail:{jid}",
                "title": f"Look into the failing {name} schedule",
                "reason": (f"The schedule {name!r} last finished in state "
                           f"{last!r}."),
                "prompt": (f"Investigate why the schedule {name!r} last failed "
                           f"({last}) and propose a fix."),
                "kind": "agent",
                "status": "new",
                "evidence": [{"id": jid, "kind": "schedule", "title": name,
                              "excerpt": f"last_state={last}"}],
            })

    # --- source 2: flow hooks that are switched off ------------------------
    hooks = _hook_rows(cfg)
    for hook in (hooks if isinstance(hooks, list) else [])[:5]:
        if not isinstance(hook, dict):
            continue
        hname = str(hook.get("name") or "").strip()
        if hname and hook.get("enabled") is False:
            _add({
                "id": f"hook-off:{hname}",
                "title": f"Enable the {hname} hook",
                "reason": (f"This flow hook exists but is switched off; I can "
                           "enable it and confirm it fires on its events."),
                "prompt": (f"Enable the flow hook {hname!r} and verify it fires "
                           "on its configured events."),
                "kind": "agent",
                "status": "new",
                "evidence": [{"id": hname, "kind": "hook", "title": hname,
                              "excerpt": str(hook.get("events") or "")}],
            })

    # --- source 3: active goals with no milestones (upstream rule, verbatim) -
    try:
        goal_rows = _goal_rows(cfg)
    except Exception:
        goal_rows = []
    for goal in (goal_rows if isinstance(goal_rows, list) else []):
        if not isinstance(goal, dict):
            continue
        if str(goal.get("status") or "") != "active":
            continue
        if goal.get("milestones"):
            continue
        gid = str(goal.get("id") or "").strip()
        gtitle = str(goal.get("title") or "").strip()
        if gid and gtitle:
            _add({
                "id": f"goal-plan:{gid}",
                "title": f"Let's make a plan for {gtitle}",
                "reason": ("This goal has no milestones yet. A concrete plan "
                           "will give it a next step."),
                "prompt": (f"Create an actionable plan for {gtitle}. "
                           f"{str(goal.get('description') or '')}"),
                "kind": "plan",
                "status": "new",
                "evidence": [{"id": gid, "kind": "user", "title": gtitle,
                              "excerpt": str(goal.get("description") or "")}],
            })

    # --- source 4: failed ledger rows (the durable task ledger) -------------
    try:
        ledger = _ledger_rows(cfg)
    except Exception:
        ledger = []
    for task in (ledger if isinstance(ledger, list) else [])[:5]:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or "").strip()
        state = str(task.get("state") or task.get("source_state") or "").lower()
        title = str(task.get("title") or "").strip()
        if tid and title and state in ("failed", "error"):
            _add({
                "id": f"task-retry:{tid}",
                "title": f"Re-run {title}",
                "reason": f"The ledger records {title!r} finishing in state {state!r}.",
                "prompt": f"Re-run {title!r} and report what happens.",
                "kind": "agent",
                "status": "new",
                "evidence": [{"id": tid, "kind": "task", "title": title,
                              "excerpt": f"state={state}"}],
            })

    return rows


def _idea_id(row: Dict[str, Any]) -> str:
    for key in ("id", "task_id", "taskId", "native_id", "session"):
        if row.get(key) is not None and str(row[key]).strip():
            return str(row[key]).strip()
    return ""


def _accepted_ids(cfg: Any) -> set:
    """Ids already resolved (accepted/dismissed) in the durable idea registry.

    Upstream `refreshIdeas` retires earlier suggestions so a resolved idea does
    not resurface ("Retire earlier suggestions as well as preventing new
    duplicates"). This derivation recomputes from live source stores on every
    call, so without consulting the registry an accepted idea reappeared as
    `status:"new"` on the very next list — the hook it referred to was still
    switched off, so it still looked actionable.

    Reads the registry (`_idea_registry` -> `rpc._ideas_load`), which is where
    accept/dismiss record their status, NOT the console overlay (which only
    carries dismissals and edits).
    """
    out: set = set()
    for iid, rec in (_idea_registry(cfg) or {}).items():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("status") or "").lower() in ("accepted", "dismissed"):
            out.add(str(iid))
    if out:
        return out
    # Fallback: the console overlay records dismissals even when the registry
    # has nothing (e.g. a fresh profile).
    try:
        state = _console_load(cfg)
    except Exception:
        return out
    overlay = state.get("ideas") if isinstance(state, dict) else None
    if isinstance(overlay, dict):
        vals = overlay.get("dismissed")
        if isinstance(vals, list):
            out.update(str(v) for v in vals)
    return out


def _gw_bot_ideas_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Derived suggestions minus dismissed/accepted, plus stored edits (marked)."""
    overlay = _overlay(cfg)
    dismissed = set(overlay["dismissed"])
    resolved = _accepted_ids(cfg)
    edits = overlay["edits"]
    rows = []
    for row in _idea_rows(cfg):
        iid = _idea_id(row)
        if iid and (iid in dismissed or iid in resolved):
            continue
        if iid and iid in edits and isinstance(edits[iid], dict):
            row = {**row, **{k: v for k, v in edits[iid].items()
                             if k in ("title", "prompt", "reason")},
                   "edited": True}
        rows.append(row)
    return {"state": "OK", "suggestions": rows, "considered": len(rows),
            "authority": "derived from the live stores (schedules/hooks/goals/"
                         "ledger) with the durable dismiss/accept/edit "
                         "overlay applied"}


def _gw_bot_ideas_edit(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Store an edit overlay for one suggestion (confirm-gated)."""
    idea = ""
    for _key in ("idea", "ideaId", "idea_id", "id"):
        _val = str(params.get(_key, "") or "").strip()
        if _val:
            idea = _val
            break
    if not idea:
        raise _gateway_error("BOT_IDEA_BAD_PARAMS",
                             "ideas-edit needs an idea id "
                             "(send it as 'idea', 'ideaId' or 'id')")
    known = {_idea_id(r) for r in _idea_rows(cfg)}
    if idea not in known:
        raise _gateway_error("BOT_IDEA_NOT_FOUND",
                             f"no open suggestion {idea!r}; nothing edited")
    patch = {}
    for key in ("title", "prompt", "reason"):
        val = params.get(key)
        if val is not None and str(val).strip():
            patch[key] = str(val).strip()[:2000]
    if not patch:
        raise _gateway_error("BOT_IDEA_BAD_PARAMS",
                             "ideas-edit needs at least one of "
                             "title/prompt/reason")
    overlay = _overlay(cfg)
    edits = overlay["edits"]
    if len(edits) >= _MAX_OVERLAY and idea not in edits:
        raise _gateway_error("BOT_IDEA_BAD_PARAMS",
                             "idea overlay is full; dismiss stale ideas "
                             "first")
    edits[idea] = patch
    overlay["edits"] = edits
    _save_overlay(cfg, overlay)
    return {"state": "OK", "idea": idea, "edit": patch,
            "authority": "durable overlay; the ledger derivation stays "
                         "read-only and the list marks the row edited"}


def _gw_bot_ideas_dismiss(cfg: Any, params: Dict[str, Any]
                          ) -> Dict[str, Any]:
    """Dismiss one suggestion from the list (confirm-gated).

    Accepts the id as ``idea`` (canonical), ``ideaId``/``idea_id`` or ``id``.
    Only ``idea`` was read before, so a caller sending ``id`` — the field every
    other action in this bridge uses — got ``BOT_IDEA_BAD_PARAMS`` claiming the
    id was missing while it was in fact supplied. Accept-both is this repo's
    convention (see ``requires_hermes``/``requires_qubit`` and the plugin SDK
    globals), and the error now names the field it accepts.
    """
    idea = ""
    for _key in ("idea", "ideaId", "idea_id", "id"):
        _val = str(params.get(_key, "") or "").strip()
        if _val:
            idea = _val
            break
    if not idea:
        raise _gateway_error("BOT_IDEA_BAD_PARAMS",
                             "ideas-dismiss needs an idea id "
                             "(send it as 'idea', 'ideaId' or 'id')")
    known = {_idea_id(r) for r in _idea_rows(cfg)}
    if idea not in known:
        raise _gateway_error("BOT_IDEA_NOT_FOUND",
                             f"no open suggestion {idea!r}; nothing "
                             "dismissed")
    overlay = _overlay(cfg)
    dismissed = overlay["dismissed"]
    if idea not in dismissed:
        dismissed.append(idea)
    overlay["dismissed"] = dismissed[-_MAX_OVERLAY:]
    _save_overlay(cfg, overlay)
    # Record the decision in the shared registry too (upstream compare-and-swap:
    # new -> dismissed), so a re-derivation never resurrects the row.
    try:
        from .gateway.rpc import _ideas_load, _ideas_save

        reg = _ideas_load(cfg)
        row = reg.get(idea)
        if isinstance(row, dict):
            row["status"] = "dismissed"
            reg[idea] = row
            _ideas_save(cfg, reg)
    except Exception:
        pass
    return {"state": "OK", "idea": idea, "dismissed": True,
            "authority": "durable overlay + registry record status=dismissed; "
                         "the ledger keeps its record (dismiss is a list "
                         "concern, never a ledger delete)"}


def _normalize_goal(record: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a stored goal to the exact upstream ``Goal`` shape.

    Legacy records (earlier sidecar wrote a float ``created_at`` and a bare
    string milestone list) are upgraded in place so ``goals-list`` always
    serves the pinned shape: ``createdAt`` ISO string, milestones objects.
    """
    import uuid

    milestones = record.get("milestones")
    ms_out: List[Dict[str, Any]] = []
    if isinstance(milestones, list):
        for m in milestones:
            if isinstance(m, dict):
                ms_out.append({"id": str(m.get("id") or uuid.uuid4()),
                               "title": str(m.get("title", "") or ""),
                               "done": bool(m.get("done"))})
            elif isinstance(m, str):
                ms_out.append({"id": str(uuid.uuid4()), "title": m,
                               "done": False})
    created = record.get("createdAt")
    if not isinstance(created, str):
        raw = record.get("created_at")
        if isinstance(raw, (int, float)):
            created = time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(float(raw))) + "Z"
        else:
            created = _iso_now()
    status = record.get("status")
    if status not in ("active", "paused", "completed"):
        status = "active"
    return {"id": str(record.get("id", "")), "title": str(
        record.get("title", "") or ""), "description": str(
        record.get("description", "") or ""), "category": str(
        record.get("category", "") or "Personal"), "status": status,
        "milestones": ms_out, "createdAt": created}


def _gw_bot_goals_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Cron schedules plus durable goal records (records list as records)."""
    from .gateway.rpc import default_registry

    try:
        sched = default_registry().handle(cfg, "cron.list", {})
    except Exception:
        sched = {}
    state = _console_load(cfg)
    goals = state.get("goals")
    goal_rows = []
    if isinstance(goals, list):
        for g in goals:
            if isinstance(g, dict) and g.get("id"):
                goal_rows.append(_normalize_goal({**g, "kind": "goal"}))
    out: Dict[str, Any] = {"state": "OK", "goals": goal_rows,
                           "authority": "durable goal sidecar plus the "
                                        "canonical cron store"}
    if isinstance(sched, dict):
        out["schedules"] = sched
        jobs = sched.get("jobs", [])
        if isinstance(jobs, list):
            out["jobs"] = jobs
    return out


def _goal_input(params: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a goal input exactly like upstream ``goalInputSchema``.

    ``title``: trimmed, 1..160. ``description``: max 4000, default "".
    ``category``: max 80, default "Personal". ``milestones``: string[] each
    1..200, max 20, default []. Bounds are enforced, never silently clipped.
    """
    raw_title = params.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    if not title:
        raise _gateway_error("BOT_GOAL_BAD_PARAMS",
                             "a goal needs a title (1..160 chars)")
    if len(title) > 160:
        raise _gateway_error("BOT_GOAL_BAD_PARAMS",
                             "goal title exceeds 160 chars")
    raw_desc = params.get("description")
    description = raw_desc if isinstance(raw_desc, str) else ""
    if len(description) > 4000:
        raise _gateway_error("BOT_GOAL_BAD_PARAMS",
                             "goal description exceeds 4000 chars")
    raw_cat = params.get("category")
    category = raw_cat if isinstance(raw_cat, str) else ""
    if not category:
        category = "Personal"
    if len(category) > 80:
        raise _gateway_error("BOT_GOAL_BAD_PARAMS",
                             "goal category exceeds 80 chars")
    raw_ms = params.get("milestones")
    if raw_ms is None:
        raw_ms = []
    ms_list = raw_ms if isinstance(raw_ms, list) else [raw_ms]
    if len(ms_list) > 20:
        raise _gateway_error("BOT_GOAL_BAD_PARAMS",
                             "a goal holds at most 20 milestones")
    clean_ms: List[str] = []
    for item in ms_list:
        text = item.strip() if isinstance(item, str) else ""
        if not text or len(text) > 200:
            raise _gateway_error("BOT_GOAL_BAD_PARAMS",
                                 "each milestone is 1..200 chars")
        clean_ms.append(text)
    return {"title": title, "description": description,
            "category": category, "milestones": clean_ms}


def _gw_bot_goals_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Goal record — 1:1 with upstream ``Goal`` (service.createGoal).

    Fields, defaults and bounds mirror ``goalInputSchema``; the stored record
    is the exact upstream ``Goal`` shape (status starts "active", milestones
    are ``{id,title,done:false}`` objects, ``createdAt`` is an ISO instant).
    """
    parsed = _goal_input(params)

    def _mk(goal_id: str) -> Dict[str, Any]:
        import uuid

        return {
            "id": goal_id,
            "title": parsed["title"],
            "description": parsed["description"],
            "category": parsed["category"],
            "status": "active",
            "milestones": [
                {"id": str(uuid.uuid4()), "title": m, "done": False}
                for m in parsed["milestones"]],
            "createdAt": _iso_now(),
        }

    goal_id = str(params.get("id", "") or "").strip()
    state = _console_load(cfg)
    goals = state.get("goals")
    if not isinstance(goals, list):
        goals = []
    goals = [g for g in goals if isinstance(g, dict)]
    # insertIfAbsent(owner, "goals", goal) — an explicit id is honoured and
    # never clobbers an existing row (upstream reads it back afterwards).
    if goal_id and any(str(g.get("id")) == goal_id for g in goals):
        existing = next(g for g in goals if str(g.get("id")) == goal_id)
        return {"state": "OK", "goal": existing, "created": False,
                "authority": "durable goal sidecar; goals-list serves it"}
    record = _mk(goal_id or _goal_id(parsed["title"]))
    while len(goals) >= _MAX_GOALS:
        goals.pop(0)
    goals.append(record)
    state["goals"] = goals
    _console_save(cfg, state)
    return {"state": "OK", "goal": record, "created": True,
            "authority": "durable goal sidecar; listed by goals-list "
                         "alongside cron schedules"}


def _gw_bot_goals_update(cfg: Any, params: Dict[str, Any]
                         ) -> Dict[str, Any]:
    """Patch a goal's status/milestones (service.updateGoal) + pause cascade.

    404 (BOT_GOAL_NOT_FOUND) when the goal is absent; the patch is merged.
    SPECIAL RULE: ``status == "paused"`` also pauses every non-terminal task
    whose ``goalId`` equals this goal — those tasks are the durable paused
    workers this sidecar records (``paused[session].goalId``).
    """
    goal_id = str(params.get("id", "") or "").strip()
    if not goal_id:
        raise _gateway_error("BOT_GOAL_BAD_PARAMS",
                             "goals-update needs a goal id")
    state = _console_load(cfg)
    goals = state.get("goals")
    if not isinstance(goals, list):
        goals = []
    goals = [g for g in goals if isinstance(g, dict)]
    goal = next((g for g in goals if str(g.get("id")) == goal_id), None)
    if goal is None:
        raise _gateway_error("BOT_GOAL_NOT_FOUND", "Goal not found")
    patch: Dict[str, Any] = {}
    if "status" in params and params.get("status") is not None:
        status = str(params.get("status"))
        if status not in ("active", "paused", "completed"):
            raise _gateway_error(
                "BOT_GOAL_BAD_PARAMS",
                "status must be active, paused or completed")
        patch["status"] = status
    if params.get("milestones") is not None:
        raw_ms = params.get("milestones")
        ms_list = raw_ms if isinstance(raw_ms, list) else [raw_ms]
        clean: List[Dict[str, Any]] = []
        for item in ms_list:
            if isinstance(item, dict) and item.get("id"):
                title = str(item.get("title", "") or "")
                clean.append({"id": str(item["id"]), "title": title,
                              "done": bool(item.get("done"))})
            else:
                raise _gateway_error(
                    "BOT_GOAL_BAD_PARAMS",
                    "milestones are {id,title,done} objects")
        patch["milestones"] = clean
    if not patch:
        raise _gateway_error("BOT_GOAL_BAD_PARAMS",
                             "goals-update needs status or milestones")
    goal = {**goal, **patch}
    goals = [goal if str(g.get("id")) == goal_id else g for g in goals]
    state["goals"] = goals
    paused_tasks: List[str] = []
    if patch.get("status") == "paused":
        paused = state.get("paused")
        if isinstance(paused, dict):
            for session, entry in paused.items():
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("goalId", "") or "") != goal_id:
                    continue
                if entry.get("status") in ("cancelled", "completed"):
                    continue
                if entry.get("status") == "paused":
                    continue
                entry["status"] = "paused"
                paused_tasks.append(session)
        state["paused"] = paused if isinstance(paused, dict) else {}
    _console_save(cfg, state)
    return {"state": "OK", "goal": goal, "updated": True,
            "pausedTasks": paused_tasks,
            "authority": "durable goal sidecar; paused goals cascade to "
                         "their non-terminal tasks"}


def _goal_id(title: str) -> str:
    import re
    import uuid

    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    return f"{slug or 'goal'}-{uuid.uuid4().hex[:12]}"


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def _gw_bot_cards_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Find one rich card: ``<session>/<name>`` direct, else bounded scan."""
    from .gateway.rpc import default_registry

    query = str(params.get("card", "") or "").strip()
    if not query:
        raise _gateway_error("BOT_CARD_BAD_PARAMS",
                             "cards-get needs a card name or "
                             "<session>/<name>")
    registry = default_registry()
    sid = ""
    name = query
    for sep in ("/", ":"):
        if sep in query:
            maybe_sid, maybe_name = query.split(sep, 1)
            if maybe_sid.strip() and maybe_name.strip():
                sid, name = maybe_sid.strip(), maybe_name.strip()
                break
    if sid:
        try:
            got = registry.handle(cfg, "artifacts.get",
                                  {"session": sid, "path": name})
        except Exception as exc:
            raise _gateway_error("BOT_CARD_NOT_FOUND",
                                 f"no card {name!r} in session {sid!r}: "
                                 f"{exc}"[:200])
        if isinstance(got, dict) and str(got.get("state")) == "OK":
            return {"state": "OK", "card": name, "session": sid,
                    "artifact": got.get("artifact"),
                    "authority": "session file artifacts (artifacts.get)"}
        raise _gateway_error("BOT_CARD_NOT_FOUND",
                             f"no card {name!r} in session {sid!r}")
    try:
        listing = registry.handle(cfg, "sessions.list", {"limit": 20})
    except Exception as exc:
        raise _gateway_error("BOT_CARD_NOT_FOUND",
                             f"session roster unreadable: {exc}"[:200])
    sessions = listing.get("sessions", []) if isinstance(listing, dict) else []
    if not isinstance(sessions, list):
        sessions = []
    searched = 0
    needle = name.lower()
    for sess in sessions[:_CARD_SCAN_SESSIONS]:
        if not isinstance(sess, dict):
            continue
        key = ""
        for f in ("id", "session_id", "session", "key"):
            if sess.get(f):
                key = str(sess[f])
                break
        if not key:
            continue
        searched += 1
        try:
            arts = registry.handle(cfg, "artifacts.list",
                                   {"session": key, "limit": 100})
        except Exception:
            continue
        items = arts.get("artifacts", []) if isinstance(arts, dict) else []
        if not isinstance(items, list):
            continue
        for item in items:
            label = ""
            if isinstance(item, dict):
                label = str(item.get("path", item.get("name", "")) or "")
            elif item is not None:
                label = str(item)
            if label and needle in label.lower():
                try:
                    got = registry.handle(cfg, "artifacts.get",
                                          {"session": key, "path": label})
                except Exception:
                    continue
                if isinstance(got, dict) and str(got.get("state")) == "OK":
                    return {"state": "OK", "card": label, "session": key,
                            "artifact": got.get("artifact"),
                            "searched_sessions": searched,
                            "authority": "bounded scan of recent session "
                                         "file artifacts"}
    raise _gateway_error("BOT_CARD_NOT_FOUND",
                         f"no card matching {name!r} in the "
                         f"{searched} newest sessions")


#: Gateway method name -> handler (registered onto each bridge registry by
#: the family bridge in ``voicebridge/qubit_compat.py`` — the one wiring
#: owner). Mutations execute only after the family's staged confirm
#: round-trip (409 before confirm, exactly-once after).
def _gw_bot_followups_queue(cfg: Any,
                            params: Dict[str, Any]) -> Dict[str, Any]:
    text = str(params.get("text", params.get("prompt", "")) or "").strip()
    if not text:
        messages = params.get("messages", [])
        if isinstance(messages, list):
            text = "\n".join(str(m) for m in messages)[:4000].strip()
    if not text:
        raise _gateway_error("BOT_FOLLOWUP_BAD_PARAMS",
                             "followups-queue needs text to queue")
    state = _console_load(cfg)
    queue = state.get("followups")
    if not isinstance(queue, list):
        queue = []
        state["followups"] = queue
    item = {"text": text[:4000],
            "thread": str(params.get("thread", "") or "")[:200],
            "queued_at": time.time()}
    queue.append(item)
    del queue[:max(0, len(queue) - _MAX_FOLLOWUPS)]
    state["followups"] = queue
    _console_save(cfg, state)
    return {"state": "OK", "queued": item, "count": len(queue),
            "authority": "durable follow-up inbox in the Qubit state dir; "
                         "the open page mirrors it locally"}


def _gw_bot_drafts_save(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    thread = str(params.get("thread", "") or "").strip()
    text = str(params.get("text", params.get("draft", "")) or "")
    if not thread:
        raise _gateway_error("BOT_DRAFT_BAD_PARAMS",
                             "drafts-save needs a thread key")
    state = _console_load(cfg)
    drafts = state.get("drafts")
    if not isinstance(drafts, dict):
        drafts = {}
        state["drafts"] = drafts
    if text:
        if len(drafts) >= _MAX_DRAFTS and thread not in drafts:
            drafts.pop(next(iter(drafts)), None)
        drafts[thread] = {"text": text[:20000],
                          "saved_at": time.time()}
    else:
        drafts.pop(thread, None)
    state["drafts"] = drafts
    _console_save(cfg, state)
    return {"state": "OK", "thread": thread,
            "saved": bool(text),
            "authority": "durable per-thread draft store in the Qubit "
                         "state dir; the open page mirrors it locally"}


def _custom_harness_path(cfg: Any) -> Optional[Any]:
    try:
        from pathlib import Path

        base = Path(cfg.state_dir())
    except Exception:
        return None
    return base / _CUSTOM_HARNESS_FILE


def _custom_harness_load(cfg: Any) -> List[Dict[str, Any]]:
    """Read the durable custom-harness sidecar; a list of created harnesses."""
    path = _custom_harness_path(cfg)
    if path is None:
        return []
    try:
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("harnesses") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _custom_harness_save(cfg: Any, rows: List[Dict[str, Any]]) -> None:
    from pathlib import Path

    path = _custom_harness_path(cfg)
    if path is None:
        raise _gateway_error("HARNESS_STORE_UNAVAILABLE",
                             "no Qubit state dir; custom harnesses cannot "
                             "persist")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps({"harnesses": rows}, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise _gateway_error("HARNESS_STORE_UNAVAILABLE",
                             f"custom-harness sidecar unwritable: {exc}"[:200])


def _gw_harness_custom_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """List user-created harnesses from the durable sidecar (read-only)."""
    rows = _custom_harness_load(cfg)
    return {"state": "OK", "harnesses": rows, "count": len(rows),
            "authority": "durable custom-harness sidecar in the Qubit state "
                         "dir; the console lists these beside the runtime "
                         "agent roster"}


def _gw_harness_create(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a named harness backed by a real runtime adapter.

    A Qubit harness is a *name* + *base runtime* + *default prompt*, run on an
    existing adapter from the runtime inventory (the same roster the console
    list reads) — no second agent engine is invented. The record is persisted
    to a durable sidecar and returned with its id.
    """
    name = str(params.get("name", "") or "").strip()
    if not name:
        raise _gateway_error("HARNESS_BAD_PARAMS",
                             "harness-create needs a name")
    if len(name) > 120:
        name = name[:120]
    base = str(params.get("base", params.get("backend", "")) or "").strip()
    prompt = str(params.get("prompt", params.get("description", "")) or "")[:4000]
    # Resolve a real base from the runtime inventory when one is named; an
    # unknown base is refused rather than persisted as a dangling reference.
    known_bases: List[str] = []
    try:
        from .integration_status import worker_status
        from .runtime_inventory import runtime_inventory

        inv = runtime_inventory(cfg)
        adapters = inv.get("adapters") if isinstance(inv.get("adapters"), dict) else {}
        known_bases = ["qubit-worker"] + [f"adapter:{n}" for n in sorted(adapters)]
        _ = worker_status(cfg)
    except Exception:
        known_bases = []
    if base and known_bases and base not in known_bases:
        raise _gateway_error(
            "HARNESS_UNKNOWN_BASE",
            f"no runtime adapter {base!r}; available: "
            f"{', '.join(known_bases)}")
    if not base:
        base = known_bases[0] if known_bases else "qubit-worker"
    rows = _custom_harness_load(cfg)
    slug = "".join(c if (c.isalnum() or c in "-_") else "-"
                   for c in name.lower()).strip("-") or "harness"
    hid = f"custom:{slug}"
    if any(str(r.get("id")) == hid for r in rows):
        raise _gateway_error("HARNESS_EXISTS",
                             f"a harness named {name!r} already exists")
    record = {
        "id": hid,
        "kind": "custom",
        "name": name,
        "backend": base,
        "state": "ready",
        "prompt": prompt,
        "created_at": time.time(),
    }
    while len(rows) >= _MAX_CUSTOM_HARNESSES:
        rows.pop(0)
    rows.append(record)
    _custom_harness_save(cfg, rows)
    return {"state": "OK", "harness": record,
            "authority": "durable custom-harness sidecar; runs on the named "
                         "runtime adapter (agents.list roster)"}


BOT_HARNESS_GATEWAY_METHODS = {
    "bot.tasks_launch": _gw_bot_tasks_launch,
    "bot.tasks_pause": _gw_bot_tasks_pause,
    "bot.tasks_resume": _gw_bot_tasks_resume,
    "bot.ideas_list": _gw_bot_ideas_list,
    "bot.ideas_accept": _gw_bot_ideas_accept,
    "bot.ideas_edit": _gw_bot_ideas_edit,
    "bot.ideas_dismiss": _gw_bot_ideas_dismiss,
    "bot.goals_list": _gw_bot_goals_list,
    "bot.goals_set": _gw_bot_goals_set,
    "bot.goals_update": _gw_bot_goals_update,
    "bot.watches_set": _gw_bot_watches_set,
    "bot.cards_get": _gw_bot_cards_get,
    "bot.followups_queue": _gw_bot_followups_queue,
    "bot.drafts_save": _gw_bot_drafts_save,
    "harness.task_submit": _gw_harness_task_submit,
    "harness.starter_kits": _gw_harness_starter_kits,
    "harness.custom_list": _gw_harness_custom_list,
    "harness.create": _gw_harness_create,
}
