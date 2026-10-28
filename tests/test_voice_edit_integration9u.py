"""Integration tests for the one voice router + accurate-STT dispatch seams.

Covers the service-level routing dispatch (one mode per utterance), the
confirm-gated task path, and the desktop helper's route/task handling. All fakes:
no model, no socket, no microphone, no desktop.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.router import RouteDecision
from voicebridge.voice_edit.schema import VoiceEditError
from voicebridge.voice_edit.service import VoiceEditService
from voicebridge.voice_edit.transform import TransformEngine, digest_text


class FakeCfg:
    def __init__(self, data=None, path=None):
        self._data = data or {}
        self.path = path

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeDesktop:
    def __init__(self, selection="hello world", available=True):
        self.selection = selection
        self.available = available
        self.inserted = []

    def session(self):
        return {"session_type": "x11", "display": ":0", "x11": True}

    def _tool(self, name):
        return name in ("xdotool", "xclip")

    def is_fullscreen(self, window_id=""):
        return False

    def focused_window(self):
        return {"app_class": "code", "window_id": "42"}

    def capture_selection(self, cfg=None):
        from voicebridge.voice_edit.schema import SelectionSnapshot
        snap = SelectionSnapshot(
            available=self.available,
            text=self.selection if self.available else "",
            char_count=len(self.selection) if self.available else 0,
            app_class="code", app_name="code", window_id="42", focused=True,
            captured_via="clipboard" if self.available else "clipboard_empty")
        if self.available:
            snap.digest = digest_text(self.selection)
        return snap

    def insert_text(self, text, snapshot, cfg=None, *, method=""):
        from voicebridge.voice_edit.schema import InsertionResult
        self.inserted.append(text)
        return InsertionResult(ok=True, method="clipboard_paste", app_class="code",
                               focus_restored=True, clipboard_restored=True)

    def revert_last(self, snapshot, cfg=None):
        from voicebridge.voice_edit.schema import InsertionResult
        return InsertionResult(ok=True, method="undo", app_class="code",
                               reverted=True, focus_restored=True)


def _cfg(**ve):
    base = {"enabled": True, "routing": "local", "confirm_before_replace": False}
    base.update(ve)
    return FakeCfg({"voice_edit": base})


def _service(*, cfg=None, router=None, desktop=None, audits=None):
    audits = audits if audits is not None else []

    def audit(op, actor, detail):
        audits.append((op, detail))

    svc = VoiceEditService(
        cfg or _cfg(), desktop=desktop or FakeDesktop(),
        engine=TransformEngine(runner=lambda *a, **k: None),
        router=router, audit=audit, estop=lambda cfg: False)
    return svc, audits


class FakeRouter:
    def __init__(self, decision):
        self._decision = decision

    def decide(self, transcript, *, has_selection):
        return self._decision


# ---------------------------------------------------------------------------
# service.route dispatch
# ---------------------------------------------------------------------------
def test_route_transform_returns_preview_and_audits_decision():
    class Runner:
        def __call__(self, *a, **k):
            class R:
                ok = True
                text = "NEW TEXT"
                code = ""
                detail = ""
                message = ""
                provider = "local"
                model = "m"
                locality = "local"
                privacy_mode = "local"
                elapsed_ms = 1
                prompt_version = "1"
                injection_suspected = False
                clarification_returned = False
                facts_dropped = []
                truncated = False

            return R()

    svc, audits = _service()
    svc.engine = TransformEngine(runner=Runner())
    svc.router = FakeRouter(RouteDecision("transform", reason="edit_command"))
    rid = svc.begin()["request_id"]
    svc.capture(rid)
    out = svc.route(rid, "make it shorter")
    assert out["mode"] == "preview" and out["proposed"] == "NEW TEXT"
    assert out["decision"]["mode"] == "transform"
    assert any(op == "voice_edit.router" for op, _ in audits)


def test_route_question_returns_answer_mode_without_typing():
    svc, audits = _service(
        router=FakeRouter(RouteDecision("answer", reason="question")))
    rid = svc.begin()["request_id"]
    svc.capture(rid)
    out = svc.route(rid, "what time is it")
    assert out["mode"] == "answer" and out["question"] == "what time is it"
    assert not svc.desktop.inserted
    assert any(op == "voice_edit.router" for op, _ in audits)


def test_route_chat_and_ignore_modes():
    svc, _ = _service(
        router=FakeRouter(RouteDecision("chat", reason="intent:conversation")))
    rid = svc.begin()["request_id"]
    svc.capture(rid)
    assert svc.route(rid, "hello there")["mode"] == "chat"
    svc.router = FakeRouter(RouteDecision("ignore", reason="empty"))
    assert svc.route(rid, "hmm")["mode"] == "ignore"
    assert not svc.desktop.inserted


def test_route_task_proposes_but_never_runs():
    svc, audits = _service(
        router=FakeRouter(RouteDecision("task", reason="intent:coding")))
    rid = svc.begin()["request_id"]
    svc.capture(rid)
    out = svc.route(rid, "search the web for qubits")
    assert out["mode"] == "task" and out["requires_confirmation"] is True
    assert out["task_text"] == "search the web for qubits"
    assert any(op == "voice_edit.task.proposed" for op, _ in audits)


# ---------------------------------------------------------------------------
# confirm-gated task execution
# ---------------------------------------------------------------------------
def test_task_refuses_without_confirm_and_runs_with_confirm(monkeypatch):
    svc, audits = _service()
    with pytest.raises(VoiceEditError) as e:
        svc.task("search the web for qubits", confirm=False)
    assert e.value.code == "VOICE_EDIT_CONFIRM_REQUIRED"
    assert e.value.status == 409

    calls = {}

    def fake_run(cfg, text, **kw):
        calls["text"] = text
        calls["tool"] = kw.get("execute_tool_fn")
        calls["confirmed"] = kw.get("confirmed")
        return {"state": "OK", "reply": "Found 3 results.",
                "tool_used": "web.search", "session": "s1"}

    import voicebridge.voice_edit.tool_task as TT
    monkeypatch.setattr(TT, "run_tool_task", fake_run)
    out = svc.task("search the web for qubits", confirm=True,
                   request_id="r-1")
    assert out["ok"] and out["mode"] == "task_result"
    assert out["reply"] == "Found 3 results."
    assert out["tool_used"] == "web.search"
    assert calls["text"] == "search the web for qubits"
    assert calls["confirmed"] is True
    assert any(op == "voice_edit.task.run" for op, _ in audits)
    assert any(op == "voice_edit.task.result" for op, _ in audits)


def test_task_confirm_refusal_audited_but_not_run(monkeypatch):
    svc, audits = _service()
    ran = {"n": 0}
    import voicebridge.voice_edit.tool_task as TT
    monkeypatch.setattr(TT, "run_tool_task",
                        lambda *a, **k: ran.__setitem__("n", ran["n"] + 1))
    with pytest.raises(VoiceEditError):
        svc.task("do something", confirm=False)
    assert ran["n"] == 0
    assert any(op == "voice_edit.task.confirm_required" for op, _ in audits)


# ---------------------------------------------------------------------------
# truthful privacy status
# ---------------------------------------------------------------------------
def test_status_privacy_reflects_debug_log():
    svc_off, _ = _service()
    assert svc_off.status()["privacy"] == \
        "no selected text is logged or persisted"

    svc_on, _ = _service(cfg=_cfg(debug_log=True))
    privacy = svc_on.status()["privacy"]
    assert privacy != "no selected text is logged or persisted"
    low = privacy.lower()
    assert "logging" in low and "record" in low


def test_execute_tool_dispatches_through_the_broker(monkeypatch):
    import voicebridge.voice_edit.tool_task as TT

    class FakeRes:
        denied = ""
        ok = True
        error = ""
        output = "volume raised"

    class FakeBroker:
        def __init__(self):
            self.calls = []

        def execute(self, name, args, *, source, session=None, **kw):
            self.calls.append((name, args, source))
            return FakeRes()

    broker = FakeBroker()
    monkeypatch.setattr(TT, "_broker", lambda cfg: broker)
    out = TT.execute_tool(object(), "web.search", {"query": "qubits"})
    assert out == "volume raised"
    assert broker.calls == [("web.search", {"query": "qubits"}, "model")]

    with pytest.raises(ValueError):
        TT.execute_tool(object(), "shell.rm", {})


def test_run_tool_task_deterministic_local_action(monkeypatch):
    import voicebridge.voice_edit.tool_task as TT

    class FakeRes:
        denied = ""
        ok = True
        error = ""
        output = "Volume up"

    class FakeBroker:
        def __init__(self):
            self.calls = []

        def execute(self, name, args, *, source, session=None, **kw):
            self.calls.append((name, source))
            return FakeRes()

    broker = FakeBroker()
    monkeypatch.setattr(TT, "_broker", lambda cfg: broker)
    monkeypatch.setattr(TT, "_persist", lambda *a, **k: None)
    out = TT.run_tool_task(_cfg(), "raise the volume")
    assert out["state"] == "OK"
    assert out["tool_used"] == "local.volume_up"
    assert broker.calls == [("local.volume_up", "model")]
    assert out["reply"] == "Volume up"


def test_ask_transform_engine_failure_falls_through_to_brain(monkeypatch):
    """A transform-shaped ask whose engine fails is NOT a typed failure.

    Lane-2 pre-route claims bare triggers ("summarize this for me" parses
    as summarize with no input), but with nothing to transform the engine
    fails — the turn must fall through to the normal ask pipeline (brain /
    escalation / skills) instead of returning VOICE_EDIT_NO_SELECTION.
    Regression 2026-10-03: the typed failure starved the local, escalation,
    and YouTube paths (3 certifier tests)."""
    from voicebridge.voice_edit.schema import TransformResult
    from voicebridge.voice_edit.transform import TransformEngine
    svc, audits = _service()

    class _FailEngine(TransformEngine):
        def transform(self, cfg, cmd, selected_text="", **kw):
            return TransformResult(
                ok=False, code="VOICE_EDIT_NO_SELECTION",
                detail="no selected text to transform",
                action=getattr(cmd, "action", ""))

    svc.engine = _FailEngine()
    calls = {}

    class FakeRes:
        ok = True
        text = "brain answer"
        provider = "local"
        model = "qubit-brain"
        code = ""
        message = ""

    import voicebridge.providers.gateway as GW
    monkeypatch.setattr(
        GW, "run_role",
        lambda prompt, cfg, **kw: (calls.update(kw), FakeRes())[1])
    out = svc.ask("summarize this for me", request_id="r-fallthrough",
                  mode="answer")
    assert out["ok"] is True and out["answer"] == "brain answer"
    assert calls.get("profile_id") == "qubit-brain"


def test_ask_escalation_routes_to_a_known_api_profile(monkeypatch):
    svc, audits = _service(cfg=_cfg(routing="api", allow_api=True,
                                    api_profile="qubit-api"))
    monkeypatch.setattr(svc, "_known_model_profile", lambda pid: pid == "qubit-api")
    calls = {}

    class FakeRes:
        ok = True
        text = "It's 14:20 in Bucharest."
        provider = "openai"
        model = "gpt"
        code = ""
        message = ""

    def fake_run_role(prompt, cfg, **kw):
        calls.update(kw)
        return FakeRes()

    import voicebridge.providers.gateway as GW
    monkeypatch.setattr(GW, "run_role", fake_run_role)
    # A non-fact question (clock/weather intents answer directly without
    # the brain) still escalates through the brain path unchanged.
    out = svc.ask("summarize this for me", request_id="r-2", mode="chat")
    assert out["ok"] and out["answer"].startswith("It's 14:20")
    assert calls.get("profile_id") == "qubit-api"
    assert any(op == "voice_edit.escalation" for op, _ in audits)


def test_ask_escalation_unknown_target_stays_local(monkeypatch):
    svc, audits = _service(cfg=_cfg(routing="api", allow_api=True,
                                    api_profile="ghost-profile"))
    monkeypatch.setattr(svc, "_known_model_profile", lambda pid: False)
    calls = {}

    class FakeRes:
        ok = True
        text = "local answer"
        provider = "local"
        model = "qubit-brain"
        code = ""
        message = ""

    import voicebridge.providers.gateway as GW
    monkeypatch.setattr(GW, "run_role",
                        lambda prompt, cfg, **kw: (calls.update(kw), FakeRes())[1])
    out = svc.ask("hello", request_id="r-3", mode="chat")
    # Unknown API target degrades to the local profile (never the resolver
    # default): ask() always passes an explicit profile_id now.
    assert out["ok"] and calls.get("profile_id") == "qubit-brain"
    esc = [d for op, d in audits if op == "voice_edit.escalation"]
    assert esc and esc[-1].get("escalate") is False
    assert esc[-1].get("reason") == "unknown_api_target"


def test_ask_local_routing_never_escalates(monkeypatch):
    svc, audits = _service(cfg=_cfg(routing="local", allow_api=True))
    calls = {}

    class FakeRes:
        ok = True
        text = "local answer"
        provider = "local"
        model = "qubit-brain"
        code = ""
        message = ""

    import voicebridge.providers.gateway as GW
    monkeypatch.setattr(GW, "run_role",
                        lambda prompt, cfg, **kw: (calls.update(kw), FakeRes())[1])
    svc.ask("hello", request_id="r-4", mode="chat")
    # routing=local resolves the local profile explicitly (default
    # qubit-brain) instead of falling through to the resolver default.
    assert calls.get("profile_id") == "qubit-brain"
    assert not [d for op, d in audits if op == "voice_edit.escalation"]


# ---------------------------------------------------------------------------
# desktop helper route/task handling (fake client, no X/mic/socket)
# ---------------------------------------------------------------------------
class FakeHelperClient:
    def __init__(self, route_out):
        self._route_out = route_out
        self.calls = []

    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self):
        return {"request_id": "req-1"}

    def capture(self, rid):
        return {"selection": {"available": False}, "target_app": ""}

    def route(self, rid, transcript):
        self.calls.append(("route", rid, transcript))
        return self._route_out

    def task(self, text, *, confirm=False, request_id=""):
        self.calls.append(("task", text, confirm))
        return {"ok": True, "mode": "task_result", "reply": "Done: " + text}

    def ask(self, text, rid=""):
        self.calls.append(("ask", text, rid))
        return "The answer."

    def speak(self, text):
        self.calls.append(("speak", text))
        return b""

    def cancel(self, rid):
        return {}


class FakeOverlay:
    def __init__(self):
        self.statuses = []
        self.previews = []
        self.errors = []

    def set_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def show_preview(self, payload):
        self.previews.append(payload)

    def show_error(self, code, detail=""):
        self.errors.append((code, detail))

    def hide(self):
        pass

    def show(self):
        pass


def _helper(route_out):
    from voicebridge.desktop_helper import DesktopHelper
    cfg = _cfg()
    client = FakeHelperClient(route_out)
    overlay = FakeOverlay()
    h = DesktopHelper(cfg, client=client, overlay=overlay,
                      desktop=FakeDesktop())
    return h, client, overlay


def test_helper_routes_answer_delegates_to_ask_and_never_types():
    h, client, overlay = _helper({"ok": True, "mode": "answer",
                                  "question": "what time is it"})
    h.request_id = "req-1"
    h._pending_task = {}
    h._process_transcript("what time is it")
    assert ("route", "req-1", "what time is it") in client.calls
    assert any(c[0] == "ask" for c in client.calls)
    assert not h.desktop.inserted


def test_helper_task_is_confirm_gated_then_runs():
    h, client, overlay = _helper({"ok": True, "mode": "task",
                                  "requires_confirmation": True,
                                  "task_text": "search the web for qubits"})
    h._process_transcript("search the web for qubits")
    # Nothing executed yet: a proposal is staged.
    assert not any(c[0] == "task" for c in client.calls)
    assert h._pending_task["text"] == "search the web for qubits"
    # Next utterance confirms -> the task runs.
    h._process_transcript("yes")
    assert ("task", "search the web for qubits", True) in client.calls
    assert h._pending_task == {}


def test_helper_task_can_be_cancelled():
    h, client, overlay = _helper({"ok": True, "mode": "task",
                                  "requires_confirmation": True,
                                  "task_text": "delete everything"})
    h._process_transcript("delete everything")
    assert h._pending_task
    h._process_transcript("no")
    assert h._pending_task == {}
    assert not any(c[0] == "task" for c in client.calls)


def test_helper_dictate_types_verbatim():
    h, client, overlay = _helper({"ok": True, "mode": "dictate",
                                  "transcript": "hello world"})
    h._process_transcript("Qubit, type this for me: hello world")
    assert h.desktop.inserted == ["hello world"]


# ---------------------------------------------------------------------------
# ask() live facts (clock always, weather on intent, failures degrade)
# ---------------------------------------------------------------------------
def test_live_facts_clock_always_weather_only_on_intent(monkeypatch):
    svc, _audits = _service()
    monkeypatch.setattr(svc, "_live_weather",
                        lambda q: (_calls.append(q), "Bucharest: 18°C")[1])
    _calls = []
    facts = svc._live_facts("what time is it")
    assert "Current local time:" in facts
    assert _calls == []  # no weather probe without intent
    facts = svc._live_facts("What's the weather in Bucharest right now?")
    assert "Current local time:" in facts
    assert "Bucharest: 18°C" in facts
    assert _calls == ["What's the weather in Bucharest right now?"]


def test_live_weather_failure_degrades_to_empty(monkeypatch):
    import subprocess
    svc, _audits = _service()

    def _boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert svc._live_weather("weather in Bucharest") == ""
    assert svc._live_facts("weather?") != ""  # clock still present


# ---------------------------------------------------------------------------
# ask() direct factual answers (no model consulted)
# ---------------------------------------------------------------------------
def _no_brain(monkeypatch):
    import voicebridge.providers.gateway as GW

    def _boom(prompt, cfg, **kw):
        raise AssertionError("brain must not be consulted for facts")

    monkeypatch.setattr(GW, "run_role", _boom)


def test_ask_answers_weather_directly_without_brain(monkeypatch):
    svc, audits = _service()
    _no_brain(monkeypatch)
    monkeypatch.setattr(svc, "_live_weather", lambda q: "Bucharest: 14°C, Clear.")
    out = svc.ask("What's the weather in Bucharest right now?",
                  request_id="w-1", mode="answer")
    assert out["ok"] and out["answer"] == "Bucharest: 14°C, Clear."
    assert out["model"] == "live-facts"
    assert any(op == "voice_edit.ask" for op, _d in audits)


def test_ask_answers_time_directly_without_brain(monkeypatch):
    svc, _audits = _service()
    _no_brain(monkeypatch)
    out = svc.ask("What time is it?", request_id="t-1", mode="answer")
    assert out["ok"] and out["answer"].startswith("It is ")
    assert out["model"] == "live-facts"


def test_ask_weather_failure_falls_through_to_brain(monkeypatch):
    svc, _audits = _service()
    monkeypatch.setattr(svc, "_live_weather", lambda q: "")

    class FakeRes:
        ok = True
        text = "brain fallback"
        provider = "local"
        model = "qubit-brain"
        code = ""
        message = ""

    import voicebridge.providers.gateway as GW
    monkeypatch.setattr(GW, "run_role", lambda prompt, cfg, **kw: FakeRes())
    out = svc.ask("weather in Bucharest?", request_id="w-2", mode="answer")
    assert out["ok"] and out["answer"] == "brain fallback"


# ---------------------------------------------------------------------------
# ask() skill-backed answers (mail digest, YouTube summary)
# ---------------------------------------------------------------------------
def test_ask_mail_without_account_answers_setup_without_brain(monkeypatch):
    svc, audits = _service()
    _no_brain(monkeypatch)
    monkeypatch.setattr(svc, "_read_gmail_rows", lambda: None)
    monkeypatch.setattr(svc, "_gmail_configured", lambda: False)
    out = svc.ask("do I have any new mail?", request_id="m-1", mode="answer")
    assert out["ok"] and "No mail account is connected yet" in out["answer"]
    assert out["model"] == "skill-mail"


def test_ask_mail_with_account_reads_deterministic_digest(monkeypatch):
    svc, _audits = _service()
    _no_brain(monkeypatch)
    monkeypatch.setattr(svc, "_read_gmail_rows", lambda: None)
    monkeypatch.setattr(svc, "_gmail_configured", lambda: True)
    monkeypatch.setattr(
        svc, "_gmail_envelopes",
        lambda: [("boss@example.com", "Q3 planning"),
                 ("news@example.com", "Weekly digest")])
    out = svc.ask("check my gmail inbox", request_id="m-2", mode="answer")
    assert out["ok"] and "Q3 planning" in out["answer"]
    assert "boss@example.com" in out["answer"]
    assert out["model"] == "skill-mail"


def test_ask_mail_prefers_logged_in_browser_session(monkeypatch):
    svc, _audits = _service()
    _no_brain(monkeypatch)
    monkeypatch.setattr(
        svc, "_read_gmail_rows",
        lambda: [("boss@example.com", "Q3 planning")])

    def _boom():
        raise AssertionError("himalaya must not run when CDP answers")

    monkeypatch.setattr(svc, "_gmail_configured", _boom)
    out = svc.ask("check my email", request_id="m-3", mode="answer")
    assert out["ok"] and "Q3 planning" in out["answer"]
    assert out["model"] == "skill-mail"


def test_ask_youtube_summarizes_without_fact_path(monkeypatch):
    svc, _audits = _service()
    monkeypatch.setattr(
        VoiceEditService, "_youtube_script",
        lambda self: "/tmp/never-called.py")

    import voicebridge.providers.gateway as GW

    class FakeRes:
        ok = True
        text = "A song about never giving up."
        provider = "local"
        model = "qubit-spark17"
        code = ""
        message = ""

    monkeypatch.setattr(GW, "run_role",
                        lambda prompt, cfg, **kw: FakeRes())
    import subprocess as _sp

    class _Done:
        returncode = 0
        stdout = "Never gonna give you up " * 50

    monkeypatch.setattr(_sp, "run", lambda *a, **k: _Done())
    out = svc.ask("summarize https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                  request_id="y-1", mode="answer")
    assert out["ok"] and out["answer"] == "A song about never giving up."
    assert out["model"] == "qubit-spark17"


def test_parse_envelopes_is_defensive():
    rows = VoiceEditService._parse_envelopes(
        '[{"subject": "Hi", "from": {"address": "a@b.c"}}]')
    assert rows == [("a@b.c", "Hi")]
    assert VoiceEditService._parse_envelopes("not json") is None
    assert VoiceEditService._parse_envelopes('{"envelopes": []}') == []


def test_ask_reply_stages_draft_for_confirmation(monkeypatch):
    svc, audits = _service()
    _no_brain(monkeypatch)
    monkeypatch.setattr(
        svc, "_run_social_script",
        lambda name: [{"name": "Pro Nume",
                       "href": "https://www.messenger.com/t/1/",
                       "preview": "hi"}])
    out = svc.ask("reply to Pro Nume on messenger saying hello there",
                  request_id="d-1", mode="answer")
    assert out["ok"] and out["mode"] == "task"
    assert out["requires_confirmation"] is True
    assert out["task_text"] == (
        "reply|messenger|Pro Nume|https://www.messenger.com/t/1/|hello there")
    assert "Pro Nume" in out["summary"] and "hello there" in out["summary"]
    assert any(op == "voice_edit.reply.staged" for op, _d in audits)


def test_ask_reply_ambiguous_names_people_without_brain(monkeypatch):
    svc, _audits = _service()
    _no_brain(monkeypatch)
    monkeypatch.setattr(
        svc, "_run_social_script",
        lambda name: [{"name": "Pro Nume", "href": "h1"},
                      {"name": "Pro Other", "href": "h2"}])
    out = svc.ask("reply to pro on messenger saying hi", request_id="d-2",
                  mode="answer")
    assert out["ok"] and "couldn't find" in out["answer"]
    assert "Pro Nume" in out["answer"] and "Pro Other" in out["answer"]
    assert out["model"] == "skill-social"


def test_ask_reply_unreachable_names_no_phantom_promise(monkeypatch):
    svc, _audits = _service()
    _no_brain(monkeypatch)
    monkeypatch.setattr(svc, "_run_social_script", lambda name: None)
    out = svc.ask("reply to Pro Nume on messenger saying hi",
                  request_id="d-3", mode="answer")
    assert out["ok"] and "can't reach messenger" in out["answer"]
    assert out["model"] == "skill-social"


# ---------------------------------------------------------------------------
# ask() social read intents (messenger / instagram, read-only)
# ---------------------------------------------------------------------------
def test_ask_messenger_reads_digest_without_brain(monkeypatch):
    svc, _audits = _service()
    _no_brain(monkeypatch)
    monkeypatch.setattr(
        svc, "_run_social_script",
        lambda name: [{"name": "Pro Nume",
                       "preview": "The audio call ended."}] if name.endswith(
            "messenger.py") else (_ for _ in ()).throw(
                AssertionError("wrong script")))
    out = svc.ask("any new messenger chats?", request_id="s-1", mode="answer")
    assert out["ok"] and "Pro Nume" in out["answer"]
    assert out["model"] == "skill-social"


def test_ask_instagram_reads_digest_without_brain(monkeypatch):
    svc, _audits = _service()
    _no_brain(monkeypatch)
    monkeypatch.setattr(
        svc, "_run_social_script",
        lambda name: [{"name": "tr.cky", "text": "hey"}] if name.endswith(
            "instagram.py") else (_ for _ in ()).throw(
                AssertionError("wrong script")))
    out = svc.ask("check my instagram dms", request_id="s-2", mode="answer")
    assert out["ok"] and "tr.cky" in out["answer"]
    assert out["model"] == "skill-social"


def test_ask_reply_verbs_fall_through_to_brain(monkeypatch):
    svc, _audits = _service()
    monkeypatch.setattr(
        svc, "_run_social_script",
        lambda name: (_ for _ in ()).throw(
            AssertionError("reads must not run for replies")))

    class FakeRes:
        ok = True
        text = "brain handles replies"
        provider = "local"
        model = "qubit-brain"
        code = ""
        message = ""

    import voicebridge.providers.gateway as GW
    monkeypatch.setattr(GW, "run_role", lambda prompt, cfg, **kw: FakeRes())
    out = svc.ask("reply to Pro Nume on messenger", request_id="s-3",
                  mode="answer")
    assert out["ok"] and out["answer"] == "brain handles replies"