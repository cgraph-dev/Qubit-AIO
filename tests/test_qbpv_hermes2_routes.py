"""QBPV Agent 2 (Hermes backend + CLI) — route/wiring repairs.

Two byte-parity defects were found by comparing the pinned Hermes backend
(``/tmp/opencode/pinned/hermes-agent`` @ ``2237be35``) and the vendored fork
(``qubit_hermes/``) against the live Qubit compat dispatcher
(``voicebridge.hermes_compat``):

1. ``PUT /api/plugins/kanban/orchestration`` (pinned
   ``plugins/kanban/dashboard/plugin_api.py``) mutates ``config.yaml``. It was
   registered by neither ``hermes_compat`` nor the empty write-proxy allowlist,
   so the active backend answered a bare ``404 NOT_FOUND`` -- not a typed
   Qubit-safe gate. The dashboard plugin UI calls it
   (``frontend/hermes-web/public/plugins/kanban/dist/index.js``
   ``saveSettings``), so the workflow silently failed.

2. ``DELETE /api/sessions/empty`` (pinned
   ``hermes_cli/web_routers/sessions.py``) is the bulk "delete every empty
   session" endpoint (backing ``api.deleteEmptySessions``). The generic
   ``DELETE /api/sessions/{id}`` pattern swallowed it first, so the request was
   treated as "delete the session literally named ``empty``" and answered
   ``SESSION_NOT_FOUND`` -- a false mapping of a bulk destructive surface.

Both repairs route the surface onto the existing typed Qubit policy gate; no
safety gate is weakened. The tests execute the production dispatcher
(:func:`voicebridge.hermes_compat.dispatch`) -- the same function
``voicebridge/webui.py::_hermes_api`` calls for every dashboard API request --
through the real Qubit config/policy stores.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_KANBAN_GATE = "KANBAN_WRITE_BROKERED"
_RECEIPT_GATE = "SESSIONS_RECEIPT_STORE"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nintent:\n  local_llm:\n    enabled: false\n")
    return C.Config.load(p)


# Every pinned kanban plugin write route (pinned plugin_api.py decorators,
# verified against qubit_hermes/plugins/kanban/dashboard/plugin_api.py). Each
# must resolve to the one shared typed broker gate -- never a fabricated 2xx,
# never a bare 404. ``PUT /orchestration`` is the row the previous list missed.
KANBAN_WRITE_ROUTES = [
    ("POST", "/api/plugins/kanban/tasks"),
    ("POST", "/api/plugins/kanban/tasks/bulk"),
    ("PATCH", "/api/plugins/kanban/tasks/task-1"),
    ("DELETE", "/api/plugins/kanban/tasks/task-1"),
    ("POST", "/api/plugins/kanban/tasks/task-1/comments"),
    ("POST", "/api/plugins/kanban/links"),
    ("DELETE", "/api/plugins/kanban/links"),
    ("POST", "/api/plugins/kanban/tasks/task-1/attachments"),
    ("DELETE", "/api/plugins/kanban/attachments/5"),
    ("POST", "/api/plugins/kanban/tasks/task-1/reclaim"),
    ("POST", "/api/plugins/kanban/tasks/task-1/reassign"),
    ("POST", "/api/plugins/kanban/tasks/task-1/specify"),
    ("POST", "/api/plugins/kanban/tasks/task-1/decompose"),
    ("POST", "/api/plugins/kanban/estimate"),
    ("POST", "/api/plugins/kanban/tasks/task-1/estimate"),
    ("POST", "/api/plugins/kanban/tasks/task-1/home-subscribe/telegram"),
    ("DELETE", "/api/plugins/kanban/tasks/task-1/home-subscribe/telegram"),
    ("POST", "/api/plugins/kanban/dispatch"),
    ("POST", "/api/plugins/kanban/boards"),
    ("PATCH", "/api/plugins/kanban/boards/board-1"),
    ("DELETE", "/api/plugins/kanban/boards/board-1"),
    ("POST", "/api/plugins/kanban/boards/board-1/export"),
    ("POST", "/api/plugins/kanban/boards/import"),
    ("POST", "/api/plugins/kanban/boards/board-1/switch"),
    ("PATCH", "/api/plugins/kanban/profiles/profile-1"),
    ("POST", "/api/plugins/kanban/profiles/profile-1/describe-auto"),
    ("POST", "/api/plugins/kanban/runs/5/terminate"),
    ("PUT", "/api/plugins/kanban/orchestration"),
]


def test_every_pinned_kanban_write_is_the_typed_gate(tmp_path, monkeypatch):
    """No pinned kanban write may 404 or fabricate success (regression: PUT
    orchestration). The production dispatcher must answer the shared typed
    broker gate."""
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path in KANBAN_WRITE_ROUTES:
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["disposition"] == "gated", (method, path, route)
        code, out = HC.dispatch(cfg, method, path, {}, {"mode": "auto"})
        assert code == 403, (method, path, code, out)
        assert out["code"] == _KANBAN_GATE, (method, path, out)
        assert out["qubit_counterpart"], (method, path, out)
        assert out["ok"] is False, (method, path, out)
        # confirm can never bypass the gate into the fork/plugin
        code2, out2 = HC.dispatch(cfg, method, path, {"confirm": "true"},
                                  {"mode": "auto", "confirm": True})
        assert code2 == 403 and out2["code"] == _KANBAN_GATE, \
            (method, path, code2, out2)


def test_put_kanban_orchestration_never_mutates_config(tmp_path, monkeypatch):
    """The orchestration PUT is a config write; the active Qubit backend must
    refuse it with a typed gate and never touch config.yaml."""
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    state = Path(cfg.state_dir())
    state.mkdir(parents=True, exist_ok=True)
    before = {str(p) for p in state.rglob("*")}

    code, out = HC.dispatch(
        cfg, "PUT", "/api/plugins/kanban/orchestration", {},
        {"mode": "auto", "max_workers": 4})
    assert code == 403, (code, out)
    assert out["ok"] is False and out["code"] == _KANBAN_GATE, out
    assert out["owner"] and out["rollback"], out

    after = {str(p) for p in state.rglob("*")}
    assert after == before, (sorted(after - before), sorted(before - after))


def test_delete_sessions_empty_is_bulk_gate_not_single_session(tmp_path,
                                                               monkeypatch):
    """DELETE /api/sessions/empty is the bulk empty-session sweep, not a delete
    of a session named ``empty``. It must be the typed receipt-store gate, and
    a real session id must still reach the confirm-gated single-session delete.
    """
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    route, _ = HC.match("DELETE", "/api/sessions/empty")
    assert route is not None, "bulk empty-session route must be registered"
    assert route["disposition"] == "gated", route
    assert route["gate"]["code"] == _RECEIPT_GATE, route

    code, out = HC.dispatch(cfg, "DELETE", "/api/sessions/empty", {}, {})
    assert code == 501, (code, out)
    assert out["ok"] is False and out["code"] == _RECEIPT_GATE, out
    assert out["qubit_counterpart"], out
    # confirm cannot turn the bulk sweep into a fabricated success
    code2, out2 = HC.dispatch(cfg, "DELETE", "/api/sessions/empty", {},
                              {"confirm": True})
    assert code2 == 501 and out2["code"] == _RECEIPT_GATE, (code2, out2)

    # A normal session id is still the confirm-gated single-session handler.
    route, params = HC.match("DELETE", "/api/sessions/rec-abc")
    assert route is not None and route["handler"] == "h_session_delete", route
    assert params.get("id") == "rec-abc", params
    code3, out3 = HC.dispatch(cfg, "DELETE", "/api/sessions/rec-abc", {}, {})
    assert code3 == 409, (code3, out3)
    assert out3["ok"] is False and out3["code"], out3


def test_implemented_routes_all_reach_a_live_handler():
    """Registry invariant: every implemented compat route names a handler that
    exists, so the dispatcher can never answer NO_HANDLER for a declared
    surface. Gated routes must carry a typed code and a Qubit counterpart."""
    from voicebridge import hermes_compat as HC

    missing = [r for r in HC.ROUTES
               if r["disposition"] == "implemented"
               and r["handler"] not in HC.HANDLERS]
    assert missing == [], missing
    for route in HC.ROUTES:
        if route["disposition"] != "gated":
            continue
        gate = route["gate"]
        assert gate.get("code"), route
        # ``_g`` stores the counterpart under ``counterpart``; dispatch maps it
        # onto the ``qubit_counterpart`` envelope field.
        assert gate.get("counterpart") or gate.get("qubit_counterpart"), route
        assert gate.get("owner"), route
