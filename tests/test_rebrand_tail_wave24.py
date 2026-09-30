"""De-brand tail wave 24: outbound product-identity headers.

The previous branding round left four vendored client files still minting the
legacy brand in outbound, user-facing request headers:

* ``qubit_runtime/qubit_cli/models.py``: ``User-Agent: hermes-cli/<ver>`` and
  ``X-Goog-Api-Client: hermes-agent/<ver>``;
* ``qubit_runtime/agent/auxiliary_client.py``: NVIDIA NIM cloud-billing
  ``X-BILLING-INVOKE-ORIGIN: HermesAgent``.

This wave de-brands those product-identity values to ``qubit-cli/`` /
``qubit-agent/`` / ``QubitAgent``. Preserved by rule (never flagged here):
external model IDs, real ``*.nousresearch.com`` service endpoints, ``~/.hermes``
read-old paths, wire identifiers (``X-Qubit-Session-Token``, the Slack
``hermes_*`` action ids, the OpenAI Codex ``originator``), and legal/provenance
comments.

The scan is source-text only: no imports, no network.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

OWNED_FILES = (
    "qubit_runtime/qubit_cli/models.py",
    "qubit_runtime/agent/auxiliary_client.py",
    "qubit_runtime/qubit_cli/copilot_auth.py",
    "qubit_runtime/plugins/platforms/slack/adapter.py",
)

# Legacy *product-identity header* values. Each pattern is deliberately narrow so
# it cannot match preserved wire/data surfaces:
#   * ``hermes-cli/``  -> a User-Agent product token (the toolset name
#     ``hermes-cli`` has no trailing slash and is preserved);
#   * ``hermes-agent/`` -> the X-Goog-Api-Client product token (prose
#     ``hermes-agent`` without a slash is not a header value);
#   * ``HermesAgent``   -> the NVIDIA NIM billing-origin value.
LEGACY_HEADER_PATTERNS = {
    "legacy-cli-user-agent": re.compile(r"hermes-cli/"),
    "legacy-google-api-client": re.compile(r"hermes-agent/"),
    "legacy-nvidia-billing-origin": re.compile(r"HermesAgent"),
}

FILE_CASES = [pytest.param(rel, id=rel) for rel in OWNED_FILES]


def _read(rel: str) -> str:
    path = REPO_ROOT / rel
    assert path.is_file(), f"owned file missing: {rel}"
    return path.read_text(encoding="utf-8")


def test_scan_scope_is_complete() -> None:
    for rel in OWNED_FILES:
        assert (REPO_ROOT / rel).is_file(), rel


@pytest.mark.parametrize("rel", FILE_CASES)
def test_no_legacy_product_identity_header_tokens(rel: str) -> None:
    text = _read(rel)
    hits = [name for name, pat in LEGACY_HEADER_PATTERNS.items() if pat.search(text)]
    assert not hits, (
        f"{rel} still carries legacy product-identity header token(s) {hits}; "
        "use qubit-cli/ / qubit-agent/ / QubitAgent"
    )


def test_models_user_agent_is_qubit() -> None:
    text = _read("qubit_runtime/qubit_cli/models.py")
    assert 'f"qubit-cli/{get_version_info().base_version}"' in text
    assert "hermes-cli/" not in text


def test_models_google_api_client_is_qubit() -> None:
    text = _read("qubit_runtime/qubit_cli/models.py")
    assert 'f"qubit-agent/{get_version_info().base_version}"' in text
    assert "hermes-agent/" not in text


def test_nvidia_billing_origin_is_qubit() -> None:
    text = _read("qubit_runtime/agent/auxiliary_client.py")
    assert '_NVIDIA_NIM_CLOUD_HEADERS = {"X-BILLING-INVOKE-ORIGIN": "QubitAgent"}' in text
    assert "HermesAgent" not in text


def test_owned_files_keep_qubit_attribution() -> None:
    # The already-de-branded attribution values must survive this wave.
    assert 'f"QubitAgent/{get_version_info().base_version}"' in _read(
        "qubit_runtime/agent/auxiliary_client.py"
    )
    assert "_QUBIT_SLACK_USER_AGENT_PREFIX" in _read(
        "qubit_runtime/plugins/platforms/slack/adapter.py"
    )
    assert '"QubitAgent/1.0"' in _read("qubit_runtime/qubit_cli/copilot_auth.py")


def test_preserved_surfaces_are_not_over_rewritten() -> None:
    # ``~/.hermes`` read-old paths and Slack interaction wire ids are wire/data
    # contracts, not outbound product-identity headers: they must remain.
    slack = _read("qubit_runtime/plugins/platforms/slack/adapter.py")
    assert "~/.hermes/.env" in slack
    assert "hermes_feedback" in slack
