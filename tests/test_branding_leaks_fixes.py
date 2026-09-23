"""Branding-leak repairs (owner Ruling 1 → Option A).

The branding-only artifacts (TS/TSX, the vendored kanban dist, the optional-MCP
manifest) have no Python call path, so these checks read the owned artifacts and
assert the product surface carries **zero** legacy brand. Upstream provenance is
attribution-only and lives in ``LICENSE`` / ``THIRD_PARTY_NOTICES.md``, not on a
rendered surface.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

DOCS = REPO / "frontend/qubit-web/src/pages/DocsPage.tsx"
ENV = REPO / "frontend/qubit-web/src/pages/EnvPage.tsx"
KANBAN = REPO / "frontend/qubit-web/public/plugins/kanban/dist/index.js"
MANIFEST = REPO / "qubit_runtime/optional-mcps/n8n/manifest.yaml"


def test_docs_page_is_qubit_branded_not_upstream():
    """DocsPage renders a Qubit-owned OpenAPI reference, not the upstream URL.

    Option A: the upstream docs URL is not a product surface. Provenance lives
    in LICENSE / THIRD_PARTY_NOTICES.md.
    """
    text = DOCS.read_text(encoding="utf-8")
    assert "https://hermes-agent.nousresearch.com" not in text, \
        "the upstream docs URL must not be rendered on a product surface"
    assert "Nous Research" not in text, \
        "upstream company name must not be rendered as product branding"
    assert 'export const UPSTREAM_DOCS_URL = ' in text, text
    assert "UPSTREAM_DOCS_LABEL" in text, text
    # the URL is rendered as a link in at least two places: the page-header
    # action and the inline attribution paragraph.
    assert text.count("href={UPSTREAM_DOCS_URL}") >= 2, text
    assert text.count("{UPSTREAM_DOCS_LABEL}") >= 2, text
    assert "title={UPSTREAM_DOCS_LABEL}" in text, text
    assert "aria-label={UPSTREAM_DOCS_LABEL}" in text, text
    # the superseded iframe wrapper must not come back
    assert "src={UPSTREAM_DOCS_URL}" not in text, text


def test_kanban_docs_link_is_qubit_branded():
    text = KANBAN.read_text(encoding="utf-8")
    assert "hermes-agent.nousresearch.com" not in text, \
        "the upstream kanban docs URL must not be rendered on a product surface"
    assert "Nous Research" not in text, text
    assert "DOCS_UPSTREAM_LABEL" in text, text
    assert '"aria-label": DOCS_UPSTREAM_LABEL' in text, text


def test_n8n_manifest_stays_byte_faithful_and_serves_qubit_branded():
    """D3 is fixed at the served boundary, not by editing the vendored tree.

    The vendored optional-MCP catalog is the provenance record: it keeps its
    genuine third-party source URL and stays byte-identical to the pinned
    upstream fork. The normal dashboard renders upstream product names as
    Qubit through the serve-layer rebrand
    (``voicebridge.qubit_plugins._rebrand_product``), so the vendored file is
    never rewritten to hide origin.
    """
    import re
    from voicebridge.qubit_plugins import _rebrand_product

    text = MANIFEST.read_text(encoding="utf-8")
    # genuine third-party provenance URL retained, never hidden or falsified
    assert "https://github.com/CyberSamuraiX/hermes-n8n-mcp" in text, text
    # the served boundary renders upstream product names as Qubit
    served = _rebrand_product(
        "Manage n8n workflows from Hermes.\n"
        "Start a new Hermes session to load the n8n tools.")
    assert "from Qubit" in served, served
    assert not re.search(r"\b(Hermes|OpenClaw)\b", served), served


def test_env_page_qwen_prefix_is_labeled_legacy():
    text = ENV.read_text(encoding="utf-8")
    assert 'prefix: "QWEN_"' in text, text
    line = next(l for l in text.splitlines() if "HERMES_QWEN_" in l)
    preceding = text[: text.index(line)].splitlines()[-3:]
    joined = " ".join(preceding).lower()
    assert "legacy" in joined and ("wire" in joined or "upstream" in joined), \
        preceding


def test_kanban_plugin_surface_resolves_through_production():
    """The rebranded kanban docs link belongs to the vendored dashboard plugin;
    prove that plugin surface still resolves through the real backend."""
    from voicebridge import qubit_plugins as HP

    surface = HP.dashboard_surface("kanban")
    assert surface is not None and surface.is_file(), surface
