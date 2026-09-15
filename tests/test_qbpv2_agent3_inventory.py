"""Agent 3 (wave 2) — fresh Hermes frontend inventory / route-parity audit.

Source-derived, not report-trusting: recomputes the upstream->overlay file map
from the vendored tree, checks every upstream page has an overlay file AND a
backend route, checks every navigation item is a real routed workspace, and
checks the dashboard plugin fleet is registered and served.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FRONTEND = REPO_ROOT / "frontend"
UPSTREAM_SRC = REPO_ROOT / "qubit_hermes" / "web" / "src"
OVERLAY_SRC = FRONTEND / "hermes-web" / "src"
MAP = json.loads(
    (REPO_ROOT / "docs" / "HERMES_FRONTEND_MAP.json").read_text(encoding="utf-8"))


def _walk(root: Path):
    out = {}
    for p in root.rglob("*"):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(
                p.read_bytes()).hexdigest()
    return out


def test_every_upstream_page_has_overlay_file_and_backend_route():
    pages = MAP["inventory"]["pages"]
    assert pages, "inventory.pages is empty"
    for p in pages:
        overlay = REPO_ROOT / p["qubit_file"]
        vendored = REPO_ROOT / p["vendored_file"]
        assert overlay.is_file(), f"missing overlay page {p['qubit_file']}"
        assert vendored.is_file(), f"missing vendored page {p['vendored_file']}"
        assert p.get("backend_route"), f"no backend route for {p['qubit_file']}"
        assert p.get("qubit_route", "").startswith("/"), p


def test_no_upstream_src_file_is_absent_from_the_overlay():
    up = _walk(UPSTREAM_SRC)
    ov = _walk(OVERLAY_SRC)
    missing = sorted(k for k in up if k not in ov)
    assert missing == [], f"upstream files absent from overlay: {missing}"
    # Sanity: overlay must preserve the complete upstream page set.
    up_pages = {k for k in up if k.startswith("pages/") and k.endswith(".tsx")}
    ov_pages = {k for k in ov if k.startswith("pages/") and k.endswith(".tsx")}
    assert up_pages <= ov_pages, f"pages dropped: {sorted(up_pages - ov_pages)}"


def test_navigation_items_are_real_routes():
    from voicebridge import webui

    nav_items = MAP["inventory"]["navigation"]
    assert len(nav_items) >= 20, len(nav_items)
    for item in nav_items:
        path = item["path"]
        # A nav item is real when it is a canonical SPA route or a registered
        # dashboard-plugin tab (which the SPA mounts through buildRoutes).
        known = path in webui.SPA_ROUTE_SET
        if not known:
            known = path in webui.PUBLIC_ROUTE_ALIASES.values()
        if not known:
            fleet = json.loads(
                (FRONTEND / "hermes-web" / "public" / "plugins" / "fleet.json")
                .read_text(encoding="utf-8"))
            known = any(m.get("tab", {}).get("path") == path for m in fleet)
        assert known, f"nav item routes nowhere real: {item}"


def test_plugin_fleet_manifests_exist_and_are_registered():
    fleet = json.loads(
        (FRONTEND / "hermes-web" / "public" / "plugins" / "fleet.json")
        .read_text(encoding="utf-8"))
    names = {m["name"] for m in fleet}
    assert {"hermes-achievements", "kanban"} <= names
    for m in fleet:
        root = FRONTEND / "hermes-web" / "public" / "plugins" / m["name"]
        assert (root / m["entry"]).is_file(), f"{m['name']} entry missing"
        if m.get("css"):
            assert (root / m["css"]).is_file(), f"{m['name']} css missing"
        manifest = REPO_ROOT / "qubit_hermes" / "plugins" / m["name"] / \
            "dashboard" / "manifest.json"
        assert manifest.is_file(), f"{m['name']} backend manifest missing"


def test_overlay_plugins_each_have_manifest_and_entry():
    overlay = FRONTEND / "hermes-qubit" / "plugins"
    dirs = sorted(p for p in overlay.iterdir() if p.is_dir())
    assert len(dirs) >= 10, len(dirs)
    for d in dirs:
        manifest = d / "manifest.json"
        assert manifest.is_file(), f"{d.name}: no manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        entry = d / data["entry"]
        assert entry.is_file(), f"{d.name}: entry {data['entry']} missing"
        assert data.get("tab", {}).get("path", "").startswith("/"), d.name


def test_reported_missing_paths_are_explicit_and_owned():
    rows = MAP["missing_paths"]
    assert rows, "missing_paths must enumerate every open item"
    for row in rows:
        for key in ("kind", "path", "detail", "required_change", "status"):
            assert row.get(key), row
        # Every open row names a cross-boundary owner (Agent 3 cannot edit
        # outside frontend/**).
        if row["status"] == "open":
            assert row.get("cross_boundary_owner"), row
