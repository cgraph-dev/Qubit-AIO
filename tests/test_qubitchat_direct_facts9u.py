"""Direct clock/weather facts on the /qubit-chat turn path (no model).

Before this change a weather question on /qubit-chat fell into the
``web_search`` branch or the model and could narrate stale data (live
2026-10-04: "Weather in Bucharest on 27 February 2026" while
``scripts/weather.py`` reported live 16°C). Voice never had this bug
(``voice_edit/service.py::_direct_fact_answer``). Now ``chat_transport``
answers the same shapes deterministically on both ``turn()`` and
``stream_turn()``; anything else (or a failed weather read) falls through
to the classifier byte-identical.

Hermetic: fake store, no e-stop, no model, no network (weather read is a
stubbed ``subprocess.run``).
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


def _stub_weather(monkeypatch, line="Bucharest: 16.0C, Clear."):
    import subprocess

    class _Proc:
        returncode = 0
        stdout = line + "\n"

    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Proc())


# -- pure matcher --------------------------------------------------------

def test_time_question_answers_clock_without_model(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    out = CT.turn({}, "what time is it", session="facts-time-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "facts", out
    assert out["reply"].startswith("It is "), out
    assert fake.assistant_texts("facts-time-9u"), "assistant row persisted"


def test_weather_question_answers_live_reading(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    _stub_weather(monkeypatch, "Bucharest: 16.0C, Clear.")
    out = CT.turn({}, "weather in Bucharest", session="facts-wx-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "facts", out
    assert "16.0C" in out["reply"], out
    assert "February 2026" not in out["reply"], out


def test_temperature_trigger_uses_weather_backend(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    _stub_weather(monkeypatch, "Madrid: 21C, Sunny.")
    out = CT.turn({}, "temperature in Madrid", session="facts-temp-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "facts", out
    assert "21C" in out["reply"], out


def test_failed_weather_read_falls_through_not_facts(monkeypatch):
    from voicebridge import chat_transport as CT

    import subprocess
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("no network")))
    assert CT._direct_fact_answer("weather in Bucharest") == ""


def test_plain_text_never_enters_fact_path():
    from voicebridge import chat_transport as CT

    assert CT._direct_fact_answer("hello there") == ""
    assert CT._direct_fact_answer("open youtube") == ""
    assert CT._direct_fact_answer("") == ""


# -- streaming (the path ChatPage.tsx actually uses) -----------------------

def test_stream_time_streams_fact_delta(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    events = list(CT.stream_turn({}, "what time is it",
                                 session="facts-stream-9u"))
    kinds = [e.get("event") for e in events]
    assert kinds[0] == "start", events
    assert "delta" in kinds and kinds[-1] == "done", events
    done = events[-1]
    assert done.get("route") == "facts", done
    assert str(done.get("reply") or "").startswith("It is "), done
