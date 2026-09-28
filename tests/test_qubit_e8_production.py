"""E8 — production closeout for the remaining code-side Qubit rows.

Every assertion drives a real, shipped caller; never a status tile or a
test-only helper. The three surfaces converge on one backend:

* CLI        ``voicebridge.cli._cmd_kanban``
* dashboard  ``voicebridge.dashboard_ops.run_op`` / ``confirm_op``
* voice      ``voicebridge.voice_commands`` registry + family backends

Covered here: kanban canvas state transitions/recovery/access control
(snapshot restore is reversible, roles gate moves, import is all-or-nothing),
Brain export/sweep scoping and persistence, the deny-by-default proxy
refusal, and the portable restore rollback contract (a failed restore never
leaves a partial store). Editor/acp/lsp plus the themes/skills/agent rows are
typed refusals by design; the tests pin the exact typed state so the ledger
stays honest rather than claiming a server/loader that is not shipped.

Hermetic: tmp state dirs, no network, no credentials, no live server, and no
second memory/broker/listener authority.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch, body: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + body)
    return C.Config.load(p)


class _NS:
    """Minimal CLI namespace (only the fields ``_cmd_kanban`` reads)."""

    def __init__(self, **kw):
        self.kanban_command = kw.pop("cmd", "list")
        self.__dict__.update(kw)


def _cli(cfg, cmd, **kw):
    from voicebridge import cli as CLI

    kw.setdefault("confirm", False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_kanban(cfg, _NS(cmd=cmd, **kw))
    return rc, json.loads(buf.getvalue() or "{}")


def _dash(cfg, op_id, args=None, *, session="e8"):
    from voicebridge import dashboard_ops as OPS

    out = OPS.run_op(cfg, op_id, dict(args or {}), actor="dashboard:user",
                     session=session)
    if out.get("state") == "needs-confirm":
        out = OPS.confirm_op(cfg, out["confirmation_id"],
                             actor="dashboard:user", session=session)
    return out


def _voice(cfg, entry_id, session="e8", **args):
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY[entry_id]
    ctx = VC.make_ctx(cfg, actor="voice:user", session=session, source="user")
    out = VC.execute(ctx, VC.Resolution(VC.MATCH, entry=entry, args=args))
    if out.kind == VC.NEEDS_CONFIRM:
        out = VC.confirm_pending(ctx, out.confirmation_id)
    return out


def _board(cfg, slug="main", name="Main"):
    from voicebridge import kanban as KB

    KB.init(cfg)
    KB.boards_create(cfg, slug, name, requested_by="e8", confirmed=True)
    return KB


# ---------------------------------------------------- kanban cross-surface
def test_e8_kanban_role_grant_revoke_share_one_backend_across_surfaces(
        tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)

    # Dashboard grants; the canonical store is the one authority.
    grant = _dash(cfg, "kanban_role_grant",
                  {"board": "main", "principal": "alice", "role": "editor"})
    assert grant["state"] == "executed", grant
    assert grant["receipt"]["result"]["restricted"] is True
    assert KB.roles_list(cfg, "main")["roles"] == {"alice": "editor"}

    # CLI sees the persisted grant and revokes it through the same store.
    rc, out = _cli(cfg, "role-revoke", board="main", principal="alice",
                   confirm=True)
    assert rc == 0 and out["revoked"] is True, out
    assert KB.roles_list(cfg, "main")["roles"] == {}

    # Voice grants a different principal; the change persists for CLI reads.
    voiced = _voice(cfg, "kanban_role_grant_voice", board="main",
                    principal="bob", role="viewer")
    assert voiced.kind == "executed", voiced.speech
    assert KB.roles_list(cfg, "main")["roles"] == {"bob": "viewer"}
    rc, out = _cli(cfg, "roles", board="main")
    assert rc == 0 and out["roles"] == {"bob": "viewer"}, out

    # Typed negative: an unknown role never widens access.
    rc, out = _cli(cfg, "role-grant", board="main", principal="eve",
                   role="admin", confirm=True)
    assert rc == 2 and out["code"] == KB.BAD_INPUT_CODE, out
    assert KB.roles_list(cfg, "main")["roles"] == {"bob": "viewer"}


def test_e8_kanban_widget_move_parity_and_access_control(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    widget = KB.widget_add(cfg, "main", "note", "w", requested_by="e8",
                           confirmed=True)["widget"]

    # CLI moves it; dashboard observes the same persisted geometry.
    rc, out = _cli(cfg, "widget-move", widget_id=widget["id"], x=3, y=1,
                   w=None, h=None, confirm=True)
    assert rc == 0 and (out["widget"]["x"], out["widget"]["y"]) == (3, 1), out
    assert KB.show_widget(cfg, widget["id"])["widget"]["x"] == 3

    # Voice moves the same widget via the family backend (geometry parse).
    voiced = _voice(cfg, "kanban_widget_move_voice", widget_id=widget["id"],
                    geometry="9 4")
    assert voiced.kind == "executed", voiced.speech
    moved = voiced.receipt["result"]["widget"]
    assert (moved["x"], moved["y"]) == (9, 4), moved

    # Negative through the dispatcher: out-of-grid geometry fails closed.
    bad = _dash(cfg, "kanban_widget_move",
                {"widget_id": str(widget["id"]), "x": "9999"})
    assert bad["state"] == "failed", bad
    assert KB.show_widget(cfg, widget["id"])["widget"]["x"] == 9

    # Access control: a viewer role restricts the board and may not move.
    KB.role_grant(cfg, "main", "mallory", "viewer", requested_by="e8",
                  confirmed=True)
    task = KB.create(cfg, "viewer job", board="main", assignee="mallory",
                     requested_by="e8", confirmed=True)["task"]
    denied = _dash(cfg, "kanban_move", {"task_id": task["id"],
                                        "status": "claimed"})
    assert denied["state"] == "failed", denied
    assert denied["code"] == "KANBAN_DENIED", denied
    assert KB.show(cfg, task["id"])["task"]["status"] == "open"
    # Revoking the restriction returns the board to unrestricted.
    KB.role_revoke(cfg, "main", "mallory", requested_by="e8", confirmed=True)
    reopened = _dash(cfg, "kanban_move", {"task_id": task["id"],
                                          "status": "claimed"})
    assert reopened["state"] == "executed", reopened


def test_e8_kanban_snapshot_restore_is_reversible_recovery(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    task = KB.create(cfg, "recoverable", board="main", requested_by="e8",
                     confirmed=True)["task"]
    KB.widget_add(cfg, "main", "note", "w1", requested_by="e8",
                  confirmed=True)
    # The snapshot returned by a mutation captures the canvas *before* it.
    target = KB.role_grant(cfg, "main", "alice", "editor", requested_by="e8",
                           confirmed=True)["snapshot_id"]
    KB.move_task(cfg, task["id"], status="done", requested_by="e8",
                 confirmed=True)
    KB.role_revoke(cfg, "main", "alice", requested_by="e8", confirmed=True)
    assert KB.show(cfg, task["id"])["task"]["status"] == "done"
    assert KB.roles_list(cfg, "main")["roles"] == {}

    restored = _dash(cfg, "kanban_snapshot_restore", {"snapshot_id": target})
    assert restored["state"] == "executed", restored
    result = restored["receipt"]["result"]
    # Widgets, roles, and task positions all come back from the snapshot.
    assert KB.roles_list(cfg, "main")["roles"] == {}
    assert KB.show(cfg, task["id"])["task"]["status"] == "open"
    assert result["tasks_repositioned"] == 1
    # The pre-restore backup is itself a restorable snapshot: real recovery.
    undo = KB.snapshot_restore(cfg, result["pre_restore_backup"],
                               requested_by="e8", confirmed=True)
    assert undo["state"] == "OK"
    assert KB.show(cfg, task["id"])["task"]["status"] == "done"
    assert [w["x"] for w in KB.widget_list(cfg, "main")["widgets"]] == [0]


def test_e8_kanban_import_all_or_nothing_via_cli(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    KB.create(cfg, "keep", board="main", requested_by="e8", confirmed=True)
    snap = KB.export(cfg)["snapshot"]
    KB.create(cfg, "extra", board="main", requested_by="e8", confirmed=True)

    good = tmp_path / "good.json"
    good.write_text(json.dumps(snap))
    rc, out = _cli(cfg, "import", snapshot=str(good), confirm=True)
    assert rc == 0 and out["restored_tasks"] == 1, out
    assert [t["title"] for t in KB.list_tasks(cfg)["tasks"]] == ["keep"]

    # Malformed input is a typed failure and never a partial restore.
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    other = KB.create(cfg, "survivor", board="main", requested_by="e8",
                      confirmed=True)["task"]
    rc, out = _cli(cfg, "import", snapshot=str(bad), confirm=True)
    assert rc == 2 and out["code"] == KB.IO_CODE, out
    assert KB.show(cfg, other["id"])["task"]["title"] == "survivor"


def test_e8_kanban_delegated_and_worker_rows_are_typed(tmp_path, monkeypatch):
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    _board(cfg)
    # Every execution-shaped subcommand is a typed delegation, never a start.
    for sub in ("swarm", "dispatch", "daemon", "watch", "heartbeat", "runs"):
        rc, out = _cli(cfg, sub)
        assert rc == 2 and out["code"] == KB.DELEGATED_CODE, out
        assert out["rollback"] == "nothing started; nothing to roll back"
    unknown = KB.refuse_delegated("worker")
    assert unknown["state"] == "DELEGATED"
    assert unknown["code"] == KB.DELEGATED_CODE

    # Dashboard + voice reach the same typed notice.
    dash = _dash(cfg, "kanban_delegated", {"subcommand": "swarm"})
    assert dash["state"] == "executed"
    assert dash["receipt"]["result"]["state"] == "DELEGATED"
    voiced = _voice(cfg, "kanban_swarm")
    assert voiced.kind == "executed", voiced.speech
    assert voiced.receipt["result"]["state"] == "DELEGATED"


# ----------------------------------------------------------------- brain
def test_e8_brain_export_sweep_scope_and_persistence(tmp_path, monkeypatch):
    from voicebridge import brain as BR

    cfg = _cfg(tmp_path, monkeypatch)
    BR.principal_add(cfg, "owner", "person", "Owner", requested_by="e8",
                     confirmed=True)
    BR.principal_add(cfg, "other", "person", "Other", requested_by="e8",
                     confirmed=True)
    BR.profile_set(cfg, "owner", "display_name", "Ada", retention_days=0,
                   requested_by="e8", confirmed=True)
    BR.profile_set(cfg, "owner", "city", "NYC", requested_by="e8",
                   confirmed=True)
    BR.profile_set(cfg, "other", "display_name", "Eve", requested_by="e8",
                   confirmed=True)

    # Sweep through the dashboard removes only the expired owner field.
    swept = _dash(cfg, "brain_sweep", {})
    assert swept["state"] == "executed", swept
    owner = {f["key"]: f for f in BR.profile_show(cfg, "owner")["fields"]}
    assert "display_name" not in owner and "city" in owner
    assert BR.profile_show(cfg, "other")["fields"][0]["key"] == "display_name"

    # Export is scoped: one principal never sees another principal's data.
    exported = _dash(cfg, "brain_export", {"principal": "owner"})
    assert exported["state"] == "executed", exported
    blob = json.dumps(exported["receipt"]["result"])
    assert "NYC" in blob and "Ada" not in blob and "Eve" not in blob

    # Voice export reaches the same scoped backend.
    voiced = _voice(cfg, "brain_profile_export", principal="owner")
    assert voiced.kind == "executed", voiced.speech
    assert voiced.receipt["result"]["profiles"]["default"]["city"]["value"] \
        == "NYC"

    # Secret-class fields are refused at the field gate (references only).
    with pytest.raises(BR.BrainError) as exc:
        BR.profile_set(cfg, "owner", "api_key", "sk-nope", requested_by="e8",
                       confirmed=True)
    assert exc.value.code == BR.SECRET_CLASS_CODE
    # Persistence: a fresh read of the store still has the surviving field.
    assert {f["key"] for f in BR.profile_show(cfg, "owner")["fields"]} == \
        {"city"}


# ----------------------------------------------------------------- proxy
def test_e8_proxy_refusal_status_and_providers(tmp_path, monkeypatch):
    from voicebridge import proxy_status as PS

    cfg = _cfg(tmp_path, monkeypatch)
    dash = _dash(cfg, "proxy_start", {})
    assert dash["state"] == "executed", dash
    result = dash["receipt"]["result"]
    assert result == PS.refuse_start()
    assert result["code"] == PS.NO_SERVER_CODE

    # Voice reaches the same refusal through its family backend.
    from voicebridge import voice_commands as VC

    ctx = VC.make_ctx(cfg, actor="voice:user", session="e8", source="user")
    voiced = VC._b_proxy(ctx, {"action": "start"})["refused"]
    assert voiced == PS.refuse_start()

    # Reads stay real and value-free.
    assert PS.status()["state"] == "NOT_RUNNING"
    prov = _dash(cfg, "proxy_providers", {})
    assert prov["state"] == "executed"
    providers = prov["receipt"]["result"]["providers"]
    assert providers["state"] == "OK"


# ----------------------------------------- editor / acp / lsp (typed refusal)
def test_e8_editor_acp_lsp_remain_typed_refusals(tmp_path, monkeypatch):
    from voicebridge import acp_lsp as AL
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    serve = _dash(cfg, "acp_serve", {})
    assert serve["receipt"]["result"] == AL.acp_refuse_serve()
    hooks = _dash(cfg, "acp_accept_hooks", {})
    assert hooks["receipt"]["result"] == AL.acp_refuse_accept_hooks()
    install = _dash(cfg, "lsp_install", {"server": "gopls"})
    assert install["receipt"]["result"] == AL.lsp_refuse_install("gopls")

    ctx = VC.make_ctx(cfg, actor="voice:user", session="e8", source="user")
    assert VC._b_acp(ctx, {"action": "serve"})["refused"] == \
        AL.acp_refuse_serve()
    assert VC._b_acp(ctx, {"action": "accept_hooks"})["refused"] == \
        AL.acp_refuse_accept_hooks()
    assert VC._b_lsp(ctx, {"action": "install", "server": "gopls"})["refused"] \
        == AL.lsp_refuse_install("gopls")
    # The real read-only lifecycle is still shipped and honest.
    assert AL.lsp_status()["layer"] == "ABSENT"


def test_e8_themes_skills_agent_refusals_stay_typed(tmp_path, monkeypatch):
    from voicebridge import themes as TH

    cfg = _cfg(tmp_path, monkeypatch)
    pets = _dash(cfg, "pets_downloads", {})
    assert pets["receipt"]["result"] == TH.refuse_pet_downloads()

    remote = _dash(cfg, "plugins_remote", {"identifier": "git://evil"})
    assert remote["state"] == "executed", remote
    assert remote["receipt"]["result"]["state"] == "BLOCKED"
    assert remote["receipt"]["result"]["code"] == "BLOCKED_REMOTE_PLUGIN"

    agent = _dash(cfg, "import_agent_import", {})
    assert agent["state"] == "executed", agent
    assert agent["receipt"]["result"]["state"] in ("REFUSED", "BLOCKED")

    from voicebridge import voice_commands as VC

    ctx = VC.make_ctx(cfg, actor="voice:user", session="e8", source="user")
    assert VC._b_themes(ctx, {"action": "pets"}) == TH.refuse_pet_downloads()


# ---------------------------------------------------- portable recovery
def test_e8_portable_failed_restore_rolls_back_completely(tmp_path, monkeypatch):
    from voicebridge import portable as PT

    cfg = _cfg(tmp_path, monkeypatch)
    state = Path(cfg.state_dir())
    (state / "memory").mkdir(parents=True, exist_ok=True)
    (state / "memory" / "keep.marker").write_text("keep", encoding="utf-8")
    assert not (state / "reports").exists()

    real = PT._restore_reports

    def explode(target, reports):
        real(target, reports)  # create the new path, then fail the restore
        raise OSError("simulated disk failure")

    monkeypatch.setattr(PT, "_restore_reports", explode)
    payload = {"state": {"memory": [], "lessons": [],
                         "reports": [{"name": "r.txt", "content": "x"}]}}
    with pytest.raises(PT.PortableError) as exc:
        PT.restore_state(cfg, payload, dry_run=False)
    assert not isinstance(exc.value, PT.PartialRollbackError)
    # Prior state is intact and the path created mid-restore is gone.
    assert (state / "memory" / "keep.marker").read_text(encoding="utf-8") \
        == "keep"
    assert not (state / "reports").exists()


def test_e8_portable_incomplete_rollback_is_typed(tmp_path, monkeypatch):
    from voicebridge import portable as PT

    cfg = _cfg(tmp_path, monkeypatch)
    state = Path(cfg.state_dir())
    (state / "memory").mkdir(parents=True, exist_ok=True)
    (state / "memory" / "keep.marker").write_text("keep", encoding="utf-8")

    def explode(target, reports):
        raise OSError("simulated disk failure")

    real_rmtree = PT.shutil.rmtree

    def locked_rmtree(path, *a, **kw):
        if str(path).endswith("memory"):
            raise OSError("locked")
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(PT, "_restore_reports", explode)
    monkeypatch.setattr(PT.shutil, "rmtree", locked_rmtree)
    payload = {"state": {"memory": [{"text": "fresh", "tier": "recall"}],
                         "lessons": [], "reports": []}}
    with pytest.raises(PT.PartialRollbackError) as exc:
        PT.restore_state(cfg, payload, dry_run=False)
    assert any("memory" in f for f in exc.value.failed_paths)
