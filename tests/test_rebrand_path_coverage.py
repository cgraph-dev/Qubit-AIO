# tests/test_rebrand_path_coverage.py
"""Engine path-rename coverage for the curated rebrand manifest rows.

The rebrand engine historically renamed only ``^hermes_<module>`` basenames.
The curated rows in the frozen rebrand manifest also rename camel / hyphen /
product basenames (``HermesConsoleModal.tsx``, ``hermes-exec-shim.sh``,
``hermes-ink``, ...).  These tests pin that coverage with an *explicit* expected
set -- never an "included or intentionally skipped" tautology.
"""
import importlib.util
import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = REPO / "docs" / "archive" / "2026-09-21" / "QUBIT_REBRAND_MANIFEST.json"


def _engine():
    p = REPO / "scripts" / "qubit_rebrand.py"
    s = importlib.util.spec_from_file_location("qb_rebrand_path_cov", p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def _manifest_basename_rows():
    """``{old_basename: new_basename}`` curated by the frozen manifest.

    Only rows whose two sides split into the same number of ``/``-separated
    segments are used; parenthetical notes and ``->`` symlink descriptions are
    stripped.  No-ops (``shared`` -> ``shared``) are dropped.
    """
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = {}
    for row in data.get("path_renames", []):
        old_parts = _clean(row["old"]).split(" / ")
        new_parts = _clean(row["new"]).split(" / ")
        if not old_parts or len(old_parts) != len(new_parts):
            continue
        for o, n in zip(old_parts, new_parts):
            ob = o.rsplit("/", 1)[-1].strip()
            nb = n.rsplit("/", 1)[-1].strip()
            if ob and nb and ob != nb:
                rows.setdefault(ob, nb)
    return rows


def _clean(value: str) -> str:
    value = value.split(" -> ")[0]
    value = re.sub(r"\s*\([^)]*\)", "", value)
    return value.strip()


# ---------------------------------------------------------------------------
# Explicit expected set -- curated manifest rows whose OLD path still exists in
# this worktree: the engine must still plan these renames.
#
# Only two curated rows remain unrenamed here:
#   * ``scripts/rebuild_ledger_hermes.py`` (manifest generator row)
#   * ``tests/test_ledger_hermes_build.py`` (manifest test-file row)
#
# The other curated rows were already applied when the current tree was
# rebranded, so their old paths do not exist and are excluded from this
# assertion.  The pinned upstream source still carries the camel/hyphen/product
# basenames; those are covered by the fixture test below.
CURRENT_TREE_EXPECTED = {
    "scripts/rebuild_ledger_hermes.py": "scripts/rebuild_ledger_qubit.py",
    "tests/test_ledger_hermes_build.py": "tests/test_ledger_qubit_build.py",
}

# Curated manifest rows already applied in this worktree: the canonical name
# exists and the legacy name is gone.  ``hermes.png`` was renamed to the
# canonical ``qubit.png`` when the tree was rebranded (the frozen manifest still
# carries the ``hermes.png -> qubit.png`` row), so it is pinned here rather than
# in the still-pending set above.
CURRENT_TREE_APPLIED = {
    "qubit_runtime/apps/desktop/public/qubit.png":
        "qubit_runtime/apps/desktop/public/hermes.png",
}

# Basenames present in the pinned upstream source (v0.21.5) that the curated
# manifest maps to Qubit names.  Staged into a fixture for a behavioural check
# of the camel/hyphen/product rules.  ``hermes-kanban-*`` and other preserved
# wire/service ids are deliberately absent (never renamed).
FIXTURE_RENAMES = [
    ("file", "docker/hermes-exec-shim.sh", "docker/qubit-exec-shim.sh"),
    ("file", "nix/hermes-agent.nix", "nix/qubit-runtime.nix"),
    ("file", "ui-tui/src/types/hermes-ink.d.ts", "ui-tui/src/types/qubit-ink.d.ts"),
    ("dir", "ui-tui/packages/hermes-ink", "ui-tui/packages/qubit-ink"),
    ("file", "web/src/components/HermesConsoleModal.tsx",
             "web/src/components/QubitConsoleModal.tsx"),
    ("file", "apps/desktop/public/hermes.png", "apps/desktop/public/qubit.png"),
    ("file", "tests/fixtures/differential/cli/vendor_hermes_constants.json",
             "tests/fixtures/differential/cli/vendor_qubit_constants.json"),
    ("file", "tests/fixtures/differential/session/vendor_hermes_state_common.json",
             "tests/fixtures/differential/session/vendor_qubit_state_common.json"),
]


def test_curated_renames_planned_for_existing_paths():
    engine = _engine()
    curated = _manifest_basename_rows()
    plan = engine.build_plan()
    planned = {(r["old"], r["new"]) for r in plan["path_renames"]}

    missing = []
    for old, new in CURRENT_TREE_EXPECTED.items():
        assert (REPO / old).exists(), f"expected curated row missing from tree: {old}"
        # Prove the row is actually curated by the frozen manifest.
        assert curated.get(old.rsplit("/", 1)[-1]) == new.rsplit("/", 1)[-1], (
            f"{old} -> {new} is not a curated manifest basename row")
        if (old, new) not in planned:
            missing.append((old, new))
    assert not missing, f"engine did not plan curated renames: {missing}"

    # Already-applied curated rows: the canonical name is present, the legacy
    # name is gone, and the row is genuinely curated by the frozen manifest.
    for canonical, legacy in CURRENT_TREE_APPLIED.items():
        assert (REPO / canonical).exists(), (
            f"expected applied canonical path missing from tree: {canonical}")
        assert not (REPO / legacy).exists(), (
            f"legacy path still present after rename: {legacy}")
        assert curated.get(legacy.rsplit("/", 1)[-1]) == canonical.rsplit("/", 1)[-1], (
            f"{legacy} -> {canonical} is not a curated manifest basename row")


def test_curated_product_basenames_renamed_on_staged_source(tmp_path, monkeypatch):
    engine = _engine()
    top = tmp_path / "stage"
    package = top / "qubit_hermes"  # staged pin package name
    for kind, old_rel, _ in FIXTURE_RENAMES:
        target = package / old_rel
        if kind == "dir":
            target.mkdir(parents=True, exist_ok=True)
            (target / "placeholder.txt").write_text("x", encoding="utf-8")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x", encoding="utf-8")
    monkeypatch.setattr(engine, "REPO", top)

    plan = engine.build_plan()
    planned = {(r["old"], r["new"]) for r in plan["path_renames"]}

    missing = []
    for _, old_rel, new_rel in FIXTURE_RENAMES:
        old = f"qubit_hermes/{old_rel}"
        new = f"qubit_hermes/{new_rel}"
        if (old, new) not in planned:
            missing.append((old, new))
    assert not missing, f"engine did not rename curated product basenames: {missing}"
