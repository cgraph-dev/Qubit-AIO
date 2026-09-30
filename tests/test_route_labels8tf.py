"""Packet 8T-F — Qubit route labels in user-facing surfaces.

Internal route/backend tokens (openclaw/hermes/local/...) stay raw in API
payloads, receipts, journals, and audit records (provenance + compat), but
every USER-FACING surface renders Qubit labels: dashboard activity/index
(vbRouteLabel), generated reports (generate_report mapping), and report
templates (Qubit placeholders).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "gen_report", REPO_ROOT / "scripts" / "generate_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generator_maps_legacy_tokens():
    mod = _load_generator()
    assert mod._qubit_label("hermes") == "Qubit agent"
    assert mod._qubit_label("openclaw") == "Qubit gateway"
    assert mod._qubit_label("local-script") == "Qubit local task"
    assert mod._qubit_label("Qubit agent") == "Qubit agent"
    assert mod._qubit_label("") == ""
    # Unknown tokens pass through (developer visibility, never a claim).
    assert mod._qubit_label("deep-worker") == "deep-worker"


def test_report_template_uses_qubit_placeholders():
    text = (REPO_ROOT / "templates" / "professional_report.md").read_text()
    assert "Qubit gateway | Qubit agent | Qubit local" in text
    assert "openclaw | hermes" not in text
    assert "openclaw|hermes" not in text


def test_dashboard_routes_render_through_mapper():
    ui = (REPO_ROOT / "frontend" / "ui.js").read_text()
    assert "vbRouteLabel" in ui
    assert "openclaw: 'Qubit gateway'" in ui or \
        'openclaw: "Qubit gateway"' in ui
    for page in ("activity.html", "index.html"):
        html = (REPO_ROOT / "frontend" / f"{page}").read_text()
        assert "vbRouteLabel" in html, page
    # No raw route token rendering remains on those pages.
    activity = (REPO_ROOT / "frontend" / "activity.html").read_text()
    assert "'· '+it.route" not in activity
    assert "'Routed to '+dest" not in activity


def test_generated_report_has_no_upstream_agent_name(tmp_path):
    import subprocess

    out = tmp_path / "r.md"
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate_report.py"),
         "--task", "label probe", "--agent", "hermes", "--route", "openclaw",
         "--summary", "done", "--out", str(out)],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=60)
    assert proc.returncode == 0, proc.stderr[-300:]
    text = out.read_text()
    assert "Qubit agent" in text and "Qubit gateway" in text
    assert "hermes" not in text.lower() or "Hermes" not in text
    body = "\n".join(l for l in text.splitlines()
                      if not l.strip().startswith(">"))
    assert "openclaw" not in body.lower()
