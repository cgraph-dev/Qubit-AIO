"""Outbound product-identity de-brand tail (wave 24).

Companion to ``tests/test_rebrand_tail_wave24.py`` (which covers ``models.py``,
``auxiliary_client.py``, ``copilot_auth.py`` and the Slack adapter). This file
covers the remaining vendored client files that still minted the legacy brand in
outbound, user-facing request headers:

* ``hermes-cli/<ver>`` User-Agent -> ``qubit-cli/<ver>``
  (``auth_constants.CODEX_OAUTH_USER_AGENT``, ``model_catalog._HERMES_USER_AGENT``);
* ``hermes-agent/...`` User-Agent / ``X-Goog-Api-Client`` values ->
  ``qubit-agent/...`` (Azure detect, debug-share upload, Gemini TTS, petdex,
  and the three optional skills).

Preserved by rule (never flagged here):

* external service endpoints (``*.nousresearch.com``) and the public
  ``nousresearch.github.io/hermes-agent`` CIMD client-metadata URL, which is an
  upstream-hosted dependency the CIMD protocol fetches directly — it cannot be
  Qubit-hosted until a Qubit-hosted client-metadata URL exists (see
  ``tools/mcp_oauth._CIMD_CLIENT_METADATA_URL``);
* the upstream OAuth ``DEFAULT_NOUS_CLIENT_ID = "hermes-cli"`` wire id (no
  trailing slash, registered with the real upstream service);
* the ``hermes-cli`` toolset name, model IDs, and legal/provenance comments.

Source-text scan only: no imports, no network.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# This wave's owned files (the outbound-identity tail).
OWNED_FILES = (
    "qubit_runtime/qubit_cli/auth_constants.py",
    "qubit_runtime/qubit_cli/model_catalog.py",
    "qubit_runtime/qubit_cli/azure_detect.py",
    "qubit_runtime/qubit_cli/diagnostics_upload.py",
    "qubit_runtime/tools/tts_tool_providers.py",
    "qubit_runtime/agent/pet/store.py",
    "qubit_runtime/agent/pet/manifest.py",
    "qubit_runtime/tools/mcp_oauth.py",
    "qubit_runtime/optional-skills/research/rss-feeds/scripts/feed.py",
    "qubit_runtime/optional-skills/social-media/reddit-reading/scripts/reddit.py",
    "qubit_runtime/optional-skills/finance/polymarket/scripts/polymarket.py",
)

# The one deliberately preserved legacy token: the upstream-hosted CIMD
# client-metadata URL. ``hermes-agent/`` inside it is a path, not a product
# header value, so it is scrubbed before scanning.
ALLOWED_LEGACY_SUBSTRINGS = (
    "https://nousresearch.github.io/hermes-agent/docs/oauth/client-metadata.json",
)

# Narrow product-identity header patterns (mirror test_rebrand_tail_wave24.py).
LEGACY_PATTERNS = {
    "legacy-cli-user-agent": re.compile(r"hermes-cli/"),
    "legacy-agent-user-agent": re.compile(r"hermes-agent/"),
    "legacy-petdex-user-agent": re.compile(r"hermes-agent-petdex"),
}

FILE_CASES = [pytest.param(rel, id=rel) for rel in OWNED_FILES]


def _read(rel: str) -> str:
    path = REPO_ROOT / rel
    assert path.is_file(), f"owned file missing: {rel}"
    return path.read_text(encoding="utf-8")


def _scrub_allowed(text: str) -> str:
    for allowed in ALLOWED_LEGACY_SUBSTRINGS:
        text = text.replace(allowed, "")
    return text


def test_scan_scope_is_complete() -> None:
    for rel in OWNED_FILES:
        assert (REPO_ROOT / rel).is_file(), rel


@pytest.mark.parametrize("rel", FILE_CASES)
def test_no_legacy_outbound_identity_token(rel: str) -> None:
    text = _scrub_allowed(_read(rel))
    hits = [name for name, pat in LEGACY_PATTERNS.items() if pat.search(text)]
    assert not hits, (
        f"{rel} still carries legacy outbound product-identity token(s) {hits}; "
        "use qubit-cli/ / qubit-agent/ / qubit-agent-petdex"
    )


def test_allowed_cimd_url_is_preserved_verbatim() -> None:
    # The allowed exception must still be present and unchanged, not deleted.
    text = _read("qubit_runtime/tools/mcp_oauth.py")
    assert ALLOWED_LEGACY_SUBSTRINGS[0] in text
    assert "_CIMD_CLIENT_METADATA_URL" in text


def test_cli_user_agents_are_qubit() -> None:
    assert 'f"qubit-cli/{get_version_info().base_version}"' in _read(
        "qubit_runtime/qubit_cli/auth_constants.py"
    )
    assert 'f"qubit-cli/{get_version_info().base_version}"' in _read(
        "qubit_runtime/qubit_cli/model_catalog.py"
    )


def test_agent_user_agents_are_qubit() -> None:
    assert '"qubit-agent/azure-detect"' in _read(
        "qubit_runtime/qubit_cli/azure_detect.py"
    )
    assert '"qubit-agent/debug-share"' in _read(
        "qubit_runtime/qubit_cli/diagnostics_upload.py"
    )
    tts = _read("qubit_runtime/tools/tts_tool_providers.py")
    assert 'f"qubit-agent/{get_version_info().base_version}"' in tts
    assert '"qubit-agent/0.0.0"' in tts
    assert '"qubit-agent-petdex"' in _read("qubit_runtime/agent/pet/store.py")
    assert '"qubit-agent-petdex"' in _read("qubit_runtime/agent/pet/manifest.py")


def test_optional_skills_user_agents_are_qubit() -> None:
    assert '"qubit-agent/1.0 (rss-feeds skill;' in _read(
        "qubit_runtime/optional-skills/research/rss-feeds/scripts/feed.py"
    )
    assert '"qubit-agent/1.0 (reddit-reading skill;' in _read(
        "qubit_runtime/optional-skills/social-media/reddit-reading/scripts/reddit.py"
    )
    assert '"qubit-agent/1.0"' in _read(
        "qubit_runtime/optional-skills/finance/polymarket/scripts/polymarket.py"
    )


def test_preserved_upstream_client_id_wire_value() -> None:
    # OAuth client id registered with the upstream service: wire contract, kept.
    assert 'DEFAULT_NOUS_CLIENT_ID = "hermes-cli"' in _read(
        "qubit_runtime/qubit_cli/auth_constants.py"
    )
