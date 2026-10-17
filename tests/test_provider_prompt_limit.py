"""Provider "prompt too long" is a typed, actionable outcome.

A chat session was killed by a provider 502 whose body read "Prompt too long
(max 6000 characters)". That number lives nowhere in this repo — it is the
PROVIDER's ceiling — yet the turn surfaced as a generic ``MODEL_ERROR``, which
says nothing about the one thing the operator can change (the trim ceiling).

These tests pin the contract:

* :func:`voicebridge.agent._is_prompt_too_long` recognises every provider
  phrasing and extracts the number, and does NOT misclassify a normal error;
* :func:`voicebridge.agent._run_vendored_session` returns
  ``code='PROMPT_TOO_LONG'`` with ``retryable=False`` when the provider rejects
  the prompt, and still returns ``MODEL_ERROR`` for an ordinary failure;
* an over-ceiling turn is prevented locally — the provider is never called.
"""
from __future__ import annotations

import sys
import types

import pytest

from voicebridge import agent as AG


# ---------------------------------------------------------------------------
# Recogniser: every phrasing, plus the ceiling number
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("message,limit,unit", [
    ("Prompt too long (max 6000 characters)", 6000, "chars"),
    ("Error code: 502 - prompt too long (max 6000 characters)", 6000, "chars"),
    ("prompt is too long: 233153 tokens > 200000", 200000, "tokens"),
    ("maximum context length is 8192 tokens", 8192, "tokens"),
    ("This model's maximum context length is 128000 tokens", 128000, "tokens"),
    ("too many tokens in request", None, "tokens"),
    ("request exceeds the context window of this model", None, "tokens"),
    ("input too long, please reduce the length", None, "chars"),
    ("input tokens max 4096", 4096, "tokens"),
])
def test_recognises_each_phrasing(message, limit, unit):
    got = AG._is_prompt_too_long(RuntimeError(message))
    assert got is not None, message
    assert got["limit"] == limit, (message, got)
    assert got["unit"] == unit, (message, got)
    assert got["detail"]


def test_recognises_case_insensitively_and_from_plain_strings():
    assert AG._is_prompt_too_long("PROMPT TOO LONG (MAX 6000 CHARACTERS)") \
        is not None
    assert AG._is_prompt_too_long(RuntimeError("CONTEXT LENGTH exceeded")) \
        is not None


@pytest.mark.parametrize("message", [
    "connection reset by peer",
    "401 Unauthorized: invalid api key",
    "rate limit exceeded, retry after 30s",
    "max_tokens too large: requested 16000 output tokens",
    "500 Internal Server Error",
    "",
    "model returned no text",
])
def test_normal_errors_are_not_misclassified(message):
    assert AG._is_prompt_too_long(RuntimeError(message)) is None


def test_recogniser_never_raises_on_odd_input():
    assert AG._is_prompt_too_long(None) is None
    assert AG._is_prompt_too_long(ValueError()) is None
    assert AG._is_prompt_too_long("") is None


# ---------------------------------------------------------------------------
# Typed outcome shape
# ---------------------------------------------------------------------------
def test_outcome_is_typed_and_not_retryable():
    out = AG._prompt_too_long_outcome(
        session="s1", limit=6000, unit="chars",
        detail="prompt too long (max 6000 characters)",
        prompt_chars=7200, ceiling_chars=8000)
    assert out["state"] == "FAILED"
    assert out["code"] == "PROMPT_TOO_LONG"
    assert out["retryable"] is False
    assert out["limits"]["limit"] == 6000
    assert out["limits"]["unit"] == "chars"
    assert out["limits"]["source"] == "provider"
    assert out["limits"]["config_key"] == "intent.local_llm.max_prompt_chars"
    assert out["session"] == "s1"


def test_outcome_names_the_local_preempt_source():
    out = AG._prompt_too_long_outcome(
        session="s", limit=None, unit="chars", detail="prevented locally: ...",
        prompt_chars=9000, ceiling_chars=8000, prevented=True)
    assert out["code"] == "PROMPT_TOO_LONG"
    assert out["limits"]["source"] == "local_preempt"
    assert "prevented locally" in out["detail"]


# ---------------------------------------------------------------------------
# The ceiling reader
# ---------------------------------------------------------------------------
class _Cfg(dict):
    """Stand-in for ``voicebridge.config.Config``.

    Supports the real access shape ``get(section, key, default=)`` where the
    section is ONE dotted string (``"intent.local_llm"``) and the key is the
    leaf, and tolerates a nested-dict fallback for readability in the tests.
    """

    def get(self, section="", key=None, default=None):
        if key is None:
            return super().get(section, default)
        node = self
        for part in str(section).split("."):
            if not isinstance(node, dict):
                return default
            node = node.get(part, default)
            if node is default:
                return default
        if isinstance(node, dict):
            return node.get(key, default)
        return default


def test_ceiling_reads_config_then_default():
    assert AG._prompt_ceiling_chars(
        _Cfg({"intent": {"local_llm": {"max_prompt_chars": 4000}}})) == 4000
    # agent.* wins when set (the more specific override)
    assert AG._prompt_ceiling_chars(
        _Cfg({"agent": {"max_prompt_chars": 2000},
              "intent": {"local_llm": {"max_prompt_chars": 4000}}})) == 2000
    # 0 disables the pre-empt
    assert AG._prompt_ceiling_chars(_Cfg({"agent": {"max_prompt_chars": 0}})) \
        == 0
    # malformed falls back to the default, never raises
    assert AG._prompt_ceiling_chars(
        _Cfg({"agent": {"max_prompt_chars": "nope"}})) > 0
    assert AG._prompt_ceiling_chars(None) > 0


# ---------------------------------------------------------------------------
# _run_vendored_session: the typed code surfaces, and a normal error does not
# ---------------------------------------------------------------------------
@pytest.fixture()
def vendored(monkeypatch):
    """Install a fake vendored runtime that raises a chosen error."""
    box: dict = {}

    def _install(error: Exception) -> dict:
        class _FakeAgent:
            def __init__(self, **kwargs):
                box["agent_kwargs"] = kwargs
                box["calls"] = box.get("calls", 0) + 1

            def run_conversation(self, text):
                raise error

            def close(self):
                box["closed"] = True

        class _Choice:
            model = "fake-model"
            provider = "fake-provider"

        class _QR:
            @staticmethod
            def use_qubit_home(cfg):
                return None

            @staticmethod
            def ensure_path():
                return None

        mods = {
            "run_agent": {"AIAgent": _FakeAgent},
            "voicebridge.qubit_runtime": {"use_qubit_home": _QR.use_qubit_home,
                                          "ensure_path": _QR.ensure_path},
            "qubit_cli": {},
            "qubit_cli.config": {"load_config": lambda: {}},
            "qubit_cli.oneshot": {
                "_resolve_model_and_provider": lambda *a, **k: _Choice()},
            "qubit_cli.runtime_provider": {
                "resolve_runtime_with_fallback":
                    lambda *a, **k: ({"provider": "fake-provider",
                                      "model": "fake-model"}, None)},
            "qubit_cli.tools_config": {
                "_get_platform_tools": lambda *a, **k: {"terminal"}},
            "agent": {},
            "agent.skill_utils": {
                "parse_config_string_list": lambda v: [],
            },
            "qubit_cli.mcp_startup": {
                "ensure_mcp_discovery_before_agent_build": lambda **k: None},
            "qubit_cli.fallback_config": {"get_fallback_chain": lambda c: None},
        }
        saved = {}
        for name, attrs in mods.items():
            saved[name] = sys.modules.get(name)
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            sys.modules[name] = mod
        box["_saved"] = saved
        return box

    yield _install

    for name, prior in box.get("_saved", {}).items():
        if prior is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prior


def _run(cfg_text: str) -> dict:
    out = AG._run_vendored_session(
        _Cfg({}), cfg_text, "test-sid", check=lambda: False, store=None,
        reasoning=None, persist_user=False)
    assert out is not None
    return out


def test_provider_prompt_too_long_surfaces_typed_code(vendored):
    vendored(RuntimeError("Error code: 502 - Prompt too long "
                          "(max 6000 characters)"))
    out = _run("hello there")
    assert out["code"] == "PROMPT_TOO_LONG", out
    assert out["state"] == "FAILED"
    assert out["retryable"] is False
    assert out["limits"]["limit"] == 6000
    assert out["limits"]["unit"] == "chars"
    assert out["limits"]["source"] == "provider"
    assert out["session"] == "test-sid"


def test_normal_provider_error_still_surfaces_model_error(vendored):
    vendored(RuntimeError("connection reset by peer"))
    out = _run("hello there")
    assert out["code"] == "MODEL_ERROR", out


def test_over_ceiling_turn_is_prevented_before_the_provider(vendored):
    box = vendored(RuntimeError("should never be called"))
    out = AG._run_vendored_session(
        _Cfg({"agent": {"enabled": True, "max_prompt_chars": 10}}),
        "x" * 500, "test-sid", check=lambda: False, store=None,
        reasoning=None, persist_user=False)
    assert out is not None
    assert out["code"] == "PROMPT_TOO_LONG", out
    assert out["limits"]["source"] == "local_preempt"
    assert "prevented locally" in out["detail"]
    assert box.get("calls", 0) == 0, "provider must not be called"
