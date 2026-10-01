"""Sender script JS hygiene: every inline JS expression in
``scripts/send_social.py`` must be syntactically valid JavaScript.

Regression: a stray paren (``})())``) shipped silently — CDP answered
``SyntaxError`` and every send refused. Hermetic: extracts complete
string constants containing DOM JS and runs ``node --check`` on each.
"""
from __future__ import annotations

import ast
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "send_social.py"


def _js_constants():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "document." in node.value):
            out.append(node.value)
    return out


def test_inline_js_parses_under_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node unavailable")
    exprs = _js_constants()
    assert exprs, "no inline JS found to check"
    for i, expr in enumerate(exprs):
        with tempfile.NamedTemporaryFile("w", suffix=".js",
                                         delete=False) as fh:
            fh.write(f"void ({expr});\n")
            path = fh.name
        proc = subprocess.run([node, "--check", path],
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, \
            f"JS expr {i} invalid: {proc.stderr[:200]}: {expr[:120]}"


# ---------------------------------------------------------------------------
# reply parse / draft / match (hermetic, no browser)
# ---------------------------------------------------------------------------
def test_parse_reply_channels_and_bodies():
    from voicebridge.voice_edit import send as S
    assert S.parse_reply("reply to Pro Nume on messenger saying hello") == {
        "channel": "messenger", "to_hint": "Pro Nume", "body": "hello"}
    assert S.parse_reply("tell tr.cky on instagram that I am late") == {
        "channel": "instagram", "to_hint": "tr.cky", "body": "I am late"}
    assert S.parse_reply("reply to the GitHub email saying got it") == {
        "channel": "gmail", "to_hint": "GitHub", "body": "got it"}
    assert S.parse_reply("reply to Pro Nume") is None  # no body
    assert S.parse_reply("tell me a joke") is None  # no channel
    assert S.parse_reply("check my email") is None  # read, not reply


def test_draft_round_trip_and_match_rules():
    from voicebridge.voice_edit import send as S
    d = S.build_draft("messenger", "Pro Nume", "https://h/1", "hi")
    assert S.parse_draft(d) == {"channel": "messenger", "to": "Pro Nume",
                                "href": "https://h/1", "body": "hi"}
    assert S.parse_draft("free text") is None
    assert S.parse_draft("reply|skype|Bob|h|hi") is None  # bad channel
    assert S.match_recipient([("Pro Nume", "h1")], "pro nume") == (
        "Pro Nume", "h1")
    assert S.match_recipient([("A", "h1"), ("B", "h2")], "zzz") is None
    assert S.match_recipient([("Pro Nume", "h1"), ("Pro Other", "h2")],
                             "pro") is None  # ambiguous: never guess


def test_resolve_uses_fresh_reads():
    from voicebridge.voice_edit import send as S
    hit = S.resolve("messenger", "nume",
                    readers={"messenger": lambda: [
                        {"name": "Pro Nume",
                         "href": "https://h/1"}]})
    assert hit == ("Pro Nume", "https://h/1")
    assert S.resolve("gmail", "github",
                     readers={"gmail": lambda: [("GitHub", "subj")]}) == (
        "GitHub", "GitHub")


def test_execute_refuses_without_resolve_or_script(monkeypatch):
    from voicebridge.voice_edit import send as S
    assert S.execute({"channel": "x", "to": "y", "body": "z"})["code"] == \
        "SEND_MALFORMED"
    monkeypatch.setattr(S, "resolve", lambda *a, **k: None)
    out = S.execute({"channel": "messenger", "to": "Ghost",
                     "href": "h", "body": "hi"})
    assert out == {"ok": False, "code": "SEND_RECIPIENT_GONE",
                   "detail": "could not re-resolve 'Ghost' on messenger"}


# ---------------------------------------------------------------------------
# broker registration: send_message is DESTRUCTIVE + confirm-demanding
# ---------------------------------------------------------------------------
def test_send_message_matches_staged_drafts_only():
    from voicebridge.local import actions as LA
    m = LA.match("reply|messenger|Pro Nume|https://h/1|hello")
    assert m is not None and m.name == "send_message"
    assert m.params == {
        "draft": "reply|messenger|Pro Nume|https://h/1|hello"}
    assert LA.match("please reply to Pro Nume") is None


def test_send_message_is_destructive_and_confirm_demanding():
    from voicebridge.broker import build_host_broker
    broker = build_host_broker()
    binding = broker.get("local.send_message")
    assert binding is not None
    assert binding.spec.needs_confirmation is True


def test_send_action_ok_and_failure_paths(monkeypatch):
    from voicebridge.local import actions as LA
    from voicebridge.voice_edit import send as S
    monkeypatch.setattr(
        S, "execute",
        lambda draft, dry_run=False: {"ok": True, "channel": "messenger",
                                      "to": "Pro Nume", "detail": "sent"})
    out = LA.ACTIONS["send_message"].fn(
        {"draft": "reply|messenger|Pro Nume|h|hi"})
    assert out == "Sent to Pro Nume on messenger."
    monkeypatch.setattr(
        S, "execute",
        lambda draft, dry_run=False: {"ok": False, "code": "SEND_GONE",
                                      "detail": "gone"})
    import pytest as _pt
    with _pt.raises(ValueError, match="SEND_GONE"):
        LA.ACTIONS["send_message"].fn(
            {"draft": "reply|messenger|Pro Nume|h|hi"})
    with _pt.raises(ValueError, match="SEND_MALFORMED"):
        LA.ACTIONS["send_message"].fn({"draft": "hello"})


def test_confirmed_task_dispatches_send_through_broker(monkeypatch):
    """The full confirm path: staged draft -> propose_tool -> broker
    (DESTRUCTIVE mints a user-issued grant) -> receipt. Hermetic
    (FakeBroker in the established tool_task style; the real broker
    sandboxes)."""
    import types
    from voicebridge.voice_edit import tool_task as TT

    class _Res:
        denied = ""
        ok = True
        error = ""
        output = "Sent to Pro Nume on messenger."

    class FakeBroker:
        def __init__(self):
            self.calls = []
            self.issued = []

        def get(self, name):
            if name != "local.send_message":
                return None
            return types.SimpleNamespace(
                spec=types.SimpleNamespace(needs_confirmation=True))

        def issue_confirmation(self, capability, args, *, issued_by,
                               ttl_s=120):
            self.issued.append((capability, dict(args), issued_by))
            return types.SimpleNamespace(id="conf-9")

        def execute(self, name, args, *, source, session=None, **kw):
            self.calls.append((name, source, kw.get("confirmation_id")))
            return _Res()

    broker = FakeBroker()
    monkeypatch.setattr(TT, "_broker", lambda cfg: broker)
    monkeypatch.setattr(TT, "_persist", lambda *a, **k: None)
    out = TT.run_tool_task(
        None, "reply|messenger|Pro Nume|https://h/1|hi", confirmed=True)
    assert out["state"] == "OK"
    assert out["tool_used"] == "local.send_message"
    assert "Pro Nume" in out["reply"]
    assert broker.issued == [("local.send_message",
                              {"draft": ("reply|messenger|Pro Nume|"
                                         "https://h/1|hi")}, "user")]
    assert broker.calls == [("local.send_message", "user", "conf-9")]
