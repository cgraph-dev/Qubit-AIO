"""Impl-4 branding-leak repairs.

The branding-only artifacts (TS/TSX, the vendored kanban dist, the optional-MCP
manifest) have no Python call path, so these checks read the owned artifacts and
assert the required Qubit/upstream labels while proving the genuine provenance
URLs are preserved (origin is never hidden).
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

DOCS = REPO / "frontend/hermes-web/src/pages/DocsPage.tsx"
ENV = REPO / "frontend/hermes-web/src/pages/EnvPage.tsx"
KANBAN = REPO / "frontend/hermes-web/public/plugins/kanban/dist/index.js"
MANIFEST = REPO / "qubit_hermes/optional-mcps/n8n/manifest.yaml"


def test_docs_page_labels_upstream_and_keeps_provenance_url():
    text = DOCS.read_text(encoding="utf-8")
    assert "https://hermes-agent.nousresearch.com/docs/" in text, \
        "the real upstream docs URL must be preserved for provenance"
    assert "UPSTREAM_DOCS_LABEL" in text, text
    assert "Upstream vendor documentation (Nous Research)" in text, text
    assert "src={UPSTREAM_DOCS_URL}" in text, text
    assert "title={UPSTREAM_DOCS_LABEL}" in text, text


def test_kanban_docs_link_labels_upstream():
    text = KANBAN.read_text(encoding="utf-8")
    assert ("https://hermes-agent.nousresearch.com/docs/user-guide/features/"
            "kanban") in text, "the real kanban docs URL must be preserved"
    assert "DOCS_UPSTREAM_LABEL" in text, text
    assert "Upstream vendor Kanban docs (Nous Research)" in text, text
    assert '"aria-label": DOCS_UPSTREAM_LABEL' in text, text


def test_n8n_manifest_stays_byte_faithful_and_serves_qubit_branded():
    """D3 is fixed at the served boundary, not by editing the vendored tree.

    The vendored optional-MCP catalog is the provenance record: it keeps its
    genuine third-party source URL and stays byte-identical to the pinned
    upstream fork. The normal dashboard renders upstream product names as
    Qubit through the serve-layer rebrand
    (``voicebridge.hermes_plugins._rebrand_product``), so the vendored file is
    never rewritten to hide origin.
    """
    import re
    from voicebridge.hermes_plugins import _rebrand_product

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
    from voicebridge import hermes_plugins as HP

    surface = HP.dashboard_surface("kanban")
    assert surface is not None and surface.is_file(), surface
