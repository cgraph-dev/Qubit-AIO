"""Packet 8T-F rebrand scans: docs, installer, routes, completions.

Regression coverage for the surfaces the page-level rebrand tests do not
own: support documentation must never instruct direct upstream use or
claim an upstream assistant identity; installer/config defaults must not
point at operator-Hermes paths; dashboard routes and shell completions
must carry Qubit identity only. Compatibility/provenance references
(gateway interop notes, lineage detection, licenses) are allowed and
asserted elsewhere.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_readme_has_no_direct_use_or_identity_claims():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    for bad in ("Hermes CLI", "ask Hermes", "Using OpenClaw", "Using Hermes",
                "OpenClaw task", "Hermes task", "your OpenClaw agent",
                "your OpenClaw safety policy", "voice bridge for OpenClaw"):
        assert bad not in text, f"README still claims/instructs: {bad!r}"
    assert "Qubit" in text, "README must name the product"


def test_installer_and_examples_use_canonical_paths():
    for rel in ("scripts/install.sh", "config/voice.example.yaml",
                ".env.example"):
        text = (REPO / rel).read_text(encoding="utf-8")
        # Migration prose may name the legacy file; no configured path may
        # point at the operator install.
        assert not re.search(
            r"(>>\s*|key_env[\"']?\s*[:=]\s*|_env:\s*)['\"]?~/.hermes", text), \
            f"{rel}: configured operator-Hermes path"
        assert "~/.config/qubit-voicebridge/.env" in text, \
            f"{rel}: must document the canonical env file"
    defaults = (REPO / "voicebridge" / "config.py").read_text()
    assert "~/.hermes/.env" not in defaults, \
        "no default credential path may point at the operator install"


def test_dashboard_routes_carry_no_upstream_identity():
    text = (REPO / "voicebridge" / "webui.py").read_text(encoding="utf-8")
    for match in re.finditer(r"""['\"]/((?:api/)?[A-Za-z0-9/_-]+)['\"]""", text):
        route = match.group(1).lower()
        # The pinned Hermes shell mount (/hermes/) is an explicit
        # compatibility path permitted by the owner amendment 2026-09-11;
        # its internal route. No normal Qubit route may carry it otherwise.
        if route == "hermes" or route.startswith("hermes/"):
            continue
        assert "hermes" not in route and "openclaw" not in route, \
            f"upstream route identity: {match.group(0)!r}"
        assert not re.search(r"(^|/)claw($|/)", route), \
            f"upstream route identity: {match.group(0)!r}"


def test_completions_carry_qubit_identity_only():
    d = REPO / "voicebridge" / "_data" / "completions"
    assert d.is_dir()
    for path in sorted(d.iterdir()):
        text = path.read_text(encoding="utf-8")
        assert "Qubit Agent" in text, f"{path.name}: missing Qubit header"
        low = text.lower()
        assert "hermes" not in low and "openclaw" not in low, \
            f"{path.name}: upstream identity in generated completions"


def test_orchestrator_hints_use_canonical_paths():
    text = (REPO / "voicebridge" / "orchestrator.py").read_text()
    assert "set DEEPSEEK_API_KEY in ~/.config/qubit-voicebridge/.env" in text
    assert "set PERPLEXITY_API_KEY in ~/.config/qubit-voicebridge/.env" in text
