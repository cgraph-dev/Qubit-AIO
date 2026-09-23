"""Wave-2 W3 — Qubit Boards (kanban) full card lifecycle.

The Qubit-native Boards page (`/qubit-boards`) is the Qubit-safe equivalent of
the pinned Kanban plugin's direct writes (which are gated as
`KANBAN_WRITE_BROKERED`). Every mutation runs through `/api/ops/run` ->
`/api/ops/confirm` on the shared broker and persists in the canonical
`<state_dir>/kanban.db`. This test drives the exact ops the plugin wires.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

BOARDS_JS = (REPO_ROOT / "frontend" / "qubit-app" / "plugins"
             / "qubit-boards" / "qubit-boards.js")


def _cfg(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home" / ".hermes"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    from voicebridge.config import Config

    return Config.load(p)


def _settle(HC, cfg, op, args):
    status, payload = HC.dispatch(
        cfg, "POST", "/api/ops/run", {}, {"id": op, "args": args})
    if status == 409:
        cid = payload.get("confirmation_id")
        assert cid, payload
        status, payload = HC.dispatch(
            cfg, "POST", "/api/ops/confirm", {}, {"confirmation_id": cid})
    assert status == 200, (op, status, payload)
    assert payload.get("ok") is True, (op, payload)
    return payload


def _tasks(HC, cfg, board, status=""):
    query = {"board": board}
    if status:
        query["status"] = status
    status_code, payload = HC.dispatch(
        cfg, "GET", "/api/ops/kanban", query, {})
    assert status_code == 200 and payload.get("ok"), payload
    return payload["result"]["tasks"]


def test_boards_plugin_exposes_the_full_card_lifecycle():
    src = BOARDS_JS.read_text(encoding="utf-8")
    for op in ("kanban_assign", "kanban_block", "kanban_unblock",
               "kanban_edit", "kanban_archive", "kanban_restore"):
        assert op in src, op
    for label in ("Assign card", "Block card", "Unblock card",
                  "Archive card", "Restore card", "Save card title",
                  "Select card"):
        assert label in src, label
    assert "/api/ops/confirm" in src


def test_card_lifecycle_persists_through_the_broker(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    _settle(HC, cfg, "kanban_boards_create", {"board": "w3", "name": "W3 Board"})
    status, payload = HC.dispatch(cfg, "GET", "/api/ops/boards", {}, {})
    assert "w3" in [b["board"] for b in payload["result"]["boards"]], payload

    _settle(HC, cfg, "kanban_create", {"title": "W3 task", "board": "w3"})
    tasks = _tasks(HC, cfg, "w3")
    assert [t["title"] for t in tasks] == ["W3 task"], tasks
    task_id = tasks[0]["id"]

    _settle(HC, cfg, "kanban_assign", {"task_id": task_id, "assignee": "alice"})
    assert _tasks(HC, cfg, "w3")[0]["assignee"] == "alice"

    _settle(HC, cfg, "kanban_block", {"task_id": task_id})
    assert _tasks(HC, cfg, "w3")[0]["status"] == "blocked"

    _settle(HC, cfg, "kanban_unblock", {"task_id": task_id})
    assert _tasks(HC, cfg, "w3")[0]["status"] == "open"

    _settle(HC, cfg, "kanban_edit",
            {"task_id": task_id, "title": "W3 task renamed"})
    assert _tasks(HC, cfg, "w3")[0]["title"] == "W3 task renamed"

    _settle(HC, cfg, "kanban_move",
            {"task_id": task_id, "board": "w3", "status": "done"})
    assert _tasks(HC, cfg, "w3")[0]["status"] == "done"

    _settle(HC, cfg, "kanban_complete", {"task_id": task_id})
    _settle(HC, cfg, "kanban_archive", {"task_id": task_id})
    # Archive removes it from the default (unarchived) board view but the
    # record is retained and reachable through the archived filter.
    assert task_id not in [t["id"] for t in _tasks(HC, cfg, "w3")]
    assert task_id in [t["id"] for t in _tasks(HC, cfg, "w3", "archived")]

    _settle(HC, cfg, "kanban_restore", {"task_id": task_id})
    assert task_id in [t["id"] for t in _tasks(HC, cfg, "w3")]


def test_vendored_kanban_writes_are_brokered_not_direct(tmp_path, monkeypatch):
    """The pinned plugin's direct writes must be a typed refusal, not a bypass."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    status, payload = HC.dispatch(
        cfg, "POST", "/api/plugins/kanban/tasks", {},
        {"title": "direct write must not land"})
    # A direct unconfirmed write never lands: it stages a real approval (409)
    # or is a typed broker refusal, never a fabricated success.
    assert status != 200, payload
    assert payload.get("ok") is False
    code = str(payload.get("code", "")).upper()
    assert code in ("CONFIRM_REQUIRED", "KANBAN_WRITE_BROKERED") or \
        "KANBAN" in code or "BROKER" in code, payload
    assert payload.get("confirmation_id") or payload.get("qubit_counterpart"), \
        payload
