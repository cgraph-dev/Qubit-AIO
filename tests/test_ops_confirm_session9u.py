"""Canonical ops confirm settles cross-session staged ids (T4 follow-up).

``voice_say`` stages under the chat session while ``POST /api/ops/confirm``
(``ops_confirm_request``) confirms as ``session="dashboard"``; the
session-scoped ``_pending_for`` lookup then answered NO_CONFIRMATION for a
live id. ``confirm_registry_op`` now falls back to a session-agnostic id
lookup (single-use target-bound, TTL/expiry preserved, actor/audit through
the same ``VC.confirm_pending`` path).

Hermetic: tmp XDG homes, tmp state dir; the shared journal only.
"""
from __future__ import annotations

import json


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _say(cfg, text, session):
    from voicebridge import voice_commands as VC

    return VC.voice_say(cfg, text, actor="voice:user", session=session,
                       source="user")


def _titles(cfg):
    from voicebridge import kanban as KB

    return [t.get("title") for t in
            KB.list_tasks(cfg, board="main").get("tasks", [])]


def test_chat_staged_confirm_settles_via_canonical_dashboard_session(
        tmp_path, monkeypatch):
    """Chat-session staged id settles through the canonical dashboard path."""
    from voicebridge import approvals as AP
    from voicebridge import dashboard_ops as OPS
    from pathlib import Path

    cfg = _cfg(tmp_path, monkeypatch)
    said = _say(cfg, "qubit, create task cross session milk",
                session="chat-sid-9u")
    assert said["state"] == "needs-confirm", said
    cid = said["confirmation_id"]
    assert cid

    done = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                          session="dashboard")
    assert done["state"] == "executed", done
    assert done["receipt"]["confirmation_id"] == cid, done
    assert "cross session milk" in _titles(cfg)

    journal = AP.ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
    rec = journal.get(cid)
    assert rec is not None and rec.decision == "approve", rec
    # Voice confirms settle approve-once to EXECUTING (single-use via
    # journal.respond + the executed-once guard); COMPLETE is the finish()
    # path other channels use.
    assert rec.state == AP.EXECUTING, rec


def test_dashboard_staged_confirm_still_settles_same_session(
        tmp_path, monkeypatch):
    """Same-session ids keep settling on the first (session-scoped) attempt."""
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    said = _say(cfg, "qubit, create task dashboard milk",
                session="dashboard")
    assert said["state"] == "needs-confirm", said
    cid = said["confirmation_id"]

    done = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                          session="dashboard")
    assert done["state"] == "executed", done
    assert "dashboard milk" in _titles(cfg)


def test_unknown_and_expired_ids_stay_no_confirmation(
        tmp_path, monkeypatch):
    """Foreign/expired ids are never executed: typed NO_CONFIRMATION."""
    from voicebridge import dashboard_ops as OPS
    from pathlib import Path

    cfg = _cfg(tmp_path, monkeypatch)
    foreign = OPS.confirm_op(cfg, "deadbeef" * 8, actor="dashboard:user",
                             session="dashboard")
    assert foreign["state"] == "failed", foreign
    assert foreign.get("code") == "NO_CONFIRMATION", foreign

    said = _say(cfg, "qubit, create task expired milk",
                session="chat-sid-9u")
    cid = said["confirmation_id"]
    path = Path(cfg.state_dir()) / "approvals.jsonl"
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(r["id"] == cid for r in rows), "staged record must be journaled"
    for row in rows:
        if row["id"] == cid:
            row["expires_at"] = 1.0  # epoch: long expired
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")

    gone = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                          session="dashboard")
    assert gone["state"] == "failed", gone
    assert gone.get("code") == "NO_CONFIRMATION", gone
    assert "expired milk" not in _titles(cfg)


def test_replay_after_cross_session_settle_is_gone(tmp_path, monkeypatch):
    """Settle-once: a second confirm of the same id fails, never executes."""
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    said = _say(cfg, "qubit, create task replay milk", session="chat-sid-9u")
    assert said["state"] == "needs-confirm", said
    cid = said["confirmation_id"]

    first = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                           session="dashboard")
    assert first["state"] == "executed", first
    replay = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                            session="dashboard")
    assert replay["state"] == "failed", replay
    assert replay.get("code") in ("NO_CONFIRMATION", "ALREADY_DONE"), replay
    assert _titles(cfg).count("replay milk") == 1
