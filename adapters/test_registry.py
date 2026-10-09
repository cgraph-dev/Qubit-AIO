"""Tests for the adapters/ registry scaffold.

Asserts the SHIPPED registry parses, has one entry per kind, and that every
recorded seam points at a file that actually EXISTS in the tree (and, for
precision, that the recorded line is within the file). This is the guard that
makes the registry meaningful: if a seam file is moved or renamed out from under
it, this test fails loudly instead of the registry silently lying.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "adapters"))

from registry import (  # noqa: E402
    Adapter, VALID_STATUSES, load_adapters, load_registry_document, kind_is_registered,
)


def test_registry_parses_and_is_non_empty():
    adapters = load_adapters()
    assert adapters, "registry.yaml shipped with no adapters"


def test_all_five_kinds_are_present():
    kinds = {a.kind for a in load_adapters()}
    assert kinds == {"skills", "plugins", "mcps", "channels", "tools"}, kinds
    for kind in ("skills", "plugins", "mcps", "channels", "tools"):
        assert kind_is_registered(kind), f"kind {kind} has no registry entry"


def test_every_seam_file_exists():
    """The real deliverable: each seam's file must exist on disk."""
    missing = [a.seam for a in load_adapters()
               if not (REPO / a.seam_path).is_file()]
    assert not missing, f"seam files missing: {missing}"


def test_every_seam_line_is_within_file():
    for a in load_adapters():
        text = (REPO / a.seam_path).read_text(encoding="utf-8-sig")
        line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
        assert 1 <= a.seam_line <= line_count, (
            f"{a.seam}: line {a.seam_line} outside file ({line_count} lines)")


def test_seam_parsing_helpers():
    a = Adapter(name="x", kind="tools", status="shipped", source="builtin",
                seam="qubit_runtime/tools/registry.py:96", symbol="discover_builtin_tools")
    assert a.seam_path == "qubit_runtime/tools/registry.py"
    assert a.seam_line == 96


def test_schema_rejects_bad_kind(tmp_path):
    bad = tmp_path / "registry.yaml"
    bad.write_text(
        "version: 1\nadapters:\n"
        "  - {name: x, kind: bogus, status: shipped, source: builtin,"
        " seam: a.py:1, symbol: f}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bad kind"):
        load_adapters(bad)


def test_schema_rejects_missing_field(tmp_path):
    bad = tmp_path / "registry.yaml"
    bad.write_text(
        "version: 1\nadapters:\n"
        "  - {name: x, kind: tools, status: shipped, source: builtin}\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="missing fields"):
        load_adapters(bad)


def test_version_present():
    assert load_registry_document().get("version") == 1
