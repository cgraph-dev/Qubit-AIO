"""Helpers for tests that assert on evidence ledgers, some of which were purged.

``eafb54e26`` deleted 340 files under ``docs/``, including the machine-readable
ledgers under ``docs/archive/2026-09-21/``. ``AGENTS.md`` forbids re-creating them:
a lost doc must be MARKED as lost, never re-authored from memory.

A test that used to say "this ledger exists and says X" therefore cannot pass by
re-reading the file — and must not be deleted either, or the coverage it provided
(that the ledger was generated, reconciled, and consistent) disappears silently.

The rule this module encodes is the one ``scripts/verify_doc_citations.py`` already
uses: a purged path is *historically retrievable* from ``eafb54e26^`` and is
*expected to be absent* from the tree. So a test can still check the ledger by
reading it from git history, read-only, and fail loudly if it was never there.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

#: The commit that purged the archive markdown and ledgers.
PURGE_COMMIT = "eafb54e26"

#: The tree as it stood immediately BEFORE the purge.
PRE_PURGE_REF = f"{PURGE_COMMIT}^"

_REPO = Path(__file__).resolve().parents[1]


def is_in_tree(rel_path: str) -> bool:
    """True when ``rel_path`` exists in the working tree today."""
    return (_REPO / rel_path).is_file()


def was_purged(rel_path: str) -> bool:
    """True when ``rel_path`` is absent now but retrievable from before the purge.

    This is the honest replacement for ``assert path.is_file()`` on a ledger that
    was deliberately deleted: absence is expected, and history is the proof it
    existed rather than never having been generated.
    """
    if is_in_tree(rel_path):
        return False
    out = subprocess.run(
        ["git", "cat-file", "-e", f"{PRE_PURGE_REF}:{rel_path}"],
        cwd=_REPO, capture_output=True,
    )
    return out.returncode == 0


def read_pre_purge_text(rel_path: str) -> Optional[str]:
    """The file's text as of before the purge, or None when it never existed there."""
    out = subprocess.run(
        ["git", "show", f"{PRE_PURGE_REF}:{rel_path}"],
        cwd=_REPO, capture_output=True, text=True,
    )
    return out.stdout if out.returncode == 0 else None


def require_ledger(rel_path: str) -> str:
    """Text of a ledger that is either live or retrievable from before the purge.

    Returns the text so a caller can assert its CONTENT (counts reconcile, every
    row has a status) rather than merely that a path exists — which is the check
    that actually had value before the file was purged.

    Raises ``AssertionError`` with a message naming the purge commit when the file
    is in neither place: that is a real defect (the ledger was never generated, or
    the path is wrong), not a stale test.
    """
    if is_in_tree(rel_path):
        return (_REPO / rel_path).read_text(encoding="utf-8")
    text = read_pre_purge_text(rel_path)
    if text is None:
        raise AssertionError(
            f"{rel_path} is neither in the tree nor retrievable from "
            f"{PRE_PURGE_REF} (purged in {PURGE_COMMIT}). Either the path is wrong "
            f"or the ledger was never generated — do not re-author it; fix the path "
            f"or regenerate the ledger."
        )
    return text
