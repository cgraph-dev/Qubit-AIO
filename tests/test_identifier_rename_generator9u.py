"""Guard the deterministic identifier/file-rename artifact generator.

``scripts/identifier_rename.py`` reproduces
``docs/archive/2026-09-21/QUBIT_IDENTIFIER_RENAME.json`` from the worktree.

Both files it reads and writes were purged in ``eafb54e26`` along with the rest
of the archive's markdown/ledgers; they are never re-created in the tree.
Instead the generator is pointed at read-only copies recovered from
``eafb54e26^`` (via ``tests/_purged_ledger.py``) staged in ``tmp_path``, so the
assertions below still compare against the REAL archived ledger rather than a
hand-written stub:

* deterministic render (two in-memory renders are byte-identical),
* the render equals the archived artifact (structurally and byte-for-byte),
* ``--check`` passes against the archived artifact and fails closed on drift
  and on absence.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from _purged_ledger import PRE_PURGE_REF, read_pre_purge_text

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "docs/archive/2026-09-21/QUBIT_IDENTIFIER_RENAME.json"
LEDGER = REPO / "docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST.json"
GENERATOR = REPO / "scripts/identifier_rename.py"


def _recovered(rel_path: str, tmp_path: Path) -> Path:
    """Stage a live-or-recovered copy of a purged ledger under ``tmp_path``."""
    if (REPO / rel_path).is_file():
        return REPO / rel_path
    text = read_pre_purge_text(rel_path)
    if text is None:
        pytest.skip("%s is neither in the tree nor retrievable from %s"
                    % (rel_path, PRE_PURGE_REF))
    out = tmp_path / Path(rel_path).name
    out.write_text(text, encoding="utf-8")
    return out


def _load(tmp_path: Path):
    """Load the generator wired to live-or-recovered inputs and output path."""
    spec = importlib.util.spec_from_file_location("identifier_rename", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["identifier_rename"] = mod
    spec.loader.exec_module(mod)
    mod.LEDGER = _recovered(LEDGER.relative_to(REPO).as_posix(), tmp_path)
    mod.OUT = _recovered(COMMITTED.relative_to(REPO).as_posix(), tmp_path)
    return mod


def test_two_renders_are_byte_identical(tmp_path):
    mod = _load(tmp_path)
    assert mod.render(mod.build()) == mod.render(mod.build())


def test_committed_artifact_is_structurally_current(tmp_path):
    mod = _load(tmp_path)
    on_disk = json.loads(mod.OUT.read_text(encoding="utf-8"))
    assert on_disk == mod.build(), (
        "docs/archive/2026-09-21/QUBIT_IDENTIFIER_RENAME.json is stale; "
        "regenerate with `python scripts/identifier_rename.py`")


def test_committed_artifact_is_byte_identical(tmp_path):
    mod = _load(tmp_path)
    assert mod.OUT.read_text(encoding="utf-8") == mod.render(mod.build())


def test_check_passes_against_committed_artifact(tmp_path):
    mod = _load(tmp_path)
    assert mod.main(["--check"]) == 0


def test_check_fails_closed_on_drift(tmp_path):
    mod = _load(tmp_path)
    drifted = tmp_path / "QUBIT_IDENTIFIER_RENAME.json"
    drifted.write_text('{"file_renames": [], "js_files": [], "python_files": []}\n',
                       encoding="utf-8")
    assert mod.main(["--check", "--out", str(drifted)]) == 2


def test_check_fails_when_absent(tmp_path):
    mod = _load(tmp_path)
    assert mod.main(["--check", "--out", str(tmp_path / "missing.json")]) == 2
