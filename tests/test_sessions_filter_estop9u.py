"""Dashboard audit fix — /sessions source filters, ordering, and mutation gates.

The Sessions page segments (Chats/Automation/All) and the source-filter popover
send ``source``/``sources``/``exclude_sources``/``order``; the handler ignored
all of them. Compat session rename/archive/delete/resume also wrote the store
directly, bypassing the e-stop latch and audit that ``h_chat_session_*`` honor.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    home = tmp_path / "home" / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed_sessions(cfg):
    from voicebridge import chat_transport as CT

    st = CT.store(cfg)
    # s-old started first but is the most recently active; s-new started later
    # but has no activity. That makes ``order=created`` and the default
    # ``order=recent`` orderings differ observably.
    st.create("old-active", session_id="s-old", created_at=10.0,
              updated_at=10.0)
    st.create("new-cold", session_id="s-new", created_at=90.0,
              updated_at=90.0)
    st.append("s-old", "user", "hello")


def test_list_honors_source_sources_and_exclude_sources(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_sessions(cfg)

    code, out = HC.h_sessions_list(cfg, {}, {}, {})
    assert code == 200 and out["total"] == 2, out

    code, out = HC.h_sessions_list(cfg, {"source": "dashboard"}, {}, {})
    assert out["total"] == 2, out

    code, out = HC.h_sessions_list(cfg, {"sources": "qubit-receipts"}, {}, {})
    assert out["total"] == 0, out

    code, out = HC.h_sessions_list(cfg, {"exclude_sources": "dashboard"}, {},
                                   {})
    assert out["total"] == 0, out

    # A source that matches nothing drops every row (was a silent no-op).
    code, out = HC.h_sessions_list(cfg, {"source": "qubit-receipts"}, {}, {})
    assert out["total"] == 0, out


def test_list_order_created_uses_started_at(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_sessions(cfg)

    code, out = HC.h_sessions_list(cfg, {"order": "created"}, {}, {})
    assert [r["id"] for r in out["sessions"]] == ["s-new", "s-old"], out

    # Default/recent ordering stays last-active (existing behavior).
    code, out = HC.h_sessions_list(cfg, {"order": "recent"}, {}, {})
    assert [r["id"] for r in out["sessions"]] == ["s-old", "s-new"], out


def test_session_mutations_refuse_while_estop_latched(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_sessions(cfg)
    sid = "s-old"

    monkeypatch.setattr(HC, "_estop", lambda c: True)
    checks = [
        (HC.h_session_patch, {"name": "x"}, "session change refused"),
        (HC.h_session_delete, {"confirm": True}, "session delete refused"),
        (HC.h_session_resume, {}, "session resume refused"),
    ]
    for fn, body, detail in checks:
        code, out = fn(cfg, {}, body, {"id": sid})
        assert code == 403 and out["code"] == "EMERGENCY_STOP", (fn, out)
        assert detail in out["detail"], out
    # The store is untouched while latched.
    assert CT.store(cfg).show(sid)["state"] == "OK"


def test_session_mutations_audit_on_success(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_sessions(cfg)

    audits = []
    monkeypatch.setattr(HC, "_audit",
                        lambda op, actor, detail: audits.append(op))

    code, out = HC.h_session_patch(cfg, {}, {"name": "renamed"},
                                   {"id": "s-old"})
    assert code == 200, (code, out)
    assert "session.patch" in audits, audits

    code, out = HC.h_session_resume(cfg, {}, {}, {"id": "s-old"})
    assert code == 200, (code, out)
    assert "session.resume" in audits, audits

    code, out = HC.h_session_delete(cfg, {}, {"confirm": True},
                                    {"id": "s-old"})
    assert code == 200, (code, out)
    assert "session.delete" in audits, audits
