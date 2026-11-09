"""The purged-ledger helper must distinguish "deleted on purpose" from "never existed".

Tests that used to read a machine-readable ledger under docs/archive/2026-09-21/ now
have to work without the file, because eafb54e26 purged it and AGENTS.md forbids
re-creating it. The helper recovers the ledger read-only from pre-purge history —
which is only honest if it still FAILS for a path that was never there. That
distinction is the whole contract, so it is pinned here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _purged_ledger import (  # noqa: E402
    PURGE_COMMIT,
    is_in_tree,
    read_pre_purge_text,
    require_ledger,
    was_purged,
)

#: A ledger that genuinely existed before the purge.
PURGED_LEDGER = "docs/archive/2026-09-21/NATIVE_EQUIVALENTS_8U.json"


def test_a_purged_ledger_is_absent_from_the_tree_but_recoverable() -> None:
    assert not is_in_tree(PURGED_LEDGER), "the purge is supposed to have removed it"
    assert was_purged(PURGED_LEDGER), f"not retrievable from {PURGE_COMMIT}^"
    # Recovering the CONTENT is the point: a test can still assert the ledger's
    # shape (it reconciled, every row has a status) rather than just its existence.
    text = require_ledger(PURGED_LEDGER)
    assert text.strip(), "recovered ledger is empty"


def test_a_path_that_never_existed_is_not_reported_as_purged() -> None:
    # The distinction the helper exists for: "deleted on purpose" must not become a
    # blanket excuse for "path is wrong".
    ghost = "docs/archive/2026-09-21/THIS_WAS_NEVER_HERE.json"
    assert not was_purged(ghost)
    assert read_pre_purge_text(ghost) is None


def test_require_ledger_names_the_purge_for_a_genuinely_missing_path() -> None:
    try:
        require_ledger("docs/archive/2026-09-21/THIS_WAS_NEVER_HERE.json")
    except AssertionError as exc:
        # The failure must explain itself: a future reader needs to know this was a
        # purge, not a typo, and must not re-author the file to silence it.
        assert PURGE_COMMIT in str(exc)
        assert "never generated" in str(exc)
    else:
        raise AssertionError("a never-existent path must not be silently satisfied")


def test_a_live_file_is_read_from_the_tree(monkeypatch) -> None:
    # A ledger regenerated into the tree must win over history, so the helper
    # keeps working if these documents are ever deliberately restored.
    assert require_ledger("pyproject.toml").strip()
