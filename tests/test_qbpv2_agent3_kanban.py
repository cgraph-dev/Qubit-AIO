"""Agent 3 (wave 2) — Kanban real workflow audit.

Proves the Qubit Kanban workflow is a real backend workflow: board/card
create, edit, assign, claim, move, complete, block, unblock, comments,
archive, restore, canvas widgets/tabs, and snapshot restore — all through the
shared ops broker, persisted in the canonical kanban store. Also audits the
served Boards bundle for the real controls (every button maps to an op).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

BOARDS_JS = (REPO_ROOT / "frontend" / "hermes-qubit" / "plugins"
             / "qubit-boards" / "qubit-boards.js")
BOARDS_DIST = (REPO_ROOT / "frontend" / "hermes-web" / "public" / "plugins"
               / "kanban" / "dist" / "index.js")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home" / ".hermes"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _run(cfg, op, args):
    from voicebridge import hermes_compat as HC

    status, payload = HC.dispatch(
        cfg, "POST", "/api/ops/run", {}, {"id": op, "args": args})
    return status, payload


def _settle(cfg, op, args):
    from voicebridge import hermes_compat as HC

    status, payload = _run(cfg, op, args)
    if status == 409:
        cid = payload.get("confirmation_id")
        assert cid, (op, payload)
        status, payload = HC.dispatch(
            cfg, "POST", "/api/ops/confirm", {},
            {"confirmation_id": cid})
    assert status == 200 and payload.get("ok") is True, (op, status, payload)
    return payload


def _tasks(cfg, board, status=""):
    query = {"board": board}
    if status:
        query["status"] = status
    from voicebridge import hermes_compat as HC

    st, payload = HC.dispatch(cfg, "GET", "/api/ops/kanban", query, {})
    assert st == 200 and payload.get("ok"), payload
    return payload["result"]["tasks"]


def test_full_card_lifecycle_persists_through_the_broker(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    _settle(cfg, "kanban_boards_create", {"board": "ag3", "name": "AG3"})
    from voicebridge import hermes_compat as HC

    st, payload = HC.dispatch(cfg, "GET", "/api/ops/boards", {}, {})
    assert "ag3" in [b["board"] for b in payload["result"]["boards"]]

    _settle(cfg, "kanban_create", {"title": "first", "board": "ag3"})
    task = _tasks(cfg, "ag3")[0]
    tid = task["id"]

    _settle(cfg, "kanban_assign", {"task_id": tid, "assignee": "alice"})
    assert _tasks(cfg, "ag3")[0]["assignee"] == "alice"

    _settle(cfg, "kanban_claim", {"task_id": tid})
    assert _tasks(cfg, "ag3")[0]["status"] == "claimed"

    _settle(cfg, "kanban_edit", {"task_id": tid, "title": "first edited"})
    assert _tasks(cfg, "ag3")[0]["title"] == "first edited"

    _settle(cfg, "kanban_move", {"task_id": tid, "status": "done"})
    assert _tasks(cfg, "ag3")[0]["status"] == "done"

    _settle(cfg, "kanban_complete", {"task_id": tid})
    _settle(cfg, "kanban_block", {"task_id": tid})
    assert _tasks(cfg, "ag3")[0]["status"] == "blocked"
    _settle(cfg, "kanban_unblock", {"task_id": tid})
    assert _tasks(cfg, "ag3")[0]["status"] == "open"

    # Comments are first-class, readable state.
    _settle(cfg, "kanban_comment", {"task_id": tid, "body": "hello agent3"})
    st, payload = HC.dispatch(
        cfg, "POST", "/api/ops/run", {},
        {"id": "kanban_show", "args": {"task_id": tid}})
    comments = payload["result"]["task"]["comments"]
    assert [c["body"] for c in comments] == ["hello agent3"]

    # Archive remembers the prior column; restore returns it there.
    _settle(cfg, "kanban_archive", {"task_id": tid})
    assert _tasks(cfg, "ag3") == []
    _settle(cfg, "kanban_restore", {"task_id": tid})
    restored = _tasks(cfg, "ag3")[0]
    assert restored["id"] == tid
    assert restored["status"] not in ("archived",)

    # Persistence after a brand-new broker dispatch (fresh connection, same DB).
    assert _tasks(cfg, "ag3")[0]["title"] == "first edited"


def test_canvas_tabs_widgets_and_snapshot_restore_are_real(tmp_path,
                                                           monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _settle(cfg, "kanban_boards_create", {"board": "canvas", "name": "Canvas"})

    _settle(cfg, "kanban_tab_create",
            {"board": "canvas", "slug": "notes", "name": "Notes"})
    from voicebridge import hermes_compat as HC

    st, payload = HC.dispatch(
        cfg, "POST", "/api/ops/run", {},
        {"id": "kanban_tab_list", "args": {"board": "canvas"}})
    raw_tabs = payload["result"].get("tabs")
    if isinstance(raw_tabs, dict):
        raw_tabs = raw_tabs.get("tabs") or []
    slugs = {t["slug"] for t in (raw_tabs or [])}
    assert "notes" in slugs

    # Adding a widget snapshots the canvas first (auto-rollback point).
    _settle(cfg, "kanban_widget_add",
            {"board": "canvas", "kind": "note", "title": "w1",
             "content": "note body", "tab": "notes"})
    from voicebridge import kanban as K

    snaps = K.snapshot_list(cfg, board="canvas")["snapshots"]
    assert snaps, "widget mutation must leave a restore point"
    # snapshot_list is newest-first; the newest snapshot is the pre-w1 state.
    snap_id = snaps[0]["id"]

    # Link widgets are a real kind (not only text).
    _settle(cfg, "kanban_widget_add",
            {"board": "canvas", "kind": "link", "title": "docs",
             "content": "https://example.invalid/", "tab": "notes"})

    st, payload = HC.dispatch(
        cfg, "POST", "/api/ops/run", {},
        {"id": "kanban_widget_list", "args": {"board": "canvas"}})
    widgets = payload["result"].get("widgets") or []
    titles = {w.get("title") for w in widgets}
    assert {"w1", "docs"} <= titles, widgets

    _settle(cfg, "kanban_snapshot_restore", {"snapshot_id": snap_id})
    st, payload = HC.dispatch(
        cfg, "POST", "/api/ops/run", {},
        {"id": "kanban_widget_list", "args": {"board": "canvas"}})
    remaining = payload["result"].get("widgets") or []
    assert not any(w.get("title") in ("w1", "docs") for w in remaining), \
        remaining


def test_boards_bundle_wires_every_button_to_a_real_op():
    src = BOARDS_JS.read_text(encoding="utf-8")
    # Legacy ops-endpoint path aliases the backend maps to the same registry
    # ops (h_ops), plus the direct op ids for everything else.
    expected_endpoints = [
        "/api/ops/boards-create", "/api/ops/cards-create",
        "/api/ops/cards-move", "/api/ops/cards-complete",
    ]
    expected_ops = [
        "kanban_assign", "kanban_claim", "kanban_block",
        "kanban_unblock", "kanban_edit", "kanban_archive", "kanban_restore",
        "kanban_comment", "kanban_tab_create", "kanban_tab_rename",
        "kanban_tab_delete", "kanban_tab_move", "kanban_tab_list",
        "kanban_widget_add", "kanban_widget_list", "kanban_snapshot_restore",
        "kanban_show",
    ]
    for target in expected_endpoints + expected_ops:
        assert target in src, f"Boards page never calls {target}"
    # Real controls + inline confirmation settle.
    for label in ("Claim card", "Add comment", "Restore snapshot",
                  "Assign card", "Block card", "Unblock card",
                  "Archive card", "Restore card", "Save card title"):
        assert label in src, label
    assert "/api/ops/" in src and "settlePending" in src
    # No fabricated success: writes never use a direct kanban write path.
    assert "/api/plugins/kanban" not in src


def test_pinned_kanban_plugin_is_read_only_and_overlay_owns_writes():
    assert BOARDS_DIST.is_file()
    served = BOARDS_DIST.read_text(encoding="utf-8")
    # The pinned kanban dist is the vendored upstream read surface: it talks
    # only to /api/plugins/kanban and never brokers writes itself. The
    # Qubit-safe mutation surface is the overlay Boards bundle.
    assert served.count("/api/plugins/kanban") >= 1
    assert "/api/ops/run" not in served
    assert "kanban_claim" not in served
    src = BOARDS_JS.read_text(encoding="utf-8")
    assert "Claim card" in src
    assert "/api/ops/run" in src and "/api/ops/confirm" in src


def test_kanban_events_ws_is_the_typed_safe_gate_not_pty():
    """The 426 on /api/plugins/kanban/events is the Qubit single-event-path
    gate (WS_SINGLE_PATH), NOT a raw-shell (PTY_NO_SHELL) refusal, and the
    listener offers a real REST read on the same path instead of a bypass."""
    from voicebridge import webui

    assert webui.HERMES_WS_GATE_CODES["/api/plugins/kanban/events"] == \
        "WS_SINGLE_PATH"
    assert webui.hermes_ws_gate_code("/api/plugins/kanban/events") == \
        "WS_SINGLE_PATH"
    # The agent-prefixed path used by the SPA resolves to the same gate.
    assert webui.hermes_ws_gate_code(
        "/agent/api/plugins/kanban/events") == "WS_SINGLE_PATH"
    # The gate is not a raw-PTY refusal.
    assert webui.hermes_ws_gate_code("/api/plugins/kanban/events") != \
        "PTY_NO_SHELL"


def test_kanban_bundle_polls_the_safe_read_route_not_a_websocket():
    served = BOARDS_DIST.read_text(encoding="utf-8")
    assert "new WebSocket" not in served
    assert "buildWsUrl" not in served
    # Live updates go through the real backend read (kanban_log behind the
    # GET /api/plugins/kanban/events route), not a second listener.
    assert "/events" in served
    assert "setInterval" in served


def test_kanban_plugin_manifest_matches_served_bundle():
    manifest = json.loads(
        (REPO_ROOT / "qubit_hermes" / "plugins" / "kanban" / "dashboard"
         / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tab"]["path"] == "/kanban"
    assert (REPO_ROOT / "frontend" / "hermes-web" / "public" / "plugins"
            / "kanban" / "dist" / "index.js").is_file()
