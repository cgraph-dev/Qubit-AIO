"""Voice-edit routing/cache/settings-semantics fix (9u).

Covers: (1) ask() honoring voice_edit.routing=local via the local profile +
dictate/transform never escalating; (2) the default ask fallback never
hard-failing through the resolver default on stock config; (3) settings PUT
invalidating the local_llm style cache + runner-contract cache with no
restart; (4) PUT per_app:{} actually clearing; (5) default_action fallback
semantics + answer_questions removal from persistence+view (read-old
tolerant).

No services, no browser, no model, no live config writes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import settings as settings_mod
from voicebridge.voice_edit.router import RouteDecision, VoiceRouter
from voicebridge.voice_edit.schema import (SelectionSnapshot, VoiceEditError)
from voicebridge.voice_edit.service import VoiceEditService
from voicebridge.voice_edit.transform import TransformEngine, digest_text


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class FakeCfg:
    """Scratch config: .get contract + .data (live sync) + .path (file)."""

    def __init__(self, data=None, path=None):
        self._data = data or {}
        self.data = self._data
        self.path = path

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeResult:
    def __init__(self, text="", ok=True, code="", message="",
                 provider="local", model="qubit-brain4", locality="local"):
        self.ok = ok
        self.text = text
        self.code = code
        self.message = message
        self.provider = provider
        self.model = model
        self.details = {"locality": locality}


class FakeDesktop:
    def __init__(self, selection="hello world", available=True):
        self.selection = selection
        self.available = available

    def session(self):
        return {"session_type": "x11", "display": ":0", "x11": True}

    def _tool(self, name):
        return name in ("xdotool", "xclip")

    def is_fullscreen(self, window_id=""):
        return False

    def capture_selection(self, cfg=None):
        snap = SelectionSnapshot(
            available=self.available,
            text=self.selection if self.available else "",
            char_count=len(self.selection) if self.available else 0,
            app_class="code", app_name="code", window_id="42",
            focused=True, captured_via="clipboard")
        if self.available:
            snap.digest = digest_text(self.selection)
        return snap

    def insert_text(self, text, snapshot, cfg=None, *, method=""):
        from voicebridge.voice_edit.schema import InsertionResult
        return InsertionResult(ok=True, method="clipboard_paste",
                               app_class="code")

    def revert_last(self, snapshot, cfg=None):
        from voicebridge.voice_edit.schema import InsertionResult
        return InsertionResult(ok=True, method="undo", app_class="code")


class FakeStore:
    def __init__(self):
        self.turns = []
        self.ensured = []

    def ensure(self, session_id, *, model=None, **_kw):
        self.ensured.append((session_id, model))
        return {"state": "OK"}

    def append(self, session_id, role, content, *, model=None, **_kw):
        self.turns.append((session_id, role, content, model))
        return {"state": "OK"}


def _svc(cfg, **kw):
    audits = []
    kw.setdefault("desktop", FakeDesktop())
    kw.setdefault("audit", lambda op, src, detail: audits.append((op, detail)))
    kw.setdefault("estop", lambda cfg: False)
    svc = VoiceEditService(cfg, **kw)
    return svc, audits


def _ve_cfg(path=None, **over):
    ve = {"enabled": True, "routing": "local", "local_profile": "qubit-brain4",
          "allow_api": True, "confirm_before_replace": False,
          "brain_router": False}
    ve.update(over)
    return FakeCfg({"voice_edit": ve}, path=path)


# ---------------------------------------------------------------------------
# 1. routing=local for ask() + escalation matrix + no-escalate invariant
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("routing,allow,mode,want", [
    # routing=local never escalates.
    ("local", True, "answer", False),
    ("local", True, "chat", False),
    ("local", True, "task", False),
    ("local", False, "task", False),
    # routing=auto escalates only a task.
    ("auto", True, "answer", False),
    ("auto", True, "chat", False),
    ("auto", True, "task", True),
    ("auto", False, "task", False),
    # routing=api escalates every escalatable mode.
    ("api", True, "answer", True),
    ("api", True, "chat", True),
    ("api", True, "task", True),
    ("api", False, "answer", False),
    # dictate/transform are never escalatable.
    ("api", True, "dictate", False),
    ("api", True, "transform", False),
    ("local", True, "dictate", False),
])
def test_escalate_for_matrix(routing, allow, mode, want):
    cfg = _ve_cfg(routing=routing, allow_api=allow)
    svc, _ = _svc(cfg)
    escalate, why = svc._escalate_for(mode)
    assert escalate is want, f"{routing}/{allow}/{mode}: {why}"
    if mode in ("dictate", "transform"):
        assert why == "not_escalatable"
    elif not allow:
        assert why == "allow_api=false"


@pytest.mark.parametrize("mode", ["dictate", "transform"])
def test_router_never_escalates_dictate_transform(mode):
    cfg = _ve_cfg(routing="api", allow_api=True)
    router = VoiceRouter(cfg)
    d = RouteDecision(mode, reason="probe")
    router._apply_escalation(d)
    assert d.escalate is False
    assert "escalation_reason" not in d.params


def test_ask_under_routing_local_uses_local_profile_no_escalation(monkeypatch):
    from voicebridge.providers import gateway as GW

    seen = {}

    def fake_run_role(prompt, cfg, **kw):
        seen.update(kw)
        return FakeResult(text="local answer", provider="local",
                          model="qubit-brain4")

    monkeypatch.setattr(GW, "run_role", fake_run_role)
    cfg = _ve_cfg(routing="local", api_profile="", allow_api=True)
    store = FakeStore()
    svc, audits = _svc(cfg, session_store=store)
    # A non-fact question (clock/weather intents answer directly without
    # the brain) still resolves the local profile with no escalation.
    out = svc.ask("summarize this for me", request_id="r-local", mode="answer")
    assert out["ok"] is True and out["answer"] == "local answer"
    assert out["provider"] == "local"
    assert seen.get("profile_id") == "qubit-brain4"
    assert not [op for op, _ in audits if op == "voice_edit.escalation"], \
        "routing=local must not record an escalation"


# ---------------------------------------------------------------------------
# 2. default ask fallback on stock-equivalent config
# ---------------------------------------------------------------------------
def test_stock_config_ask_takes_local_path_not_resolver_default(monkeypatch):
    """routing=auto + api_profile='' (shipped default): ask() must resolve
    the local profile explicitly instead of profile None (the resolver
    default API profile that fails MALFORMED_RESPONSE)."""
    from voicebridge.providers import gateway as GW

    seen = {}

    def fake_run_role(prompt, cfg, **kw):
        seen.update(kw)
        return FakeResult(text="stock answer", provider="local",
                          model="qubit-brain4")

    monkeypatch.setattr(GW, "run_role", fake_run_role)
    cfg = _ve_cfg(routing="auto", api_profile="", allow_api=True)
    # Stock config has no api_profile key at all; drop it explicitly.
    del cfg._data["voice_edit"]["api_profile"]
    svc, audits = _svc(cfg, session_store=FakeStore())
    out = svc.ask("is the sky blue?", request_id="r-stock", mode="answer")
    assert out["ok"] is True and out["answer"] == "stock answer"
    assert seen.get("profile_id") == "qubit-brain4"
    assert seen.get("profile_id") is not None


def test_escalated_ask_without_target_degrades_to_local(monkeypatch):
    """routing=api with no api_profile: recorded escalation degrades to the
    local profile (never the resolver default)."""
    from voicebridge.providers import gateway as GW

    seen = {}

    def fake_run_role(prompt, cfg, **kw):
        seen.update(kw)
        return FakeResult(text="degraded answer", provider="local",
                          model="qubit-brain4")

    monkeypatch.setattr(GW, "run_role", fake_run_role)
    cfg = _ve_cfg(routing="api", api_profile="", allow_api=True)
    svc, audits = _svc(cfg, session_store=FakeStore())
    out = svc.ask("hello?", request_id="r-degrade", mode="chat")
    assert out["ok"] is True
    assert seen.get("profile_id") == "qubit-brain4"
    esc = [d for op, d in audits if op == "voice_edit.escalation"]
    assert esc and esc[0]["reason"] == "no_api_profile"
    assert esc[0]["escalate"] is False


# ---------------------------------------------------------------------------
# 3. cache invalidation on settings PUT (no restart)
# ---------------------------------------------------------------------------
def test_style_cache_invalidated_on_llm_put(tmp_path, monkeypatch):
    import yaml

    import voicebridge.local_llm as LLM
    from voicebridge.config import Config

    p = tmp_path / "voice.yaml"
    p.write_text(yaml.safe_dump({"voice_edit": {"enabled": True}}))
    cfg = FakeCfg({"voice_edit": {"enabled": True}}, path=p)
    # Simulate a running service with a stale snapshot.
    old_cache = dict(LLM._STYLE_CACHE)
    LLM._STYLE_CACHE["v"] = (0.1, False)
    try:
        settings_mod.update_voice_edit(
            cfg, {"local_temperature": 1.9, "local_think": True})
        assert LLM._STYLE_CACHE == {}, \
            "settings PUT must clear the style snapshot"
        # The fresh values are visible with no restart: Config.load reads
        # the scratch file through the monkeypatched loader.
        monkeypatch.setattr(Config, "load",
                            classmethod(lambda cls, path=None: cfg))
        assert LLM._local_style() == (1.9, True)
        payload = LLM._chat_payload("hi", "", "qubit-brain4", 100, "",
                                    False, num_ctx=4096)
        assert payload["options"]["temperature"] == 1.9
        assert payload["think"] is True
    finally:
        LLM._STYLE_CACHE.clear()
        LLM._STYLE_CACHE.update(old_cache)


def test_runner_cache_invalidated_on_model_put(tmp_path, monkeypatch):
    import voicebridge.runner_contract as RC

    monkeypatch.setattr(RC, "_fetch_digest", lambda url, alias: None)
    p = tmp_path / "voice.yaml"
    p.write_text("voice_edit:\n  enabled: true\n")
    cfg = FakeCfg({"voice_edit": {"enabled": True}}, path=p)
    logged, digests = set(RC._LOGGED_KEYS), dict(RC._KNOWN_DIGESTS)
    try:
        RC.resolve(cfg)
        assert RC._LOGGED_KEYS, "expected a cached resolution entry"
        settings_mod.update_voice_edit(cfg, {"local_model": "probe-model:7b"})
        assert RC._LOGGED_KEYS == set() and RC._KNOWN_DIGESTS == {}, \
            "settings PUT must clear the runner resolution cache"
        assert RC.resolve(cfg).alias == "probe-model:7b"
    finally:
        RC._LOGGED_KEYS.clear()
        RC._LOGGED_KEYS.update(logged)
        RC._KNOWN_DIGESTS.clear()
        RC._KNOWN_DIGESTS.update(digests)


# ---------------------------------------------------------------------------
# 4. per_app:{} clears, non-empty deep-merges
# ---------------------------------------------------------------------------
def test_per_app_empty_object_clears(tmp_path):
    import yaml

    p = tmp_path / "voice.yaml"
    p.write_text(yaml.safe_dump(
        {"voice_edit": {"enabled": True,
                        "per_app": {"code": "summarize",
                                    "nano": "email"}}}))
    cfg = FakeCfg({"voice_edit": {"enabled": True,
                                  "per_app": {"code": "summarize",
                                              "nano": "email"}}}, path=p)
    settings_mod.update_voice_edit(cfg, {"per_app": {}})
    on_disk = yaml.safe_load(p.read_text())
    assert "per_app" not in (on_disk.get("voice_edit") or {}), \
        f"per_app node must be deleted from file: {on_disk}"
    assert "per_app" not in cfg.data.get("voice_edit", {}), \
        "per_app node must be deleted from the live config too"


def test_per_app_non_empty_merges_both_directions(tmp_path):
    import yaml

    p = tmp_path / "voice.yaml"
    p.write_text(yaml.safe_dump(
        {"voice_edit": {"enabled": True,
                        "per_app": {"code": "summarize"}}}))
    cfg = FakeCfg({"voice_edit": {"enabled": True,
                                  "per_app": {"code": "summarize"}}}, path=p)
    settings_mod.update_voice_edit(cfg, {"per_app": {"slack": "shorten"}})
    on_disk = yaml.safe_load(p.read_text())["voice_edit"]["per_app"]
    assert on_disk == {"code": "summarize", "slack": "shorten"}
    assert cfg.data["voice_edit"]["per_app"] == on_disk
    # A second merge keeps accumulating (no silent drop).
    settings_mod.update_voice_edit(cfg, {"per_app": {"nano": "email"}})
    on_disk2 = yaml.safe_load(p.read_text())["voice_edit"]["per_app"]
    assert on_disk2 == {"code": "summarize", "slack": "shorten",
                        "nano": "email"}


# ---------------------------------------------------------------------------
# 5a. default_action fallback when the router yields clarify on empty input
# ---------------------------------------------------------------------------
def _transform_svc(cfg):
    engine = TransformEngine(
        runner=lambda *a, **k: FakeResult(text="SUMMARIZED"))
    svc, audits = _svc(cfg, engine=engine)
    rid = svc.begin(session_id="s")["request_id"]
    svc.capture(rid)
    return svc, audits, rid


def test_default_action_used_on_empty_transcript():
    cfg = _ve_cfg(default_action="summarize")
    svc, audits, rid = _transform_svc(cfg)
    out = svc.transform(rid, "")
    assert out["mode"] == "preview", out
    assert out["command"]["action"] == "summarize"
    assert out["command"]["router"] == "default_action"
    assert [d for op, d in audits if op == "voice_edit.default_action"]


def test_shipped_default_still_clarifies_on_empty_transcript():
    cfg = _ve_cfg()
    svc, audits, rid = _transform_svc(cfg)
    out = svc.transform(rid, "")
    assert out["mode"] == "clarify"


def test_spoken_but_unmapped_utterance_still_clarifies():
    """A real utterance the parser cannot map is never silently
    substituted, even with a default_action configured."""
    cfg = _ve_cfg(default_action="summarize")
    svc, audits, rid = _transform_svc(cfg)
    out = svc.transform(rid, "please make this not sound like a bot")
    assert out["mode"] == "clarify"


# ---------------------------------------------------------------------------
# 5b. answer_questions removed from persistence+view (read-old tolerant)
# ---------------------------------------------------------------------------
def test_answer_questions_removed_from_keys_and_view(tmp_path):
    assert "answer_questions" not in settings_mod.SETTINGS_KEYS
    cfg = FakeCfg({"voice_edit": {"enabled": True}}, path=None)
    view = settings_mod.settings_view(cfg)
    assert "answer_questions" not in view


def test_answer_questions_put_rejected_and_legacy_key_tolerated(tmp_path):
    import yaml

    p = tmp_path / "voice.yaml"
    p.write_text(yaml.safe_dump(
        {"voice_edit": {"enabled": True, "answer_questions": False}}))
    cfg = FakeCfg({"voice_edit": {"enabled": True,
                                  "answer_questions": False}}, path=p)
    # A PUT carrying only the removed key is unrecognised (not persisted).
    with pytest.raises(VoiceEditError):
        settings_mod.update_voice_edit(cfg, {"answer_questions": False})
    # The stored legacy key is tolerated: other PUTs succeed and the key is
    # neither rejected nor deleted by the merge.
    settings_mod.update_voice_edit(cfg, {"routing": "local"})
    on_disk = yaml.safe_load(p.read_text())["voice_edit"]
    assert on_disk["answer_questions"] is False
    assert on_disk["routing"] == "local"
