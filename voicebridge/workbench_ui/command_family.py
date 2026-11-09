"""The workbench command families on Qubit's git/project authority.

FOUR FAMILIES, ONE CONFINEMENT RULE
---------------------------------------------------------------------------
``git/*``        reads (status, diff, log, branches, worktree list, rev-parse)
                 and confirm-gated writes (stage, discard, commit, checkout,
                 push, pull, worktree add/remove) — :mod:`.git_family`
``worktree/*``   ``git worktree list`` at the workspace top level
``terminal/*``   an AGENT-backed terminal behind upstream's wire
                 — :mod:`.terminal_family` (no shell, no PTY)
``workspace*``   the registry rows / add / rename / remove / reorder, and the
                 refused upload — :mod:`.workspace_family`

Every one of them takes a path, and every path is proven to be inside a
REGISTERED workspace root before a command exists to run
(:mod:`.workspace_scope`). A path outside every root is ``WB_WORKSPACE_OUTSIDE``
(403) and NOTHING is executed — that refusal is the whole point of the module,
so it is checked first, in one place, for every endpoint.

WHY THESE PATHS ANSWERED ``WB_NOT_IN_PORT_SET`` BEFORE
---------------------------------------------------------------------------
``terminal/start|input|resize|close`` were routed to a blanket
``WB_PTY_FORBIDDEN`` refusal; ``workspaces`` listed rows rebuilt from
``/api/projects`` in a different shape; ``workspaces/add|remove|rename|reorder``,
``git/*`` and ``worktree/*`` were not routed at all. The ported page's own
Workspaces panel (``panels.js``) and the composer terminal (``terminal.js``)
therefore either showed nothing or a generic 501.

WHAT STAYS REFUSED, AND WHY (usually because Qubit already decided)
---------------------------------------------------------------------------
``workspace/upload``         a file write through a viewer; Qubit writes are
                             brokered + confirmed (``file_patch``) — typed 403.
``workspaces/reorder``       the registry has no order field; a second ordering
                             would disagree with the CLI/voice — typed 501.
``git/*`` mutations          applied through the ORIGINAL ``/api/git/...``
                             compat rows (same journal, same grant). Re-bound
                             here because the ported page calls THESE paths.
``terminal``                 never a shell — it is the agent session, and every
                             payload says ``backend: "agent"``, ``shell: false``.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from typing import Any as _Any

from . import git_family, terminal_family, workspace_family


def _wrapped(fn: Callable[..., Tuple[int, Any]]) -> Callable[..., Tuple[int, Any]]:
    """A payload-only function with the compat signature ``(cfg, q, b, p)``.

    Handlers that need no request data are still reached the one way the gateway
    calls everything, so nothing here has a second calling convention.
    """

    def _call(cfg: _Any, q: Dict[str, _Any], b: Dict[str, _Any],
              p: Dict[str, _Any]) -> Tuple[int, _Any]:
        return fn(cfg)

    _call.__name__ = getattr(fn, "__name__", "handler")
    return _call

#: ``<ws>/api/<sub>`` → the handler that answers it. Checked EXACT first, then
#: by prefix, so ``session/worktree/status`` keeps its session handler and
#: ``git/branch/switch`` reaches the git family.
ROUTES: Dict[str, Callable[..., Tuple[int, Any]]] = {
    # -- workspace rows and registry writes -------------------------------
    "workspaces": _wrapped(workspace_family.list_payload),
    "workspaces/add": workspace_family.add,
    "workspaces/rename": workspace_family.rename,
    "workspaces/remove": workspace_family.remove,
    "workspaces/reorder": workspace_family.reorder,
    "workspace/upload": workspace_family.upload,
    # -- git reads ---------------------------------------------------------
    "git/status": git_family.status,
    "git/diff": git_family.diff,
    "git/log": git_family.log,
    "git/branches": git_family.branches,
    "git/worktrees": git_family.worktrees,
    "git/review/rev-parse": git_family.rev_parse,
    # -- git / worktree writes (confirm-gated) ------------------------------
    "git/stage": git_family.stage,
    "git/unstage": git_family.unstage,
    "git/discard": git_family.discard,
    "git/commit": git_family.commit,
    "git/branch/switch": git_family.branch_switch,
    "git/checkout": git_family.branch_switch,
    "git/push": git_family.push,
    "git/pull": git_family.pull,
    "git/fetch": git_family.fetch,
    "git/worktree/add": git_family.worktree_add,
    "git/worktree/remove": git_family.worktree_remove,
    "worktree/list": git_family.worktrees,
    "worktree/add": git_family.worktree_add,
    "worktree/remove": git_family.worktree_remove,
    "worktrees": git_family.worktrees,
    # -- terminal ----------------------------------------------------------
    "terminal/start": terminal_family.start,
    "terminal/input": terminal_family.input_text,
    "terminal/resize": terminal_family.resize,
    "terminal/close": terminal_family.close,
}

#: ``/api/worktrees`` and ``/api/worktree`` mean the same list as
#: ``/api/git/worktrees`` for a client that has only a workspace path.
PREFIXES: Tuple[Tuple[str, Callable[..., Tuple[int, Any]]], ...] = ()

#: Paths served as SSE rather than JSON (the pane opens an EventSource).
STREAMS = frozenset({"terminal/output"})


def stream_frames(sub: str, cfg: Any, q: Dict[str, str],
                  *, should_stop: Any = None):
    """SSE frames for a stream path this family owns, or ``None``."""
    clean = str(sub or "").strip("/")
    if clean == "terminal/output":
        return terminal_family.output_frames(cfg, dict(q or {}),
                                             should_stop=should_stop)
    return None


def handler_for(sub: str) -> Callable[..., Tuple[int, Any]] | None:
    """The handler for a path below ``<mount>/api/``, or ``None``."""
    clean = str(sub or "").strip("/")
    if not clean:
        return None
    direct = ROUTES.get(clean)
    if direct is not None:
        return direct
    for prefix, fn in PREFIXES:
        if clean.startswith(prefix) and len(clean) > len(prefix):
            return fn
    return None


__all__ = ["ROUTES", "STREAMS", "handler_for", "stream_frames",
           "git_family", "terminal_family", "workspace_family"]
