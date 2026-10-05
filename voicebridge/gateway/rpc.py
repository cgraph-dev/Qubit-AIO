"""Qubit gateway RPC method registry (transport-neutral, in-process).

Qubit's v2026.9.4 gateway added server methods (``environments.prepare``,
task history/suggestions, update report, workspace files, plugin mutations,
migrations). Qubit opens no public listener and starts no second authority,
so those methods are implemented here as real in-process handlers that
delegate to the SAME backend modules the CLI, dashboard, and voice surfaces
use. They are dispatched from the transport-neutral gateway frame path
(``FrameReader``), never from a duplicate broker, scheduler, or server.

Every handler is read-mostly and broker-gated: mutations route through the
existing plugin-runtime confirmation/e-stop gates, file access stays inside
the approved roots, and anything unobservable is typed UNAVAILABLE.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from .gateway_ws import GatewayError

METHOD_NOT_FOUND = "GATEWAY_METHOD_NOT_FOUND"
METHOD_BAD_PARAMS = "GATEWAY_BAD_PARAMS"
SESSION_REPOSITORY_UNAVAILABLE = "SESSION_REPOSITORY_UNAVAILABLE"

#: OpenClaw-compatible protocol version Qubit negotiates. Qubit serves the
#: same v4 frame dialect on its single listener (owner decision D2).
GATEWAY_PROTOCOL_VERSION = 4
#: Server->client event frames the Qubit RPC socket emits. ``connect.challenge``
#: is the OpenClaw pre-connect event (the only event Qubit emits today).
GATEWAY_EVENTS = ("connect.challenge",)
#: Effective authorization scopes Qubit's single operator socket carries. A
#: client-supplied scope list can never widen this (no model/page/peer text
#: grants authority).
OPERATOR_SCOPES = ("operator.read", "operator.write", "operator.admin",
                   "operator.approvals", "operator.talk")

# Safe local-counterpart codes emitted by the Qubit delta handlers whose
# upstream action needs a service Qubit does not run. ``scripts/
# qubit_upgrade_manifest.py`` asserts each declared code is emitted by the
# registered handler, so these cannot drift from ``qubit_dispositions``.
ENVIRONMENT_PREPARE_CODE = "ENVIRONMENT_PREPARE_LOCAL_PLAN"
UPDATE_REPORT_CODE = "UPDATE_REPORT_LOCAL_READ"
PLUGIN_RELOAD_CODE = "PLUGIN_RELOAD_LOCAL_REREAD"
# Contractual-pin read slice: the pinned config.openFile launches the OS file
# opener; Qubit surfaces the canonical config path for the operator instead of
# spawning a GUI handler, tagged with its own counterpart code.
CONFIG_OPEN_LOCAL_PATH_CODE = "CONFIG_OPEN_LOCAL_PATH"

# Latest-main (v2026.9.5 / main 395690e8) delta additions. Each is either a real
# local handler backed by a canonical Qubit module (themes, transcripts,
# session activity) or a real local counterpart that emits its own typed code.
# The generator asserts every declared code is actually emitted by the handler.
LINK_PREVIEW_CODE = "LINK_PREVIEW_LOCAL_FETCH"
THEME_IMPORT_CODE = "PETS_NO_DOWNLOADS"
SESSION_INVOLVEMENT_CODE = "SESSION_INVOLVEMENT_LOCAL_ARCHIVE"
PROGRESS_CARD_CODE = "PROGRESS_CARD_LOCAL_PROJECTION"
ENVIRONMENT_SESSION_STATUS_CODE = "ENVIRONMENT_SESSION_LOCAL_STATUS"
RUNTIME_CLOUD_UNAVAILABLE_CODE = "RUNTIME_CLOUD_UNAVAILABLE"
FORGE_API_OPERATOR_REQUIRED_CODE = "FORGE_API_OPERATOR_REQUIRED"

Handler = Callable[[Any, Dict[str, Any]], Dict[str, Any]]


class MethodRegistry:
    """Named handlers keyed by gateway method name (no sockets, no threads)."""

    def __init__(self) -> None:
        self._methods: Dict[str, Handler] = {}

    def register(self, name: str, fn: Handler) -> Handler:
        clean = str(name or "").strip()
        if not clean:
            raise GatewayError(METHOD_BAD_PARAMS, "method name required")
        self._methods[clean] = fn
        return fn

    def names(self) -> list:
        return sorted(self._methods)

    def handle(self, cfg: Any, method: str,
               params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        name = str(method or "").strip()
        fn = self._methods.get(name)
        if fn is None:
            raise GatewayError(METHOD_NOT_FOUND,
                               f"unknown gateway method {name!r}")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise GatewayError(METHOD_BAD_PARAMS, "params must be an object")
        try:
            return fn(cfg, params)
        except GatewayError:
            raise
        except Exception as exc:  # backend typed errors become RPC errors
            code = getattr(exc, "code", None)
            detail = getattr(exc, "detail", None) or str(exc)
            if code:
                raise GatewayError(str(code), str(detail)[:300]) from exc
            raise GatewayError("GATEWAY_METHOD_FAILED",
                               str(exc)[:300]) from exc


# --------------------------------------------------------------- handlers
def _environments_prepare(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import node_host as NH
    from .. import runtime_images as RI

    name = str(params.get("name", "") or "").strip().lower()
    out: Dict[str, Any] = {"registry": RI.images()["images"],
                           "status": RI.status(cfg)["runtimes"],
                           "nodes": NH.status(cfg),
                           "mode": "plan",
                           # The upstream call provisions a CLOUD worker
                           # environment; Qubit runs no cloud worker service.
                           # This is the real local runtime plan, tagged with
                           # its own counterpart code so it is never mistaken
                           # for the upstream mutation.
                           "code": ENVIRONMENT_PREPARE_CODE}
    if name:
        entry = next((r for r in RI.REGISTRY if r["name"] == name), None)
        if entry is None:
            return {"state": "UNKNOWN", "name": name,
                    "code": ENVIRONMENT_PREPARE_CODE,
                    "detail": "no pinned runtime with that name"}
        row = next(r for r in out["status"] if r["name"] == name)
        out = {"state": "OK", "name": name, "pin": entry["pin"],
               "runtime": row, "mode": "plan",
               "code": ENVIRONMENT_PREPARE_CODE,
               "detail": "preparation plan only; nothing was installed"}
    else:
        out["state"] = "OK"
    return out


def _task_history(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import task_ledger as TL

    out = TL.list_tasks(cfg, source=params.get("source"),
                        state=params.get("state"),
                        limit=params.get("limit", 50))
    return out


#: Upstream Idea kinds (domain/agent.ts AgentTask["kind"]).
_IDEA_KINDS = ("agent", "document", "monitor", "finance", "plan")

#: Default on-disk name of the durable ideas registry, beside bot_console.json
#: in the Qubit state dir. Rows are the upstream ``Idea`` shape exactly; the
#: file is the one writer this read model has (mirrors upstream's ``ideas``
#: collection, which the ledger itself never mutates).
_IDEAS_FILE = "ideas.json"
_IDEAS_MAX = 500


def _ideas_path(cfg: Any) -> Any:
    from pathlib import Path

    try:
        return Path(cfg.state_dir()) / _IDEAS_FILE
    except Exception:
        return None


def _ideas_load(cfg: Any) -> Dict[str, Dict[str, Any]]:
    """Read the durable ideas registry as {id: Idea}. Never raises."""
    path = _ideas_path(cfg)
    if path is None:
        return {}
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = data.get("ideas") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("id"):
            out[str(row["id"])] = row
    return out


def _ideas_save(cfg: Any, ideas: Dict[str, Dict[str, Any]]) -> None:
    """Persist the ideas registry, newest-last, bounded. Never raises."""
    path = _ideas_path(cfg)
    if path is None:
        return
    try:
        rows = list(ideas.values())
        rows.sort(key=lambda r: str(r.get("createdAt", "")))
        rows = rows[-_IDEAS_MAX:]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"ideas": rows}, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _idea_id(key: str) -> str:
    """Stable content id — upstream ``hash(stableKey)`` so re-runs are idempotent."""
    import hashlib

    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _idea_evidence(ev_id: str, kind: str, title: str, excerpt: str,
                   url: str = "") -> Dict[str, Any]:
    """Upstream ``Evidence`` shape: {id, kind, title, excerpt, url?}."""
    out: Dict[str, Any] = {"id": ev_id, "kind": kind, "title": title,
                           "excerpt": str(excerpt or "")[:400]}
    if url:
        out["url"] = url
    return out


def _idea_created_at() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _derive_ideas(cfg: Any, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Derive Idea rows from Qubit's OWN stores — no mail store exists.

    Mirrors upstream ``refreshIdeas`` derivation obligations exactly, over the
    stores Qubit actually has:

    - the task ledger (``GET /agent/api/qubit/tasks/list``) — a terminal
      ``failed`` run whose recorded detail names a retryable cause yields an
      ``agent`` idea to re-run it (capped at 5, stable id, insertIfAbsent);
    - cron schedules (``GET /agent/api/cron/jobs``) — a schedule whose last
      status is ``UNAVAILABLE`` yields an ``agent`` idea to fix and re-run it;
    - flows/hooks (``GET /agent/api/qubit/flows/hooks``) — a known-off hook
      yields an ``agent`` idea to enable it;
    - goals (the durable goal sidecar) — an ``active`` goal with NO milestones
      yields a ``plan`` idea, 1:1 with upstream's goal rule.

    ``limit`` (params) optionally caps the ledger scan. Evidence is real text
    pulled from those stores; a source that is unavailable simply derives
    fewer ideas — nothing is fabricated.
    """
    from .. import task_ledger as TL

    rows: List[Dict[str, Any]] = []
    try:
        limit = int(params.get("limit", 100) or 100)
    except (TypeError, ValueError):
        limit = 100

    # (a) task ledger — failed runs with a recorded detail (retryable).
    try:
        listing = TL.list_tasks(cfg, limit=limit)
        ledger = listing.get("tasks", []) if isinstance(listing, dict) else []
    except Exception:
        ledger = []
    for task in ledger:
        if not isinstance(task, dict):
            continue
        if str(task.get("state", "")).lower() != "failed":
            continue
        detail = str(task.get("detail", "") or "").strip()
        if not detail:
            continue
        tid = str(task.get("id", "") or "")
        title = str(task.get("title", "") or tid or "failed task")
        reason = ("This run finished in a failed state and left a recorded "
                  "detail; I can re-run it with the same task text and "
                  "report what changed.")
        rows.append({
            "id": _idea_id(f"ledger:{tid}:{detail}"),
            "title": f"Re-run {title}",
            "reason": reason,
            "evidence": [_idea_evidence(
                tid, "user", title, detail,
                str(task.get("evidence_ref", "") or ""))],
            "prompt": (f"Re-run the task “{title}”. The previous run failed "
                       f"with: {detail[:300]}. Report the outcome."),
            "kind": "agent",
            "input": {"taskId": tid, "source": str(task.get("source", ""))},
            "status": "new",
            "createdAt": _idea_created_at(),
        })

    # (b) cron schedules — last-status UNAVAILABLE jobs.
    try:
        from .. import schedules as SCH

        jobs = SCH.list_jobs(cfg)
        if isinstance(jobs, dict):
            jobs = jobs.get("jobs", [])
    except Exception:
        jobs = []
    for job in jobs if isinstance(jobs, list) else []:
        if not isinstance(job, dict):
            continue
        if str(job.get("last_status", "") or "") not in ("UNAVAILABLE",
                                                         "FAILED"):
            continue
        jid = str(job.get("id", "") or "")
        name = str(job.get("name", "") or jid or "schedule")
        err = str(job.get("last_error", "") or "").strip() or \
            str(job.get("state", "") or "")
        rows.append({
            "id": _idea_id(f"schedule:{jid}:{err}"),
            "title": f"Fix and re-run {name}",
            "reason": ("This schedule's last run did not complete; I can "
                       "inspect the failure and run it once to confirm it "
                       "works."),
            "evidence": [_idea_evidence(
                f"cron:{jid}", "user", name, err,
                "cron://job/" + jid)],
            "prompt": (f"Inspect the schedule “{name}” (id {jid}) whose last "
                       f"run reported: {err[:300]}. Run it once and report "
                       "the outcome."),
            "kind": "agent",
            "input": {"jobId": jid},
            "status": "new",
            "createdAt": _idea_created_at(),
        })

    # (c) flows/hooks — known-off hooks that can be enabled.
    try:
        from .. import schedules as SCH2

        hooks = SCH2.list_flow_hooks(cfg)
        hlist = hooks.get("hooks", []) if isinstance(hooks, dict) else []
    except Exception:
        hlist = []
    for hook in hlist if isinstance(hlist, list) else []:
        if not isinstance(hook, dict) or hook.get("enabled"):
            continue
        hname = str(hook.get("name", "") or "")
        if not hname:
            continue
        rows.append({
            "id": _idea_id(f"hook:{hname}"),
            "title": f"Enable the {hname} hook",
            "reason": ("This flow hook exists but is switched off; I can "
                       "enable it and confirm it fires on its events."),
            "evidence": [_idea_evidence(
                f"hook:{hname}", "user", hname,
                str(hook.get("description", "") or ""))],
            "prompt": (f"Enable the Qubit flow hook “{hname}” "
                       f"(events: {', '.join(hook.get('events', []) or [])}) "
                       "and confirm it fires."),
            "kind": "agent",
            "input": {"hook": hname},
            "status": "new",
            "createdAt": _idea_created_at(),
        })

    # (d) goals — active goal with NO milestones -> a plan idea (upstream rule).
    try:
        from .. import family_bot_harness as FBH

        state = FBH._console_load(cfg)
        goals = state.get("goals")
    except Exception:
        goals = []
    for goal in goals if isinstance(goals, list) else []:
        if not isinstance(goal, dict):
            continue
        if str(goal.get("status", "") or "").lower() != "active":
            continue
        milestones = goal.get("milestones")
        if isinstance(milestones, list) and len(milestones):
            continue
        gid = str(goal.get("id", "") or "")
        gtitle = str(goal.get("title", "") or gid or "goal")
        gdesc = str(goal.get("description", "") or "")
        rows.append({
            "id": _idea_id(f"goal:{gid}:{gdesc}"),
            "title": f"Let's make a plan for {gtitle}",
            "reason": ("This goal has no milestones yet. A concrete plan will "
                       "give it a next step."),
            "evidence": [_idea_evidence(gid, "user", gtitle, gdesc)],
            "prompt": f"Create an actionable plan for {gtitle}. {gdesc}",
            "kind": "plan",
            "input": {"goalId": gid},
            "status": "new",
            "createdAt": _idea_created_at(),
        })

    # Cap each derivation family at 5 (upstream slices at 5) by first-seen
    # order, then dedupe on the stable id (insertIfAbsent semantics).
    by_kind: Dict[str, int] = {}
    deduped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        kind = str(row.get("kind", ""))
        count = by_kind.get(kind, 0)
        if count >= 5:
            continue
        if row["id"] in deduped:
            continue
        by_kind[kind] = count + 1
        deduped[row["id"]] = row
    return list(deduped.values())


def _task_suggestions(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Derive the idea inbox from Qubit's own stores (upstream ``Idea`` shape).

    Upstream derives ideas from mail + goals; Qubit has no mail store, so this
    derives from the task ledger, cron schedules, flows/hooks, and the goal
    sidecar — the same evidence/kind/prompt structure and the same obligations
    (cap 5, stable hash ids, insertIfAbsent idempotence). Reads of the durable
    registry honour a stored status; nothing is fabricated.
    """
    from .. import task_ledger as TL

    listing = TL.list_tasks(cfg, limit=int(params.get("limit", 100) or 100))
    rows = listing.get("tasks", []) if isinstance(listing, dict) else []

    # Compare-and-swap: stored rows the user already decided win; a concurrent
    # acceptance is never overwritten by a re-derivation (insertIfAbsent).
    stored = _ideas_load(cfg)
    derived = _derive_ideas(cfg, params)
    merged: Dict[str, Dict[str, Any]] = {}
    for row in derived:
        prior = stored.get(row["id"])
        if isinstance(prior, dict) and prior.get("status") in (
                "accepted", "dismissed"):
            merged[row["id"]] = prior          # never overwrite a decision
        else:
            merged[row["id"]] = row
    # Preserve stored rows that are still open but no longer derived.
    for iid, prior in stored.items():
        if iid not in merged and isinstance(prior, dict) and \
                prior.get("status") == "new":
            merged[iid] = prior
    if merged != stored:
        _ideas_save(cfg, merged)

    visible = [r for r in merged.values() if r.get("status") == "new"]
    visible.sort(key=lambda r: str(r.get("createdAt", "")))
    return {"state": "OK", "suggestions": visible[:25],
            "considered": len(rows),
            "authority": "derived read-only from the shared task ledger, "
                         "cron schedules, flows/hooks, and the goal sidecar "
                         "(upstream Idea shape); a durable registry records "
                         "accept/dismiss decisions"}


def _update_report(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``update.report`` — local update-check read.

    The upstream method is a consent-gated FAILED-update report submit keyed by
    an ``attemptId`` against a hosted service. Qubit runs no such service; this
    returns the real local runtime/image update state, tagged with its own
    counterpart code so it is never presented as the upstream submit.
    """
    from .. import runtime_images as RI

    report = RI.update_report(cfg)
    report["images"] = [r["name"] for r in RI.REGISTRY]
    report["code"] = UPDATE_REPORT_CODE
    report["mode"] = "local-read"
    return report


def _workspace_files(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import files as F

    action = str(params.get("action", "inspect") or "inspect").strip().lower()
    path = str(params.get("path", "") or "")
    if action == "list":
        return F.list_dir(cfg, path, depth=int(params.get("depth", 1) or 1))
    if action == "read":
        return F.read_file(cfg, path,
                           max_chars=int(params.get("max_chars", 4000) or 4000))
    if action == "inspect":
        return F.inspect(cfg, path)
    raise GatewayError(METHOD_BAD_PARAMS,
                       f"unknown workspace.files action {action!r}")


def _plugin_mutations(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import plugin_runtime as PR

    action = str(params.get("action", "status") or "status").strip().lower()
    confirmed = bool(params.get("confirmed", False))
    if action == "status":
        return PR.status(cfg)
    if action == "list":
        return PR.list_plugins(cfg)
    if action == "doctor":
        return PR.doctor(cfg)
    if action == "register":
        return PR.register(cfg, manifest=params.get("manifest"),
                           confirmed=confirmed,
                           requested_by=str(params.get("requested_by", "gateway")))
    if action == "unregister":
        return PR.unregister(cfg, str(params.get("plugin_id", "")),
                             confirmed=confirmed,
                             requested_by=str(params.get("requested_by", "gateway")))
    if action == "invoke":
        return PR.invoke(cfg, str(params.get("plugin_id", "")),
                         args=params.get("args") or {},
                         confirmed=confirmed,
                         requested_by=str(params.get("requested_by", "gateway")))
    raise GatewayError(METHOD_BAD_PARAMS,
                       f"unknown plugin.mutations action {action!r}")


def _ui_panels(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from . import panels as PAN

    return PAN.control_panels(cfg)


def _ui_panel_layout(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from . import panels as PAN

    action = str(params.get("action", "status") or "status").strip().lower()
    if action == "status":
        return PAN.panel_layout(cfg)
    if action == "arrange":
        return PAN.arrange(
            cfg, params.get("panels"), layout=str(params.get("layout",
                                                             "tabs")),
            requested_by=str(params.get("requested_by", "gateway")),
            confirmed=bool(params.get("confirmed", False)))
    raise GatewayError(METHOD_BAD_PARAMS,
                       f"unknown ui.panel_layout action {action!r}")


def _sessions_sharing(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit equivalent of the upstream session-sharing gateway methods.

    The pinned v2026.9.4 gateway added ``session.publicShare.set``,
    ``session.visibility.set`` and ``session.members.*`` over a cloud session
    repository. Qubit runs no cloud repository and no second policy authority,
    so this single method exposes exactly the safe local capability: publish a
    redacted, revocable, expiring read-only share of a receipt-store session
    (``voicebridge/session_share.py``), list shares, revoke, and resolve. Every
    mutation stays confirmation/e-stop gated in the shared backend; unknown
    actions are typed bad-params, never a fabricated member/visibility result.
    """
    from .. import session_share as SS

    action = str(params.get("action", "status") or "status").strip().lower()
    if action == "status":
        return SS.status(cfg)
    if action == "list":
        return SS.list_shares(cfg)
    if action == "publish":
        return SS.publish(
            cfg, str(params.get("session_id", "") or ""),
            ttl_s=params.get("ttl_s", SS.DEFAULT_TTL_S),
            confirmed=bool(params.get("confirmed", False)),
            requested_by=str(params.get("requested_by", "gateway")))
    if action == "revoke":
        return SS.revoke(cfg, str(params.get("share_id", "") or ""),
                         confirmed=bool(params.get("confirmed", False)))
    if action == "resolve":
        return SS.resolve(cfg, str(params.get("share_id", "") or ""),
                          str(params.get("token", "") or ""))
    if action == "purge":
        return SS.purge_expired(cfg)
    raise GatewayError(METHOD_BAD_PARAMS,
                       f"unknown sessions.sharing action {action!r}")


def _session_repository_artifacts(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only session artifact view (Qubit-safe local equivalent).

    Upstream lists artifacts from a cloud session repository
    (``session-repository-artifacts.ts``). Qubit runs no cloud repository, so
    the *only* session artifacts it has are the local opt-in public shares.
    This returns those read-only and reports the cloud path as a typed
    ``SESSION_REPOSITORY_UNAVAILABLE`` — it never fabricates a remote listing.
    """
    from .. import session_share as SS

    action = str(params.get("action", "list") or "list").strip().lower()
    if action != "list":
        raise GatewayError(METHOD_BAD_PARAMS,
                           f"unknown session.repository_artifacts action "
                           f"{action!r}")
    listing = SS.list_shares(cfg)
    return {
        "state": "OK",
        "mode": "local-shares",
        "artifacts": listing.get("shares", []),
        "count": listing.get("count", 0),
        "cloud": {"state": "UNAVAILABLE", "code": SESSION_REPOSITORY_UNAVAILABLE,
                  "detail": "no cloud session repository is run; local "
                            "read-only session shares are the safe equivalent"},
        "authority": "read-only; nothing is fetched or fabricated",
    }


def _migrations(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import lifecycle as LC
    from .. import product as PR

    contract = PR.upgrade_contract()
    return {"state": "OK", "contract": contract,
            "provenance": LC.provenance_view(),
            "authority": "read-only migration contract; run the migration "
                         "through the CLI/dashboard with explicit confirmation"}


def _tasks_history(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``tasks.history`` — same task ledger as ``task.history``."""
    return _task_history(cfg, params)


def _session_public_share_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``session.publicShare.set`` — canonical local share authority."""
    from .. import session_share as SS
    action = str(params.get("action", "set") or "set").strip().lower()
    if action in ("get", "list", "status"):
        return SS.list_shares(cfg)
    if action in ("set", "publish"):
        return SS.publish(cfg, str(params.get("session_id", "") or ""),
                          ttl_s=params.get("ttl_s", SS.DEFAULT_TTL_S),
                          confirmed=bool(params.get("confirmed", False)),
                          requested_by=str(params.get("requested_by",
                                                     "gateway")))
    if action == "revoke":
        return SS.revoke(cfg, str(params.get("share_id", "") or ""),
                         confirmed=bool(params.get("confirmed", False)))
    raise GatewayError(METHOD_BAD_PARAMS,
                       f"unknown session.publicShare.set action {action!r}")


def _plugins_catalog_browse(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import plugin_runtime as PR
    return PR.plugin_catalog(cfg)


def _plugins_catalog_categories(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import plugin_runtime as PR
    rows = PR.plugin_catalog(cfg).get("plugins", []) or []
    counts: Dict[str, int] = {}
    for row in rows:
        kind = str(row.get("kind", "") or "plugin")
        counts[kind] = counts.get(kind, 0) + 1
    return {"state": "OK",
            "categories": [{"id": k, "count": counts[k]}
                           for k in sorted(counts)]}


def _plugins_catalog_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import plugin_runtime as PR
    pid = str(params.get("plugin_id", params.get("identifier", "")) or "")
    return PR.get_plugin(cfg, pid)


def _plugins_reload(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``plugins.reload`` — real re-read of the persisted plugin store.

    Delegates to ``plugin_runtime.reload``: a fresh on-disk read of the plugin
    registry and reviewed catalog with a content digest and a truthful
    ``changed`` flag. Qubit never imports/re-executes entrypoints, so this is
    the safe local counterpart of the upstream lifecycle mutation, not a fake
    success and not a no-op that always returns ``reloaded: True``.
    """
    from .. import plugin_runtime as PR
    out = PR.reload(cfg)
    out["code"] = PLUGIN_RELOAD_CODE
    # Params are explicitly ignored by the safe local counterpart; report that
    # honestly instead of pretending the upstream arguments were applied.
    return {**out, "ignored_params": sorted(params) if params else []}


def _plugins_skills_read(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ..verified_skills import verified_skills_store
    store = verified_skills_store(cfg)
    return {"state": "OK", "bundled": store.bundled(),
            "stats": store.stats()}


def _skills_workshop_read(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ..verified_skills import (COLLECTION_CONTRACTS, CURATOR_ROLES,
                                   verified_skills_store)
    store = verified_skills_store(cfg)
    return {"state": "OK", "contracts": COLLECTION_CONTRACTS,
            "curator_roles": CURATOR_ROLES, "stats": store.stats()}


def _transcripts_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import chat_transport as CT
    return {"state": "OK",
            "authority": CT.session_authority(),
            "store": CT.store_boundary()}


def _transcripts_export(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import chat_transport as CT
    sid = str(params.get("session_id", "") or "")
    if not sid:
        raise GatewayError(METHOD_BAD_PARAMS, "session_id required")
    return CT.store(cfg).export(sid)


def _computer_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import computer_use as CU
    return {"state": "OK", "control": CU.control_status(),
            "desktop": CU.status()}


def _talk_voice_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ..audio import settings as AS
    return {"state": "OK", "devices": AS.status(cfg)}


def _talk_voice_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ..audio import settings as AS
    kwargs: Dict[str, Any] = {}
    if "mic" in params:
        kwargs["mic_name"] = params.get("mic")
    if "output" in params:
        kwargs["output_name"] = params.get("output")
    if not kwargs:
        raise GatewayError(METHOD_BAD_PARAMS,
                           "talk.voice.set requires 'mic' and/or 'output'")
    backup = AS.write_audio_config(cfg, **kwargs)
    return {"state": "OK", "backup": str(backup),
            "changed": sorted(kwargs)}


def _speech_profiles_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ..providers import resolver as R
    return R.list_speech_profiles(cfg, params.get("kind") or None)


def _speech_profile_show(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ..providers import resolver as R
    pid = str(params.get("profile_id", "") or "")
    if not pid:
        raise GatewayError(METHOD_BAD_PARAMS, "profile_id required")
    return R.show_speech_profile(cfg, pid)


def _speech_profile_upsert(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ..providers import resolver as R
    from ..providers.speech import SpeechProfile
    pid = str(params.get("profile_id", params.get("id", "")) or "")
    if not pid:
        raise GatewayError(METHOD_BAD_PARAMS, "profile_id required")
    record = {
        "id": pid,
        "kind": str(params.get("kind", "speech_in") or "speech_in"),
        "provider": str(params.get("provider", "") or ""),
        "model": str(params.get("model", "") or ""),
        "voice": str(params.get("voice", "") or ""),
        "device": str(params.get("device", "") or ""),
        "language": str(params.get("language", "") or ""),
        "locality": str(params.get("locality", "local") or "local"),
        "egress_consent": str(params.get("egress_consent", "deny") or "deny"),
        "key_ref": str(params.get("key_ref", "") or ""),
        "base_url": str(params.get("base_url", "") or ""),
        "engine": str(params.get("engine", "") or ""),
        "fallback_to": str(params.get("fallback_to", "") or ""),
    }
    return R.upsert_speech_profile(
        cfg, SpeechProfile.from_record(record),
        confirm=bool(params.get("confirm", False)))


def _speech_profile_remove(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ..providers import resolver as R
    pid = str(params.get("profile_id", "") or "")
    if not pid:
        raise GatewayError(METHOD_BAD_PARAMS, "profile_id required")
    return R.remove_speech_profile(cfg, pid,
                                   confirm=bool(params.get("confirm", False)))


def _sessions_activity_summary_ensure(cfg: Any,
                                      params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import session_activity as SA
    sid = str(params.get("session_id", "") or "")
    if not sid:
        raise GatewayError(METHOD_BAD_PARAMS, "session_id required")
    return SA.build_projection(cfg, sid,
                               git_root=str(params.get("git_root", "") or ""))


def _diagnostics_profile(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(params.get("kind", "cpu") or "cpu")
    return {"state": "UNAVAILABLE",
            "code": "DIAGNOSTICS_OPERATOR_REQUIRED",
            "kind": kind,
            "detail": "CPU/heap profiles require a Node inspector attached to "
                      "the runtime process; Qubit does not attach one, so no "
                      "profile is fabricated.",
            "qubit_counterpart": "operator `node --inspect` + Chrome DevTools"}


def _models_auth(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "REFUSED", "code": "OAUTH_MANUAL_HANDOFF",
            "detail": "No live model OAuth token dance is performed; "
                      "enrollment is a manual handoff in your own browser.",
            "qubit_counterpart": "CLI `qubit auth login --provider`"}


def _models_auth_set_api_key(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``models.authSetApiKey`` — typed raw-secret write refusal.

    Distinct from login/refresh: this upstream method writes a raw API key.
    Qubit never stores a raw secret from a gateway call, so it emits the same
    ``SECRET_WRITE_REFUSED`` code the env/config write paths use and points at
    the reference-only enrollment path.
    """
    return {"state": "REFUSED", "code": "SECRET_WRITE_REFUSED",
            "detail": "Raw API keys are never accepted over the gateway; only "
                      "env:/file: credential references are recorded.",
            "qubit_counterpart": "CLI `qubit auth login --provider` / "
                                 "`qubit auth status` (references only)"}


def _mcp_auth_login(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "REFUSED", "code": "OAUTH_MANUAL_HANDOFF",
            "detail": "No live MCP OAuth flow is performed; enrollment is a "
                      "manual handoff in your own browser.",
            "qubit_counterpart": "CLI `qubit mcp status` + `qubit auth login`"}


def _controlui_pr_checks(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``controlUi.sessionPullRequests.checks`` — typed operator gate.

    Qubit runs no forge service and stores no forge token by default, so remote
    pull-request CI checks cannot be fetched. This is an OPERATOR_ONLY
    capability: the call returns a typed refusal with the exact prerequisite and
    a real local counterpart instead of an unregistered-method error.
    """
    return {"state": "UNAVAILABLE", "code": "FORGE_API_OPERATOR_REQUIRED",
            "detail": "Remote pull-request CI checks require a configured forge "
                      "credential and a network-reachable forge API; Qubit "
                      "stores no forge token by default.",
            "operator_prerequisite": "a configured forge credential (e.g. a "
                                     "GitHub/GitLab token) and a "
                                     "network-reachable forge API",
            "qubit_counterpart": "local change review: CLI `qubit git "
                                 "status|log|diff|show` and the dashboard git "
                                 "review surface"}


def _computer_invoke(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``computer.invoke`` — broker-gated desktop action (OC-13)."""
    from .. import computer_use as CU
    return CU.act(cfg, str(params.get("action", "") or ""),
                  args=params.get("args"),
                  confirmed=bool(params.get("confirmed", False)),
                  requested_by=str(params.get("requested_by", "gateway")))


def _sessions_storage_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``sessions.storage.status`` — canonical state.db storage read."""
    from .. import qubit_session_store as HS
    path = HS.qubit_state_db_path(cfg)
    out: Dict[str, Any] = {"state": "OK", "path": str(path),
                           "exists": path.is_file(),
                           "authority": "voicebridge.qubit_session_store."
                                        "QubitSessionStore"}
    if path.is_file():
        out["bytes"] = path.stat().st_size
        wal = path.with_name(path.name + "-wal")
        out["wal_bytes"] = wal.stat().st_size if wal.is_file() else 0
    return out


def _sessions_storage_run(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``sessions.storage.run`` — confirmed FTS storage maintenance.

    Runs the vendored ``SessionDB.optimize_fts_storage`` (resumable, self
    guarding: it returns ``ok: false`` with a reason when FTS is unavailable or
    the DB is read-only) against the canonical state.db. Confirmation-gated and
    never fabricates a maintenance result.
    """
    from .. import qubit_session_store as HS
    if not bool(params.get("confirmed", False)):
        return {"state": "CONFIRM_REQUIRED",
                "code": "STORAGE_CONFIRM_REQUIRED",
                "detail": "session storage maintenance requires explicit "
                          "confirmation",
                "path": str(HS.qubit_state_db_path(cfg)),
                "qubit_counterpart": "confirmed dashboard/CLI maintenance"}
    path = HS.qubit_state_db_path(cfg)
    if not path.is_file():
        return {"state": "UNAVAILABLE", "code": "STORE_MISSING",
                "detail": "no canonical state.db exists yet", "path": str(path)}
    db = HS._db_for(path)
    result = db.optimize_fts_storage(vacuum=bool(params.get("vacuum", True)))
    return {"state": "OK" if result.get("ok") else "FAILED",
            "path": str(path), "result": result}


def _plugins_credentials_inspect(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``plugins.credentials.inspect`` — credential REFERENCES only.

    Delegates to the canonical ``auth_status`` inventory: provider names and
    ``env:``/``file:`` reference kinds, never a secret value.
    """
    from ..auth_status import auth_status
    out = auth_status(cfg)
    return {"state": "OK", "inventory": out,
            "authority": "credential references only; no value is read or "
                         "returned"}


def _desktop_release(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``desktop.release`` — confirmed release of presentation mode."""
    from .. import computer_use as CU
    return CU.release_presentation(
        cfg, confirmed=bool(params.get("confirmed", False)),
        requested_by=str(params.get("requested_by", "gateway")))


def _claws_monitors(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``claws.monitors`` — package/runtime health over Qubit's
    canonical runtime-image manager (read-only)."""
    from .. import runtime_images as RI
    return {"state": "OK", "images": RI.status(cfg), "doctor": RI.doctor(cfg),
            "authority": "read-only over the canonical Qubit runtime-image "
                         "store; nothing is installed or removed"}


def _claws_packages_remove(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``claws.packages.remove`` — confirmed, audited runtime removal.

    Delegates to ``runtime_images.remove`` (the shared Qubit runtime/package
    manager): confirmation-gated, e-stop-aware, refuses while a supervised run
    is active, and keeps the previous receipt for rollback.
    """
    from .. import runtime_images as RI
    name = str(params.get("name", params.get("package", "")) or "")
    if not name:
        raise GatewayError(METHOD_BAD_PARAMS, "package/image name required")
    return RI.remove(cfg, name,
                     confirmed=bool(params.get("confirmed", False)),
                     requested_by=str(params.get("requested_by", "gateway")))


def _cloud_workers_config_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import cloud_workers as CW
    return CW.config_get(cfg)


def _cloud_workers_config_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import cloud_workers as CW
    return CW.config_set(
        cfg, provider=str(params.get("provider", "") or ""),
        endpoint=str(params.get("endpoint", "") or ""),
        key_ref=str(params.get("key_ref", "") or ""),
        enabled=bool(params.get("enabled", True)),
        confirmed=bool(params.get("confirmed", False)),
        requested_by=str(params.get("requested_by", "gateway")))


def _cloud_workers_snapshot_build(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    from .. import cloud_workers as CW
    return CW.snapshot_build(
        cfg, name=str(params.get("name", "") or ""),
        confirmed=bool(params.get("confirmed", False)),
        requested_by=str(params.get("requested_by", "gateway")))


def _canvas_document_preview(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``canvas.document.preview`` — bounded document preview.

    Qubit runs no Qubit cloud canvas; the safe equivalent is the read-only
    browser document snapshot (title/url/bounded text) the browser tool serves.
    """
    from .. import browser_tabs as BT
    target = str(params.get("target", "") or "")
    out = BT.read(cfg, target)
    return {"state": str(out.get("state", "OK")),
            "preview": {
                "title": str(out.get("title", "") or ""),
                "url": str(out.get("url", "") or ""),
                "text": str(out.get("text", "") or ""),
                "trust": str(out.get("trust", "") or ""),
            },
            "counterpart": "Qubit browser document preview (read-only); the "
                           "hosted cloud canvas is not run",
            "authority": str(out.get("authority", "") or "")}


def _talk_voice_complete(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``talk.voice.complete`` — render spoken audio via local TTS.

    Uses the same configured TTS provider chain the orchestrator and
    ``/api/audio/speak`` select; returns a base64 data URL. An unavailable
    provider is a typed error, never a fabricated clip.
    """
    import base64
    import os
    import tempfile
    from pathlib import Path

    from ..qubit_compat import _tts_from_cfg
    from ..tts.base import TTSUnavailable

    text = str(params.get("text", "") or "").strip()
    if not text:
        raise GatewayError(METHOD_BAD_PARAMS, "text is required")
    out = Path(tempfile.gettempdir()) / (
        f"qubit-rpc-tts-{os.getpid()}-{abs(hash(text)) % 10 ** 8}.wav")
    try:
        tts = _tts_from_cfg(cfg)
        tts.load()
        tts.synthesize(text[:4000], out)
        data = out.read_bytes()
        mime = ("audio/wav" if out.suffix.lower() == ".wav"
                else "audio/mpeg")
        return {"state": "OK", "mime_type": mime,
                "data_url": f"data:{mime};base64,"
                            f"{base64.b64encode(data).decode('ascii')}",
                "provider": str(getattr(tts, "name", "") or "")}
    except TTSUnavailable as exc:
        raise GatewayError("AUDIO_TTS_UNAVAILABLE",
                           f"local TTS provider unavailable: "
                           f"{str(exc)[:160]}") from exc
    finally:
        try:
            if out.exists():
                out.unlink()
        except OSError:
            pass


def _themes_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``themes.list`` — the real local theme catalog.

    Upstream lists built-in, plugin, and user-imported Control UI themes from
    canonical user preferences. Qubit runs one coherent theme system
    (``voicebridge/themes.py``); this reads that real catalog (built-in presets)
    rather than inventing plugin/user theme entries.
    """
    from .. import themes as TH

    out = TH.list_themes()
    return {**out, "authority": "voicebridge.themes.list_themes"}


def _themes_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``themes.get`` — the active preset or a named preset."""
    from .. import themes as TH

    tid = str(params.get("id", params.get("name", "")) or "").strip().lower()
    if tid:
        meta = TH.THEMES.get(tid)
        if meta is None:
            raise GatewayError(METHOD_BAD_PARAMS,
                               f"unknown theme {tid!r}")
        return {"state": "OK", "theme": {
            "id": tid, "name": meta["label"],
            "description": meta["description"],
            "overrides": dict(meta["overrides"])},
            "authority": "voicebridge.themes.THEMES"}
    out = TH.show(cfg)
    return {**out, "authority": "voicebridge.themes.show"}


def _themes_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``themes.set`` — confirmed selection of a real local preset."""
    from .. import themes as TH

    name = str(params.get("id", params.get("name",
                   params.get("theme", ""))) or "")
    if not name:
        raise GatewayError(METHOD_BAD_PARAMS,
                           "themes.set requires 'id' (theme name)")
    out = TH.set_theme(
        cfg, name, confirmed=bool(params.get("confirmed", False)),
        requested_by=str(params.get("requested_by", "gateway")))
    return {**out, "authority": "voicebridge.themes.set_theme"}


def _themes_import(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``themes.import`` — typed refusal of third-party theme code.

    Upstream imports an arbitrary theme definition into user preferences. Qubit
    never installs unpinned third-party theme/sprite code (the pinned-source
    policy), so it refuses with the canonical ``PETS_NO_DOWNLOADS`` code and
    points at the real local counterpart: built-in presets and token tweaks.
    """
    from .. import themes as TH

    out = TH.refuse_pet_downloads()
    return {**out, "code": THEME_IMPORT_CODE,
            "requested_id": str(params.get("id", "") or ""),
            "qubit_counterpart": "`qubit theme` presets + `qubit theme "
                                 "set-tweak`",
            "authority": "voicebridge.themes (pinned-source policy)"}


def _broker_web_dispatch(cfg: Any, capability: str, args: Dict[str, Any], *,
                         source: str):
    """Dispatch a read-only ``web.*`` capability through the canonical broker.

    One decision point: the SSRF-guarded web tools are registered on the SAME
    host broker (e-stop / policy / shared confirmation journal / audit) the
    CLI, dashboard, voice, and planner paths use, so a gateway handler must
    never call ``web_tool.fetch`` directly. ``source`` is the gateway's
    existing operator source (``user``). Returns the broker ``ExecResult``.
    """
    from ..broker import build_host_broker, register_web_tools
    from ..power import power_control

    broker = build_host_broker(power_control=power_control(cfg), cfg=cfg)
    register_web_tools(broker, cfg=cfg)
    return broker.execute(capability, dict(args or {}), source=source)


def _controlui_link_preview(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``controlUi.linkPreview`` — bounded local link fetch.

    Upstream fetches remote favicon/OpenGraph metadata for Control UI hover
    previews. Qubit runs no hosted favicon/OG proxy; the safe local counterpart
    is the SSRF-guarded canonical web fetch (``voicebridge/web_tool.py``) plus
    the browser favicon cache. The fetch is dispatched as the ``web.fetch``
    broker capability, so e-stop / policy / audit / redaction apply exactly
    once through the same junction the planner/model agent-tool path uses.
    """
    import json as _json

    url = str(params.get("url", "") or "").strip()
    if not url:
        raise GatewayError(METHOD_BAD_PARAMS, "url is required")
    res = _broker_web_dispatch(cfg, "web.fetch", {"url": url}, source="user")
    if res.denied:
        raise GatewayError(str(res.denied),
                           "web fetch refused by the shared power control")
    if not res.ok:
        raise GatewayError("WEB_FETCH_FAILED", str(res.error)[:300])
    try:
        out = _json.loads(res.output)
    except (TypeError, ValueError) as exc:
        raise GatewayError("WEB_FETCH_FAILED",
                           "web fetch returned a malformed result") from exc
    return {"state": str(out.get("state", "OK")), "code": LINK_PREVIEW_CODE,
            "url": out.get("url", ""), "origin": out.get("origin", ""),
            "content_type": out.get("content_type", ""),
            "description": str(out.get("text", ""))[:280],
            "qubit_counterpart": "Qubit `web.fetch` (SSRF-guarded) + browser "
                                 "favicon cache",
            "authority": "bounded local fetch; no hosted favicon/OpenGraph "
                         "proxy is run"}


def _controlui_github_detail(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``controlUi.githubDetail`` — typed forge operator gate.

    Qubit runs no forge service and stores no forge token by default, so a
    remote GitHub detail read cannot be performed. OPERATOR_ONLY: the call
    returns the typed prerequisite and a real local counterpart instead of an
    unregistered-method error.
    """
    return {"state": "UNAVAILABLE",
            "code": FORGE_API_OPERATOR_REQUIRED_CODE,
            "detail": "Remote GitHub detail requires a configured forge "
                      "credential and a network-reachable forge API; Qubit "
                      "stores no forge token by default.",
            "operator_prerequisite": "a configured forge credential (e.g. a "
                                     "GitHub/GitLab token) and a "
                                     "network-reachable forge API",
            "qubit_counterpart": "local change review: CLI `qubit git "
                                 "status|log|diff|show` and the dashboard git "
                                 "review surface"}


def _transcripts_summarize(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``transcripts.summarize`` — summarize a real session transcript.

    Reads the canonical chat-session store (the same authority
    ``transcripts.export``/``transcripts.status`` use) and produces a bounded
    summary through ``voicebridge/summarizer.py`` (extractive, no network).
    """
    from .. import chat_transport as CT
    from .. import summarizer as SM

    sid = str(params.get("session_id", params.get("sessionId", "")) or "")
    if not sid:
        raise GatewayError(METHOD_BAD_PARAMS, "session_id is required")
    rows = CT.store(cfg).messages(sid).get("messages", []) or []
    text = "\n".join(str(r.get("content", "")) for r in rows
                     if isinstance(r, dict))
    summary = SM.summarize(text, cfg={"provider": "extractive"})
    return {"state": "OK", "session_id": sid, "summary": summary,
            "messages": len(rows), "chars": len(text),
            "authority": "voicebridge.chat_transport.ChatSessionStore + "
                         "voicebridge.summarizer"}


def _sessions_set_involvement(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``sessions.setInvolvement`` — local hide/show of a session.

    Upstream toggles a per-profile "hidden from Involving Me" flag. Qubit is
    single-operator with no per-user involvement profiles; the safe local
    counterpart is the canonical session archive/restore flag in the shared
    chat-session store. The handler performs the real mutation and emits its
    own code.
    """
    from .. import chat_transport as CT

    sid = str(params.get("key", params.get("session_id", "")) or "")
    if not sid:
        raise GatewayError(METHOD_BAD_PARAMS, "key is required")
    hidden = bool(params.get("hidden", True))
    out = CT.store(cfg).archive(sid, archived=hidden)
    return {**out, "code": SESSION_INVOLVEMENT_CODE,
            "hidden_from_involving_me": hidden,
            "qubit_counterpart": "dashboard session archive/restore",
            "authority": "single-operator local equivalent; no per-user "
                         "involvement profiles exist"}


def _progress_card_refresh(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``progressCard.refresh`` — real session activity projection.

    Upstream dispatches an agent turn to rewrite a progress card. Qubit has no
    progress-card tool; the safe local counterpart is the canonical session
    activity projection (``sessions.activitySummary.ensure``), which reads the
    real activity store, git state, and bounded summary. No agent turn is
    started and no card is fabricated.
    """
    from .. import session_activity as SA

    sid = str(params.get("sessionKey", params.get("session_id", "")) or "")
    if not sid:
        raise GatewayError(METHOD_BAD_PARAMS, "sessionKey is required")
    out = SA.build_projection(cfg, sid)
    return {**out, "code": PROGRESS_CARD_CODE,
            "qubit_counterpart": "sessions.activitySummary.ensure (session "
                                 "activity projection)",
            "authority": "real local activity/git/summary projection; no "
                         "agent-driven progress card exists"}


def _environments_session_status(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``environments.session.status`` — local cloud-worker state read.

    Upstream reports the cloud worker environment attached to a conversation.
    Qubit runs no cloud worker service, so no attachment can exist; this reads
    the real persisted cloud-worker config and reports the typed local status.
    """
    from .. import cloud_workers as CW

    conf = CW.config_get(cfg)
    return {"state": "OK", "code": ENVIRONMENT_SESSION_STATUS_CODE,
            "attachment": None, "environment": None,
            "cloud_workers": conf.get("config", {}),
            "qubit_counterpart": "voicebridge.cloud_workers config + `qubit "
                                 "runtime cells`",
            "authority": "no cloud worker service runs; no attachment exists"}


def _environments_session_create(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``environments.session.create`` — typed cloud operator gate."""
    return {"state": "UNAVAILABLE", "code": RUNTIME_CLOUD_UNAVAILABLE_CODE,
            "detail": "Cloud worker environments require a provider transport "
                      "Qubit does not run; no environment is fabricated.",
            "operator_prerequisite": "an injected cloud-worker provider "
                                     "transport and a reachable provider",
            "qubit_counterpart": "voicebridge.cloud_workers + local process "
                                 "cells (`qubit runtime cells`)"}


def _environments_session_destroy(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``environments.session.destroy`` — typed cloud operator gate."""
    return {"state": "UNAVAILABLE", "code": RUNTIME_CLOUD_UNAVAILABLE_CODE,
            "stopped": False,
            "detail": "No cloud worker environment is attached or attachable; "
                      "nothing is destroyed.",
            "operator_prerequisite": "an injected cloud-worker provider "
                                     "transport and a reachable provider",
            "qubit_counterpart": "voicebridge.cloud_workers + local process "
                                 "cells (`qubit runtime cells`)"}


def _environments_session_exec(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``environments.session.exec`` — typed cloud operator gate.

    Upstream executes a command inside an attached cloud environment. Qubit
    runs no cloud environment; local command execution is the separate,
    policy-gated terminal/PTY surface, never this method.
    """
    return {"state": "UNAVAILABLE", "code": RUNTIME_CLOUD_UNAVAILABLE_CODE,
            "detail": "No matching cloud environment is attached; Qubit does "
                      "not execute commands through this method.",
            "operator_prerequisite": "an attached cloud-worker environment "
                                     "(requires a provider transport)",
            "qubit_counterpart": "local policy-gated terminal/PTY (`/chat`) "
                                 "and `qubit runtime cells`"}


def _pinned_health(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``health`` — real installation-health inventory."""
    from ..lifecycle import health_inventory

    return {"state": "OK", "health": health_inventory(cfg),
            "authority": "voicebridge.lifecycle.health_inventory"}


def _pinned_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``status`` — real runtime inventory."""
    from ..runtime_inventory import runtime_inventory

    return {"state": "OK", "runtime": runtime_inventory(cfg),
            "authority": "voicebridge.runtime_inventory.runtime_inventory"}


def _pinned_logs_tail(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``logs.tail`` — bounded read of the canonical event log."""
    from .. import dashboard_ops as OPS

    try:
        limit = max(1, min(int(params.get("limit",
                                         params.get("lines", 100)) or 100),
                           500))
    except (TypeError, ValueError):
        limit = 100
    out = OPS.run_op(cfg, "logs", {"limit": limit},
                     actor="gateway", session="gateway")
    return {"state": "OK", "limit": limit, "logs": out,
            "authority": "voicebridge.dashboard_ops logs op"}


def _pinned_config_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``config.get`` — the live canonical Qubit config tree."""
    from .. import config as C

    data = getattr(cfg, "data", None)
    if not isinstance(data, dict):
        data = C.Config.load().data
    return {"state": "OK", "config": data,
            "authority": "voicebridge.config.Config"}


def _pinned_config_schema(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``config.schema`` — real field descriptors from the config schema."""
    from .. import config as C
    from ..qubit_compat import _config_fields

    fields = _config_fields(C.DEFAULTS)
    categories = {f["category"] for f in fields.values()}
    order = [str(k) for k in C.DEFAULTS.keys()
             if str(k) in categories and not str(k).startswith("_")]
    return {"state": "OK", "fields": fields, "category_order": order,
            "authority": "voicebridge.config.DEFAULTS"}


# --------------------------------------------------------------------------
# Contractual-pin (v2026.9.2) read slice.
#
# These pinned methods delegate to the SAME canonical Qubit modules the CLI,
# dashboard, `/qubit-chat`, and voice surfaces use. Upstream reads a managed
# cloud/gateway store; Qubit reads its one local authority. Handlers are
# read-mostly; the only safe-local counterparts (plugins.refresh,
# config.openFile) emit a typed code so they are never mistaken for the
# upstream mutation.
# --------------------------------------------------------------------------
def _pin_session_id(params: Dict[str, Any]) -> str:
    return str(params.get("session_id",
                          params.get("sessionId",
                                     params.get("key",
                                                params.get("cursor", "")))) or "")


def _pinned_sessions_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``sessions.list`` — the canonical chat-session store index."""
    from .. import chat_transport as CT

    return CT.store(cfg).list(
        limit=params.get("limit", 50),
        offset=params.get("offset", 0),
        query=str(params.get("search", params.get("query", "")) or ""),
        include_archived=bool(params.get("includeUnknown", True)))


def _pinned_sessions_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``sessions.get`` — one session from the canonical store."""
    from .. import chat_transport as CT

    sid = _pin_session_id(params)
    if not sid:
        raise GatewayError(METHOD_BAD_PARAMS, "session key is required")
    return CT.store(cfg).show(sid)


def _pinned_sessions_search(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``sessions.search`` — bounded query over the canonical store."""
    from .. import chat_transport as CT

    query = str(params.get("query", "") or "")
    return CT.store(cfg).list(
        limit=params.get("limit", 50), query=query,
        include_archived=bool(params.get("includeUnknown", True)))


def _pinned_sessions_catalog_list(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``sessions.catalog.list`` — the canonical session catalog index."""
    from .. import chat_transport as CT

    return CT.store(cfg).list(limit=params.get("limit", 50),
                              query=str(params.get("catalogId", "") or ""))


def _pinned_sessions_catalog_read(cfg: Any,
                                  params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``sessions.catalog.read`` — read one catalog session entry."""
    from .. import chat_transport as CT

    sid = _pin_session_id(params)
    if not sid:
        raise GatewayError(METHOD_BAD_PARAMS, "session key is required")
    return CT.store(cfg).show(sid)


def _pinned_tasks_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``tasks.list`` — the shared task ledger, newest first."""
    from .. import task_ledger as TL

    status = params.get("status")
    if isinstance(status, list):
        status = status[0] if status else None
    return TL.list_tasks(cfg, source=params.get("source"),
                         state=status or params.get("state"),
                         limit=params.get("limit", 100))


def _pinned_tasks_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``tasks.get`` — one task record from the shared ledger."""
    from .. import task_ledger as TL

    task_id = str(params.get("taskId", params.get("task_id", "")) or "")
    if not task_id:
        raise GatewayError(METHOD_BAD_PARAMS, "taskId is required")
    return TL.show(cfg, task_id, source=params.get("source"))


def _pinned_plugins_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``plugins.list`` — the canonical managed-plugin inventory."""
    from .. import plugin_runtime as PR

    return PR.list_plugins(cfg)


def _pinned_plugins_inspect(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``plugins.inspect`` — one managed plugin from the canonical store."""
    from .. import plugin_runtime as PR

    pid = str(params.get("pluginId", params.get("plugin_id",
                   params.get("id", ""))) or "")
    if not pid:
        raise GatewayError(METHOD_BAD_PARAMS, "pluginId is required")
    return PR.get_plugin(cfg, pid)


def _pinned_plugins_search(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``plugins.search`` — bounded query over the canonical catalog."""
    from .. import plugin_runtime as PR

    query = str(params.get("query", "") or "").strip().lower()
    try:
        limit = max(1, min(int(params.get("limit", 25) or 25), 100))
    except (TypeError, ValueError):
        limit = 25
    catalog = PR.plugin_catalog(cfg)
    rows = catalog.get("plugins", []) if isinstance(catalog, dict) else []
    if query:
        def _hit(row: Dict[str, Any]) -> bool:
            hay = " ".join(str(row.get(k, "")) for k in
                           ("id", "name", "description", "kind")).lower()
            return query in hay
        rows = [r for r in rows if _hit(r)]
    return {"state": "OK", "results": rows[:limit], "total": len(rows),
            "query": query,
            "authority": "voicebridge.plugin_runtime.plugin_catalog"}


def _pinned_plugins_refresh(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``plugins.refresh`` — real on-disk plugin-store re-read.

    Upstream refreshes managed plugin metadata and requires a gateway restart
    to load code. Qubit never imports or re-executes entrypoints, so it
    performs the safe observable part (a fresh canonical read with a changed
    digest) and emits its own counterpart code rather than claiming a code load.
    """
    from .. import plugin_runtime as PR

    out = PR.reload(cfg)
    return {**out, "code": PLUGIN_RELOAD_CODE, "restartRequired": False,
            "qubit_counterpart": "dashboard Plugins rescan + CLI `qubit "
                                 "plugin list`",
            "authority": "real on-disk plugin-store re-read; Qubit never "
                         "re-executes plugin code"}


def _pin_cron_job_id(params: Dict[str, Any]) -> int:
    raw = params.get("id", params.get("jobId",
                                       params.get("job_id", 0)))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _pinned_cron_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``cron.list`` — the canonical schedule store, newest first."""
    from .. import schedules as S
    from ..qubit_compat import _cron_shape

    out = S.list_jobs(cfg,
                      include_disabled=bool(params.get("includeDisabled",
                                                      True)),
                      limit=params.get("limit", 50))
    jobs = out.get("jobs", []) if isinstance(out, dict) else []
    return {"state": "OK", "jobs": [_cron_shape(j) for j in jobs],
            "total": len(jobs),
            "authority": "voicebridge.schedules.list_jobs"}


def _pinned_cron_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``cron.get`` — one schedule with its recent runs."""
    from .. import schedules as S
    from ..qubit_compat import _cron_shape

    job_id = _pin_cron_job_id(params)
    if not job_id:
        raise GatewayError(METHOD_BAD_PARAMS, "id is required")
    out = S.get_job(cfg, job_id)
    job = out.get("job", {}) if isinstance(out, dict) else {}
    return {"state": str(out.get("state", "OK")), "job": _cron_shape(job),
            "recent_runs": job.get("recent_runs", []),
            "authority": "voicebridge.schedules.get_job"}


def _pinned_cron_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``cron.status`` — canonical scheduler posture (read-only)."""
    from .. import schedules as S

    return {**S.scheduler_status(cfg),
            "authority": "voicebridge.schedules.scheduler_status"}


def _pinned_cron_runs(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``cron.runs`` — canonical run history across/for schedules."""
    from .. import schedules as S

    return {**S.history(cfg, job_id=_pin_cron_job_id(params),
                        limit=params.get("limit", 30)),
            "authority": "voicebridge.schedules.history"}


def _pinned_exec_approvals_get(cfg: Any,
                               params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``exec.approvals.get`` — the shared confirmation journal.

    Reads the SAME pending-confirmation journal the CLI ``qubit approvals``,
    voice yes/no, and the dashboard settle through; only id/capability/summary
    metadata is returned (redacted), never an executable allowlist edit.
    """
    from pathlib import Path

    from ..approvals import ApprovalJournal

    state_dir = Path(cfg.state_dir())
    journal = ApprovalJournal(state_dir / "approvals.jsonl", default_ttl_s=120.0)
    items = [{"id": a.id, "capability": str(a.capability or ""),
              "summary": str(a.summary or "")[:300],
              "created_at": a.created_at, "expires_at": a.expires_at,
              "state": a.state} for a in journal.pending()]
    data = getattr(cfg, "data", None)
    approvals_cfg = data.get("approvals", {}) if isinstance(data, dict) else {}
    return {"state": "OK", "approvals": items, "count": len(items),
            "resolvedDefaults": approvals_cfg,
            "authority": "voicebridge.approvals.ApprovalJournal (shared "
                         "confirmation journal)"}


def _pinned_memory_search(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``memory.search`` — active recall over the canonical MemoryStore."""
    from .. import active_memory as AM

    query = str(params.get("query", params.get("q", "")) or "")
    session = str(params.get("session", params.get("sessionKey", "")) or "")
    limit = params.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int):
        limit = None
    return AM.recall(cfg, query, session=session, limit=limit)


def _pinned_usage(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``usage.status`` / ``usage.cost`` — canonical usage aggregates."""
    from .. import qubit_session_store as HS

    try:
        days = max(1, min(int(params.get("days", 30) or 30), 365))
    except (TypeError, ValueError):
        days = 30
    usage = HS.analytics_usage(cfg, days=days)
    if usage is None:
        empty = {"total_input": 0, "total_output": 0, "total_cache_read": 0,
                 "total_reasoning": 0, "total_estimated_cost": 0.0,
                 "total_actual_cost": 0.0, "total_sessions": 0,
                 "total_api_calls": 0}
        return {"state": "EMPTY", "totals": empty, "by_model": [], "daily": [],
                "period_days": days,
                "authority": "voicebridge.qubit_session_store.analytics_usage"}
    return {"state": "OK", "totals": usage.get("totals", {}),
            "by_model": usage.get("by_model", []),
            "daily": usage.get("daily", []), "period_days": days,
            "authority": "voicebridge.qubit_session_store.analytics_usage"}


def _pin_toolset_rows(cfg: Any) -> list:
    from .. import toolsets as TS

    # The canonical toolset catalog is the vendored configurable-toolset list
    # (the same authority ``qubit_compat.h_toolsets`` / CLI ``qubit tools``
    # serve), NOT the broker action inventory. ``platform_toolset_states``
    # yields every catalog name; persisted dashboard records are unioned in so a
    # toolset recorded outside the platform list still appears.
    names = list(TS.platform_toolset_states(cfg))
    for name in TS.toolset_states(cfg):
        if name not in names:
            names.append(name)
    rows = TS.toolset_rows(cfg, names)
    # ``toolset_rows`` defaults an unrecorded toolset to enabled; reconcile with
    # the same authority the dashboard read path serves
    # (``effective_toolset_enabled``: persisted record -> vendored platform
    # state -> default) so the pinned gateway catalog never reports a
    # platform-disabled toolset as enabled.
    for row in rows:
        row["enabled"] = TS.effective_toolset_enabled(cfg, str(row.get("name")))
    return rows


def _pinned_tools_catalog(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``tools.catalog`` — canonical toolset/broker catalog (read-only)."""
    rows = _pin_toolset_rows(cfg)
    return {"state": "OK", "tools": rows, "count": len(rows),
            "authority": "voicebridge.toolsets (vendored platform catalog + "
                         "effective_toolset_enabled)"}


def _pinned_tools_effective(cfg: Any,
                            params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``tools.effective`` — the enabled subset of the canonical catalog."""
    rows = _pin_toolset_rows(cfg)
    enabled = [r for r in rows if r.get("enabled")]
    return {"state": "OK", "tools": enabled, "count": len(enabled),
            "considered": len(rows),
            "authority": "voicebridge.toolsets (vendored platform catalog + "
                         "effective_toolset_enabled)"}


def _pinned_hooks_status(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``hooks.status`` — canonical hook declarations + catalog."""
    from .. import hooks as H

    listing = H.list_hooks(cfg)
    catalog = H.bundled()
    hooks = listing.get("hooks", []) if isinstance(listing, dict) else []
    return {"state": "OK", "hooks": hooks, "count": len(hooks),
            "catalog": catalog.get("bundled", []),
            "events_known": catalog.get("events_known", []),
            "authority": "voicebridge.hooks"}


def _pinned_system_info(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``system.info`` — real host + product information."""
    import os
    import platform as _pl

    from .. import distribution as D

    uname = _pl.uname()
    return {"state": "OK", "hostname": uname.node, "platform": uname.system,
            "release": uname.release, "os_version": uname.version,
            "arch": uname.machine, "python_version": _pl.python_version(),
            "python_impl": _pl.python_implementation(),
            "cpu_count": os.cpu_count(), "pid": os.getpid(),
            "product_version": str(getattr(
                D, "PACKAGE_VERSION", getattr(D, "VERSION", ""))),
            "authority": "voicebridge.distribution + host probe"}


def _pinned_gateway_identity_get(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``gateway.identity.get`` — canonical device identity snapshot."""
    from .. import device as DEV

    profile = DEV.profile(cfg)
    return {"state": "OK", "deviceId": str(profile.get("machine_id", "")),
            "enrolled": bool(profile.get("enrolled", False)),
            "profile": profile,
            "authority": "voicebridge.device.profile"}


def _pinned_config_schema_lookup(cfg: Any,
                                 params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``config.schema.lookup`` — a dotted path into the live schema."""
    from .. import config as C
    from ..qubit_compat import _config_fields

    path = str(params.get("path", "") or "").strip()
    if not path:
        raise GatewayError(METHOD_BAD_PARAMS, "path is required")
    fields = _config_fields(C.DEFAULTS)
    parts = [p for p in path.split(".") if p]
    node: Any = C.DEFAULTS
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise GatewayError(METHOD_BAD_PARAMS,
                               f"config schema path not found: {path!r}")
        node = node[part]
    descriptor = fields.get(path)
    value: Any = node if not isinstance(node, dict) else {
        k: node[k] for k in sorted(node) if not str(k).startswith("_")}
    return {"state": "OK", "path": path, "field": descriptor,
            "value": value,
            "authority": "voicebridge.config.DEFAULTS"}


def _pinned_config_open_file(cfg: Any,
                             params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``config.openFile`` — the canonical config path.

    Upstream launches the OS file opener. Qubit does not spawn a GUI handler
    from a gateway call; it returns the real canonical path with its own
    counterpart code so the operator opens it in their editor.
    """
    from .. import config as C

    path = C.resolve_config_path()
    return {"state": "OK", "code": CONFIG_OPEN_LOCAL_PATH_CODE,
            "opened": False, "path": str(path),
            "qubit_counterpart": "CLI `qubit config path` / open the file in "
                                 "your editor",
            "authority": "canonical config path; no GUI opener is spawned"}


def _pinned_agents_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Pinned ``agents.list`` — read-only canonical agent/worker roster."""
    from ..integration_status import worker_status
    from ..runtime_inventory import runtime_inventory

    inv = runtime_inventory(cfg)
    worker = worker_status(cfg)
    agents = [{"id": "qubit-worker", "kind": "worker",
               "route": worker.get("route", "local"),
               "state": worker.get("state", "OK"),
               "enabled": bool(worker.get("agent_enabled", True)),
               "model": str(worker.get("agent_model", "") or "")}]
    adapters = inv.get("adapters") if isinstance(inv.get("adapters"),
                                                 dict) else {}
    for name in sorted(adapters):
        row = adapters[name] if isinstance(adapters[name], dict) else {}
        agents.append({"id": f"adapter:{name}", "kind": "adapter",
                       "route": str(row.get("route", "") or ""),
                       "state": str(row.get("state", "OK") or "OK")})
    return {"state": "OK", "agents": agents, "worker": worker,
            "agent": inv.get("agent"),
            "authority": "read-only aggregation of the canonical worker lane, "
                         "runtime adapters, and agent profile"}


def _gateway_connect(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Qubit ``connect`` — OpenClaw-compatible ``hello-ok`` negotiation.

    The OpenClaw gateway handshake is a ``connect`` request answered with a
    ``hello-ok`` response carrying the required ``server``, ``features``,
    ``snapshot``, ``policy``, and ``auth`` payloads
    (``docs/gateway/protocol/handshake.md``). Qubit serves the same frame on its
    single listener (owner decision D2) but authorizes it through its own
    already-authenticated socket: the negotiated role/scopes are Qubit's fixed
    operator scopes, and a client-supplied ``role``/``scopes`` list can never
    widen them. ``features.methods`` is the real in-process registry; no method
    is advertised that is not actually registered.
    """
    import uuid

    from ..product import PRODUCT_NAME, PRODUCT_VERSION

    from .gateway_ws import MAX_FRAME_BYTES

    conn_id = str(params.get("connId") or uuid.uuid4().hex)
    return {
        "type": "hello-ok",
        "protocol": GATEWAY_PROTOCOL_VERSION,
        "server": {"version": PRODUCT_VERSION, "product": PRODUCT_NAME,
                   "connId": conn_id},
        "features": {"methods": default_registry().names(),
                     "events": list(GATEWAY_EVENTS)},
        "snapshot": {"product": PRODUCT_NAME, "version": PRODUCT_VERSION,
                     "state": "ready",
                     "authority": "voicebridge.gateway.rpc"},
        "policy": {"maxPayload": MAX_FRAME_BYTES,
                   "maxBufferedBytes": MAX_FRAME_BYTES * 2,
                   "tickIntervalMs": 15000},
        "auth": {"role": "operator", "scopes": list(OPERATOR_SCOPES)},
    }


def default_registry() -> MethodRegistry:
    """The canonical Qubit gateway RPC surface (single backend path)."""
    registry = MethodRegistry()
    registry.register("connect", _gateway_connect)
    # Contractual-pin (v2026.9.2) introspection methods backed by real Qubit
    # canonical modules. These are the bounded, provable pinned slice; every
    # other pinned method stays explicitly OPEN in the manifest.
    registry.register("health", _pinned_health)
    registry.register("status", _pinned_status)
    registry.register("logs.tail", _pinned_logs_tail)
    registry.register("config.get", _pinned_config_get)
    registry.register("config.schema", _pinned_config_schema)
    # Contractual-pin read slice: real canonical Qubit reads (sessions, tasks,
    # plugins, cron, approvals, memory, usage, tools, hooks, system, config)
    # plus two typed safe-local counterparts (plugins.refresh,
    # config.openFile). Every handler delegates to the same module the
    # CLI/dashboard/voice use.
    registry.register("sessions.list", _pinned_sessions_list)
    registry.register("sessions.get", _pinned_sessions_get)
    registry.register("sessions.search", _pinned_sessions_search)
    registry.register("sessions.catalog.list", _pinned_sessions_catalog_list)
    registry.register("sessions.catalog.read", _pinned_sessions_catalog_read)
    registry.register("tasks.list", _pinned_tasks_list)
    registry.register("tasks.get", _pinned_tasks_get)
    registry.register("plugins.list", _pinned_plugins_list)
    registry.register("plugins.inspect", _pinned_plugins_inspect)
    registry.register("plugins.search", _pinned_plugins_search)
    registry.register("plugins.refresh", _pinned_plugins_refresh)
    registry.register("cron.list", _pinned_cron_list)
    registry.register("cron.get", _pinned_cron_get)
    registry.register("cron.status", _pinned_cron_status)
    registry.register("cron.runs", _pinned_cron_runs)
    registry.register("exec.approvals.get", _pinned_exec_approvals_get)
    registry.register("memory.search", _pinned_memory_search)
    registry.register("usage.status", _pinned_usage)
    registry.register("usage.cost", _pinned_usage)
    registry.register("tools.catalog", _pinned_tools_catalog)
    registry.register("tools.effective", _pinned_tools_effective)
    registry.register("hooks.status", _pinned_hooks_status)
    registry.register("system.info", _pinned_system_info)
    registry.register("gateway.identity.get", _pinned_gateway_identity_get)
    registry.register("config.schema.lookup", _pinned_config_schema_lookup)
    registry.register("config.openFile", _pinned_config_open_file)
    registry.register("agents.list", _pinned_agents_list)
    registry.register("environments.prepare", _environments_prepare)
    registry.register("task.history", _task_history)
    registry.register("tasks.history", _tasks_history)
    registry.register("task.suggestions", _task_suggestions)
    registry.register("update.report", _update_report)
    registry.register("workspace.files", _workspace_files)
    registry.register("plugin.mutations", _plugin_mutations)
    registry.register("ui.panels", _ui_panels)
    registry.register("ui.panel_layout", _ui_panel_layout)
    registry.register("sessions.sharing", _sessions_sharing)
    registry.register("session.publicShare.set", _session_public_share_set)
    registry.register("session.repository_artifacts",
                      _session_repository_artifacts)
    registry.register("plugins.catalog.browse", _plugins_catalog_browse)
    registry.register("plugins.catalog.categories", _plugins_catalog_categories)
    registry.register("plugins.catalog.get", _plugins_catalog_get)
    registry.register("plugins.reload", _plugins_reload)
    registry.register("plugins.skills.read", _plugins_skills_read)
    registry.register("skills.workshop.read", _skills_workshop_read)
    registry.register("transcripts.status", _transcripts_status)
    registry.register("transcripts.export", _transcripts_export)
    registry.register("computer.status", _computer_status)
    registry.register("computer.invoke", _computer_invoke)
    registry.register("desktop.release", _desktop_release)
    registry.register("sessions.storage.status", _sessions_storage_status)
    registry.register("sessions.storage.run", _sessions_storage_run)
    registry.register("plugins.credentials.inspect",
                      _plugins_credentials_inspect)
    registry.register("talk.voice.get", _talk_voice_get)
    registry.register("talk.voice.set", _talk_voice_set)
    registry.register("sessions.activitySummary.ensure",
                      _sessions_activity_summary_ensure)
    registry.register("diagnostics.cpuProfile",
                      lambda cfg, p: _diagnostics_profile(cfg, {"kind": "cpu"}))
    registry.register("diagnostics.heapProfile",
                      lambda cfg, p: _diagnostics_profile(
                          cfg, {"kind": "heap"}))
    registry.register("models.authLogin", _models_auth)
    registry.register("models.authRefresh", _models_auth)
    registry.register("models.authSetApiKey", _models_auth_set_api_key)
    registry.register("mcp.authLogin", _mcp_auth_login)
    registry.register("controlUi.sessionPullRequests.checks",
                      _controlui_pr_checks)
    # Canonical speech (STT/TTS/realtime) profiles — same registry the
    # orchestrator ASR/TTS seams, the dashboard ops, and the CLI use.
    registry.register("talk.voice.profiles.list", _speech_profiles_list)
    registry.register("talk.voice.profiles.show", _speech_profile_show)
    registry.register("talk.voice.profiles.upsert", _speech_profile_upsert)
    registry.register("talk.voice.profiles.remove", _speech_profile_remove)
    registry.register("claws.monitors", _claws_monitors)
    registry.register("claws.packages.remove", _claws_packages_remove)
    registry.register("canvas.document.preview", _canvas_document_preview)
    registry.register("talk.voice.complete", _talk_voice_complete)
    registry.register("cloud_workers.config.get", _cloud_workers_config_get)
    registry.register("cloud_workers.config.set", _cloud_workers_config_set)
    registry.register("cloud_workers.snapshot.build",
                      _cloud_workers_snapshot_build)
    registry.register("migrations", _migrations)
    # Latest-main (v2026.9.5) delta additions. Real local handlers or typed
    # local-counterpart/operator gates, each emitting its declared code.
    registry.register("themes.list", _themes_list)
    registry.register("themes.get", _themes_get)
    registry.register("themes.set", _themes_set)
    registry.register("themes.import", _themes_import)
    registry.register("controlUi.linkPreview", _controlui_link_preview)
    registry.register("controlUi.githubDetail", _controlui_github_detail)
    registry.register("transcripts.summarize", _transcripts_summarize)
    registry.register("sessions.setInvolvement", _sessions_set_involvement)
    registry.register("progressCard.refresh", _progress_card_refresh)
    registry.register("environments.session.status",
                      _environments_session_status)
    registry.register("environments.session.create",
                      _environments_session_create)
    registry.register("environments.session.destroy",
                      _environments_session_destroy)
    registry.register("environments.session.exec", _environments_session_exec)
    # OpenClaw contractual-pin family handlers. Each family module registers
    # real handlers over the same canonical Qubit backends; composing them here
    # keeps one registry, one listener, and one authority.
    from .qubit_handlers import register_all as _register_families
    _register_families(registry)
    return registry


def handle_request(registry: MethodRegistry, cfg: Any,
                   msg: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a ``req`` frame into a ``res`` frame (never raises for RPC)."""
    rid = msg.get("id")
    if not rid:
        raise GatewayError("FRAME_BAD_SHAPE", "gateway request has no id")
    try:
        payload = registry.handle(cfg, str(msg.get("method", "")),
                                  msg.get("params") or {})
        return {"type": "res", "id": rid, "ok": True, "payload": payload}
    except GatewayError as exc:
        return {"type": "res", "id": rid, "ok": False,
                "error": {"code": exc.code, "message": exc.message,
                          "details": exc.details}}
