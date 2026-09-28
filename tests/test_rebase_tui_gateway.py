# tests/test_rebase_tui_gateway.py
"""Task 6 gate: the ``tui_gateway/`` bucket merged onto v0.21.5."""
from __future__ import annotations

import rebase_bucket_support as S
from rebase_bucket_support import runtime_on_path  # noqa: F401

BUCKET = "tui_gateway"
NEW_FILES = ["tui_gateway/methods_display.py"]
MODULES = ["tui_gateway.methods_display"]


def test_new_files_landed():
    S.assert_new_files(BUCKET, NEW_FILES)


def test_import_smoke(runtime_on_path):
    S.assert_imports(MODULES)


def test_recorded_in_audit():
    S.assert_bucket_audit(BUCKET, min_conflicts=31)
