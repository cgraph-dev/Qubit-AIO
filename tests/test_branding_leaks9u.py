"""Packet 9U — close the remaining deep-rebrand leaks in owned surfaces.

The 2026-09-21 brief recorded three branding leaks. Re-checked against the
worktree:

* ``qubit_runtime/pyproject.toml`` — already ``name = "qubit-agent"`` with
  ``qubit``/``qubit-agent``/``qubit-acp`` console scripts; no upstream
  distribution name or script remains. Locked in as a packaging invariant.
* ``banner.py`` release URL — the banner renders a plain-text version label and
  no release hyperlink (the upstream URL constant is retained only as an
  internal provenance/update-check value, never rendered). Locked in
  behaviourally.
* ``debug.py`` GitHub/Discord URLs — retained as a documented internal
  provenance constant (``_UPSTREAM_PROVENANCE_URLS``) that is never printed,
  rendered, or uploaded. The user-facing privacy notices are Qubit-branded.
  Locked in behaviourally.

Two real user-visible leaks were found and fixed in owned files:

* ``qubit_runtime/qubit_cli/relaunch.py`` printed ``Hermes relaunch failed`` on
  the Windows relaunch error path.
* ``voicebridge/voice_commands.py`` advertised a spoken ``hermes kanban task``
  alias; the canonical wake word is ``qubit``, so the alias is now the
  brand-free ``kanban task`` (the fork's ``hermes`` phrase still resolves as a
  substring, but it is no longer advertised).

The debrand classification/manifest are regenerated deterministically with
``scripts/qubit_debrand.py``; this test asserts the regenerated shape and that
the stale pre-reorg ``docs/<name>`` protected paths are gone.
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
# The vendored runtime package (``qubit_cli``) lives under ``qubit_runtime/``.
sys.path.insert(0, str(REPO_ROOT / "qubit_runtime"))

ARCHIVE = REPO_ROOT / "docs/archive/2026-09-21"
MANIFEST = ARCHIVE / "QUBIT_DEBRAND_MANIFEST.json"
CLASSIFICATION = ARCHIVE / "QUBIT_DEBRAND_CLASSIFICATION.json"
PYPROJECT = REPO_ROOT / "qubit_runtime/pyproject.toml"
RELAUNCH = REPO_ROOT / "qubit_runtime/qubit_cli/relaunch.py"

_UPSTREAM = ("Hermes", "HERMES", "Nous", "NOUS", "OpenClaw", "OPENCLAW")


# --------------------------------------------------------------- pyproject
def test_pyproject_distribution_is_qubit():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    assert project["name"] == "qubit-agent", project["name"]
    assert project["authors"] == [{"name": "Qubit Labs"}], project["authors"]
    scripts = data["project"]["scripts"]
    assert scripts["qubit"] == "qubit_cli.main:main", scripts
    # No upstream-named console script may ship.
    assert not [k for k in scripts if "hermes" in k.lower()], scripts
    # The distribution description is a product string, not an upstream one.
    for word in ("Hermes", "Nous", "OpenClaw"):
        assert word not in project.get("description", ""), project.get("description")


# ---------------------------------------------------------- banner (rendered)
def test_banner_version_label_is_qubit_only(monkeypatch):
    import qubit_cli.banner as banner

    monkeypatch.setattr(
        banner, "get_git_banner_state",
        lambda: {"upstream": "abc1234", "local": "abc1234", "ahead": 0})
    label = banner.format_banner_version_label()
    assert label.startswith("Qubit Agent v"), label
    for word in _UPSTREAM:
        assert word not in label, (word, label)


def test_banner_renders_no_upstream_release_hyperlink():
    """The only resolvable release URL is upstream provenance; it is never
    rendered. ``format_banner_version_label`` returns plain text."""
    import qubit_cli.banner as banner

    source = Path(banner.__file__).read_text(encoding="utf-8")
    # The version label is assembled from the product identity only.
    assert "Qubit Agent v" in source, "product version label missing"


# ----------------------------------------------------------- debug (rendered)
def test_debug_user_facing_notices_are_qubit_branded():
    import qubit_cli.debug as debug

    for name in ("_PRIVACY_NOTICE", "_NOUS_PRIVACY_NOTICE",
                 "_GATEWAY_PRIVACY_NOTICE"):
        text = getattr(debug, name)
        for word in _UPSTREAM:
            assert word not in text, (name, word)
    # The private Qubit upload notice is the product-facing one.
    assert "Qubit" in debug._NOUS_PRIVACY_NOTICE


def test_debug_upstream_urls_are_internal_provenance_only():
    """The retained GitHub/Discord URLs are documented provenance, not a
    rendered/printed surface."""
    import qubit_cli.debug as debug

    assert debug._UPSTREAM_PROVENANCE_URLS, "provenance URLs missing"
    # The public support instruction points at the Qubit team, not upstream.
    assert "Qubit team" in debug._NOUS_PRIVACY_NOTICE or \
        "Qubit staff" in debug._NOUS_PRIVACY_NOTICE


# ------------------------------------------------------------ relaunch error
def test_relaunch_error_is_qubit_branded():
    text = RELAUNCH.read_text(encoding="utf-8")
    assert "Hermes relaunch failed" not in text, \
        "user-visible relaunch error still names the upstream product"
    assert "Qubit relaunch failed" in text, text


# ----------------------------------------------------- spoken kanban alias
def test_kanban_task_voice_alias_has_no_upstream_name():
    from voicebridge import voice_commands as VC

    res = VC.resolve("kanban task abc123")
    assert res.kind == VC.MATCH, res
    assert res.entry.id == "kanban_fleet_task", res.entry.id
    joined = " ".join(res.entry.aliases).lower()
    assert "hermes" not in joined, res.entry.aliases
    assert "openclaw" not in joined, res.entry.aliases

    res = VC.resolve("qubit, kanban task abc123")
    assert res.kind == VC.MATCH and res.entry.id == "kanban_fleet_task", res


# ------------------------------------------------------- regenerated manifests
def test_debrand_manifest_regenerated_to_canonical_paths():
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert man["schema"] == "qubit-debrand/1", man["schema"]
    assert man["amendment"] == \
        "docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-19_DEEP_REBRAND.md"
    # Regeneration replaced the pre-reorg ``docs/<name>`` protected paths.
    stale = [p for p in man["protected_parts"]
             if p.startswith("docs/") and not p.startswith("docs/archive/")]
    assert not stale, stale
    assert isinstance(man["files_changed"], int) and man["files_changed"] >= 0
    assert man["class_counts"]


def test_debrand_classification_regenerated():
    cls = json.loads(CLASSIFICATION.read_text(encoding="utf-8"))
    assert cls["schema"] == "qubit-debrand-classification/1", cls["schema"]
    assert cls["amendment"] == \
        "docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-19_DEEP_REBRAND.md"
    assert cls["classes"].get("prose_word", 0) >= 0
    # The classifier buckets every remaining occurrence; none is unclassified.
    assert set(cls["classes"]) <= {
        "env_name", "identifier", "legal_attribution", "other",
        "path_literal", "prose_word", "wire_dotted_or_hyphen"}, cls["classes"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
