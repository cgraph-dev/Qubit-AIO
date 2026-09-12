"""D5 — Qubit code-side kanban rows, driven through the shared dispatcher.

Every test reaches the real production backend (``voicebridge.kanban``) via
the same ``run_op`` / ``confirm_op`` / ``cancel_op`` path the CLI, dashboard,
and voice all use — never a private test-only helper. Covered contract:
real state transitions, persistence, confirmation/cancellation, recovery
(all-or-nothing import; auto-snapshot rollback), per-board access control,
and typed negatives. Hermetic (tmp state dir, no mocks, no network).
"""
from __future__ import annotations

import json

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _op(cfg, op_id, args=None, *, sensitive=None, session="d5"):
    """Run one op through the production dispatcher, confirming inline."""
    from voicebridge import dashboard_ops as OPS

    out = OPS.run_op(cfg, op_id, dict(args or {}), actor="dashboard:user",
                     session=session)
    if out.get("state") == "needs-confirm":
        out = OPS.confirm_op(cfg, out["confirmation_id"],
                             actor="dashboard:user", session=session,
                             sensitive=sensitive)
    return out


def _cancel(cfg, out, *, session="d5"):
    from voicebridge import dashboard_ops as OPS

    return OPS.cancel_op(cfg, out["confirmation_id"], actor="dashboard:user",
                         session=session)


def _setup(cfg):
    from voicebridge import kanban as KB

    KB.init(cfg)
    KB.boards_create(cfg, "main", "main", requested_by="t", confirmed=True)
    return KB.create(cfg, "seed task", board="main", requested_by="t",
                     confirmed=True)["task"]


def _latch(cfg, on=True):
    from voicebridge.power import power_control_store

    store = power_control_store(cfg)
    store.set(emergency_stop=bool(on), global_disable=False,
              issued_by="cli:user")


# ------------------------------------------------------------ role grant/revoke
def test_d5_role_grant_revoke_persist_and_scope_through_dispatcher(
        tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup(cfg)

    grant = _op(cfg, "kanban_role_grant",
                {"board": "main", "principal": "alice", "role": "editor"})
    assert grant["state"] == "executed", grant
    result = grant["receipt"]["result"]
    assert result["principal"] == "alice" and result["role"] == "editor"
    assert result["restricted"] is True
    assert result["snapshot_id"] >= 1
    # Persisted: a fresh read sees the grant.
    assert KB.roles_list(cfg, "main")["roles"] == {"alice": "editor"}

    revoke = _op(cfg, "kanban_role_revoke",
                 {"board": "main", "principal": "alice"})
    assert revoke["state"] == "executed", revoke
    assert revoke["receipt"]["result"]["revoked"] is True
    assert revoke["receipt"]["result"]["restricted"] is False
    assert KB.roles_list(cfg, "main")["roles"] == {}

    # Negative: bad role is a typed failure, never a silent partial grant
    # (rejected by the dispatcher's choice coercion before the backend).
    bad = _op(cfg, "kanban_role_grant",
              {"board": "main", "principal": "bob", "role": "admin"})
    assert bad["state"] == "failed", bad
    assert bad["code"] == "BAD_ARGS", bad
    assert KB.roles_list(cfg, "main")["roles"] == {}


def test_d5_roles_gate_task_moves_through_dispatcher(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup(cfg)
    _op(cfg, "kanban_role_grant",
        {"board": "main", "principal": "mallory", "role": "viewer"})
    task = KB.create(cfg, "viewer job", board="main", assignee="mallory",
                     requested_by="t", confirmed=True)["task"]

    denied = _op(cfg, "kanban_move", {"task_id": task["id"],
                                      "status": "claimed"})
    assert denied["state"] == "failed", denied
    assert denied["code"] == "KANBAN_DENIED", denied
    # The denied move changed nothing.
    assert KB.show(cfg, task["id"])["task"]["status"] == "open"

    # An editor principal may move it.
    _op(cfg, "kanban_role_grant",
        {"board": "main", "principal": "mallory", "role": "editor"})
    moved = _op(cfg, "kanban_move", {"task_id": task["id"],
                                     "status": "claimed"})
    assert moved["state"] == "executed", moved
    assert moved["receipt"]["result"]["task"]["status"] == "claimed"


# ------------------------------------------------------------------ widget move
def test_d5_widget_move_through_dispatcher_and_negatives(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup(cfg)
    widget = KB.widget_add(cfg, "main", "note", "w", requested_by="t",
                           confirmed=True)["widget"]

    moved = _op(cfg, "kanban_widget_move",
                {"widget_id": widget["id"], "x": "6", "y": "2"})
    assert moved["state"] == "executed", moved
    fresh = moved["receipt"]["result"]["widget"]
    assert (fresh["x"], fresh["y"]) == (6, 2)
    assert fresh["snapshot_id"] >= 1
    assert KB.show_widget(cfg, widget["id"])["widget"]["x"] == 6

    # Negative: an unknown widget is typed, never fabricated.
    missing = _op(cfg, "kanban_widget_move", {"widget_id": "99999", "x": "1"})
    assert missing["state"] == "failed", missing
    assert missing["code"] == "KANBAN_NOT_FOUND", missing
    # Negative: out-of-grid geometry fails closed and does not move.
    bad = _op(cfg, "kanban_widget_move", {"widget_id": widget["id"],
                                          "x": "9999"})
    assert bad["state"] == "failed", bad
    assert KB.show_widget(cfg, widget["id"])["widget"]["x"] == 6


# ------------------------------------------------------------- snapshot restore
def test_d5_snapshot_restore_recovers_canvas_through_dispatcher(
        tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup(cfg)
    keep = KB.widget_add(cfg, "main", "note", "keep", requested_by="t",
                         confirmed=True)["widget"]
    # Adding a second widget auto-snapshots the state that already holds
    # ``keep``; that is the canvas we restore.
    KB.widget_add(cfg, "main", "note", "other", requested_by="t",
                  confirmed=True)
    target = KB.snapshot_list(cfg, "main")["snapshots"][0]["id"]
    KB.widget_remove(cfg, keep["id"], requested_by="t", confirmed=True)
    assert [w["title"] for w in KB.widget_list(cfg, "main")["widgets"]] == \
        ["other"]

    restored = _op(cfg, "kanban_snapshot_restore", {"snapshot_id": target})
    assert restored["state"] == "executed", restored
    result = restored["receipt"]["result"]
    assert result["restored_snapshot"] == target
    assert result["pre_restore_backup"] > target
    assert [w["title"] for w in KB.widget_list(cfg, "main")["widgets"]] == \
        ["keep"]

    # Negative: unknown snapshot is typed, nothing changes.
    missing = _op(cfg, "kanban_snapshot_restore", {"snapshot_id": "99999"})
    assert missing["state"] == "failed", missing
    assert missing["code"] == "KANBAN_NOT_FOUND", missing


# ---------------------------------------------------------------------- import
def test_d5_import_through_dispatcher_restores_exactly(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup(cfg)
    snap = KB.export(cfg)["snapshot"]
    KB.create(cfg, "will be rolled back", board="main", requested_by="t",
              confirmed=True)

    out = _op(cfg, "kanban_import", {"snapshot": json.dumps(snap)})
    assert out["state"] == "executed", out
    result = out["receipt"]["result"]
    assert result["restored_tasks"] == 1
    titles = [t["title"] for t in KB.list_tasks(cfg)["tasks"]]
    assert titles == ["seed task"], titles


def test_d5_import_is_all_or_nothing_on_bad_snapshot(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup(cfg)
    KB.create(cfg, "survivor", board="main", requested_by="t", confirmed=True)

    bad = KB.export(cfg)["snapshot"]
    bad["tasks"][0]["status"] = "banana"

    # Direct backend: validated before the first delete, typed error.
    with pytest.raises(KB.KanbanError) as exc:
        KB.import_snapshot(cfg, bad, requested_by="t", confirmed=True)
    assert exc.value.code == KB.BAD_INPUT_CODE
    assert sorted(t["title"] for t in KB.list_tasks(cfg)["tasks"]) == \
        ["seed task", "survivor"]

    # Same via the production dispatcher: failed, store untouched.
    out = _op(cfg, "kanban_import", {"snapshot": json.dumps(bad)})
    assert out["state"] == "failed", out
    assert out["code"] == KB.BAD_INPUT_CODE, out
    assert sorted(t["title"] for t in KB.list_tasks(cfg)["tasks"]) == \
        ["seed task", "survivor"]

    # Malformed / non-object JSON is a typed failure too.
    assert _op(cfg, "kanban_import",
               {"snapshot": "{not json"})["code"] == "KANBAN_BAD_SNAPSHOT"
    assert _op(cfg, "kanban_import",
               {"snapshot": "[1, 2]"})["code"] == "KANBAN_BAD_SNAPSHOT"


# ------------------------------------------------------------- cancel + estop
def test_d5_cancel_leaves_store_unchanged(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup(cfg)
    staged = OPS.run_op(cfg, "kanban_role_grant",
                        {"board": "main", "principal": "alice",
                         "role": "editor"},
                        actor="dashboard:user", session="d5")
    assert staged["state"] == "needs-confirm", staged
    cancelled = _cancel(cfg, staged)
    assert cancelled["state"] == "executed", cancelled
    # Nothing was applied; the confirmation can no longer execute.
    assert KB.roles_list(cfg, "main")["roles"] == {}
    after = OPS.confirm_op(cfg, staged["confirmation_id"],
                           actor="dashboard:user", session="d5")
    assert after["state"] == "failed", after


def test_d5_estop_refuses_mutations_but_keeps_reads(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _setup(cfg)
    try:
        _latch(cfg, True)
        refused = _op(cfg, "kanban_role_grant",
                      {"board": "main", "principal": "alice",
                       "role": "editor"})
        assert refused["state"] == "refused", refused
        assert refused["code"] == "ESTOP_LATCHED", refused
        assert KB.roles_list(cfg, "main")["roles"] == {}
        # Reads stay available while latched.
        assert KB.roles_list(cfg, "main")["state"] == "OK"
        assert KB.board_open(cfg, "main")["state"] == "OK"
    finally:
        _latch(cfg, False)


# ---------------------------------------------------------------- delegated
def test_d5_delegated_notice_is_typed_for_known_and_unknown(tmp_path,
                                                             monkeypatch):
    from voicebridge import kanban as KB

    for sub in ("swarm", "dispatch", "daemon", "watch", "heartbeat", "runs"):
        out = KB.refuse_delegated(sub)
        assert out["state"] == "DELEGATED" and out["code"] == KB.DELEGATED_CODE
        assert out["rollback"] == "nothing started; nothing to roll back"
    # Unknown subcommands are the same typed notice, never a KeyError.
    unknown = KB.refuse_delegated("frobnicate")
    assert unknown["state"] == "DELEGATED"
    assert unknown["code"] == KB.DELEGATED_CODE
    assert "frobnicate" in unknown["explanation"]
    assert "heartbeat" in unknown["known_subcommands"]

    cfg = _cfg(tmp_path, monkeypatch)
    _setup(cfg)
    out = _op(cfg, "kanban_delegated", {"subcommand": "swarm"})
    assert out["state"] == "executed", out
    assert out["receipt"]["result"]["state"] == "DELEGATED"
    assert out["receipt"]["result"]["rollback"] == \
        "nothing started; nothing to roll back"
