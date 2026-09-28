"""Outbound brand-header de-brand tests (wave 24).

Qubit's vendored runtime mints attribution headers for third-party providers,
platforms and optional skills. Those outbound, user-facing wire values must not
carry the legacy brand:

* ``User-Agent: HermesAgent/<version>`` -> ``User-Agent: QubitAgent/<version>``
* ``HTTP-Referer: https://hermes-agent.nousresearch.com`` ->
  ``https://github.com/cgraph-dev/qubit-voicebridge`` (the canonical Qubit source)

Deliberately NOT covered (preserved by rule):

* external model IDs (e.g. ``NousResearch/Hermes-4-70B``);
* real third-party/operator service endpoints (managed-bot setup host, website
  build scripts, legal/provenance comments);
* wire identifiers such as the OpenAI Codex ``originator``.

The NVIDIA NIM ``X-BILLING-INVOKE-ORIGIN`` attribution value is no longer
preserved: it is de-branded to ``QubitAgent`` (wave 24 tail).

This test scans source text only; it performs no imports and makes no network
calls.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

MODEL_PROVIDER_DIR = REPO_ROOT / "qubit_runtime" / "plugins" / "model-providers"

# Client files that mint outbound provider / platform / skill request headers.
ATTRIBUTION_CLIENT_FILES = (
    "qubit_runtime/agent/anthropic_adapter.py",
    "qubit_runtime/agent/auxiliary_client.py",
    "qubit_runtime/agent/client_lifecycle.py",
    "qubit_runtime/agent/codex_headers.py",
    "qubit_runtime/qubit_cli/models.py",
    "qubit_runtime/qubit_cli/copilot_auth.py",
    "qubit_runtime/plugins/platforms/slack/adapter.py",
    "qubit_runtime/plugins/platforms/wecom/adapter.py",
    "qubit_runtime/plugins/platforms/wecom/media.py",
    "qubit_runtime/plugins/platforms/feishu/adapter.py",
    "qubit_runtime/plugins/platforms/teams/adapter.py",
    "qubit_runtime/plugins/cron_providers/chronos/verify.py",
    "qubit_runtime/plugins/dashboard_auth/_shared.py",
    "qubit_runtime/skills/research/arxiv/scripts/search_arxiv.py",
    "qubit_runtime/skills/productivity/maps/scripts/maps_client.py",
    "qubit_runtime/optional-skills/blockchain/solana/scripts/solana_client.py",
    "qubit_runtime/optional-skills/blockchain/hyperliquid/scripts/hyperliquid_client.py",
    "qubit_runtime/optional-skills/finance/stocks/scripts/stocks_client.py",
)

LEGACY_USER_AGENT_TOKEN = "HermesAgent/"
LEGACY_REFERER_HOST = "hermes-agent.nousresearch.com"
CANONICAL_REFERER = "https://github.com/cgraph-dev/qubit-voicebridge"

# A dict/JSON ``HTTP-Referer`` entry whose value is the legacy upstream host.
LEGACY_REFERER_PATTERN = re.compile(
    r"""["']HTTP-Referer["']\s*:\s*["']https?://hermes-agent\.nousresearch\.com"""
)
# Any HTTP-Referer entry (used to prove the canonical source is in place).
HTTP_REFERER_PATTERN = re.compile(r"""["']HTTP-Referer["']\s*:\s*["']([^"']+)["']""")


def _scanned_files() -> list[Path]:
    files = list(MODEL_PROVIDER_DIR.rglob("*.py"))
    files.extend(REPO_ROOT / rel for rel in ATTRIBUTION_CLIENT_FILES)
    return sorted(set(files))


SCANNED_FILES = _scanned_files()
FILE_CASES = [pytest.param(p, id=str(p.relative_to(REPO_ROOT))) for p in SCANNED_FILES]


def test_scan_scope_is_non_empty() -> None:
    assert MODEL_PROVIDER_DIR.is_dir()
    # The model-provider attribution set plus the listed clients.
    assert len(SCANNED_FILES) >= len(ATTRIBUTION_CLIENT_FILES) + 5
    assert all(p.is_file() for p in SCANNED_FILES)


@pytest.mark.parametrize("path", FILE_CASES)
def test_no_legacy_user_agent_token(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert LEGACY_USER_AGENT_TOKEN not in text, (
        f"{path.relative_to(REPO_ROOT)} still sends a legacy User-Agent "
        f"{LEGACY_USER_AGENT_TOKEN!r}; use QubitAgent/<version>"
    )


@pytest.mark.parametrize("path", FILE_CASES)
def test_no_legacy_http_referer(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    match = LEGACY_REFERER_PATTERN.search(text)
    assert match is None, (
        f"{path.relative_to(REPO_ROOT)} still sends HTTP-Referer to the legacy "
        f"host {LEGACY_REFERER_HOST!r}; use {CANONICAL_REFERER!r}"
    )


@pytest.mark.parametrize("path", FILE_CASES)
def test_http_referers_use_canonical_qubit_source(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    values = HTTP_REFERER_PATTERN.findall(text)
    if not values:
        pytest.skip("no HTTP-Referer header in this file")
    for value in values:
        assert value == CANONICAL_REFERER, (
            f"{path.relative_to(REPO_ROOT)} HTTP-Referer must be the canonical "
            f"Qubit source {CANONICAL_REFERER!r}, got {value!r}"
        )
