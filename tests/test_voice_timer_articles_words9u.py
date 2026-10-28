"""Fix lane 2026-10-03 — voice "yes" dies + timer misroutes (9u).

EVIDENCE 1 (timer misroute, fully proven): "Set the timer for one minute."
fell through to the brain (intent:conversation) as bare WORDS — no staged
task, so the owner's "Yes." 10 s later arrived unarmed (wake.discarded).
Root cause: ``voicebridge/local/actions.py`` timer patterns rejected the
article "the" (only "a" allowed) and needed ASCII digits (number-word "one"
missed). The §71 proof used "set A timer", which is why it passed.

EVIDENCE 3 (step-3 "in chat" file task): NO "in chat" wake.command reached
the backend at all (404 wake.commands audited; none contains it). The long
sentence likely fragmented at the 8 s segment cap (or lost its prefix to
onset-clip) and the brain answered bare "say yes" WORDS. This file pins:
(a) the owner's EXACT long sentence routes whole through
``parse_route_word`` + the ``ask()`` bypass end-to-end; (b) the truncation
mechanism that explains the loss (text-layer proof, no production change).

All hermetic: no mic, no model, no service, no secrets.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# EVIDENCE 1 — timer articles + number-words (owner's EXACT sentence first)
# ---------------------------------------------------------------------------

def test_timer_owner_exact_sentence_sets_one_minute():
    """RED-first: the owner's exact utterance must stage a 1-minute timer."""
    from voicebridge.local import actions as LA
    m = LA.match("Set the timer for one minute.")
    assert m is not None and m.name == "timer", m
    assert int(m.params.get("minutes")) == 1, m.params


def test_timer_article_variants():
    from voicebridge.local import actions as LA
    for text, want in (
        ("set a timer for 5 minutes", 5),
        ("set the timer for 5 minutes", 5),
        ("set my timer for 5 minutes", 5),
        ("set an timer for 5 minutes", 5),  # tolerate spoken "an"
        ("start the timer", 5),  # no duration -> default
        ("start a timer", 5),
    ):
        m = LA.match(text)
        assert m is not None and m.name == "timer", (text, m)
        assert int(m.params.get("minutes")) == want, (text, m.params)


def test_timer_number_word_variants():
    from voicebridge.local import actions as LA
    for text, want in (
        ("set a timer for two minutes", 2),
        ("set a timer for three minutes", 3),
        ("set the timer for five minutes", 5),
        ("set a timer for ten minutes", 10),
        ("set a timer for twenty minutes", 20),
        ("set a timer for thirty minutes", 30),
        ("remind me in two minutes", 2),
        ("remind me in five minutes", 5),
        ("timer for one minute", 1),
        ("timer for 10 minutes", 10),
        ("set a timer for a minute", 1),
        ("set a timer for 90 seconds", 2),  # ceil(90/60)
        ("set a timer for ten seconds", 1),
    ):
        m = LA.match(text)
        assert m is not None and m.name == "timer", (text, m)
        assert int(m.params.get("minutes")) == want, (text, m.params)


def test_timer_classifier_routes_local_not_conversation():
    """The classifier must see the fixed sentence as a local action, so the
    router stages a task (propose_task) instead of answering bare words."""
    from voicebridge.intent.classifier import Classifier
    from voicebridge.intent.state import ConfirmationState, ConversationState
    # LLM disabled: hermetic. The local-action match precedes the LLM branch,
    # so this proves the deterministic route the router takes to task staging.
    clf = Classifier({"llm_classifier": False}, ConfirmationState(),
                      ConversationState())
    intent = clf.classify("Set the timer for one minute.")
    assert intent.kind == "local", intent
    assert intent.params.get("action") == "timer", intent.params


def test_timer_does_not_steal_neighbours():
    from voicebridge.local import actions as LA
    assert LA.match("start a timer").name == "timer"
    assert LA.match("remind me in 10 minutes").name == "timer"
    assert LA.match("remind me to call mom").name == "quick_note"
    assert LA.match("play lofi hip hop").name == "play_youtube"


# ---------------------------------------------------------------------------
# EVIDENCE 3 — owner's EXACT long sentence routes whole (parse + ask bypass)
# ---------------------------------------------------------------------------

OWNER_LONG_SENTENCE = (
    "hey qubit, in chat: create a file called voice-test.txt in my Documents "
    "containing the text hello voice"
)


def test_long_sentence_route_word_keeps_full_text():
    from voicebridge import chat_transport as CT
    # The wake layer strips "hey qubit" before the backend ever sees it, so
    # the backend receives the "in chat: ..." remainder whole.
    backend_text = "in chat: create a file called voice-test.txt in my Documents " \
        "containing the text hello voice"
    rw = CT.parse_route_word(backend_text)
    assert rw is not None and rw["route"] == "in-chat", rw
    assert rw["action"] == "agent_task", rw
    assert rw["task_text"] == backend_text[len("in chat:"):].strip(), rw
    assert "voice-test.txt" in rw["task_text"], rw
    assert "hello voice" in rw["task_text"], rw
    d = CT.decide_turn_action(backend_text, cfg={})
    assert d["action"] == "agent_task", d
    assert d["task_text"] == rw["task_text"], d
    assert d["reason"] == "route-word:in-chat", d


def test_long_sentence_ask_bypass_dispatches_worker_with_full_text():
    """ask() must skip the deterministic stages and hand the FULL stripped
    text to the headless /chat worker (no truncation, no brain narration)."""
    from voicebridge.voice_edit.router import RouteDecision  # noqa: F401 (import parity)
    from voicebridge.voice_edit.service import VoiceEditService
    from voicebridge.voice_edit.transform import TransformEngine

    class FakeCfg:
        def __init__(self, data=None):
            self._data = data or {}

        def get(self, *path, default=None):
            node = self._data
            for p in path:
                if isinstance(node, dict) and p in node:
                    node = node[p]
                else:
                    return default
            return node

        def state_dir(self):
            import tempfile
            return tempfile.gettempdir()

    class FakeDesktop:
        def session(self):
            return {"session_type": "x11", "display": ":0", "x11": True}

        def _tool(self, name):
            return False

    cfg = FakeCfg({"voice_edit": {"enabled": True, "routing": "local",
                                   "local_profile": "qubit-brain"}})
    audits = []

    def audit(op, actor, detail):
        audits.append((op, detail))

    svc = VoiceEditService(cfg, desktop=FakeDesktop(),
                           engine=TransformEngine(runner=lambda *a, **k: None),
                           audit=audit, estop=lambda cfg: False)
    seen = {}
    stages = []

    def fail_stage(question):
        stages.append("stage")
        return ("should-never-answer", "skill-x")

    import voicebridge.voice_edit.service as SVC
    svc._skill_answer = fail_stage
    svc._direct_fact_answer = lambda q: stages.append("fact") or "FACT"
    svc._reply_draft = lambda q: stages.append("draft")

    def capture(question, **kw):
        seen["q"] = question
        return {"ok": True, "answer": "WORKER_DID_IT",
                "mode": kw.get("mode", "answer"),
                "provider": "local", "model": "full-turn",
                "spoken": False}

    svc._full_turn_answer = capture
    backend_text = "in chat: create a file called voice-test.txt in my Documents " \
        "containing the text hello voice"
    out = svc.ask(backend_text)
    assert out["ok"] is True and out["answer"] == "WORKER_DID_IT", out
    assert stages == [], stages  # no deterministic stage ran
    assert seen["q"] == backend_text[len("in chat:"):].strip(), seen
    assert "voice-test.txt" in seen["q"] and "hello voice" in seen["q"], seen


def test_continuation_without_wake_word_is_discarded_proving_truncation_gap():
    """Text-layer proof of the fragmentation mechanism: if the 8 s cap cuts
    the long sentence mid-flight, the continuation segment carries NO wake
    word, so the wake layer discards it (strip_wake -> None). This is the
    hook point for any future continuation work — NOT implemented here
    (joining segments would change _emit semantics; arming alone would
    double-emit fragments). Proves the truncation, changes nothing.
    """
    from voicebridge.voice_edit.wake import strip_wake
    first_half = "hey qubit, in chat: create a file called voice-"
    second_half = "test.txt in my Documents containing the text hello voice"
    # First half carries the wake word -> routes.
    assert strip_wake(first_half) is not None
    # Continuation carries no wake word -> discarded by the loop's unarmed
    # fallback path (wake.py _handle_segment: remainder None -> discarded).
    assert strip_wake(second_half) is None


def test_energy_endpointer_cuts_continuous_speech_at_max():
    """The 8 s cap is real: continuous voicing completes a segment at the
    cap even with no silence (hook point: EnergyEndpointer.add_frame's
    ``total >= _max_frames`` branch; the audio/vad.py Endpointer has the
    same branch)."""
    import numpy as np
    from voicebridge.voice_edit.wake import EnergyEndpointer
    ep = EnergyEndpointer(frame_ms=300, speech_ms=300, silence_ms=5000,
                          max_s=8.0, rms_gate=0.0018)
    frame = (np.ones(4800, dtype=np.float32) * 0.1 * 32768).astype("<i2").tobytes()
    states = [ep.add_frame(frame) for _ in range(40)]
    assert "speech" in states, states  # it opened ...
    assert states[-1] == "done", states  # ... and the cap closed it
