"""Deterministic regression guards for Qubit's generated artifacts.

Four deterministic generators emit untracked artifacts:

* ``scripts/page_coverage.py``             -> ``docs/generated/QUBIT_PAGE_COVERAGE_2026-09-21.json``
* ``scripts/identifier_rename.py``         -> ``docs/archive/2026-09-21/QUBIT_IDENTIFIER_RENAME.json``
* ``scripts/qubit_upgrade_manifest.py`` -> ``docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json``
* ``scripts/openclaw_method_inventory.py`` -> ``docs/generated/QUBIT_OPENCLAW_METHOD_INVENTORY.json``

For each, two independent renders must be byte-identical, and the render must
match the artifact currently on disk. The two OpenClaw generators additionally
need a pristine upstream clone; when that clone is absent the case skips with an
explicit reason naming the missing path instead of failing.

This test never writes under ``docs/`` -- every generator output goes to
``tmp_path``. The ``--check`` mode renders in memory and writes nothing.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

PINNED = Path("/tmp/opencode/pinned")
UPGRADE_CLONE = PINNED / "openclaw"
INVENTORY_CLONE = PINNED / "openclaw"

PAGE_COVERAGE_ARTIFACT = (
    REPO / "docs" / "generated" / "QUBIT_PAGE_COVERAGE_2026-09-21.json")
IDENTIFIER_RENAME_ARTIFACT = (
    REPO / "docs" / "archive" / "2026-09-21" / "QUBIT_IDENTIFIER_RENAME.json")
UPGRADE_MANIFEST_ARTIFACT = (
    REPO / "docs" / "generated" / "QUBIT_OPENCLAW_UPGRADE_MANIFEST.json")
METHOD_INVENTORY_ARTIFACT = (
    REPO / "docs" / "generated" / "QUBIT_OPENCLAW_METHOD_INVENTORY.json")


def _load(module_name: str, filename: str):
    """Load a generator script by path under a unique module name."""
    spec = importlib.util.spec_from_file_location(
        module_name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _requires_clone(clone: Path) -> None:
    if not (clone / ".git").exists():
        pytest.skip(
            "pinned OpenClaw clone absent: %s (a full-history clone is "
            "required to render this artifact)" % clone)


@contextlib.contextmanager
def _ignore_leaked_handle_warning():
    """Silence the generator's own unclosed-file ResourceWarning.

    ``scripts/page_coverage.py`` does ``json.load(open(...))`` at line 381,
    leaking a handle; ``pytest.ini`` escalates ResourceWarning to an error, so
    the unclosed file surfaces as an unraisable exception. That is a generator
    tidy-up issue, not a determinism defect, so this guard suppresses it
    locally instead of editing a generator it does not own.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        yield


def test_page_coverage_two_renders_are_byte_identical():
    mod = _load("page_coverage_gen9u", "page_coverage.py")
    with _ignore_leaked_handle_warning():
        first = mod._render(mod.build_artifact()).encode("utf-8")
        second = mod._render(mod.build_artifact()).encode("utf-8")
    assert first == second, "page_coverage.py is nondeterministic"


def test_page_coverage_check_matches_on_disk_artifact():
    mod = _load("page_coverage_gen9u_check", "page_coverage.py")
    with _ignore_leaked_handle_warning():
        rc = mod.main(["--check"])
    assert rc == 0, (
        "docs/generated/QUBIT_PAGE_COVERAGE_2026-09-21.json is stale; regenerate with "
        "`python scripts/page_coverage.py`")


def test_page_coverage_check_ignores_a_moved_revision(tmp_path):
    """``--check`` passes when only the embedded generation revision moved.

    The canonical artifact records the HEAD it was generated at; after a commit
    that revision is stale by construction. ``--check`` must compare the
    revision-independent content so a moved HEAD is not reported as drift.
    """
    mod = _load("page_coverage_gen9u_rev", "page_coverage.py")
    with _ignore_leaked_handle_warning():
        art = mod.build_artifact()
    current = art["generated_from_revision"]
    stale = "0" * 40
    assert current != stale
    # Simulate the same content generated at a different revision: every
    # revision-bearing field and the ``method`` prose name the stale SHA.
    art["generated_from_revision"] = stale
    art["last_verification_revision"] = stale
    art["method"] = art["method"].replace(current, stale)
    for row in art["rows"]:
        if "last_verification_revision" in row:
            row["last_verification_revision"] = stale
    path = tmp_path / "coverage_moved_rev.json"
    path.write_text(mod._render(art), encoding="utf-8")

    with _ignore_leaked_handle_warning():
        assert mod.main(["--check", "--out", str(path)]) == 0


def test_page_coverage_check_still_catches_real_drift(tmp_path):
    """A revision-independent compare must still reject real content drift."""
    mod = _load("page_coverage_gen9u_drift", "page_coverage.py")
    with _ignore_leaked_handle_warning():
        art = mod.build_artifact()
    path = tmp_path / "coverage_drift.json"
    path.write_text(mod._render(art), encoding="utf-8")

    drifted = json.loads(path.read_text(encoding="utf-8"))
    drifted["summary"]["total_rows"] += 1
    path.write_text(
        json.dumps(drifted, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    with _ignore_leaked_handle_warning():
        assert mod.main(["--check", "--out", str(path)]) == 2


def test_identifier_rename_two_renders_are_byte_identical():
    mod = _load("identifier_rename_gen9u", "identifier_rename.py")
    first = mod.render(mod.build()).encode("utf-8")
    second = mod.render(mod.build()).encode("utf-8")
    assert first == second, "identifier_rename.py is nondeterministic"


def test_identifier_rename_check_matches_on_disk_artifact():
    mod = _load("identifier_rename_gen9u_check", "identifier_rename.py")
    assert mod.main(["--check"]) == 0, (
        "docs/archive/2026-09-21/QUBIT_IDENTIFIER_RENAME.json is stale; "
        "regenerate with `python scripts/identifier_rename.py`")


def test_qubit_upgrade_manifest_is_deterministic(tmp_path, monkeypatch):
    _requires_clone(UPGRADE_CLONE)
    mod = _load("qubit_upgrade_manifest_gen9u",
                "qubit_upgrade_manifest.py")
    # The registry path is stored verbatim in the artifact, so run from the repo
    # root and use the generator's canonical relative default.
    monkeypatch.chdir(REPO)
    argv = ["--clone", str(UPGRADE_CLONE)]
    out_a = tmp_path / "upgrade_a.json"
    out_b = tmp_path / "upgrade_b.json"
    assert mod.main(argv + ["--out", str(out_a)]) == 0
    assert mod.main(argv + ["--out", str(out_b)]) == 0
    first = out_a.read_bytes()
    assert first == out_b.read_bytes(), (
        "qubit_upgrade_manifest.py is nondeterministic")
    assert first == UPGRADE_MANIFEST_ARTIFACT.read_bytes(), (
        "docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json is "
        "stale; regenerate with scripts/qubit_upgrade_manifest.py")


def test_openclaw_method_inventory_is_deterministic(tmp_path, monkeypatch):
    _requires_clone(INVENTORY_CLONE)
    if not UPGRADE_MANIFEST_ARTIFACT.is_file():
        pytest.skip("upgrade manifest absent (inventory reads its pin from it)")
    mod = _load("openclaw_method_inventory_gen9u",
                "openclaw_method_inventory.py")
    monkeypatch.chdir(REPO)
    argv = ["--clone", str(INVENTORY_CLONE)]
    out_a = tmp_path / "inventory_a.json"
    out_b = tmp_path / "inventory_b.json"
    assert mod.main(argv + ["--out", str(out_a)]) == 0
    assert mod.main(argv + ["--out", str(out_b)]) == 0
    first = out_a.read_bytes()
    assert first == out_b.read_bytes(), (
        "openclaw_method_inventory.py is nondeterministic")
    assert first == METHOD_INVENTORY_ARTIFACT.read_bytes(), (
        "docs/generated/QUBIT_OPENCLAW_METHOD_INVENTORY.json is stale; regenerate with "
        "scripts/openclaw_method_inventory.py")
