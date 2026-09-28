"""Tests for the Nous-Qubit-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"qubit"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``hermes-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "qubit" tag namespace.

``is_nous_qubit_non_agentic`` should only match the actual Qubit Labs
Qubit-3 / Qubit-4 chat family.
"""

from __future__ import annotations

import pytest

from qubit_cli.model_switch import (
    _HERMES_MODEL_WARNING,
    _check_qubit_model_warning,
    is_nous_qubit_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "NousResearch/Qubit-3-Llama-3.1-70B",
        "NousResearch/Qubit-3-Llama-3.1-405B",
        "qubit-3",
        "Qubit-3",
        "qubit-4",
        "qubit-4-405b",
        "qubit_4_70b",
        "openrouter/qubit3:70b",
        "openrouter/nousresearch/qubit-4-405b",
        "NousResearch/Qubit3",
        "qubit-3.1",
    ],
)
def test_matches_real_nous_qubit_chat_models(model_name: str) -> None:
    assert is_nous_qubit_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous Qubit 3/4"
    )
    assert _check_qubit_model_warning(model_name) == _HERMES_MODEL_WARNING


