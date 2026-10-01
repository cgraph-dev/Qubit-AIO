"""Wave B3 Task 4 (backend only): /qubit-chat browser verbs resolve via the shared voice registry.

Plan: docs/superpowers/plans/2026-09-30-qubit-chat-voice-routing.md Task 4 Steps 1-2.
Scope: resolve() checks only — no broker/execute, no CDP, no new clients/stores.
Backends stay on the existing ``browser_tabs`` module (the same module the
CLI/dashboard call through ``_b_tabs``); read-only (read tab) and mutating
(open/close tab) entries stay separate so confirmation tiers differ.
Auth-gated origins answer typed login-needed and unknown sites clarify/fail
typed at execute time — never a fake "opened" (backend ``_check_url`` only
accepts http(s); voice ``execute`` surfaces the typed error).
"""


def test_qubitchat_browser_open_resolves_registry():
    from voicebridge import voice_commands as VC
    r = VC.resolve("open youtube in the browser")
    assert r.kind in ("match", "clarify", "ambiguous"), r


def test_qubitchat_browser_open_site_arg():
    from voicebridge import voice_commands as VC
    r = VC.resolve("open youtube in the browser")
    assert r.kind == "match", r
    assert r.entry is not None and r.entry.id == "browser_open", r
    assert str(r.args.get("url", "")).strip() == "youtube", r


def test_qubitchat_browser_open_full_url():
    from voicebridge import voice_commands as VC
    r = VC.resolve("open https://example.com in the browser")
    assert r.kind == "match", r
    assert r.entry is not None and r.entry.id == "browser_open", r
    assert "example.com" in str(r.args.get("url", "")), r


def test_qubitchat_browser_open_without_the():
    from voicebridge import voice_commands as VC
    r = VC.resolve("open gmail in browser")
    assert r.kind == "match", r
    assert r.entry is not None and r.entry.id == "browser_open", r
    assert str(r.args.get("url", "")).strip() == "gmail", r


def test_qubitchat_browser_close_that_tab():
    from voicebridge import voice_commands as VC
    r = VC.resolve("close that tab")
    assert r.kind == "match", r
    assert r.entry is not None and r.entry.id == "browser_close_tab", r


def test_qubitchat_browser_read_that_tab():
    from voicebridge import voice_commands as VC
    r = VC.resolve("read that tab")
    assert r.kind == "match", r
    assert r.entry is not None and r.entry.id == "browser_read", r


def test_qubitchat_browser_read_close_tiers_differ():
    from voicebridge import voice_commands as VC
    read = VC.resolve("read that tab")
    close = VC.resolve("close that tab")
    opened = VC.resolve("open youtube in the browser")
    assert read.entry is not None and close.entry is not None
    assert opened.entry is not None
    # Read-only entry runs unconfirmed; mutating entries are confirm-gated.
    assert read.entry.confirm is False, read.entry
    assert close.entry.confirm is True, close.entry
    assert opened.entry.confirm is True, opened.entry
    # Same shared backend family (CLI/dashboard/voice call one module).
    assert read.entry.backend == "browser_tabs", read.entry
    assert close.entry.backend == "browser_tabs", close.entry
    assert opened.entry.backend == "browser_tabs", opened.entry


def test_qubitchat_browser_bare_open_not_claimed():
    # Bare "open <name>" stays UNKNOWN in the registry: it may be an app
    # ("open vscode" is the local-action path), so the registry must not
    # hijack it — only site-qualified "open ... in the browser" claims.
    from voicebridge import voice_commands as VC
    r = VC.resolve("open youtube")
    assert r.kind == "unknown", r


# --- T4 staged-confirm settle path (mock journal/voice_say) -----------------
import sys as _sys
from pathlib import Path as _Path
from types import SimpleNamespace as _NS

_REPO_ROOT = _Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))


class _FakeChatStore:
    def __init__(self):
        self.rows = {}
        self.n = 0

    def ensure(self, sid, *, model=None, identity="dashboard:user"):
        sid = (sid or "").strip() or "dashboard-chat"
        self.rows.setdefault(sid, [])
        return {"state": "OK", "session": {"id": sid}, "messages": []}

    def append(self, sid, role, content, *, meta=None, model=None,
               timestamp=None):
        self.rows.setdefault(sid, []).append(
            {"role": role, "content": content, "meta": meta or {}})
        self.n += 1
        return {"state": "OK",
                "message": {"role": role, "content": content},
                "message_count": self.n}

    def messages(self, sid, *, limit=500, offset=0):
        msgs = list(self.rows.get((sid or "").strip() or "dashboard-chat",
                                  []))
        return {"state": "OK", "session_id": sid, "messages": msgs,
                "total": len(msgs), "limit": limit, "offset": offset}


def _hermetic_ct(monkeypatch, fake):
    from voicebridge import chat_transport as CT

    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    return CT


def test_qubitchat_browser_staged_settles_on_yes(monkeypatch):
    """Staged browser_open + affirmative follow-up settles via same journal."""
    from voicebridge import chat_transport as CT
    from voicebridge.voice_commands import Outcome

    fake = _FakeChatStore()
    CT = _hermetic_ct(monkeypatch, fake)
    sid = "test-t4-browser-settle-9u"

    voice_calls = []
    confirm_calls = []
    executed = []

    def _fake_say(cfg, text, **kw):
        voice_calls.append({"text": text, "session": kw.get("session")})
        assert kw.get("session") == sid, kw
        return {"state": "needs-confirm",
                "speech": "Open a URL: say yes to confirm.",
                "confirmation_id": "cid-t4-1",
                "route": "browser_open", "entry": "browser_open"}

    def _fake_pending(cfg, s):
        # First turn: nothing pending yet (stage happens during the turn);
        # follow-up: one staged record waiting.
        if voice_calls and not confirm_calls:
            return [_NS(id="cid-t4-1")]
        return []

    def _fake_confirm(ctx):
        confirm_calls.append({"session": ctx.session})
        assert ctx.session == sid, ctx.session
        executed.append(True)
        entry = _NS(id="browser_open")
        return Outcome("executed", entry, "Browser open.",
                       {"entry": "browser_open",
                        "confirmation_id": "cid-t4-1"})

    monkeypatch.setattr(CT, "_pending_registry", _fake_pending)
    monkeypatch.setattr("voicebridge.voice_commands.voice_say", _fake_say)
    monkeypatch.setattr("voicebridge.voice_commands.confirm_pending",
                        _fake_confirm)
    monkeypatch.setattr(
        CT, "decide_turn_action",
        lambda text, **kw: {"action": "registry", "kind": "registry",
                            "task_text": text, "params": {},
                            "reason": "test", "entry": "browser_open"})

    first = CT.turn({}, "open https://example.com in the browser",
                    session=sid)
    assert first.get("ok") is False, first
    assert first.get("code") == "CONFIRM_REQUIRED", first
    assert first.get("confirmation_id") == "cid-t4-1", first
    assert executed == [], first  # nothing executed before affirmation

    second = CT.turn({}, "yes", session=sid)
    assert second.get("ok") is True, second
    assert second.get("route") == "registry", second
    assert "Browser open" in (second.get("text") or ""), second
    assert len(voice_calls) == 1, voice_calls  # no fresh classify+stage
    assert len(confirm_calls) == 1, confirm_calls
    assert executed == [True], executed


def test_qubitchat_browser_yes_without_pending_executes_nothing(monkeypatch):
    """Affirmative with no pending confirmation must not fake-execute."""
    from voicebridge import chat_transport as CT

    fake = _FakeChatStore()
    CT = _hermetic_ct(monkeypatch, fake)
    sid = "test-t4-browser-nopending-9u"

    def _boom(ctx, confirmation_id=None):
        raise AssertionError("confirm_pending must not run with no pending")

    monkeypatch.setattr(CT, "_pending_registry", lambda cfg, s: [])
    monkeypatch.setattr("voicebridge.voice_commands.confirm_pending", _boom)
    monkeypatch.setattr(
        CT, "decide_turn_action",
        lambda text, **kw: {"action": "answer_local", "kind": "conversation",
                            "task_text": text, "params": {},
                            "reason": "test-clarify"})
    monkeypatch.setattr(
        CT, "resolve_route",
        lambda cfg, requested=None, **kw: {"ok": True, "model": "qubit-brain4",
                                          "kind": "local"})
    monkeypatch.setattr(
        CT, "_generate",
        lambda cfg, sid_, clean_, model_, ctx_, **kw: {
            "ok": True, "reply": "hello there", "session": sid_,
            "session_id": sid_})

    out = CT.turn({}, "yes", session=sid)
    # Falls through to the normal path — never a fake browser success.
    assert not (out.get("ok") is True
                and out.get("route") == "registry"
                and "browser open" in str(out.get("text") or "").lower()), out


def test_qubitchat_browser_expired_foreign_id_is_typed(monkeypatch):
    """Expired/foreign staged id settles typed NO_CONFIRMATION, no execute."""
    from voicebridge import chat_transport as CT
    from voicebridge.voice_commands import Outcome

    fake = _FakeChatStore()
    CT = _hermetic_ct(monkeypatch, fake)
    sid = "test-t4-browser-expired-9u"
    backend_ran = []

    def _fake_confirm(ctx):
        assert ctx.session == sid, ctx.session
        return Outcome("failed", None, "That confirmation is gone.", {})

    monkeypatch.setattr(CT, "_pending_registry",
                        lambda cfg, s: [_NS(id="cid-t4-gone")])
    monkeypatch.setattr("voicebridge.voice_commands.confirm_pending",
                        _fake_confirm)

    out = CT.turn({}, "yes", session=sid)
    assert out.get("ok") is False, out
    assert out.get("code") == "NO_CONFIRMATION", out
    assert backend_ran == [], out
    assert "opened" not in str(out.get("detail") or "").lower(), out
