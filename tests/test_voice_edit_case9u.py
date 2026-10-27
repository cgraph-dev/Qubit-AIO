"""Deterministic case transforms: parser mappings + no-model engine.

"make it uppercase" / "lowercase" / "title case" used to parse as
``custom_transform`` + clarification and then depend on a model that has no
such action. These tests pin the deterministic path: the parser maps the
command, the engine returns the exact text locally (the injected runner would
raise if it were ever called), and the existing preview/apply flow works.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import parser, prompts
from voicebridge.voice_edit.schema import (ACTIONS, InsertionResult,
                                           SelectionSnapshot)
from voicebridge.voice_edit.service import VoiceEditService
from voicebridge.voice_edit.transform import (TransformEngine, digest_text,
                                              title_case_text)


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


def _cfg(**over):
    ve = {"enabled": True, "routing": "local",
          "confirm_before_replace": False}
    ve.update(over)
    return FakeCfg({"voice_edit": ve})


def _no_model(*args, **kwargs):
    raise AssertionError(
        "the provider must not be called for a deterministic case transform")


class FakeDesktop:
    """In-memory desktop adapter; no real clipboard, keys, or display."""

    def __init__(self, selection="hello world"):
        self.selection = selection
        self.inserted = []
        self.clipboard = []

    def session(self):
        return {"session_type": "x11", "display": ":0", "x11": True}

    def _tool(self, name):
        return True

    def _clip_tool(self):
        return "fake"

    def set_clipboard(self, text):
        self.clipboard.append(text)
        return True

    def is_fullscreen(self, window_id=""):
        return False

    def capture_selection(self, cfg=None):
        snap = SelectionSnapshot(
            available=True, text=self.selection,
            char_count=len(self.selection), app_class="code",
            app_name="code", window_id="42", focused=True,
            captured_via="clipboard")
        snap.digest = digest_text(self.selection)
        return snap

    def insert_text(self, text, snapshot, cfg=None, *, method=""):
        self.inserted.append(text)
        return InsertionResult(ok=True, method="clipboard_paste",
                               app_class="code", focus_restored=True,
                               clipboard_restored=True)

    def revert_last(self, snapshot, cfg=None):
        return InsertionResult(ok=True, method="undo", app_class="code",
                               reverted=True, focus_restored=True)


# ---------------------------------------------------------------------------
# schema + parser
# ---------------------------------------------------------------------------
def test_case_actions_are_registered_additively():
    assert {"uppercase", "lowercase", "title_case"} <= ACTIONS
    # Existing actions are untouched.
    assert {"rewrite", "clarify", "custom_transform", "dictate_text"} <= ACTIONS
    for action in ("uppercase", "lowercase", "title_case"):
        assert prompts.default_prompt(action) is not None


@pytest.mark.parametrize("text,action", [
    ("make it uppercase", "uppercase"),
    ("make this uppercase", "uppercase"),
    ("make this text uppercase", "uppercase"),
    ("uppercase this text", "uppercase"),
    ("make the whole thing uppercase", "uppercase"),
    ("make it all caps", "uppercase"),
    ("all caps", "uppercase"),
    ("uppercase", "uppercase"),
    ("convert this to upper case", "uppercase"),
    ("change it to capital letters", "uppercase"),
    ("MAKE IT UPPERCASE.", "uppercase"),
    ("make it lowercase", "lowercase"),
    ("make this lowercase", "lowercase"),
    ("lowercase", "lowercase"),
    ("all lowercase", "lowercase"),
    ("convert it to lower case", "lowercase"),
    ("change it to small letters", "lowercase"),
    ("make it lower case please", "lowercase"),
    ("title case", "title_case"),
    ("make it title case", "title_case"),
    ("convert this to title case", "title_case"),
    ("capitalize the words", "title_case"),
    ("capitalize every word", "title_case"),
    ("capitalize each word", "title_case"),
    ("capitalize the first letter of each word", "title_case"),
])
def test_parser_maps_case_commands_without_clarification(text, action):
    cmd = parser.parse(text, selected_text="hello world")
    assert cmd.action == action
    assert cmd.needs_clarification is False
    assert cmd.clarification == ""


@pytest.mark.parametrize("text", [
    "I keep the lowercase letters in the password",
    "The title case study is on the desk",
    "Capitalize on the opportunity while it lasts",
    "uppercase letters are easier to read in this document",
    "make it a better title for the case study",
])
def test_case_rules_do_not_hijack_unrelated_sentences(text):
    cmd = parser.parse(text, selected_text="hello world")
    assert cmd.action not in {"uppercase", "lowercase", "title_case"}


# ---------------------------------------------------------------------------
# engine: exact text, zero provider calls
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("command,selected,expected", [
    ("make it uppercase", "Hello, world! 123", "HELLO, WORLD! 123"),
    ("make it lowercase", "Hello, WORLD! 123", "hello, world! 123"),
    ("title case", "hello world", "Hello World"),
    ("capitalize the words", "the quick brown fox", "The Quick Brown Fox"),
])
def test_engine_case_transform_is_exact_and_model_free(command, selected,
                                                       expected):
    cmd = parser.parse(command, selected_text=selected)
    res = TransformEngine(runner=_no_model).transform(_cfg(), cmd)
    assert res.ok is True, res
    assert res.text == expected
    assert res.action == cmd.action
    assert res.model == "deterministic"
    assert res.provider == ""
    assert res.locality == "local"
    assert res.privacy_mode == "local"
    assert res.prompt_version == "case"
    assert isinstance(res.elapsed_ms, int) and res.elapsed_ms >= 0


@pytest.mark.parametrize("command,selected", [
    ("uppercase", "ALREADY UPPER"),
    ("lowercase", "already lower"),
    ("title case", "Hello World"),
    ("capitalize the words", "Already Title Cased"),
])
def test_already_in_case_is_typed_no_change(command, selected):
    cmd = parser.parse(command, selected_text=selected)
    res = TransformEngine(runner=_no_model).transform(_cfg(), cmd)
    assert res.ok is False
    assert res.code == "VOICE_EDIT_NO_CHANGE"
    assert res.action == cmd.action
    assert res.model == "deterministic"
    assert res.locality == "local"
    assert res.text == ""


def test_case_transform_never_consults_routing_config_for_a_provider():
    # Even an api-only configuration must stay local: the case path is before
    # prompt rendering and profile resolution.
    cfg = _cfg(routing="api", allow_api=False, api_profile="")
    cmd = parser.parse("make it uppercase", selected_text="hello")
    res = TransformEngine(runner=_no_model).transform(cfg, cmd)
    assert res.ok is True and res.text == "HELLO"
    assert res.privacy_mode == "local"
    assert res.provider == ""


def test_case_transform_without_selection_refuses_before_the_model():
    cmd = parser.parse("make it uppercase", selected_text="")
    res = TransformEngine(runner=_no_model).transform(_cfg(), cmd)
    assert res.ok is False
    assert res.code == "VOICE_EDIT_NO_SELECTION"


# ---------------------------------------------------------------------------
# unicode / title behavior
# ---------------------------------------------------------------------------
def test_title_case_unicode_apostrophes_and_whitespace():
    assert title_case_text("élan vital déjà vu") == "Élan Vital Déjà Vu"
    assert title_case_text("don't stop") == "Don't Stop"
    assert title_case_text('  "hello"   world  ') == '  "Hello"   World  '
    assert title_case_text("multiple---dashes") == "Multiple---Dashes"


def test_uppercase_and_lowercase_unicode():
    upper = parser.parse("make it uppercase", selected_text="straße grüße")
    up_res = TransformEngine(runner=_no_model).transform(_cfg(), upper)
    assert up_res.text == "STRASSE GRÜSSE"

    lower = parser.parse("make it lowercase", selected_text="ÉLAN VITAL")
    low_res = TransformEngine(runner=_no_model).transform(_cfg(), lower)
    assert low_res.text == "élan vital"


# ---------------------------------------------------------------------------
# service preview + apply compatibility (no model, no real desktop)
# ---------------------------------------------------------------------------
def test_service_preview_and_apply_case_transform():
    desktop = FakeDesktop(selection="hello world")
    audits = []
    svc = VoiceEditService(
        _cfg(), desktop=desktop,
        engine=TransformEngine(runner=_no_model),
        audit=lambda op, actor, detail: audits.append((op, detail)),
        estop=lambda cfg: False)
    rid = svc.begin()["request_id"]
    svc.capture(rid)
    out = svc.transform(rid, "make it uppercase")
    assert out["mode"] == "preview"
    assert out["original"] == "hello world"
    assert out["proposed"] == "HELLO WORLD"
    assert out["detected"]["action"] == "uppercase"
    assert out["model"] == "deterministic"
    assert out["locality"] == "local"
    assert out["prompt_version"] == "case"

    applied = svc.apply(rid, confirm=True)
    assert applied["ok"] is True and applied["mode"] == "applied"
    assert desktop.inserted == ["HELLO WORLD"]
    # The audit never carries the selected or proposed text.
    blob = repr(audits)
    assert "hello world" not in blob and "HELLO WORLD" not in blob
