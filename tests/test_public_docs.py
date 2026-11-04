"""Public documentation guard.

The root documentation set is shipped to users. These tests keep it present
and keep operator-specific paths and internal-only documents out of it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

PUBLIC_DOCS = (
    "README.md",
    "INSTALL.md",
    "TROUBLESHOOTING.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
)

# Built at runtime so the operator marker never appears verbatim in the
# public tree that scripts/export_public_tree.py scans.
PERSONAL_MARKERS = (
    "/home/" + "trick",
    "/Users/",
    "C:\\Users\\",
)

INTERNAL_DOC_MARKERS = (
    "docs/STATUS.md",
    "docs/OPEN_THREADS.md",
    "docs/DECISIONS.md",
    "docs/FROZEN_TARGET.md",
    "docs/PLAN.md",
    "AGENTS.md",
    "graft/",
)


@pytest.mark.parametrize("name", PUBLIC_DOCS)
def test_public_doc_exists_and_is_nonempty(name: str) -> None:
    path = REPO / name
    assert path.is_file(), f"missing public doc: {name}"
    assert path.read_text(encoding="utf-8").strip(), f"empty public doc: {name}"


@pytest.mark.parametrize("name", PUBLIC_DOCS)
def test_public_doc_has_no_operator_paths(name: str) -> None:
    text = (REPO / name).read_text(encoding="utf-8")
    for marker in PERSONAL_MARKERS:
        assert marker not in text, f"{name} contains an operator path marker: {marker!r}"


@pytest.mark.parametrize("name", PUBLIC_DOCS)
def test_public_doc_does_not_reference_internal_docs(name: str) -> None:
    text = (REPO / name).read_text(encoding="utf-8")
    for marker in INTERNAL_DOC_MARKERS:
        assert marker not in text, f"{name} references an internal doc: {marker!r}"
