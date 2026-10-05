"""Wake-path dictation without a selection capture (live 2026-10-04).

The wake path never captures a selection by design (service-side Ctrl+C
into the focused window is SIGINT in a terminal). But ``route()``
delegated dictate/transform decisions to ``transform()``, whose first
line raises ``VOICE_EDIT_NO_CAPTURE`` when ``req.selection is None``.
Result (journal 18:33): "Type this for me, 2 plus 5 equals 99." reached
the helper twice and vanished — nothing typed, nothing spoken.

Dictation carries its own payload and never needs a selection; only the
selection-replacement transform does. ``route()`` must seed the same
explicit unavailable snapshot the helper builds locally
(``captured_via="wake_no_capture"``) so dictate executes and selection
transforms degrade to clarify instead of raising.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from test_voice_edit9u import FakeDesktop, enabled_cfg  # noqa: E402
from voicebridge.voice_edit.service import VoiceEditService  # noqa: E402


def _svc():
    cfg = enabled_cfg()
    return VoiceEditService(cfg, desktop=FakeDesktop(),
                            audit=lambda *a: None,
                            estop=lambda cfg: False)


def test_wake_dictate_without_capture_types_payload():
    svc = _svc()
    rid = svc.begin()["request_id"]
    # NO svc.capture(rid) — the wake path never captures.
    out = svc.route(rid, "Type this for me, 2 plus 5 equals 99.")
    assert out.get("mode") == "dictate", out
    assert out.get("transcript") == "2 plus 5 equals 99.", out


def test_wake_loose_cue_without_capture_types_payload():
    svc = _svc()
    rid = svc.begin()["request_id"]
    out = svc.route(rid, "type this, for me, hello world")
    assert out.get("mode") == "dictate", out
    assert "type this" not in out.get("transcript", "").lower(), out


def test_wake_selection_transform_without_capture_clarifies_not_raises():
    svc = _svc()
    rid = svc.begin()["request_id"]
    out = svc.route(rid, "make it sound more professional")
    # No selection to replace: clarify (or ignore), never an exception
    # and never a typed NO_CAPTURE refusal surfacing as a crash.
    assert out.get("mode") in ("clarify", "answer", "ignore"), out


def test_right_ctrl_with_capture_unchanged():
    svc = _svc()
    rid = svc.begin()["request_id"]
    svc.capture(rid)
    out = svc.route(rid, "Type this for me, hello world")
    assert out.get("mode") == "dictate", out
    assert out.get("transcript") == "hello world", out
