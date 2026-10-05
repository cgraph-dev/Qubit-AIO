"""Qubit-chat slash turns execute through the same backend as /chat.

Send path under test:
  ChatPage.tsx send() -> streamChatTurn({text, session}) (api.ts)
  -> POST /api/qubit/chat/stream (qubit_compat h_chat_stream)
  -> chat_transport.stream_turn / turn.

Before this change a leading-slash turn (``/skills``, ``/model``, ``/mcp``,
``/help``) fell into the intent classifier and came back as model chatter
or a worker narration — never executed. Now any ``/...`` turn dispatches
to the real backend registry and persists the real result as the assistant
message. Non-slash turns never enter this path (parse returns None and
turn()/stream_turn() guard on ``startswith("/")`` only).

Hermetic: fake store, no e-stop, no model, no broker, no network. Backend
registries are pinned to deterministic rows; the assertions pin that the
router executes them and renders their real data (not a refusal, not
narration), and that unknown slash answers honest help.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _FakeStore:
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

    def assistant_texts(self, sid):
        return [m["content"] for m in self.rows.get(sid, [])
                if m.get("role") == "assistant"]


def _hermetic(monkeypatch, fake):
    from voicebridge import chat_transport as CT

    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    return CT


# -- parse ---------------------------------------------------------------

def test_parse_routes_known_verbs():
    from voicebridge import chat_transport as CT

    assert CT.parse_qubitchat_slash("/skills")["command"] == "skills"
    assert CT.parse_qubitchat_slash("/model")["command"] == "model"
    assert CT.parse_qubitchat_slash("/mcp")["command"] == "mcp"
    assert CT.parse_qubitchat_slash("/plugins")["command"] == "plugins"
    assert CT.parse_qubitchat_slash("/help")["command"] == "help"
    assert CT.parse_qubitchat_slash("  /skills guitar  ")["command"] == "skills"


def test_parse_non_slash_is_none():
    from voicebridge import chat_transport as CT

    assert CT.parse_qubitchat_slash("hello there") is None
    assert CT.parse_qubitchat_slash("") is None
    assert CT.parse_qubitchat_slash("open youtube") is None


def test_parse_unknown_slash_is_unknown_not_none():
    from voicebridge import chat_transport as CT

    parsed = CT.parse_qubitchat_slash("/frobnicate xyz")
    assert parsed is not None and parsed["command"] == "unknown"
    assert parsed["name"] == "frobnicate"


# -- execute: 4 real-backend slash commands --------------------------------

def test_slash_skills_resolves_real_catalog(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    monkeypatch.setattr(
        "voicebridge.skill_catalog.catalog_entries",
        lambda cfg: [{"name": "gmail-skill",
                      "description": "read gmail over CDP"},
                     {"name": "weather-skill",
                      "description": "live forecast"}])
    out = CT.turn({}, "/skills", session="slash-skills-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "slash", out
    assert "gmail-skill" in out["reply"] and "weather-skill" in out["reply"]
    assert fake.assistant_texts("slash-skills-9u"), "assistant row persisted"


def test_slash_model_shows_effective_profile(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    monkeypatch.setattr(
        CT, "resolve_route",
        lambda cfg, req=None, **kw: {"ok": True, "model": "qubit-spark17",
                                     "provider": "ollama", "kind": "local",
                                     "profile_id": "qubit-spark17"})
    out = CT.turn({}, "/model", session="slash-model-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "slash", out
    assert "qubit-spark17" in out["reply"] and "ollama" in out["reply"]


def test_slash_mcp_lists_real_servers(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    monkeypatch.setattr(
        "voicebridge.mcp_servers.list_servers",
        lambda cfg: {"servers": [{"name": "qubit-graft",
                                  "transport": "stdio",
                                  "invocable": True}]})
    out = CT.turn({}, "/mcp", session="slash-mcp-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "slash", out
    assert "qubit-graft" in out["reply"], out


def test_slash_plugins_lists_real_registry(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    monkeypatch.setattr(
        "voicebridge.plugin_runtime.list_plugins",
        lambda cfg: {"state": "OK", "plugins": [{"id": "builtin.echo"},
                                                {"id": "kanban"}]})
    out = CT.turn({}, "/plugins", session="slash-plugins-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "slash", out
    assert "builtin.echo" in out["reply"] and "kanban" in out["reply"]


# -- unknown slash: honest help, never phantom success --------------------

def test_unknown_slash_answers_honest_help(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    out = CT.turn({}, "/frobnicate xyz", session="slash-unknown-9u")
    assert out.get("ok") is True, out
    reply = str(out.get("reply") or "")
    assert "nknown" in reply.lower(), reply  # names the miss
    for verb in ("/skills", "/model", "/mcp", "/help"):
        assert verb in reply, reply  # lists the valid set
    for phantom in ("executed", "done", "sent", "completed"):
        assert phantom not in reply.lower(), reply


# -- non-slash path untouched ----------------------------------------------

def test_plain_text_never_enters_slash_path(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)

    def _boom(cfg, text):
        raise AssertionError("slash path must not run for plain text")

    monkeypatch.setattr(CT, "run_qubitchat_slash", _boom)
    # Keep the legacy path hermetic: classify as plain chat, refuse the
    # model route before any model touch. The point is only that the turn
    # resolves WITHOUT the slash path (no AssertionError) and reports a
    # non-slash route/code.
    monkeypatch.setattr(CT, "_decide_branch",
                        lambda text, cfg: {"action": "answer_local",
                                           "kind": "conversation",
                                           "task_text": text, "params": {},
                                           "reason": "test"})
    monkeypatch.setattr(CT, "resolve_route",
                        lambda cfg, req=None, **kw: {"ok": False,
                                                    "code": "MODEL_NOT_ALLOWED",
                                                    "detail": "test pin"})
    out = CT.turn({}, "hello there", session="slash-plain-9u")
    assert out.get("route") != "slash", out


# -- streaming (the path ChatPage.tsx actually uses) -----------------------

def test_stream_slash_model_streams_real_reply(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    monkeypatch.setattr(
        CT, "resolve_route",
        lambda cfg, req=None, **kw: {"ok": True, "model": "qubit-spark17",
                                     "provider": "ollama", "kind": "local",
                                     "profile_id": "qubit-spark17"})
    events = list(CT.stream_turn({}, "/model", session="slash-stream-9u"))
    kinds = [e.get("event") for e in events]
    assert kinds[0] == "start", events
    assert "delta" in kinds and kinds[-1] == "done", events
    done = events[-1]
    assert "qubit-spark17" in str(done.get("reply") or ""), done
