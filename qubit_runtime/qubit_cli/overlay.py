"""Read-first adapters over the ported overlay backends.

Each helper calls the REAL backend module (no new transports, no new
daemons) and returns its dict envelope. ``voicebridge.*`` is imported
lazily so the CLI still loads where the overlay tree is absent — callers
get :class:`OverlayUnavailable` and print the honest reason instead.

Backend map (mirrors the dashboard pages):
- board ..... ``voicebridge.kanban`` (board_open/tab_list/widget_list/roles_list/log)
- harness ... ``voicebridge.family_bot_harness`` gateway methods (starter kits
  + task_submit) and the shared context session/file index for sessions/files
- viking .... ``voicebridge.context_store`` (status/tree/search/add)
- bot ....... ``voicebridge.family_bot_harness`` gateway methods (tasks/ideas/goals/cards)
- webchat ... ``voicebridge.chat_transport`` session store
- memory .... ``voicebridge.family_memory`` gateway methods (status/provider/probe)
- canvas .... ``voicebridge.files.canvas_host`` (list/get/add/remove)
- taskflow .. ``voicebridge.schedules`` (flows) + ``voicebridge.hooks`` (hooks),
  falling back to the local cron backend for the flow list
"""

from __future__ import annotations

import importlib
from typing import Any


class OverlayUnavailable(Exception):
    """The overlay backend module (or a config to drive it) is not importable."""


def voicebridge_module(name: str) -> Any | None:
    """``voicebridge.<name>`` or None when the overlay tree is absent."""
    try:
        return importlib.import_module(f"voicebridge.{name}")
    except ImportError:
        pass
    try:
        # Standalone checkout layout: ``voicebridge/`` is a top-level dir.
        return importlib.import_module(name)
    except ImportError:
        return None


def overlay_cfg() -> Any:
    """A default overlay config (``Config.load()``), or raise OverlayUnavailable."""
    mod = voicebridge_module("config")
    if mod is None:
        raise OverlayUnavailable("voicebridge config module is not importable")
    try:
        return mod.Config.load()
    except Exception as exc:
        raise OverlayUnavailable(f"overlay config failed to load: {exc}")


def _need(modname: str) -> Any:
    mod = voicebridge_module(modname)
    if mod is None:
        raise OverlayUnavailable(f"voicebridge.{modname} is not importable here")
    return mod


def _call_gateway_method(table: dict, method: str, cfg: Any, params: dict) -> dict:
    fn = table.get(method)
    if fn is None:
        raise OverlayUnavailable(f"gateway method {method!r} is not registered")
    out = fn(cfg, dict(params))
    return out if isinstance(out, dict) else {"state": "OK", "result": out}


# ---- board (voicebridge.kanban) -------------------------------------------

def board_snapshot(board: str = "main") -> dict:
    kb = _need("kanban")
    return kb.board_open(overlay_cfg(), board or "main")


def board_tabs(board: str = "main") -> dict:
    kb = _need("kanban")
    return kb.tab_list(overlay_cfg(), board or "main")


def board_widgets(board: str = "main") -> dict:
    kb = _need("kanban")
    return kb.widget_list(overlay_cfg(), board or "main")


def board_roles(board: str = "main") -> dict:
    kb = _need("kanban")
    return kb.roles_list(overlay_cfg(), board or "main")


def board_log(board: str = "", limit: int = 50) -> dict:
    kb = _need("kanban")
    return kb.log(overlay_cfg(), board=board or "", limit=limit)


# ---- harness + bot (voicebridge.family_bot_harness) ------------------------

def _bot_harness_table() -> dict:
    mod = _need("family_bot_harness")
    return dict(getattr(mod, "BOT_HARNESS_GATEWAY_METHODS", {}))


def harness_kits() -> dict:
    """Static starter-kit list — needs no config (read-only)."""
    mod = _need("family_bot_harness")
    fn = getattr(mod, "_gw_harness_starter_kits", None)
    if fn is None:
        return _call_gateway_method(_bot_harness_table(), "harness.starter_kits",
                                    None, {})
    return fn(None, {})


def harness_submit(harness: str, prompt: str) -> dict:
    return _call_gateway_method(_bot_harness_table(), "harness.task_submit",
                                overlay_cfg(),
                                {"harness": harness, "prompt": prompt,
                                 "requested_by": "cli:user"})


def harness_sessions(limit: int = 50) -> dict:
    """Recent bot sessions via the shared context session index."""
    cs = _need("context_store")
    return cs.bot_sessions(overlay_cfg(), limit=limit)


def harness_files(group: str = "") -> dict:
    """Context files (per group, or every group when empty)."""
    cs = _need("context_store")
    cfg = overlay_cfg()
    if group:
        return {"state": "OK", "group": group,
                "files": cs.tier_files(cfg, group)}
    tree = cs.tree(cfg)
    out: dict[str, Any] = {"state": "OK", "groups": []}
    for grp in tree.get("groups", []):
        out["groups"].append({"key": grp.get("key"),
                              "files": cs.tier_files(cfg, str(grp.get("key")))})
    return out


def bot_ideas() -> dict:
    return _call_gateway_method(_bot_harness_table(), "bot.ideas_list",
                                overlay_cfg(), {})


def bot_goals() -> dict:
    return _call_gateway_method(_bot_harness_table(), "bot.goals_list",
                                overlay_cfg(), {})


def bot_launch(prompt: str, title: str = "") -> dict:
    return _call_gateway_method(_bot_harness_table(), "bot.tasks_launch",
                                overlay_cfg(),
                                {"prompt": prompt, "title": title,
                                 "requested_by": "cli:user"})


def bot_pause(session: str) -> dict:
    return _call_gateway_method(_bot_harness_table(), "bot.tasks_pause",
                                overlay_cfg(),
                                {"session": session, "requested_by": "cli:user"})


def bot_resume(session: str) -> dict:
    return _call_gateway_method(_bot_harness_table(), "bot.tasks_resume",
                                overlay_cfg(),
                                {"session": session, "requested_by": "cli:user"})


def bot_card(card: str) -> dict:
    return _call_gateway_method(_bot_harness_table(), "bot.cards_get",
                                overlay_cfg(), {"card": card})


# ---- viking (voicebridge.context_store) ------------------------------------

def viking_status() -> dict:
    cs = _need("context_store")
    return cs.status(overlay_cfg())


def viking_tree() -> dict:
    cs = _need("context_store")
    return cs.tree(overlay_cfg())


def viking_search(query: str, limit: int = 20) -> dict:
    cs = _need("context_store")
    return cs.search(overlay_cfg(), query, limit=limit)


def viking_add(text: str, scope: str = "user") -> dict:
    cs = _need("context_store")
    return cs.add_memory(overlay_cfg(), text, scope=scope,
                         requested_by="cli:user")


def viking_add_resource(uri: str, title: str = "", notes: str = "") -> dict:
    cs = _need("context_store")
    return cs.add_resource(overlay_cfg(), uri, title=title, notes=notes,
                           requested_by="cli:user")


# ---- webchat (voicebridge.chat_transport) -----------------------------------

def _webchat_store() -> Any:
    ct = _need("chat_transport")
    return ct.store(overlay_cfg())


def webchat_list(limit: int = 50) -> dict:
    rows = _webchat_store().list(limit=limit)
    return {"state": "OK", "sessions": list(rows)}


def webchat_show(session_id: str) -> dict:
    return _webchat_store().show(session_id)


def webchat_messages(session_id: str, limit: int = 50) -> dict:
    rows = _webchat_store().messages(session_id, limit=limit)
    return {"state": "OK", "session": session_id, "messages": list(rows)}


def webchat_delete(session_id: str, confirmed: bool = False) -> dict:
    return _webchat_store().delete(session_id, confirm=confirmed)


# ---- memory backends (voicebridge.family_memory) ----------------------------

def _memory_table() -> dict:
    mod = _need("family_memory")
    return dict(getattr(mod, "MEMORY_GATEWAY_METHODS", {}))


def memory_status() -> dict:
    return _call_gateway_method(_memory_table(), "memory.status",
                                overlay_cfg(), {})


def memory_provider() -> dict:
    return _call_gateway_method(_memory_table(), "memory.provider",
                                overlay_cfg(), {})


def memory_probe() -> dict:
    return _call_gateway_method(_memory_table(), "memory.probe",
                                overlay_cfg(), {})


# ---- canvas (voicebridge.files.canvas_host) ----------------------------------

def canvas_list() -> dict:
    mod = _need("files.canvas_host")
    return mod.list_files(overlay_cfg())


def canvas_get(path: str) -> dict:
    mod = _need("files.canvas_host")
    return mod.get_file(overlay_cfg(), path)


def canvas_add(path: str, content: str, confirmed: bool = False) -> dict:
    mod = _need("files.canvas_host")
    return mod.add_file(overlay_cfg(), path, content, confirmed=confirmed,
                        requested_by="cli:user")


def canvas_remove(path: str, confirmed: bool = False) -> dict:
    mod = _need("files.canvas_host")
    return mod.remove_file(overlay_cfg(), path, confirmed=confirmed,
                           requested_by="cli:user")


# ---- taskflows (voicebridge.schedules + voicebridge.hooks) -------------------

def taskflow_flows(limit: int = 50) -> dict:
    """Durable flows, newest-first; falls back to the local cron backend."""
    try:
        sched = _need("schedules")
        return sched.list_jobs(overlay_cfg(), limit=limit)
    except OverlayUnavailable:
        pass
    from qubit_cli.cron import _cron_api
    jobs = _cron_api(action="list", include_disabled=True)
    if isinstance(jobs, dict):
        return {"state": "OK", "jobs": jobs.get("jobs", []),
                "authority": "local cron backend (fallback)"}
    return {"state": "OK", "jobs": jobs if isinstance(jobs, list) else [],
            "authority": "local cron backend (fallback)"}


def taskflow_flow(job_id: int) -> dict:
    sched = _need("schedules")
    return sched.get_job(overlay_cfg(), int(job_id))


def taskflow_pause(job_id: int) -> dict:
    sched = _need("schedules")
    return sched.pause(overlay_cfg(), int(job_id), requested_by="cli:user")


def taskflow_resume(job_id: int) -> dict:
    sched = _need("schedules")
    return sched.resume(overlay_cfg(), int(job_id), requested_by="cli:user")


def taskflow_trigger(job_id: int) -> dict:
    sched = _need("schedules")
    return sched.trigger(overlay_cfg(), int(job_id), requested_by="cli:user")


def taskflow_delete(job_id: int) -> dict:
    sched = _need("schedules")
    return sched.delete(overlay_cfg(), int(job_id), requested_by="cli:user")


def taskflow_hooks() -> dict:
    hooks = _need("hooks")
    return hooks.list_hooks(overlay_cfg())
