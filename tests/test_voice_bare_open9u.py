"""Bug-2 lane (2026-10-04): bare single-verb "open" must clarify, never task.

Journal-proof defect (06:59:38, pid 1674120): bare "Open." routed
`task/coding` (`reason: intent:coding`, escalated) instead of clarifying.
Mechanism: `TASK_HINTS` scores any bare `\\bopen\\b` as 0.5 (partial task
hints) so the classifier emits `coding`; no local action matches a bare
verb (open_website needs a domain/shortcut, open_document needs a
document word). Long-standing since the original voice bridge
(`aa7d1b9f`, 2026-08-19) — not an onset-wave regression (the onset diff
touches capture/priming only; zero arm/route hunks).

Contract pinned here (classifier fakes: no network/model):
* bare "open" (any case/punctuation, optional wake address) -> chat with a
  clarify reason, NEVER task — even when the classifier says `coding`;
* the classifier is not even consulted for a bare open;
* "Open YouTube." / "Open a text document." still route task (no shadow).
"""
from __future__ import annotations

import pytest

from voicebridge.voice_edit.router import VoiceRouter


class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, *path, default=None):
        cur = self._data
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return default
        return cur


class FakeIntent:
    def __init__(self, kind, task_text=""):
        self.kind = kind
        self.task_text = task_text


class FakeClassifier:
    def __init__(self, kind="conversation"):
        self.kind = kind
        self.calls = []

    def _task_hint_score(self, text):
        return 0.0

    def classify(self, text):
        self.calls.append(text)
        return FakeIntent(self.kind, text)


def _router(kind="coding"):
    cfg = FakeCfg({"voice_edit": {"routing": "local", "allow_api": True,
                                  "router": "deterministic"}})
    clf = FakeClassifier(kind)
    return VoiceRouter(cfg, classifier=clf), clf


@pytest.mark.parametrize("text", [
    "Open.", "open", "OPEN", "open!", "Open?",
    "Qubit, open.", "hey qubit open",
])
def test_bare_open_clarifies_never_tasks(text):
    router, clf = _router(kind="coding")
    d = router.decide(text, has_selection=False)
    assert d.mode == "chat", d
    assert d.mode != "task"
    assert clf.calls == [], "bare open must short-circuit before classify"


def test_bare_open_even_coding_verdict_becomes_chat():
    # The strongest pin: even a classifier `coding` verdict for a bare
    # open (the journal shape) must never become a voice task.
    router, _ = _router(kind="coding")
    d = router.decide("Open.", has_selection=False)
    assert d.mode == "chat"
    assert "open" in (d.reason or "").lower()


def test_open_with_object_still_tasks():
    for text, kind in (("Open YouTube.", "local"),
                       ("Open a text document.", "coding"),
                       ("Open the pod bay doors.", "agent")):
        router, _ = _router(kind=kind)
        d = router.decide(text, has_selection=False)
        assert d.mode == "task", (text, d)


def test_bare_open_does_not_dictate_or_ignore():
    router, _ = _router(kind="coding")
    d = router.decide("Open.", has_selection=False)
    assert d.mode not in ("dictate", "ignore", "task")
