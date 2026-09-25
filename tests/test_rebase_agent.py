# tests/test_rebase_agent.py
"""Task 6 gate: the ``agent/`` bucket merged onto v0.21.5."""
from __future__ import annotations

import rebase_bucket_support as S
from rebase_bucket_support import runtime_on_path  # noqa: F401

BUCKET = "agent"
NEW_FILES = ["agent/legacy_cli.py", "agent/auxiliary_hooks.py"]
MODULES = ["agent.auxiliary_hooks"]


def test_new_files_landed():
    S.assert_new_files(BUCKET, NEW_FILES)


def test_import_smoke(runtime_on_path):
    S.assert_imports(MODULES)


def test_recorded_in_audit():
    S.assert_bucket_audit(BUCKET, min_conflicts=100)
