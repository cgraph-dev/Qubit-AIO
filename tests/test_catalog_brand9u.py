"""Catalog brand parity — no legacy vendor name in a rendered catalog label.

Product-surface brand defect (audit F1/F5, SP-13): the served catalogs rendered
legacy vendor names on ``/plugins`` and ``/mcp``:

* ``/agent/api/dashboard/plugins/catalog`` exposed ``NousResearch`` in the
  curated pinned index (maintainer + provenance) and a raw
  ``https://github.com/NousResearch/hermes-media-studio`` link text;
* local plugin descriptions authored by the vendored manifests carried
  ``nous-account-service`` and ``~/.hermes/auth.json``;
* ``/agent/api/mcp/catalog`` presented ``github.com/.../hermes-n8n-mcp.git`` as a
  visible install source and ``~/.hermes/config.yaml`` in setup prose.

This test proves the *rendered* product surface is clean while genuine
third-party provenance is preserved:

* the curated pinned index (Qubit-owned data) renders no legacy token in any
  visible label, yet keeps the real third-party ``name``/``repo``/``homepage``
  verbatim (never a fabricated Qubit URL);
* the local plugin catalog's visible prose (label/description/source), after the
  serve-boundary overlay sanitizer, contains no legacy token — identifiers and
  ``HERMES_*`` wire env names are preserved, not brand copy;
* the MCP catalog's visible prose is clean after the overlay sanitizer, while
  the genuine ``source``/``install_url`` repository URLs stay verbatim;
* the sanitizer never fabricates a Qubit URL from a real third-party URL.

REBASE DEBT (vendored/other-owned serving paths that still emit raw strings and
must route through these overlay sanitizers at the next 1:1 rebase):

* ``voicebridge/qubit_compat.h_mcp_catalog_live`` builds MCP rows from the
  vendored ``qubit_cli.mcp_catalog`` / ``_catalog_entry_json`` and does NOT call
  ``voicebridge.mcp_servers.sanitize_catalog_entry``; the MCP page therefore also
  applies the equivalent de-brand at the React render boundary
  (``frontend/qubit-web/src/pages/McpPage.tsx::brandText``).
* ``voicebridge/plugin_catalog.local_catalog`` builds local rows from the
  vendored plugin manifests and does NOT pass prose through
  ``voicebridge.qubit_plugins.rebrand_catalog_text``; the Plugins page applies the
  equivalent de-brand at the render boundary
  (``frontend/qubit-web/src/pages/PluginsPage.tsx::brandText``).

The vendored manifests themselves (``qubit_runtime/plugin-catalog/**``,
``qubit_runtime/optional-mcps/**``, ``qubit_runtime/plugins/**``) are the
byte-faithful provenance record and are intentionally NOT edited.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

#: Legacy product tokens that must never reach a visible catalog label. ``hermes-``
#: deliberately does not match the ``HERMES_*`` wire env identifiers (underscore),
#: which are preserved configuration keys, not brand copy.
LEGACY_TOKEN = re.compile(r"NousResearch|NousPortal|nousresearch|hermes-",
                          re.IGNORECASE)

#: Visible prose fields the dashboard renders for a local plugin row.
_PLUGIN_VISIBLE = ("label", "description", "source")
#: Visible prose fields for a curated pinned-index row.
_PINNED_VISIBLE = ("label", "description", "maintainer", "tier", "category")
#: Visible prose fields for an MCP catalog row.
_MCP_VISIBLE = ("name", "description", "post_install")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _render(text) -> str:
    """The backend serve-boundary sanitizer the frontend ``brandText`` mirrors."""
    from voicebridge import qubit_plugins as QP

    return QP.rebrand_catalog_text(text)


def _plugin_catalog(cfg):
    from voicebridge import qubit_compat as HC

    code, out = HC.dispatch(cfg, "GET", "/api/dashboard/plugins/catalog", {}, {})
    assert code == 200 and out["ok"] is True, out
    return out


def _mcp_catalog(cfg):
    from voicebridge import qubit_compat as HC

    code, out = HC.dispatch(cfg, "GET", "/api/mcp/catalog", {}, {})
    assert code == 200, out
    return out


def test_pinned_index_visible_labels_are_debranded(tmp_path, monkeypatch):
    """No curated pinned-index row renders a legacy vendor token."""
    out = _plugin_catalog(_cfg(tmp_path, monkeypatch))
    entries = out["pinned_index"]["entries"]
    assert entries, "pinned index must not be empty"
    for entry in entries:
        for field in _PINNED_VISIBLE:
            value = str(entry.get(field) or "")
            assert not LEGACY_TOKEN.search(value), (entry.get("name"), field, value)
        for key, value in (entry.get("provenance") or {}).items():
            assert not LEGACY_TOKEN.search(str(value)), (
                entry.get("name"), "provenance." + str(key), value)


def test_pinned_index_keeps_real_third_party_source(tmp_path, monkeypatch):
    """Genuine provenance is preserved verbatim — no fabricated Qubit URL."""
    out = _plugin_catalog(_cfg(tmp_path, monkeypatch))
    for entry in out["pinned_index"]["entries"]:
        repo = str(entry["repo"])
        assert repo.startswith("https://"), repo
        assert "github.com/" in repo, repo
        # The upstream identifier and source URL are the real record, untouched.
        assert "hermes-" in entry["name"] or entry["name"].startswith("plugin-"), entry
        assert "qubit" not in repo.lower(), repo
        # The real source is reachable from the row (repo or its pinned subdir).
        assert (entry["name"] in repo
                or entry["name"] in str(entry.get("homepage") or "")), (
            entry["name"], repo, entry.get("homepage"))


def test_local_plugin_visible_prose_is_debranded(tmp_path, monkeypatch):
    """Rendered local-plugin labels/descriptions carry no legacy vendor token.

    The raw rows come from the vendored plugin manifests; the overlay sanitizer
    (what the page renders) must neutralize ``nous-account-service`` and the
    legacy ``~/.hermes`` home path. ``HERMES_*`` wire env names and ids are
    identifiers and are explicitly out of scope.
    """
    out = _plugin_catalog(_cfg(tmp_path, monkeypatch))
    entries = out["entries"]
    assert entries, "catalog must not be empty"
    for entry in entries:
        for field in _PLUGIN_VISIBLE:
            rendered = _render(entry.get(field) or "")
            assert not LEGACY_TOKEN.search(rendered), (
                entry.get("id"), field, rendered)


def test_local_plugin_wire_identifiers_are_preserved(tmp_path, monkeypatch):
    """De-branding is copy-only: ids and ``HERMES_*`` env keys stay real."""
    out = _plugin_catalog(_cfg(tmp_path, monkeypatch))
    by_id = {e["id"]: e for e in out["entries"]}
    assert "dashboard_auth/drain" in by_id
    drain = by_id["dashboard_auth/drain"]
    assert "HERMES_DASHBOARD_DRAIN_SECRET" in drain["provides"]["requires_env"]
    # The rendered description no longer names the legacy account service.
    rendered = _render(drain["description"])
    assert "nous-account-service" not in rendered.lower()
    assert "Qubit account service" in rendered


def test_mcp_catalog_visible_prose_is_debranded(tmp_path, monkeypatch):
    """The overlay MCP sanitizer cleans prose without touching real URLs."""
    from voicebridge import mcp_servers as MS

    out = _mcp_catalog(_cfg(tmp_path, monkeypatch))
    entries = out["entries"]
    assert entries, "MCP catalog must not be empty"
    for entry in entries:
        clean = MS.sanitize_catalog_entry(entry)
        for field in _MCP_VISIBLE:
            rendered = _render(clean.get(field) or "")
            assert not LEGACY_TOKEN.search(rendered), (
                entry.get("name"), field, rendered)
        # Genuine third-party source/install URLs are retained verbatim.
        for url_field in ("source", "install_url"):
            original = entry.get(url_field)
            if isinstance(original, str) and original.startswith("https://"):
                assert clean.get(url_field) == original, (entry.get("name"),
                                                          url_field)


def test_sanitizer_handles_account_service_home_and_spaced_portal():
    """The exact leaking strings are neutralized; wire names are untouched."""
    assert _render("nous-account-service provisions a token") == (
        "Qubit account service provisions a token")
    assert _render("auth at ~/.hermes/auth.json") == (
        "auth at ~/.config/qubit-voicebridge/auth.json")
    assert _render("See Nous Portal and NousResearch") == "See Qubit and Qubit"
    # ``HERMES_*`` wire env identifiers are preserved, not rewritten.
    assert _render("HERMES_DASHBOARD_DRAIN_SECRET") == (
        "HERMES_DASHBOARD_DRAIN_SECRET")


def test_sanitizer_never_fabricates_a_qubit_url():
    """A real third-party source URL is returned verbatim, never rewritten."""
    from voicebridge.qubit_plugins import rebrand_catalog_text as rebrand

    for url in (
        "https://github.com/NousResearch/hermes-media-studio",
        "https://github.com/CyberSamuraiX/hermes-n8n-mcp.git",
        "https://github.com/anpicasso/hermes-plugin-chrome-profiles",
    ):
        assert rebrand(url) == url
    # The dead upstream *portal* host (not a third-party source) is neutralized.
    assert "nousresearch.com" not in rebrand(
        "https://hermes-agent.nousresearch.com/docs/api/plugin-catalog.json")
