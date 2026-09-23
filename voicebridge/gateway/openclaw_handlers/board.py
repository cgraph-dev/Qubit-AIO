"""OpenClaw contractual-pin ``board.*`` family.

Qubit's canonical board authority is the slug-scoped kanban canvas
(:mod:`voicebridge.kanban`) that the CLI ``qubit board``, the dashboard canvas,
voice, and the already-registered ``board.get`` / ``board.widget.put`` /
``board.data.read`` handlers all use. That authority models **tabs and widgets
by id/slug**, not the upstream session-scoped, name-addressed widget sandbox:

* ``board.update`` — the tab operations (``tab_create`` / ``tab_update`` /
  ``tab_delete`` / ``tabs_reorder``) are applied for real through the canonical
  kanban tab primitives; the widget operations address widgets by ``name``,
  which Qubit's board does not have, so they fail closed with a typed code
  before anything is applied (never a silent partial update).
* ``board.widget.grant`` — SAFE_EQUIVALENT: Qubit grants board/widget
  capabilities through the canonical kanban role authority
  (``kanban.role_grant`` / ``tab_role_grant``), so this returns its own code.
* ``board.event`` / ``board.prompt.authorize`` / ``board.action`` —
  OPERATOR_ONLY: they need the board widget sandbox, signed view tickets, and
  the capability authority Qubit does not run.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..gateway_ws import GatewayError

BRD_BAD_PARAMS = "GATEWAY_BAD_PARAMS"

#: Typed code emitted when an op has no Qubit board analogue (name-addressed
#: widget ops). Fail-closed before any mutation is applied.
BOARD_UPDATE_UNSUPPORTED_OP = "BOARD_UPDATE_UNSUPPORTED_OP"
#: SAFE_EQUIVALENT typed code (real local counterpart: kanban roles).
BOARD_WIDGET_GRANT_LOCAL_ROLES = "BOARD_WIDGET_GRANT_LOCAL_ROLES"
#: OPERATOR_ONLY typed codes (missing board sandbox / view-ticket runtime).
BOARD_EVENT_OPERATOR_REQUIRED = "BOARD_EVENT_OPERATOR_REQUIRED"
BOARD_VIEW_TICKET_OPERATOR_REQUIRED = "BOARD_VIEW_TICKET_OPERATOR_REQUIRED"
BOARD_ACTION_OPERATOR_REQUIRED = "BOARD_ACTION_OPERATOR_REQUIRED"

_TAB_OPS = ("tab_create", "tab_update", "tab_delete", "tabs_reorder")
_WIDGET_OPS = ("widget_move", "widget_resize", "widget_remove")


def _brd_str(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _brd_int(value: Any) -> Any:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _brd_board(params: Dict[str, Any]) -> str:
    return _brd_str(params, "board", "slug") or "main"


def _brd_update(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import kanban as K

    board = _brd_board(params)
    ops = params.get("ops")
    if not isinstance(ops, list) or not ops:
        raise GatewayError(BRD_BAD_PARAMS, "ops must be a non-empty array")

    # Pre-validate every op so a request is applied atomically or not at all.
    for op in ops:
        if not isinstance(op, dict):
            raise GatewayError(BRD_BAD_PARAMS, "each op must be an object")
        kind = str(op.get("kind", "") or "")
        if kind in _WIDGET_OPS:
            raise GatewayError(
                BOARD_UPDATE_UNSUPPORTED_OP,
                f"board.update op {kind!r} addresses a widget by name; Qubit's "
                "board addresses widgets by id and has no session-scoped "
                "widget sandbox")
        if kind not in _TAB_OPS:
            raise GatewayError(BRD_BAD_PARAMS, f"unknown board op {kind!r}")

    actor = "gateway:board"
    applied: List[Dict[str, Any]] = []
    for op in ops:
        kind = str(op["kind"])
        if kind == "tab_create":
            slug = _brd_str(op, "tabId", "tab_id")
            title = _brd_str(op, "title", "name")
            if not slug or not title:
                raise GatewayError(BRD_BAD_PARAMS,
                                   "tab_create needs tabId and title")
            K.tab_create(cfg, board, slug=slug, name=title,
                         requested_by=actor, confirmed=True)
            applied.append({"kind": kind, "tabId": slug})
        elif kind == "tab_update":
            slug = _brd_str(op, "tabId", "tab_id")
            if not slug:
                raise GatewayError(BRD_BAD_PARAMS, "tab_update needs tabId")
            title = _brd_str(op, "title")
            position = _brd_int(op.get("position"))
            if title:
                K.tab_rename(cfg, board, slug, title, requested_by=actor,
                             confirmed=True)
            if position is not None:
                K.tab_move(cfg, board, slug, position, requested_by=actor,
                           confirmed=True)
            applied.append({"kind": kind, "tabId": slug})
        elif kind == "tab_delete":
            slug = _brd_str(op, "tabId", "tab_id")
            if not slug:
                raise GatewayError(BRD_BAD_PARAMS, "tab_delete needs tabId")
            K.tab_delete(cfg, board, slug, requested_by=actor, confirmed=True)
            applied.append({"kind": kind, "tabId": slug})
        else:  # tabs_reorder
            tab_ids = op.get("tabIds") or op.get("tab_ids") or []
            if not isinstance(tab_ids, list):
                raise GatewayError(BRD_BAD_PARAMS,
                                   "tabs_reorder needs tabIds array")
            for position, tab in enumerate(tab_ids):
                K.tab_move(cfg, board, str(tab), position, requested_by=actor,
                           confirmed=True)
            applied.append({"kind": kind, "tabIds": [str(t) for t in tab_ids]})

    snap = K.board_open(cfg, board)
    return {"state": "OK", "board": snap.get("board", board),
            "tabs": snap.get("tabs", []), "widgets": snap.get("widgets", []),
            "applied": applied,
            "authority": "voicebridge.kanban.tab_create/tab_rename/tab_move/"
                         "tab_delete + board_open"}


def _brd_widget_grant(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import kanban as K

    board = _brd_board(params)
    return {"state": "UNAVAILABLE", "code": BOARD_WIDGET_GRANT_LOCAL_ROLES,
            "detail": ("Qubit grants board/widget capabilities through the "
                       "canonical kanban role authority, not a per-widget "
                       "grant/revision/instance decision"),
            "qubit_counterpart": ("`qubit board role grant` "
                                  "(kanban.role_grant / tab_role_grant)"),
            "board": board,
            "roles": K.roles_list(cfg, board).get("roles")}


def _brd_event(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import kanban as K

    board = _brd_board(params)
    return {"state": "UNAVAILABLE", "code": BOARD_EVENT_OPERATOR_REQUIRED,
            "detail": ("board.event appends a sandboxed widget notice; Qubit "
                       "runs no board widget sandbox"),
            "operator_prerequisite": ("a board widget sandbox runtime + signed "
                                      "view tickets"),
            "qubit_counterpart": "`qubit board log` (kanban board event log)",
            "board": board, "log": K.log(cfg, board=board).get("events", [])}


def _brd_prompt_authorize(cfg: Any, params: Dict[str, Any]
                          ) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": BOARD_VIEW_TICKET_OPERATOR_REQUIRED,
            "detail": ("board.prompt.authorize validates a signed board view "
                       "ticket; Qubit issues no board view tickets"),
            "operator_prerequisite": "a signed board view-ticket runtime",
            "qubit_counterpart": ("`qubit board roles` + the shared "
                                  "confirmation journal")}


def _brd_action(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE", "code": BOARD_ACTION_OPERATOR_REQUIRED,
            "detail": ("board.action runs a board capability verb through the "
                       "widget sandbox/capability authority; Qubit runs neither"),
            "operator_prerequisite": ("a board widget sandbox + capability "
                                      "authority runtime"),
            "qubit_counterpart": ("`qubit cron` (schedules) + the shared "
                                  "confirmation journal")}


def register(registry: Any) -> None:
    """Register the ``board.*`` handlers on *registry*."""
    registry.register("board.update", _brd_update)
    registry.register("board.widget.grant", _brd_widget_grant)
    registry.register("board.event", _brd_event)
    registry.register("board.prompt.authorize", _brd_prompt_authorize)
    registry.register("board.action", _brd_action)
