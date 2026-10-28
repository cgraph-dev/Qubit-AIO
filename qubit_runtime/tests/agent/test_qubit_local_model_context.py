"""Qubit-local Ollama models resolve their real context window instead of the
256K fallback, even with no ``base_url``.

The voice bridge / ``/qubit-chat`` / native-voice paths build the vendored agent
without an endpoint URL, so the Ollama ``/api/show`` probes never run. Before the
catalog entries, every local model fell through to ``DEFAULT_FALLBACK_CONTEXT``
(256,000) and logged "Could not determine context length".

The registered values mirror what the runtime's own Ollama ``/api/show`` resolves
live (``ollama show``): ``qubit-spark17`` / ``qubit-spark4`` advertise 1,048,576;
``qubit-brain`` serves a 65,536-token Modelfile window (GGUF max 262,144), above
the 64K tool-use floor. These are pure table lookups — no network.
"""

import logging

import pytest

from agent.model_metadata import (
    DEFAULT_FALLBACK_CONTEXT,
    get_model_context_length,
)

EXPECTED_CONTEXT = {
    "qubit-spark17": 1_048_576,
    "qubit-spark4": 1_048_576,
    "qubit-brain": 65536,
}


@pytest.fixture(autouse=True)
def _clear_fallback_warning_dedup():
    """``_FALLBACK_WARNED`` is a process-global; a warning recorded by an earlier
    test would hide this module's warning. Clear it so the caplog assertion below
    is meaningful in both directions."""
    import agent.model_metadata as _mm

    _mm._FALLBACK_WARNED.clear()
    yield
    _mm._FALLBACK_WARNED.clear()


@pytest.mark.parametrize("model,expected", EXPECTED_CONTEXT.items())
def test_local_model_resolves_context_without_base_url(model, expected):
    resolved = get_model_context_length(model, base_url="", provider="ollama")
    assert resolved == expected
    assert resolved != DEFAULT_FALLBACK_CONTEXT


def test_provider_prefixed_and_tagged_local_models_resolve():
    """``local:qubit-brain`` and the ``:latest`` tag are the same model."""
    assert get_model_context_length("local:qubit-brain", base_url="", provider="ollama") == 65536
    assert get_model_context_length("qubit-brain:latest", base_url="", provider="ollama") == 65536
    assert get_model_context_length("qubit-spark17:latest", base_url="", provider="ollama") == 1_048_576


def test_no_fallback_warning_for_local_models(caplog):
    with caplog.at_level(logging.WARNING, logger="agent.model_metadata"):
        for model in EXPECTED_CONTEXT:
            get_model_context_length(model, base_url="", provider="ollama")
    assert "Could not determine context length" not in caplog.text
