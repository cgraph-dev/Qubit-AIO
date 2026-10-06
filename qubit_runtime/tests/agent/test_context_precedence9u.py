"""Context-length precedence for Qubit-local models.

Regression for the reported reading ``get_model_context_length("ollama","qubit-brain4")``
returning 131072. The custom-endpoint fallback at
``agent/model_metadata.py::_resolve_custom_endpoint_context_length`` ends with
``_longest_key_match(DEFAULT_CONTEXT_LENGTHS, model.lower())``; because
``_catalog_key_matches`` was a bare substring test, the provider/runtime token
``ollama`` matched the ``llama`` family key (``"llama" in "ollama"``) and resolved
to the llama family default (131,072) instead of leaving the real registration
(``qubit-brain4`` -> 4,096, its Modelfile ``num_ctx``) authoritative.

The fix requires a catalog key to match at a token start, so a provider token can
never shadow a registered model. These tests pin both the fixed collision and the
kept behavior for real model slugs.
"""

import pytest

from agent.model_metadata import (
    DEFAULT_CONTEXT_LENGTHS,
    DEFAULT_FALLBACK_CONTEXT,
    _longest_key_match,
    get_model_context_length,
)


def test_registered_local_model_value_is_authoritative():
    """``qubit-brain4`` resolves to its Modelfile ``num_ctx`` (4,096), not a family default."""
    assert _longest_key_match(DEFAULT_CONTEXT_LENGTHS, "qubit-brain4") == ("qubit-brain4", 4096)
    assert get_model_context_length("qubit-brain4", base_url="", provider="ollama") == 4096
    assert get_model_context_length("qubit-brain4", base_url="", provider="ollama") != DEFAULT_FALLBACK_CONTEXT


def test_provider_token_does_not_claim_the_llama_family_default():
    """``ollama`` contains ``llama``; that mid-word match must never yield 131,072."""
    assert _longest_key_match(DEFAULT_CONTEXT_LENGTHS, "ollama") is None
    assert get_model_context_length("ollama", "qubit-brain4") != DEFAULT_CONTEXT_LENGTHS["llama"]
    assert get_model_context_length("ollama", "qubit-brain4") != 131072


@pytest.mark.parametrize(
    "model",
    ["llama", "llama-3.1-70b", "meta-llama/Llama-3.1-8B", "ollama/llama3"],
)
def test_real_llama_slugs_still_resolve(model):
    """A key separated from its prefix (or starting the id) keeps matching."""
    assert _longest_key_match(DEFAULT_CONTEXT_LENGTHS, model.lower()) == ("llama", 131072)


def test_generation_glued_keys_still_match():
    """A trailing digit is not a token boundary, so ``qwen3``/``gemma4`` keep matching."""
    assert _longest_key_match(DEFAULT_CONTEXT_LENGTHS, "qwen3-coder")[1] == 262144
    assert _longest_key_match(DEFAULT_CONTEXT_LENGTHS, "gemma4") == ("gemma4", 256000)
