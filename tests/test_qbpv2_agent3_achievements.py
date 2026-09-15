"""Agent 3 (wave 2) — Achievements real workflow audit.

Verifies the vendored hermes-achievements plugin is a REAL workflow: a 60+
catalog with categories/tiers/states, progress, persistence across reload,
rescan, and a confirmation-gated reset, all served by the production compat
backend. Also audits the served dashboard bundle for the real controls and the
render-boundary rebrand seam. No mocked progress, no static badge.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

BUNDLE = (REPO_ROOT / "frontend" / "hermes-web" / "public" / "plugins"
          / "hermes-achievements" / "dist" / "index.js")
ENGINE_API = (REPO_ROOT / "qubit_hermes" / "plugins" / "hermes-achievements"
              / "dashboard" / "plugin_api.py")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _dispatch(cfg, method, path, query=None, body=None):
    from voicebridge import hermes_compat as HC

    return HC.dispatch(cfg, method, path, query or {}, body or {})


def test_backend_engine_is_the_vendored_source():
    assert ENGINE_API.is_file()
    text = ENGINE_API.read_text(encoding="utf-8")
    assert "ACHIEVEMENTS" in text
    assert "@router.get(\"/achievements\")" in text
    assert "@router.post(\"/rescan\")" in text
    assert "@router.post(\"/reset-state\")" in text


def test_catalog_has_60_badges_categories_tiers_and_states(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    status, payload = _dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements")
    assert status == 200, payload
    achievements = payload["achievements"]
    assert len(achievements) >= 60, len(achievements)
    assert payload.get("total_count", 0) >= 60

    cats = {a["category"] for a in achievements}
    assert {"Agent Autonomy", "Debugging Chaos", "Lifestyle", "Model Lore",
            "Research/Web", "Tool Mastery", "Vibe Coding"} <= cats

    states = {a["state"] for a in achievements}
    assert states <= {"unlocked", "discovered", "secret"}
    assert states, "no achievement states"

    # Every definition carries a real progression model.
    kinds = set()
    for a in achievements:
        assert a.get("id") and a.get("name") and a.get("description")
        assert a.get("icon")
        assert a.get("kind") in ("lifetime", "best_session", "multi_condition")
        kinds.add(a["kind"])
        if a["kind"] == "multi_condition":
            assert a.get("requirements"), a["id"]
        else:
            assert a.get("tiers"), a["id"]
            assert len(a["tiers"]) == 5, a["id"]
    assert {"lifetime", "best_session", "multi_condition"} <= kinds


def test_rescan_persists_real_state_across_reload(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    status, payload = _dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/rescan")
    assert status == 200, payload
    assert payload.get("ok") is True
    assert payload.get("scan_meta", {}).get("mode") in (
        "full", "incremental", "pending", "in_progress")

    # A brand-new dispatch (fresh process image of the same state dir) still
    # reports the same catalog and discovered/unlocked counts.
    status2, payload2 = _dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements")
    assert status2 == 200
    assert payload2["total_count"] == payload["total_count"]
    assert payload2["discovered_count"] == payload["discovered_count"]

    import os

    state_root = Path(os.environ["HERMES_HOME"]) / "plugin-data" / \
        "hermes-achievements"
    assert state_root.is_dir(), state_root
    assert (state_root / "state.json").exists() or \
        (state_root / "scan_snapshot.json").exists(), list(state_root.iterdir())


def test_reset_requires_confirmation_and_then_clears_unlocks(tmp_path,
                                                             monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _dispatch(cfg, "POST", "/api/plugins/hermes-achievements/rescan")
    before = _dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements")[1]

    status, payload = _dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/reset-state")
    assert status == 409, (status, payload)
    assert payload.get("code") == "CONFIRM_REQUIRED"

    status, payload = _dispatch(
        cfg, "POST", "/api/plugins/hermes-achievements/reset-state",
        body={"confirm": True})
    assert status == 200 and payload.get("ok") is True, payload

    after = _dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements")[1]
    assert after["unlocked_count"] == 0
    assert before["total_count"] == after["total_count"]


def test_scan_status_and_recent_unlocks_are_real_routes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    status, scan = _dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/scan-status")
    assert status == 200
    assert scan.get("state") in ("idle", "pending", "running", "done", "error")
    status, recent = _dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/recent-unlocks")
    assert status == 200
    assert isinstance(recent, (list, dict))


def test_served_bundle_wires_the_real_workflow_controls():
    assert BUNDLE.is_file()
    src = BUNDLE.read_text(encoding="utf-8")
    # Real API seam + all read/write plugin routes.
    assert "/api/plugins/hermes-achievements" in src
    for route in ('"/achievements"', '"/rescan"', '"/reset-state"'):
        assert route in src, route
    # Real controls, not a static badge.
    for label in ("actions.rescan", "actions.reset", "filters.all_categories",
                  "filters.visibility_unlocked", "filters.visibility_secret",
                  "card.share_title", "stats.highest_tier"):
        assert label in src, label
    # Reset is a two-step confirmation against the confirm-gated endpoint.
    assert "setResetArmed" in src
    assert "confirm: true" in src


def test_served_bundle_rebrands_backend_product_names():
    src = BUNDLE.read_text(encoding="utf-8")
    assert "rebrandPayload" in src
    assert "SDK.fetchJSON(url, options).then(rebrandPayload)" in src
    # The only permitted upstream token is the compat auth header name.
    for i, line in enumerate(src.splitlines(), 1):
        for bad in ("Hermes", "OpenClaw"):
            if bad in line:
                assert "X-Hermes-Session-Token" in line, (i, line)


_REBRAND_JS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('const UPSTREAM_PRODUCT_RE');
const end = src.indexOf('function api(');
if (start < 0 || end < 0) { console.error('rebrand seam missing'); process.exit(2); }
const frag = src.slice(start, end);
const fn = new Function(frag + '\nreturn rebrandPayload;');
const payload = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = fn()(payload);
const s = JSON.stringify(out);
for (const bad of ['Hermes', 'OpenClaw']) {
  if (s.includes(bad)) { console.error('leak ' + bad); process.exit(3); }
}
console.log('ok');
"""


def test_catalog_payload_is_rebranded_on_render_boundary(tmp_path, monkeypatch):
    """Execute the shipped normalizer against the real backend payload."""
    import subprocess

    cfg = _cfg(tmp_path, monkeypatch)
    status, payload = _dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements")
    assert status == 200
    # The backend rebrands at the served boundary: the raw JSON API must already
    # be Qubit-only; the shipped SPA normalizer remains a second guard.
    blob = json.dumps(payload)
    assert "Hermes" not in blob and "OpenClaw" not in blob, \
        "served achievements payload still authors an upstream name"
    assert payload["achievements"], "test precondition: achievements present"
    out = tmp_path / "payload.json"
    out.write_text(json.dumps(payload))
    res = subprocess.run(
        ["node", "-e", _REBRAND_JS, str(BUNDLE), str(out)],
        capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
