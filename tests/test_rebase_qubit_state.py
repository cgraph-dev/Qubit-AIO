# tests/test_rebase_qubit_state.py
"""Task 6 gate: the ``qubit_state*`` bucket merged onto v0.21.5."""
from __future__ import annotations

import rebase_bucket_support as S
from rebase_bucket_support import runtime_on_path  # noqa: F401

BUCKET = "qubit_state*"
NEW_FILES = ["qubit_state_health.py"]
MODULES = ["qubit_state_health"]


def test_new_files_landed():
    S.assert_new_files(BUCKET, NEW_FILES)


def test_import_smoke(runtime_on_path):
    S.assert_imports(MODULES)


def test_recorded_in_audit():
    S.assert_bucket_audit(BUCKET, min_conflicts=6)
