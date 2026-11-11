"""The Qubit Office visible-brand guard and its legal/provenance floor."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "check_qoffice_brand.py"


def _guard():
    spec = importlib.util.spec_from_file_location("qoffice_brand_guard", GUARD)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repository_visible_brand_is_clean():
    guard = _guard()
    assert guard.check() == []


def test_index_metadata_violation_is_detected(tmp_path):
    guard = _guard()
    html = (
        '<html><head><title>Habblaud</title>'
        '<meta property="og:title" content="Habblaud" />'
        '<meta name="description" content="Qubit Office" />'
        "</head></html>"
    )
    target = tmp_path / "index.html"
    target.write_text(html, encoding="utf-8")
    problems = guard.index_metadata_violations(target)
    assert any("<title>" in problem for problem in problems)
    assert any("og:title" in problem for problem in problems)


def test_manifest_violation_is_detected(tmp_path):
    guard = _guard()
    target = tmp_path / "manifest.webmanifest"
    target.write_text('{"name": "Habblaud", "short_name": "Habblaud"}',
                      encoding="utf-8")
    problems = guard.manifest_violations(target)
    assert any("name=" in problem for problem in problems)


def test_brand_module_keeps_legal_notice_but_hides_upstream_name(tmp_path):
    guard = _guard()
    source = guard.BRAND_TS.read_text(encoding="utf-8")
    clean = tmp_path / "brand.ts"
    clean.write_text(source, encoding="utf-8")
    assert guard.brand_source_violations(clean) == []

    dirty = tmp_path / "brand-dirty.ts"
    dirty.write_text(source.replace('name: "Qubit Office"',
                                    'name: "Habblaud"'), encoding="utf-8")
    assert guard.brand_source_violations(dirty)


def test_shell_copy_violation_is_detected_and_comments_are_ignored(tmp_path):
    guard = _guard()
    with_comment = tmp_path / "shell-comment.tsx"
    with_comment.write_text(
        '/* The embedded Habblaud document has no hook. */\n'
        'const label = "Qubit Office";\n',
        encoding="utf-8",
    )
    assert guard.shell_copy_violations(with_comment) == []

    with_copy = tmp_path / "shell-copy.tsx"
    with_copy.write_text('const title = "Habblaud virtual office";\n',
                         encoding="utf-8")
    assert guard.shell_copy_violations(with_copy)


def test_legal_and_provenance_records_are_present():
    guard = _guard()
    assert guard.legal_notice_violations() == []


def test_inventory_document_lists_every_category():
    inventory = REPO / "docs" / "QOFFICE-REBRAND-INVENTORY.md"
    text = inventory.read_text(encoding="utf-8")
    for category in ("user-visible", "public API", "private code", "tests",
                     "assets/bundle", "legal/provenance", "generated"):
        assert category in text
    assert "Non-asset, non-test references" in text
