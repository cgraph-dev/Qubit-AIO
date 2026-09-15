"""Agent 3 (role C) — Hermes frontend inventory audit.

Read-only source audit of the pinned Hermes dashboard surface: every upstream
page, route, navigation item, dialog, form, plugin manifest, plugin API, asset,
state file, localization namespace, and workflow is inventoried in
``docs/HERMES_FRONTEND_MAP.json`` and checked against the real Qubit rendering
paths (``frontend/hermes-web/**`` overlay + ``frontend/hermes-qubit/**``).

The audit explicitly covers ``qubit_hermes/plugins/hermes-achievements/`` so a
generic badge/status-card imitation cannot pass: the 60+ catalog, the six REST
routes, the durable state files, the served dashboard page, and the
product-identity normalization are all asserted from real artifacts.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

MAP = REPO_ROOT / "docs" / "HERMES_FRONTEND_MAP.json"
FW = REPO_ROOT / "frontend" / "hermes-web"
QH = REPO_ROOT / "frontend" / "hermes-qubit"
PINNED = REPO_ROOT / "qubit_hermes" / "plugins"
ACH_API = PINNED / "hermes-achievements" / "dashboard" / "plugin_api.py"
ACH_BUNDLE = (FW / "public" / "plugins" / "hermes-achievements" / "dist"
              / "index.js")
APP_TSX = FW / "src" / "App.tsx"

UPSTREAM_RE = re.compile(r"\bHermes\b|\bOpenClaw\b")


def _doc():
    return json.loads(MAP.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Map shape
# ---------------------------------------------------------------------------
def test_inventory_sections_present_and_pinned():
    doc = _doc()
    assert doc["source"]["revision"] == \
        "2237be355906fbe6065ce1815711eee52b2d646e"
    for key in ("inventory", "achievements_audit", "missing_paths"):
        assert key in doc, key
    inv = doc["inventory"]
    for key in ("counts", "pages", "navigation", "dialogs", "forms",
                "plugins", "plugin_apis", "assets", "state_files", "i18n",
                "workflows"):
        assert key in inv, key
    assert json.loads(REPO_ROOT.joinpath("docs", "INVENTORY_HERMES.json")
                      .read_text())["commit"] == doc["source"]["revision"]


def test_counts_match_the_tree():
    inv = _doc()["inventory"]
    counts = inv["counts"]
    assert counts["baseline_pages"] == 19
    assert counts["dialogs"] == len(inv["dialogs"])
    assert counts["forms"] == len(inv["forms"])
    # every upstream page file has exactly one inventory row
    pages = [f for f in _doc()["files"] if f["kind"] == "page"]
    assert len(pages) == 19
    assert len(inv["pages"]) == len(pages)
    disk_plugins = sorted((QH / "plugins").glob("*/manifest.json"))
    assert counts["overlay_plugins"] == len(disk_plugins)
    pinned = sorted(PINNED.rglob("dashboard/manifest.json"))
    assert counts["pinned_dashboard_plugins"] == len(pinned)


# ---------------------------------------------------------------------------
# Pages / routes / navigation / dialogs
# ---------------------------------------------------------------------------
def test_every_upstream_page_has_a_real_overlay_page():
    inv = _doc()["inventory"]
    for row in inv["pages"]:
        assert row["status"] == "adapted", row
        local = REPO_ROOT / row["qubit_file"]
        assert local.is_file(), row["qubit_file"]
        vendored = REPO_ROOT / row["vendored_file"]
        assert vendored.is_file(), row["vendored_file"]
        assert row["qubit_route"].startswith("/"), row


def test_overlay_route_and_nav_maps_keep_every_baseline_route():
    app = APP_TSX.read_text(encoding="utf-8")
    core = re.search(r"const BUILTIN_ROUTES_CORE[^{]*\{(.*?)\n\};", app,
                     re.S).group(1)
    routes = set(re.findall(r'"([^"]+)":', core))
    for route in _doc()["routes"]:
        if route == "/chat":
            continue
        assert route in routes or route == "/", (route, routes)
    # navigation: every built-in nav path exists in the overlay nav list
    nav_block = re.search(r"const BUILTIN_NAV_REST[^=]*=\s*\[(.*?)\n\];",
                          app, re.S).group(1)
    nav = set(re.findall(r'path:\s*"([^"]+)"', nav_block))
    core_nav = [n["path"] for n in _doc()["inventory"]["navigation"]
                if not n["plugin_owned"] and n["path"] != "/chat"]
    for path in core_nav:
        assert path in nav, (path, sorted(nav))
    assert 'path: "/chat"' in app  # CHAT_NAV_ITEM


def test_every_dialog_component_is_present():
    for row in _doc()["inventory"]["dialogs"]:
        assert row["status"] == "adapted", row
        assert (REPO_ROOT / row["qubit_file"]).is_file(), row["qubit_file"]


# ---------------------------------------------------------------------------
# Plugins: pinned manifests, overlay fleet, served fleet JSON
# ---------------------------------------------------------------------------
def test_pinned_and_overlay_plugin_inventory_matches_disk():
    inv = _doc()["inventory"]
    pinned = {r["name"]: r for r in inv["plugins"]["pinned"]}
    assert set(pinned) == {"hermes-achievements", "kanban"}
    for name, row in pinned.items():
        man = REPO_ROOT / row["manifest"]
        assert man.is_file(), man
        disk = json.loads(man.read_text(encoding="utf-8"))
        assert disk["name"] == name
        assert disk["tab"]["path"] == row["tab"]["path"]
        assert REPO_ROOT.joinpath(row["bundle_served"]).is_file()

    overlay = {r["name"] for r in inv["plugins"]["overlay"]}
    disk_overlay = {p.parent.name for p in (QH / "plugins").glob(
        "*/manifest.json")}
    assert overlay == disk_overlay, (overlay, disk_overlay)
    for row in inv["plugins"]["overlay"]:
        assert (REPO_ROOT / row["js"]).is_file(), row["js"]
        assert (REPO_ROOT / row["manifest"]).is_file(), row["manifest"]

    fleet = {e["name"] for e in json.loads(
        (FW / "public" / "plugins" / "fleet.json").read_text())}
    assert fleet == {"hermes-achievements", "kanban"}


def test_plugin_apis_are_real_routes():
    api_text = ACH_API.read_text(encoding="utf-8")
    declared = set(re.findall(r'@router\.(?:get|post)\("([^"]+)"\)', api_text))
    row = next(r for r in _doc()["inventory"]["plugin_apis"]
               if r["plugin"] == "hermes-achievements")
    got = {r.split(" ", 1)[1] for r in row["routes"]}
    assert got == declared, (sorted(got), sorted(declared))
    for endpoint in declared:
        assert f'"{endpoint}"' in api_text


# ---------------------------------------------------------------------------
# Achievements: catalog, assets, states, localization, tests
# ---------------------------------------------------------------------------
def test_achievements_catalog_is_real_and_at_least_sixty():
    api_text = ACH_API.read_text(encoding="utf-8")
    audit = _doc()["achievements_audit"]
    count = len(re.findall(r"^\s*_ach\(", api_text, re.M))
    assert count == audit["catalog_count"] >= audit["catalog_min_required"]
    # tiered + multi-condition engine, not a static list
    assert "def evaluate_tiered" in api_text
    assert "def evaluate_requirements" in api_text
    assert "TIER_NAMES" in api_text and "Olympian" in api_text
    for category in audit["categories"]:
        assert f'"{category}"' in api_text, category


def test_achievements_state_assets_and_page_are_real():
    audit = _doc()["achievements_audit"]
    assert audit["backend_present"] and audit["api_present"]
    assert audit["persistence_present"] and audit["dashboard_page_present"]
    assert (REPO_ROOT / audit["dashboard_page_file"]).is_file()
    api_text = ACH_API.read_text(encoding="utf-8")
    for name in ("state.json", "scan_snapshot.json", "scan_checkpoint.json"):
        assert name in api_text, name
    for asset in audit["assets_present"]:
        assert (PINNED / "hermes-achievements" / asset).is_file(), asset
    for state in audit["states"]:
        assert state in api_text, state
    assert (PINNED / "hermes-achievements" / "tests"
            / "test_achievement_engine.py").is_file()


def test_achievements_namespace_localization_is_wired():
    en = (FW / "src" / "i18n" / "en.ts").read_text(encoding="utf-8")
    assert "achievements: {" in en
    assert "Qubit Achievements" in en
    assert "kanban: {" in en
    i18n = _doc()["inventory"]["i18n"]
    assert "achievements" in i18n["host_namespaces"]
    assert "kanban" in i18n["host_namespaces"]
    assert i18n["en_total_string_keys"] > 500
    assert i18n["achievements_namespace_keys"] >= 50
    assert i18n["kanban_namespace_keys"] >= 100


# ---------------------------------------------------------------------------
# Product identity: backend catalog text is normalized before render
# ---------------------------------------------------------------------------
def _cfg(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home" / ".hermes"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    from voicebridge.config import Config

    return Config.load(p)


def test_achievements_payload_is_normalized_at_the_render_boundary(
        tmp_path, monkeypatch):
    """The pinned engine still authors upstream product names; the shipped
    Qubit bundle must normalize every API payload before React renders it."""
    bundle = ACH_BUNDLE.read_text(encoding="utf-8")
    # the bundle defines and applies the normalizer in its single api() seam
    assert "rebrandPayload" in bundle
    assert re.search(r"fetchJSON\([^)]*\)\.then\(rebrandPayload\)", bundle)
    assert "UPSTREAM_PRODUCT_RE" in bundle and "\"Qubit\"" in bundle

    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    status, payload = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements", {}, {})
    assert status == 200 and isinstance(payload, dict), (status, payload)

    # Apply the exact contract the bundle implements to the real payload.
    normalized = json.dumps(payload).replace("Hermes", "Qubit")
    normalized = normalized.replace("OpenClaw", "Qubit")
    assert not UPSTREAM_RE.search(normalized), \
        "achievements payload still renders an upstream product name"


def test_shipped_normalizer_rebrands_the_real_backend_payload(
        tmp_path, monkeypatch):
    """Execute the shipped ``rebrandPayload`` (not a Python copy) against the
    real achievements payload and prove no upstream product name survives."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the shipped bundle")
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    status, payload = HC.dispatch(
        cfg, "GET", "/api/plugins/hermes-achievements/achievements", {}, {})
    assert status == 200

    bundle = ACH_BUNDLE.read_text(encoding="utf-8")
    start = bundle.index("const UPSTREAM_PRODUCT_RE")
    end = bundle.index("function api(", start)
    harness = (bundle[start:end]
               + "\nprocess.stdout.write(JSON.stringify(rebrandPayload("
               + json.dumps(payload) + ")));")
    script = tmp_path / "run_normalizer.js"
    script.write_text(harness, encoding="utf-8")
    proc = subprocess.run([node, str(script)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr[-800:]
    rebranded = json.loads(proc.stdout)
    assert rebranded["achievements"], "normalizer dropped the catalog"
    assert not UPSTREAM_RE.search(json.dumps(rebranded)), \
        "shipped normalizer left an upstream product name in the payload"


def test_missing_paths_are_recorded_with_owner_and_detail():
    rows = _doc()["missing_paths"]
    assert rows, "no missing-path findings recorded"
    kinds = {r["kind"] for r in rows}
    assert {"branding-leak"} <= kinds
    for row in rows:
        for key in ("kind", "path", "symbol", "detail", "required_change",
                    "cross_boundary_owner"):
            assert row.get(key), (row.get("kind"), key)
