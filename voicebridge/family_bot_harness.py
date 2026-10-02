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
- ``harness.starter_kits`` -> a static curated kit list. Kit *concepts*
  follow upstream ``HarnessRouter/harnessrouter`` (Apache-2.0; kit rows name
  real local skills/plugins only — every ``skills:`` entry names a skill
  directory present in the runtime home, every ``plugins:`` entry names a
  bundled plugin; launching a kit submits its starter prompt through the
  real ``harness/task_submit`` row). No harness runner is ported: upstream
  runs kits on its own runner, Qubit runs the kit prompt as a local turn.
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


def _gw_bot_ideas_accept(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Accept an idea: resolve the ledger suggestion, launch it as a worker."""
    from . import orchestrator as ORCH
    from . import task_ledger as TL

    idea = str(params.get("idea", "") or "").strip()
    if not idea:
        raise _gateway_error("BOT_IDEA_BAD_PARAMS",
                             "ideas-accept needs an idea id")
    if idea in set(_overlay(cfg)["dismissed"]):
        raise _gateway_error("BOT_IDEA_DISMISSED",
                             f"idea {idea!r} was dismissed; relaunch its "
                             "prompt via tasks-launch explicitly")
    listing = TL.list_tasks(cfg, limit=100)
    rows = listing.get("tasks", []) if isinstance(listing, dict) else []
    match = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("id", "task_id", "taskId", "session", "session_key"):
            if str(row.get(key, "") or "") == idea:
                match = row
                break
        if match is not None:
            break
    if match is None:
        raise _gateway_error("BOT_IDEA_NOT_FOUND",
                             f"no open suggestion {idea!r} in the task "
                             "ledger; nothing was launched")
    prompt = _suggestion_text(match)
    if not prompt:
        raise _gateway_error("BOT_IDEA_BAD_PARAMS",
                             f"suggestion {idea!r} carries no prompt text; "
                             "nothing was launched")
    try:
        session_key = ORCH.launch_task(prompt, cfg=cfg)
    except ORCH.TaskLaunchDenied as exc:
        raise _gateway_error(str(exc.code), str(exc.detail))
    _remember_prompt(cfg, session_key, prompt, _suggestion_text(match)[:200])
    return {"state": "OK", "idea": idea, "session": session_key,
            "authority": "ledger suggestion launched as a durable delegated "
                         "worker; track via tasks-get / wait_task",
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


#: Static curated starter kits. Kit concepts follow upstream
#: ``HarnessRouter/harnessrouter`` (Apache-2.0); every skill/plugin named
#: below exists locally (verified against the runtime skills home and the
#: bundled plugin fleet). Launching a kit submits its starter ``prompt``
#: through the real ``harness/task_submit`` row.
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
                         "voicebridge/family_bot_harness.py; kit concepts "
                         "follow upstream HarnessRouter/harnessrouter, every "
                         "named skill/plugin exists locally"}


_CONSOLE_FILE = "bot_console.json"
_MAX_GOALS = 200
_MAX_PAUSED = 50
_MAX_OVERLAY = 500
_MAX_FOLLOWUPS = 200
_MAX_DRAFTS = 100
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


def _idea_rows(cfg: Any) -> List[Dict[str, Any]]:
    from .gateway.rpc import default_registry

    out = default_registry().handle(cfg, "task.suggestions", {})
    rows = out.get("suggestions", []) if isinstance(out, dict) else []
    return [r for r in rows if isinstance(r, dict)]


def _idea_id(row: Dict[str, Any]) -> str:
    for key in ("id", "task_id", "taskId", "native_id", "session"):
        if row.get(key) is not None and str(row[key]).strip():
            return str(row[key]).strip()
    return ""


def _gw_bot_ideas_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Ledger suggestions minus dismissed, plus stored edits (marked)."""
    overlay = _overlay(cfg)
    dismissed = set(overlay["dismissed"])
    edits = overlay["edits"]
    rows = []
    for row in _idea_rows(cfg):
        iid = _idea_id(row)
        if iid and iid in dismissed:
            continue
        if iid and iid in edits and isinstance(edits[iid], dict):
            row = {**row, **{k: v for k, v in edits[iid].items()
                             if k in ("title", "prompt", "reason")},
                   "edited": True}
        rows.append(row)
    return {"state": "OK", "suggestions": rows, "considered": len(rows),
            "authority": "read-only ledger derivation (task.suggestions) "
                         "with the durable dismiss/edit overlay applied"}


def _gw_bot_ideas_edit(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Store an edit overlay for one suggestion (confirm-gated)."""
    idea = str(params.get("idea", "") or "").strip()
    if not idea:
        raise _gateway_error("BOT_IDEA_BAD_PARAMS",
                             "ideas-edit needs an idea id")
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
    """Dismiss one suggestion from the list (confirm-gated)."""
    idea = str(params.get("idea", "") or "").strip()
    if not idea:
        raise _gateway_error("BOT_IDEA_BAD_PARAMS",
                             "ideas-dismiss needs an idea id")
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
    return {"state": "OK", "idea": idea, "dismissed": True,
            "authority": "durable overlay hides the row; the ledger keeps "
                         "its record (dismiss is a list concern, never a "
                         "ledger delete)"}


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
                goal_rows.append({**g, "kind": "goal"})
    out: Dict[str, Any] = {"state": "OK", "goals": goal_rows,
                           "authority": "durable goal sidecar plus the "
                                        "canonical cron store"}
    if isinstance(sched, dict):
        out["schedules"] = sched
        jobs = sched.get("jobs", [])
        if isinstance(jobs, list):
            out["jobs"] = jobs
    return out


def _gw_bot_goals_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Store a goal record (title/description/milestones, confirm-gated)."""
    import re

    title = str(params.get("title", "") or "").strip()
    if not title:
        raise _gateway_error("BOT_GOAL_BAD_PARAMS",
                             "goals-set needs a title")
    description = str(params.get("description", "") or "").strip()[:2000]
    milestones = params.get("milestones")
    clean_ms = [str(m).strip()[:300] for m in milestones
                if isinstance(milestones, list)
                and m is not None and str(m).strip()][:20]
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    gid = f"{slug or 'goal'}-{int(time.time())}"
    state = _console_load(cfg)
    goals = state.get("goals")
    if not isinstance(goals, list):
        goals = []
    goals = [g for g in goals if isinstance(g, dict)]
    while len(goals) >= _MAX_GOALS:
        goals.pop(0)
    record = {"id": gid, "title": title, "description": description,
              "milestones": clean_ms, "status": "active",
              "created_at": time.time()}
    goals.append(record)
    state["goals"] = goals
    _console_save(cfg, state)
    return {"state": "OK", "goal": record,
            "authority": "durable goal sidecar; listed by goals-list "
                         "alongside cron schedules"}


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
    "bot.watches_set": _gw_bot_watches_set,
    "bot.cards_get": _gw_bot_cards_get,
    "bot.followups_queue": _gw_bot_followups_queue,
    "bot.drafts_save": _gw_bot_drafts_save,
    "harness.task_submit": _gw_harness_task_submit,
    "harness.starter_kits": _gw_harness_starter_kits,
}
