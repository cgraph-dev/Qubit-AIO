"""Pure tests for the ONE voice decision router (Part B).

No network and no model: the intent classifier and the brain router are
injected fakes, and the config is a minimal ``.get(*path, default=)`` shim.
"""
from __future__ import annotations

import pytest

from voicebridge.voice_edit.router import MODES, RouteDecision, VoiceRouter


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
    def __init__(self, kind="conversation", raises=False):
        self.kind = kind
        self.raises = raises
        self.calls = []

    def classify(self, text):
        self.calls.append(text)
        if self.raises:
            raise RuntimeError("classifier boom")
        return FakeIntent(self.kind, text)


class FakeBrain:
    def __init__(self, result=None, raises=False):
        self.result = result
        self.raises = raises
        self.calls = []

    def classify(self, cfg, transcript, *, has_selection,
                 timeout_s=4.0, cancel=None):
        self.calls.append((transcript, has_selection, timeout_s))
        if self.raises:
            raise RuntimeError("brain boom")
        return self.result


def cfg_for(routing="local", allow_api=True, router="deterministic",
            **voice_edit):
    ve = {"routing": routing, "allow_api": allow_api, "router": router}
    ve.update(voice_edit)
    return FakeCfg({"voice_edit": ve})


def test_modes_are_exactly_the_frozen_set():
    assert MODES == ("dictate", "transform", "answer", "task", "chat",
                     "ignore")


def test_empty_is_ignored():
    router = VoiceRouter(cfg_for(), classifier=FakeClassifier())
    for text in ("", "   ", "\n\t"):
        d = router.decide(text, has_selection=False)
        assert d.mode == "ignore"
        assert d.reason == "empty"


def test_explicit_dictation_cue_is_dictate():
    router = VoiceRouter(cfg_for(), classifier=FakeClassifier("conversation"))
    d = router.decide("Qubit, type this for me: hello", has_selection=False)
    assert d.mode == "dictate"
    assert d.task_text == "hello"


def test_selection_with_edit_command_is_transform():
    clf = FakeClassifier("conversation")
    router = VoiceRouter(cfg_for(), classifier=clf)
    d = router.decide("fix the grammar", has_selection=True)
    assert d.mode == "transform"
    assert d.reason == "edit_command"
    assert d.params["action"] == "correct_grammar"
    # The classifier must not be consulted when a selection owns the turn.
    assert clf.calls == []


def test_selection_ambiguous_text_still_transform_clarify():
    router = VoiceRouter(cfg_for(router="deterministic"),
                         classifier=FakeClassifier("coding"))
    d = router.decide("do the thing", has_selection=True)
    assert d.mode == "transform"
    assert d.reason == "clarify"
    assert d.params.get("clarification")


def test_selection_brain_maps_ambiguous_command():
    brain = FakeBrain(result={"action": "shorten", "style": "concise"})
    router = VoiceRouter(cfg_for(router="brain"), classifier=FakeClassifier(),
                         brain=brain)
    d = router.decide("say less basically", has_selection=True)
    assert d.mode == "transform"
    assert d.reason == "brain"
    assert d.params["action"] == "shorten"
    assert d.params["decision"] == {"action": "shorten", "style": "concise"}
    assert brain.calls[0][1] is True


def test_selection_brain_no_match_stays_transform_clarify():
    brain = FakeBrain(result=None)
    router = VoiceRouter(cfg_for(router="brain"), classifier=FakeClassifier(),
                         brain=brain)
    d = router.decide("do the thing", has_selection=True)
    assert d.mode == "transform"
    assert d.reason == "clarify"


def test_brain_mode_uncertain_selection_consults_brain():
    brain = FakeBrain(result={"action": "correct_grammar", "style": ""})
    router = VoiceRouter(cfg_for(router="brain"), classifier=FakeClassifier(),
                         brain=brain)
    d = router.decide("do the thing", has_selection=True)
    assert d.mode == "transform"
    assert d.reason == "brain"
    assert d.params["action"] == "correct_grammar"
    assert len(brain.calls) == 1
    assert brain.calls[0][1] is True


def test_deterministic_mode_never_consults_brain():
    brain = FakeBrain(result={"action": "correct_grammar", "style": ""})
    router = VoiceRouter(cfg_for(router="deterministic"),
                         classifier=FakeClassifier(), brain=brain)
    d = router.decide("do the thing", has_selection=True)
    assert d.mode == "transform"
    assert d.reason == "clarify"
    assert brain.calls == []


def test_brain_mode_none_result_falls_back_to_clarify():
    brain = FakeBrain(result=None)
    router = VoiceRouter(cfg_for(router="brain"), classifier=FakeClassifier(),
                         brain=brain)
    d = router.decide("do the thing", has_selection=True)
    assert d.mode == "transform"
    assert d.reason == "clarify"
    assert len(brain.calls) == 1


def test_no_selection_question_is_answer():
    clf = FakeClassifier("coding")
    router = VoiceRouter(cfg_for(), classifier=clf)
    d = router.decide("what time is it?", has_selection=False)
    assert d.mode == "answer"
    assert d.reason == "question"
    assert clf.calls == []


def test_classifier_coding_maps_to_task():
    router = VoiceRouter(cfg_for(), classifier=FakeClassifier("coding"))
    d = router.decide("refactor the parser", has_selection=False)
    assert d.mode == "task"
    assert d.reason == "intent:coding"
    assert d.intent == "coding"


@pytest.mark.parametrize("kind", ["agent", "coding", "local"])
def test_classifier_task_kinds_map_to_task(kind):
    router = VoiceRouter(cfg_for(), classifier=FakeClassifier(kind))
    d = router.decide("open the file", has_selection=False)
    assert d.mode == "task"
    assert d.intent == kind


# ---------------------------------------------------------------------------
# system guard: only a short, anchored system command is a task
# ---------------------------------------------------------------------------
def test_greeting_containing_help_keyword_is_chat_not_task():
    # The classifier substring-matches ``help`` -> kind ``system``; the router
    # must not treat the pleasantry as a task.
    router = VoiceRouter(cfg_for(), classifier=FakeClassifier("system"))
    d = router.decide("good morning qubit, thanks for your help today",
                      has_selection=False)
    assert d.mode == "chat"
    assert d.mode != "task"
    assert d.intent == "system"


@pytest.mark.parametrize("text", ["status", "mute", "help",
                                  "what can you do", "how do i use you"])
def test_anchored_system_command_is_task(text):
    router = VoiceRouter(cfg_for(), classifier=FakeClassifier("system"))
    d = router.decide(text, has_selection=False)
    assert d.mode == "task"
    assert d.intent == "system"


@pytest.mark.parametrize("text", [
    "thanks", "thank you", "how are you", "nice work", "well done",
    "good evening", "good night",
])
def test_greeting_pleasantries_are_not_task(text):
    router = VoiceRouter(cfg_for(), classifier=FakeClassifier("system"))
    d = router.decide(text, has_selection=False)
    assert d.mode != "task"


def test_long_system_labelled_sentence_is_not_task():
    router = VoiceRouter(cfg_for(), classifier=FakeClassifier("system"))
    d = router.decide("please show me the system status of everything now",
                      has_selection=False)
    assert d.mode != "task"


def test_classifier_conversation_maps_to_chat():
    router = VoiceRouter(cfg_for(), classifier=FakeClassifier("conversation"))
    d = router.decide("I like turtles", has_selection=False)
    assert d.mode == "chat"
    assert d.reason == "intent:conversation"
    assert d.intent == "conversation"


@pytest.mark.parametrize("kind", ["clarify", "confirm", "cancel"])
def test_classifier_control_kinds_are_ignored(kind):
    router = VoiceRouter(cfg_for(), classifier=FakeClassifier(kind))
    d = router.decide("hmm maybe", has_selection=False)
    assert d.mode == "ignore"
    assert d.intent == kind


def test_classifier_exception_falls_back_to_chat():
    router = VoiceRouter(cfg_for(), classifier=FakeClassifier(raises=True))
    d = router.decide("something unclear", has_selection=False)
    assert d.mode == "chat"
    assert d.reason == "classifier_error"


def test_router_never_raises_with_broken_cfg():
    class BoomCfg:
        def get(self, *path, default=None):
            raise RuntimeError("cfg boom")

    router = VoiceRouter(BoomCfg(), classifier=FakeClassifier("conversation"))
    d = router.decide("hello", has_selection=False)
    assert d.mode == "chat"


def test_escalation_api_chat_escalates():
    router = VoiceRouter(cfg_for(routing="api", allow_api=True),
                         classifier=FakeClassifier("conversation"))
    d = router.decide("I like turtles", has_selection=False)
    assert d.mode == "chat"
    assert d.escalate is True
    assert d.params["escalation_reason"] == "routing=api"


def test_escalation_local_does_not_escalate():
    router = VoiceRouter(cfg_for(routing="local", allow_api=True),
                         classifier=FakeClassifier("conversation"))
    d = router.decide("I like turtles", has_selection=False)
    assert d.mode == "chat"
    assert d.escalate is False


def test_escalation_allow_api_false_does_not_escalate():
    router = VoiceRouter(cfg_for(routing="api", allow_api=False),
                         classifier=FakeClassifier("conversation"))
    d = router.decide("I like turtles", has_selection=False)
    assert d.escalate is False


def test_escalation_auto_only_for_task():
    task = VoiceRouter(cfg_for(routing="auto"),
                       classifier=FakeClassifier("coding"))
    d = task.decide("refactor the parser", has_selection=False)
    assert d.mode == "task"
    assert d.escalate is True

    chat = VoiceRouter(cfg_for(routing="auto"),
                       classifier=FakeClassifier("conversation"))
    d = chat.decide("I like turtles", has_selection=False)
    assert d.mode == "chat"
    assert d.escalate is False


def test_dictation_never_escalates():
    router = VoiceRouter(cfg_for(routing="api", allow_api=True),
                         classifier=FakeClassifier("conversation"))
    d = router.decide("Qubit, type this for me: hello", has_selection=False)
    assert d.mode == "dictate"
    assert d.escalate is False
    assert "escalation_reason" not in d.params


def test_transform_never_escalates():
    router = VoiceRouter(cfg_for(routing="api", allow_api=True),
                         classifier=FakeClassifier("conversation"))
    d = router.decide("fix the grammar", has_selection=True)
    assert d.mode == "transform"
    assert d.escalate is False
