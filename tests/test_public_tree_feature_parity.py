"""Static feature/page parity gate for the public source export.

Hermetic: Git objects plus source files only.  No server, browser, network, or
operator HOME is read.  See ``scripts/check_public_tree_feature_parity.py`` and
``scripts/public_tree_feature_parity.json``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.check_public_tree_feature_parity import check  # noqa: E402
from scripts.export_public_tree import selected_paths  # noqa: E402

MANIFEST = REPO / "scripts" / "public_tree_feature_parity.json"

EXPECTED_PAGES = {
    "achievements",
    "approvals",
    "boards",
    "gateway",
    "memory",
    "models",
    "office",
    "office2",
    "office3",
    "operations",
    "reports",
    "runtime",
    "tv",
}


def test_public_export_keeps_every_declared_qubit_surface() -> None:
    report = check(REPO)
    assert report.ok, "\n".join([
        "public-tree feature parity failed:",
        *[f"  - {item}" for item in report.missing],
    ])
    assert report.pages_checked == len(EXPECTED_PAGES)
    assert len(report.required_routes) >= len(EXPECTED_PAGES)
    assert report.included_routes == report.required_routes


def test_manifest_covers_the_qbit_only_page_set() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert {page["id"] for page in manifest["pages"]} == EXPECTED_PAGES
    for page in manifest["pages"]:
        assert page["routes"], page["id"]
        assert page.get("backend") or page.get("deliberate_backend_omission"), page["id"]


def test_tui_and_plugin_dist_assets_are_in_the_export() -> None:
    _tracked, included, _excluded, _counts, _personal = selected_paths(REPO)
    included = set(included)
    for rel in (
        "qubit_runtime/ui-tui/dist/entry.js",
        "qubit_runtime/ui-tui/dist/hermes-build.json",
        "qubit_runtime/plugins/qubit-achievements/dashboard/dist/index.js",
        "qubit_runtime/plugins/qubit-achievements/dashboard/dist/style.css",
        "frontend/qubit-web/public/plugins/qubit-achievements/dist/index.js",
        "frontend/qubit-web/public/plugins/qubit-achievements/dist/style.css",
    ):
        assert rel in included, rel


def test_missing_report_names_the_path_and_source(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    tv = next(page for page in manifest["pages"] if page["id"] == "tv")
    tv["routes"].append("/qubit-not-a-real-route")
    tv["files"].append("frontend/qubit-web/src/pages/DoesNotExist.tsx")
    custom = tmp_path / "parity.json"
    custom.write_text(json.dumps(manifest), encoding="utf-8")

    report = check(REPO, custom)
    text = "\n".join(report.missing)
    assert not report.ok
    assert "MISSING route /qubit-not-a-real-route" in text
    assert "MISSING export frontend/qubit-web/src/pages/DoesNotExist.tsx" in text
    assert "voicebridge/webui.py" in text
