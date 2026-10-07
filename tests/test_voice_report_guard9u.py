"""Voice report lane — defect-report over-match guard (verifier, 2026-10-07).

``is_report_request("report a bug in the session manager")`` matched the old
``_REPORT_CHAT_RE`` purely because a report-ish verb and the noun ``session``
sat in the same sentence (``[^.?!]*`` bridged them). That routed a bug report
into the chat-session summarizer. The matcher is now adjacency-bounded and
explicitly excludes a defect-report frame, so a bug/issue/problem report is a
normal ask.

Hermetic: no store, model, or network. The guard is pure text classification,
plus one ``_report_answer`` call proving the defect text never reaches the
transcript read.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.voice_edit.service import VoiceEditService  # noqa: E402
from voicebridge.voice_edit.transform import TransformEngine  # noqa: E402


class _FakeCfg:
    def get(self, *path, default=None):
        return default


class _FakeDesktop:
    def session(self):
        return {"session_type": "x11"}

    def _tool(self, name):
        return False


def _service():
    return VoiceEditService(
        _FakeCfg(), desktop=_FakeDesktop(),
        engine=TransformEngine(runner=lambda *a, **k: None),
        router=None, estop=lambda cfg: False)


# ---------------------------------------------------------------------------
# the reported over-match (red before the guard)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "report a bug in the session manager",
    "report an issue with the session",
    "report a problem in my chat session",
    "report the issue in the conversation",
    "report an error in the chat session",
    "file a bug report about the session",
    "file an issue report on the conversation",
])
def test_defect_report_is_not_a_chat_report(text):
    assert VoiceEditService.is_report_request(text) is False, text


# ---------------------------------------------------------------------------
# the genuine chat-session report still matches
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "report on my chat",
    "give me a report on the last session",
    "summarize the chat session",
    "summarize our conversation",
    "recap the conversation",
    "what did we do in that session",
    "what happened in that chat?",
])
def test_chat_report_still_matches(text):
    assert VoiceEditService.is_report_request(text) is True, text


@pytest.mark.parametrize("text", [
    "what time is it",
    "summarize this document",
    "how are you",
    "open youtube",
    "check my email",
    "what did we do",              # no chat noun -> conservative miss
    "say that again",
    "tell me a joke",
])
def test_ordinary_questions_do_not_match(text):
    assert VoiceEditService.is_report_request(text) is False, text


def test_defect_report_never_reaches_the_transcript_read(monkeypatch):
    """End-to-end guard: a defect report returns ("", "") before any store read."""
    import voicebridge.chat_transport as CT

    touched = []

    def _boom_store(cfg):  # pragma: no cover - must not be called
        touched.append(True)
        raise AssertionError("defect text reached the report transcript read")

    monkeypatch.setattr(CT, "store", _boom_store)
    svc = _service()
    assert svc._report_answer("report a bug in the session manager",
                              request_id="") == ("", "")
    assert touched == []
