"""Guard the deterministic identifier/file-rename artifact generator.

``scripts/identifier_rename.py`` reproduces
``docs/archive/2026-09-21/QUBIT_IDENTIFIER_RENAME.json`` from the worktree.
These tests prove the generator is deterministic (two in-memory renders are
byte-identical), that the on-disk artifact is current, and that ``--check``
fails closed on drift.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "docs/archive/2026-09-21/QUBIT_IDENTIFIER_RENAME.json"
GENERATOR = REPO / "scripts/identifier_rename.py"


def _load():
    spec = importlib.util.spec_from_file_location("identifier_rename", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["identifier_rename"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_two_renders_are_byte_identical():
    mod = _load()
    assert mod.render(mod.build()) == mod.render(mod.build())


def test_committed_artifact_is_structurally_current():
    mod = _load()
    on_disk = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert on_disk == mod.build(), (
        "docs/archive/2026-09-21/QUBIT_IDENTIFIER_RENAME.json is stale; "
        "regenerate with `python scripts/identifier_rename.py`")


def test_committed_artifact_is_byte_identical():
    mod = _load()
    assert COMMITTED.read_text(encoding="utf-8") == mod.render(mod.build())


def test_check_passes_against_committed_artifact():
    mod = _load()
    assert mod.main(["--check"]) == 0


def test_check_fails_closed_on_drift(tmp_path):
    mod = _load()
    drifted = tmp_path / "QUBIT_IDENTIFIER_RENAME.json"
    drifted.write_text('{"file_renames": [], "js_files": [], "python_files": []}\n',
                       encoding="utf-8")
    assert mod.main(["--check", "--out", str(drifted)]) == 2


def test_check_fails_when_absent(tmp_path):
    mod = _load()
    assert mod.main(["--check", "--out", str(tmp_path / "missing.json")]) == 2
