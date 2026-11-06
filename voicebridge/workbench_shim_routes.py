"""Workbench shim route table — the ported UI's ``/api/wb/*`` surface.

WHY A SEPARATE FILE (same reasoning as ``voicebridge/hud_routes.py`` and
``voicebridge/workbench/workbench_routes.py``)
---------------------------------------------------------------------------
``voicebridge/qubit_compat.py`` is the one large vendored-compat dispatch table.
This surface is Qubit-owned, so its rows are declared in one file that can be
read end to end, and registered into the compat table by ONE block (delivered,
not applied, in this branch)::

    from .workbench_shim_routes import (workbench_shim_routes as _wb_shim_routes,
                                        workbench_shim_handlers as _wb_shim_handlers)
    for _m, _p, _h in _wb_shim_routes():
        _r(_m, _p, _h)
    HANDLERS.update(_wb_shim_handlers())

WHY ``/api/wb/*`` AND NOT UPSTREAM'S OWN PATHS
---------------------------------------------------------------------------
Qubit already serves ``/api/sessions*``, ``/api/crons`` (a real vendored family),
``/api/status`` and ``/api/commands``-adjacent surfaces with Qubit's own wire
types, and the live dashboard reads them. Binding upstream's shapes onto those
paths would break the dashboard that exists today. ``/api/wb/*`` is the shim
namespace; the shapes live inside it are upstream's, verbatim where Qubit has the
data (``docs/WORKBENCH_SHIM_MAP.md`` has the per-route table).

THE IMPORT RULE (load-bearing)
---------------------------------------------------------------------------
Nothing here imports a Qubit subsystem at import time. Each handler resolves its
authority INSIDE the call. A tree where the session store, the workspace adapter,
the cron store or the approval journal is absent still imports this module and
boots the dashboard; the route answers a typed 503 naming the missing capability
instead of failing the listener with an ImportError. A missing capability is a
refusal, never a crash.

AUTH
---------------------------------------------------------------------------
No route here reads a header. Every ``/agent/api/*`` request already passed the
listener's ``X-Qubit-Session-Token`` gate (``webui._qubit_api``) before dispatch,
so there is one scheme and one enforcement point.

Handler signature is the compat table's: ``handler(cfg, q, b, p) ->
(status_code, payload)``.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from . import workbench_shim as _shim
from .workbench_shim import approvals as _approvals
from .workbench_shim import crons as _crons
from .workbench_shim import sessions as _sessions
from .workbench_shim import system as _system
from .workbench_shim import workspace as _workspace

#: The unported concepts, re-exported so the route module is the single place a
#: reader (and a test) looks to see what this surface does NOT serve.
GAPS: Dict[str, str] = dict(_shim.GAPS)

_FAMILIES = (_sessions, _workspace, _crons, _approvals, _system)

_HANDLERS: Dict[str, Callable[..., Tuple[int, Any]]] = {}
for _family in _FAMILIES:
    _HANDLERS.update(_family.HANDLERS)


def workbench_shim_routes() -> List[Tuple[str, str, str]]:
    """Every shim row as ``(METHOD, pattern, handler_name)``.

    Order matters: the compat table is first-match-wins, so each family lists
    its specific sub-paths before any bare-id row.
    """
    rows: List[Tuple[str, str, str]] = []
    for family in _FAMILIES:
        rows.extend(family.routes())
    return rows


def workbench_shim_handlers() -> Dict[str, Callable[..., Tuple[int, Any]]]:
    """The ``HANDLERS`` entries the names in :func:`workbench_shim_routes` use."""
    return dict(_HANDLERS)
