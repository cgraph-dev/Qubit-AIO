"""The assembled local prompt has a hard ceiling (one config key, one guard).

A live session died with a provider 502 "Prompt too long (max 6000
characters)" because only the RAW user text was bounded: the assembled turn
(compiled context + instructions + question) had no total ceiling. The guard
under test lives at the end of ``local_llm._user_content`` — the single choke
point both payload paths share — and TRIMS (context first, question kept)
rather than failing the turn.

Contract proved here:
  * oversized assembly -> trimmed to ``intent.local_llm.max_prompt_chars``,
    a turn still runs end-to-end, and ``runner.prompt.trimmed`` is journaled;
  * a prompt under the ceiling is returned byte-for-byte unchanged;
  * the user's own text (and its ``/no_think`` marker) survives trimming;
  * the config key is honoured when set (and read per turn, no restart).
"""
from __future__ import annotations

import json

import pytest

import voicebridge.local_llm as llm
from voicebridge.config import DEFAULT_MAX_PROMPT_CHARS

# A large but realistic assembly: bounded per-section budgets still let the
# compiled context grow past a provider's limit.
_BIG = 12000


def _fake_compiler(prompt_chars: int):
    """Minimal stand-in for the 5C compiler: ``prompt_chars`` of body text."""
    body = ("CONTEXT-MARKER " + "x" * prompt_chars)

    class _Report:
        sections_included = ["active", "core"]
        retrieval_ms = 1
        failures: list = []
        omitted: list = []

    class _Turn:
        text = body
        report = _Report()
        est_tokens = prompt_chars // 4

    class _Compiler:
        def compile(self, *a, **kw):
            return _Turn()

    return _Compiler()


@pytest.fixture
def oversized(monkeypatch):
    """5C path with a context that blows past the default ceiling."""
    monkeypatch.setattr(llm, "_configured_compiler",
                        lambda: ("5c", _fake_compiler(_BIG), True))
    llm._reset_compiler_cache()
    return monkeypatch


@pytest.fixture
def journal(monkeypatch):
    """Capture log_event calls instead of writing the operator's journal."""
    seen: list = []
    monkeypatch.setattr(llm, "log_event",
                        lambda event, **fields: seen.append((event, fields)))
    return seen


def _set_key(monkeypatch, value) -> None:
    """Point Config.load at a temp config carrying ``value``."""
    import voicebridge.config as cfg_mod

    real_load = cfg_mod.Config.load

    def _load(cls, path=None):
        cfg = real_load.__func__(cls, path)
        cfg.data.setdefault("intent", {}).setdefault("local_llm", {})
        if value is None:
            cfg.data["intent"]["local_llm"].pop("max_prompt_chars", None)
        else:
            cfg.data["intent"]["local_llm"]["max_prompt_chars"] = value
        return cfg

    monkeypatch.setattr(cfg_mod.Config, "load", classmethod(_load))


def test_oversized_prompt_trimmed_and_turn_still_runs(oversized, journal):
    question = "summarise my week"
    out = llm._user_content(question, "", "", 4096, 100)

    # Ceiling honoured, whole assembled string — not just the user text.
    assert len(out) <= DEFAULT_MAX_PROMPT_CHARS
    # Trimmed, not silently kept: the journal records both sizes.
    kinds = [e for e, _ in journal]
    assert "runner.prompt.trimmed" in kinds
    fields = [f for e, f in journal if e == "runner.prompt.trimmed"][0]
    assert fields["original_chars"] > DEFAULT_MAX_PROMPT_CHARS
    assert fields["sent_chars"] == len(out)
    assert fields["limit"] == DEFAULT_MAX_PROMPT_CHARS

    # The real payload path (what is serialized for the provider) carries it.
    payload = llm._chat_payload(question, "", "qubit-brain", 100, "",
                                stream=False)
    user_msg = payload["messages"][-1]["content"]
    assert len(json.dumps(payload["messages"])) > 0
    assert len(user_msg) <= DEFAULT_MAX_PROMPT_CHARS
    # A turn runs: the request body serializes fine and is bounded.
    body = json.dumps(payload, ensure_ascii=False)
    assert len(body) < _BIG


def test_normal_prompt_is_untouched(monkeypatch, journal):
    """Under the ceiling nothing changes — not one byte."""
    monkeypatch.setattr(llm, "_configured_compiler",
                        lambda: ("4b", None, True))
    llm._reset_compiler_cache()
    # The pre-guard assembly, rebuilt independently of _user_content.
    expected = llm._compile_4b("hello there", "Recent conversation: hi",
                               "demo fact about user", 4096, 100) + "\n\n"
    expected += (
        "Reply ONLY in plain English. Do NOT reply in any other language.\n"
        "FACTS OVERRIDE HISTORY: the <about-the-user> facts above are ALWAYS true. "
        "If older conversation turns contain a different or refusing answer "
        "(e.g. \"I'm not capable\"), correct it now with the real fact and do not repeat the old mistake."
    )
    expected += "\nUser: hello there /no_think"

    out = llm._user_content("hello there", "Recent conversation: hi",
                            "demo fact about user", 4096, 100)
    assert out == expected
    assert not [e for e, _ in journal if e == "runner.prompt.trimmed"]


def test_user_text_survives_trimming(oversized, journal):
    question = "what did I decide about the demo project yesterday"
    out = llm._user_content(question, "", "", 4096, 100)
    assert len(out) <= DEFAULT_MAX_PROMPT_CHARS
    # Question + marker intact at the tail (the /no_think switch is load-bearing).
    assert out.endswith(f"\nUser: {question} /no_think")
    assert "[... context trimmed ...]" in out
    # The context head is what got dropped, and the marker is not the cut edge.
    assert "CONTEXT-MARKER" in out


def test_config_key_is_honoured(oversized, journal, monkeypatch):
    _set_key(monkeypatch, 3000)
    assert llm._max_prompt_chars() == 3000
    out = llm._user_content("short question", "", "", 4096, 100)
    assert len(out) <= 3000
    assert out.endswith("\nUser: short question /no_think")
    fields = [f for e, f in journal if e == "runner.prompt.trimmed"][0]
    assert fields["limit"] == 3000

    # Per turn, no restart: a different configured value applies immediately.
    _set_key(monkeypatch, 5000)
    assert llm._max_prompt_chars() == 5000
    out = llm._user_content("short question", "", "", 4096, 100)
    assert 3000 < len(out) <= 5000


def test_malformed_or_nonpositive_key_falls_back_to_default(oversized,
                                                            journal):
    _set_key(oversized, "not-a-number")
    assert llm._max_prompt_chars() == DEFAULT_MAX_PROMPT_CHARS
    _set_key(oversized, 0)
    assert llm._max_prompt_chars() == DEFAULT_MAX_PROMPT_CHARS


def test_default_key_present_in_defaults():
    from voicebridge.config import DEFAULTS
    assert DEFAULTS["intent"]["local_llm"]["max_prompt_chars"] == \
        DEFAULT_MAX_PROMPT_CHARS == 8000
