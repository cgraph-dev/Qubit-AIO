"""Tests for the STEAL_LIST implementations (docs/archive/2026-09-21/STEAL_LIST.md).

Covers: qubit-brain4 thinking fallback (AirLLM), auto-preference CLHF
(CopilotKit), classifier tool-schema parsing (CopilotKit), activity delta
polling + ETag (CopilotKit), sandbox tool runner (OpenSandbox).

Run: python -m pytest tests/test_steal_list.py -v
No gateway/GPU/audio required.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.intent.llm import _parse_json_loose, VALID_KINDS
from voicebridge.learning import LearningLedger, _norm_key
from voicebridge.local_llm import _extract_reply
from voicebridge.sandbox import run_tool
from voicebridge.webui import ActivityFeed


# --------------------------------------------------------------- AirLLM (item 1)
def test_extract_reply_prefers_content():
    body = {"message": {"content": " 7 times 6 is 42. ", "thinking": "long thought"}}
    assert _extract_reply(body) == "7 times 6 is 42."


def test_extract_reply_never_speaks_thinking():
    """Packet 4B REVERSAL: under the /no_think template thinking text is
    genuine chain-of-thought, never a speakable answer — content stays empty
    so the caller's bounded retry / cloud fallback takes over instead of
    reading reasoning junk aloud."""
    body = {"message": {"content": "", "thinking": "12 * 8 = 96"}}
    assert _extract_reply(body) == ""


def test_extract_reply_all_empty():
    assert _extract_reply({"message": {}}) == ""
    assert _extract_reply({}) == ""


# ---------------------------------------------------------------- CLHF (items 2+4)
def test_auto_preference_after_repeated_good(tmp_path):
    led = LearningLedger(tmp_path)
    led.record("what's my instagram?", "It's @tr.cky", feedback="good")
    # first like: no preference yet
    assert not [r for r in led._read() if r.get("source") == "auto-good"]
    led.record("whats my instagram", "It's @tr.cky", feedback="good")
    prefs = [r for r in led._read() if r.get("pref") and r.get("source") == "auto-good"]
    assert len(prefs) == 1
    assert "@tr.cky" in prefs[0]["pref"]
    # same pattern again -> still exactly one preference (deduped)
    led.record("what is my instagram?", "It's @tr.cky", feedback="good")
    prefs = [r for r in led._read() if r.get("pref") and r.get("source") == "auto-good"]
    assert len(prefs) == 1


def test_correction_beats_wrong_answer_in_prompt(tmp_path):
    led = LearningLedger(tmp_path)
    led.record("what's my instagram", "I can't access it", feedback="correction",
               corrected="It's @tr.cky")
    prompt = led.as_prompt()
    assert "@tr.cky" in prompt
    assert "do NOT repeat" in prompt


def test_norm_key_strips_punctuation():
    assert _norm_key("What's my Instagram?") == _norm_key("whats my instagram")


# ------------------------------------------------------- CopilotKit schema (item 3)
def test_parse_json_loose_plain():
    assert _parse_json_loose('{"kind": "coding"}')["kind"] == "coding"


def test_parse_json_loose_fenced():
    raw = '```json\n{"kind": "conversation", "confidence": 0.9}\n```'
    assert _parse_json_loose(raw)["kind"] == "conversation"


def test_parse_json_loose_prose_wrapped():
    raw = 'Sure! {"kind": "agent", "task": "x"} hope that helps'
    assert _parse_json_loose(raw)["kind"] == "agent"


def test_parse_json_loose_garbage():
    assert _parse_json_loose("no json here at all") is None
    assert _parse_json_loose("") is None


def test_valid_kinds_cover_schema_tools():
    for k in ("system", "confirm", "cancel", "coding", "local",
              "agent", "conversation", "clarify"):
        assert k in VALID_KINDS


# -------------------------------------------------------- delta polling (item 5)
def test_activity_feed_since_filter():
    feed = ActivityFeed()
    feed.push({"id": "a", "ts": 100.0})
    feed.push({"id": "b", "ts": 200.0})
    feed.push({"id": "c", "ts": 300.0})
    assert [i["id"] for i in feed.recent(limit=10)] == ["c", "b", "a"]
    delta = feed.recent(limit=10, since=150.0)
    assert [i["id"] for i in delta] == ["c", "b"]      # newest first, > watermark
    assert feed.recent(limit=10, since=300.0) == []
    full = feed.recent(limit=10)                        # backward compatible
    assert len(full) == 3


# ------------------------------------------------------------ OpenSandbox (item 7)
def test_run_tool_captures_output():
    res = run_tool("echo hello", timeout_s=5)
    assert res.ok and res.stdout == "hello"


def test_run_tool_timeout_kills_hung_process():
    t0 = time.time()
    res = run_tool("echo partial && sleep 30", timeout_s=2)
    assert res.timed_out and not res.ok
    assert time.time() - t0 < 10                       # did NOT wait 30s
    assert res.stdout == "partial"                     # bounded capture kept


def test_run_tool_bounds_output():
    res = run_tool("yes hi | head -c 50000", timeout_s=10, max_output=200)
    assert len(res.stdout) == 200 and res.truncated


def test_run_tool_argv_and_failure():
    assert run_tool(["echo", "ok"]).stdout == "ok"
    bad = run_tool("exit 3", timeout_s=5)
    assert not bad.ok and bad.rc == 3


# --------------------------------------------- local LLM streaming plumbing (item 6)
def test_chat_local_stream_emits_sentences(monkeypatch):
    import voicebridge.local_llm as llm

    class FakeResp:
        def __init__(self, chunks):
            self._chunks = chunks

        def __iter__(self):
            return iter(self._chunks)

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        payload = json.loads(req.data.decode())
        assert payload["stream"] is True
        lines = [
            {"message": {"content": "Qubit here. "}},
            {"message": {"content": "All good!"}},
            {"message": {"content": "", "done": True}},
        ]
        return FakeResp([json.dumps(l).encode() + b"\n" for l in lines])

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    got = []
    full = llm.chat_local_stream("hi", on_sentence=got.append)
    assert got == ["Qubit here.", "All good!"]
    assert full == "Qubit here. All good!"
