"""Agent 8 — verified-skill rows through the production voice/dashboard caller.

Closes the Qubit ``verified_*`` rows (list/show/recall/verdict/pin/unpin) and
the workshop recovery rows by exercising the exact backend the voice command
registry and the dashboard dispatcher invoke (``voice_commands._b_verified`` /
``_b_workshop``), plus the same store the CLI calls.

The store's owner-only gate requires a canonical confirmed-human principal.
The CLI passes ``"user"``; the voice/dashboard backend passes
``"<channel>:user"``. This test proves both execute, that persistence survives
a reopen, and that tool/agent/model actors are still refused
(no self-verification).
"""
from __future__ import annotations

import pytest

from voicebridge import verified_skills as V


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _Ctx:
    """Minimal production context (the backend reads only ``.cfg``)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.source = "voice"
        self.actor = "voice:user"
        self.session = "test-session"


def _proposal(**kw):
    base = dict(name="add_two", interface="add_two(x)",
                implementation="return x + 2",
                acceptance_test="assert run(3)==6 and run(0)==2",
                task="add two to a number", cost="normal", trust=1)
    base.update(kw)
    return V.SkillProposal(**base)


def test_human_requester_canonical_set():
    assert V.is_human_requester("user")
    for channel in ("cli", "voice", "dashboard", "tui", "web"):
        assert V.is_human_requester(f"{channel}:user")
    for bad in ("", "tool", "model", "agent:8", "core:lifecycle",
                "system", "dashboard", "evil:user"):
        assert not V.is_human_requester(bad)
    assert set(V.HUMAN_REQUESTERS) == {
        "user", "cli:user", "voice:user", "dashboard:user", "tui:user",
        "web:user"}


def test_voice_backend_verified_lifecycle_and_persistence(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    store = V.verified_skills_store(cfg)
    cand = store.submit(_proposal())
    store.close()

    ctx = _Ctx(cfg)

    listed = VC._b_verified(ctx, {"action": "list"})
    assert [s["id"] for s in listed["skills"]] == [cand.id]

    shown = VC._b_verified(ctx, {"action": "show", "skill_id": cand.id})
    assert shown["skill"]["status"] == V.ST_QUARANTINED

    verdict = VC._b_verified(ctx, {"action": "verdict", "skill_id": cand.id,
                                   "decision": "verified"})
    assert verdict["status"] == V.ST_VERIFIED

    recalled = VC._b_verified(ctx, {"action": "recall",
                                    "query": "add two to a number"})
    assert [s["id"] for s in recalled["selected"]] == [cand.id]

    pinned = VC._b_verified(ctx, {"action": "pin", "skill_id": cand.id})
    assert pinned["pinned"] is True

    # persistence: the production caller's store sees the pin after reopen
    reopened = V.verified_skills_store(cfg)
    assert reopened.get(cand.id).pinned is True
    assert reopened.get(cand.id).status == V.ST_VERIFIED
    reopened.close()

    unpinned = VC._b_verified(ctx, {"action": "unpin", "skill_id": cand.id})
    assert unpinned["pinned"] is False
    reopened = V.verified_skills_store(cfg)
    assert reopened.get(cand.id).pinned is False
    reopened.close()


def test_dashboard_dispatch_confirmed_pin_end_to_end(tmp_path, monkeypatch):
    """The shared dashboard/voice dispatcher runs the confirmed pin."""
    from voicebridge import dashboard_ops as DO

    cfg = _cfg(tmp_path, monkeypatch)
    store = V.verified_skills_store(cfg)
    cand = store.submit(_proposal())
    store.apply_verdict(cand.id, decision="verified", requested_by="user")
    store.close()

    staged = DO.run_op(cfg, "verified_pin", {"skill_id": cand.id},
                       actor="dashboard:user", session="dash")
    assert staged["state"] in ("needs-confirm", "needs_confirm"), staged
    assert staged.get("confirmation_id")

    done = DO.confirm_op(cfg, staged["confirmation_id"],
                         actor="dashboard:user", session="dash")
    assert done["state"] == "executed", done

    reopened = V.verified_skills_store(cfg)
    assert reopened.get(cand.id).pinned is True
    reopened.close()


def test_voice_backend_typed_negatives(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _Ctx(cfg)
    with pytest.raises(ValueError):
        VC._b_verified(ctx, {"action": "show", "skill_id": "skill_missing"})
    with pytest.raises(ValueError):
        VC._b_verified(ctx, {"action": "bogus"})


def test_store_refuses_non_human_and_accepts_channels(tmp_path):
    vs = V.VerifiedSkillStore(tmp_path / "skills.db")
    try:
        c = vs.submit(_proposal())
        with pytest.raises(V.VerifiedSkillError):
            vs.apply_verdict(c.id, decision="verified", requested_by="tool")
        with pytest.raises(V.VerifiedSkillError):
            vs.pin(c.id, requested_by="dashboard")  # bare channel: not human
        with pytest.raises(V.VerifiedSkillError):
            vs.remove(c.id, requested_by="agent:8")
        assert vs.get(c.id).status == V.ST_QUARANTINED

        vs.apply_verdict(c.id, decision="verified",
                         requested_by="voice:user")
        vs.pin(c.id, requested_by="dashboard:user")
        assert vs.get(c.id).pinned is True
        vs.unpin(c.id, requested_by="cli:user")
        assert vs.get(c.id).pinned is False
        vs.remove(c.id, requested_by="voice:user")
        assert vs.get(c.id) is None
    finally:
        vs.close()


def test_workshop_recovery_accepts_channel_human(tmp_path):
    store = V.VerifiedSkillStore(tmp_path / "skills.db")
    ws = V.SkillWorkshop(store, tmp_path / "workshop" / "backups")
    rec = ws.author(_proposal())["skill"]
    b = ws.backup(label="quarantined")
    store.apply_verdict(rec["id"], decision="verified", requested_by="user")
    assert store.get(rec["id"]).status == V.ST_VERIFIED

    out = ws.restore(b["backup_id"], requested_by="voice:user")
    assert out["restored_from"] == b["backup_id"]
    assert store.get(rec["id"]).status == V.ST_QUARANTINED

    with pytest.raises(V.WorkshopError):
        ws.rollback(requested_by="agent:8")
    store.close()
