"""Unit tests for the extended router: local PC actions, Hermes delegation,
and the PersonaPlex duplex client URL/query building."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.intent.classifier import Classifier, Intent  # noqa: E402
from voicebridge.intent.state import ConfirmationState, ConversationState  # noqa: E402
from voicebridge.local import actions as local_actions  # noqa: E402


def _cls(**overrides) -> Classifier:
    cfg = {
        "llm_classifier": False,  # deterministic tests: no LLM round-trip
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "qwen3-vl:8b",
        "classify_timeout_s": 1.0,
    }
    cfg.update(overrides)
    return Classifier(cfg, ConfirmationState(), ConversationState(max_turns=4))


def test_local_volume_up():
    i = _cls().classify("turn the volume up please")
    assert i.kind == "local" and i.backend == "local"
    assert i.params.get("action") == "volume_up"


def test_local_mute_mic():
    i = _cls().classify("mute the microphone")
    assert i.kind == "local" and i.params.get("action") == "mic_mute"


def test_local_open_app():
    i = _cls().classify("open the terminal")
    assert i.kind == "local" and i.params.get("action") == "open_app"
    assert i.params.get("action_params", {}).get("app") == "terminal"


def test_local_screenshot():
    i = _cls().classify("take a screenshot")
    assert i.kind == "local" and i.params.get("action") == "screenshot"


def test_hermes_delegation_prefix():
    i = _cls().classify("ask hermes to review the pr")
    assert i.kind == "hermes" and i.backend == "hermes"
    assert "review the pr" in i.task_text


def test_hermes_delegation_direct():
    i = _cls().classify("hermes, check the cron logs")
    assert i.kind == "hermes"
    assert "cron" in i.task_text


def test_coding_still_routes_to_openclaw():
    i = _cls().classify("open the current project and inspect the stripe webhook validation")
    assert i.kind == "coding" and i.backend == "openclaw"


def test_conversation_untouched():
    i = _cls().classify("what do you think about this marketplace idea")
    assert i.kind in ("conversation", "clarify")


def test_local_action_match_and_execute_unknown():
    assert local_actions.match("raise the volume") is not None
    assert local_actions.match("sing me a song") is None


def test_duplex_client_query_build():
    from voicebridge.personaplex.client import DuplexClient
    c = DuplexClient(url="wss://personaplex:8998/api/chat",
                     voice_prompt="NATM1.pt", text_prompt="hello")
    q = c._query()
    assert "voice_prompt=NATM1.pt" in q
    assert "text_prompt=hello" in q
    assert "seed=-1" in q
    assert q.startswith("wss://personaplex:8998/api/chat?")
