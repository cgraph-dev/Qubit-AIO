"""A1 — integrity of the vendored Office art tree.

The vendored tree is written to TWO locations (see `scripts/vendor_office_assets.py`):

* `frontend/qubit-web/public/plugins/qubit-office/assets/` — the durable,
  git-tracked source the dashboard build copies into dist; and
* `frontend/qubit-dist/plugins/qubit-office/assets/` — the served copy, reachable
  by the browser at `/plugins/qubit-office/assets/<path>`
  (`90-CORRECTIONS.md` §C1: the app tree 404s a PNG; the dist tree serves it).

These tests assert BOTH trees are exactly what the vendoring script produces, so a
manual edit to a staged PNG fails CI instead of silently diverging:

* every path in each `MANIFEST.json` exists and hashes as recorded;
* no file under `assets/` is absent from the manifest;
* the run is reproducible (the script's plan == what is on disk);
* no celebrity/meme sprite family is present (owner decision O1/C4);
* the vendored tree is under the 100 MB cap.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Import the vendoring module's plan so the test and the script cannot drift.
_VENDOR_PATH = REPO / "scripts" / "vendor_office_assets.py"


def _load_vendor():
    spec = importlib.util.spec_from_file_location("vendor_office_assets", _VENDOR_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VENDOR = _load_vendor()

#: Both written trees: (label, assets root).
TREES = [(r.relative_to(REPO).as_posix(), r) for r in VENDOR.TARGETS]

#: The served tree the browser actually hits.
SERVED_ROOT = VENDOR.ASSET_ROOT

#: Families that must NEVER appear — celebrity/meme cosplay sets (C4/O1).
FORBIDDEN_FAMILIES = [
    "approved_elonmusk",
    "approved_harrypotter",
    "approved_heisenberg",
    "approved_xenomorph",
    "approved_samaltman",
    "approved_tonymontana",
    "approved_tylerdurden",
    "approved_ricksanchez",
    "approved_pepe",
    "approved_masterchief",
    "approved_morpheus",
    "approved_catwoman",
    "approved_ghostface",
    "approved_robocop",
    "approved_ultron",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def served_manifest() -> dict:
    mp = SERVED_ROOT / "MANIFEST.json"
    assert mp.is_file(), f"missing {mp} — run scripts/vendor_office_assets.py"
    return json.loads(mp.read_text(encoding="utf-8"))


def test_both_trees_exist():
    for label, root in TREES:
        assert root.is_dir(), f"vendored asset tree absent: {label}"


@pytest.mark.parametrize("label,root", TREES, ids=[t[0] for t in TREES])
def test_manifest_schema(label, root):
    m = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    assert m["schema"] == "qubit-office-assets/1"
    assert m["asset_base_url"] == "/plugins/qubit-office"
    assert m["file_count"] == len(m["files"])
    assert m["file_count"] > 0


@pytest.mark.parametrize("label,root", TREES, ids=[t[0] for t in TREES])
def test_every_manifest_file_exists_and_hashes(label, root):
    """The core contract: recorded path exists and matches its recorded sha256."""
    m = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    bad = []
    for entry in m["files"]:
        f = root / entry["path"]
        if not f.is_file():
            bad.append(f"missing: {entry['path']}")
            continue
        if _sha256(f) != entry["sha256"]:
            bad.append(f"hash mismatch: {entry['path']}")
        elif f.stat().st_size != entry["bytes"]:
            bad.append(f"size mismatch: {entry['path']}")
    assert not bad, f"{label} diverges from manifest:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("label,root", TREES, ids=[t[0] for t in TREES])
def test_no_unlisted_files_present(label, root):
    """Any file under assets/ not in the manifest is a drop or an unnoticed add."""
    m = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    listed = {e["path"] for e in m["files"]}
    on_disk = {
        f.relative_to(root).as_posix()
        for f in root.rglob("*")
        if f.is_file() and f.name != "MANIFEST.json"
    }
    extra = on_disk - listed
    assert not extra, f"{label}: unlisted files: {sorted(extra)[:20]}"


def test_reproducible_from_script_plan(served_manifest):
    """Each on-disk tree equals exactly what the vendoring script would produce."""
    refs = VENDOR.DEFAULT_REFS
    if not refs.is_dir():
        pytest.skip(f"refs staging absent: {refs}")
    plan = VENDOR.build_plan(refs)
    expected = {dest for _, dest in plan}
    expected_bytes = sum(s.stat().st_size for s, _ in plan)
    for label, root in TREES:
        actual = {
            f.relative_to(root).as_posix()
            for f in root.rglob("*")
            if f.is_file() and f.name != "MANIFEST.json"
        }
        assert actual == expected, (
            f"{label}: only in plan: {sorted(expected - actual)[:20]}; "
            f"only on disk: {sorted(actual - expected)[:20]}"
        )
    assert served_manifest["total_bytes"] == expected_bytes


def test_no_celebrity_families_present(served_manifest):
    """Owner decision O1/C4 — neutral families only, no meme/cosplay skins."""
    paths = " ".join(e["path"] for e in served_manifest["files"])
    for family in FORBIDDEN_FAMILIES:
        assert family not in paths, f"forbidden sprite family vendored: {family}"


def test_only_approved_neutral_sprite_families(served_manifest):
    """Every vendored sprite dir is on the neutral allowlist."""
    allowed = set(VENDOR.SPRITE_FAMILIES)
    seen = set()
    for entry in served_manifest["files"]:
        if entry["path"].startswith("sprites/"):
            seen.add(entry["path"].split("/")[1])
    assert seen, "no sprite families vendored at all"
    unexpected = seen - allowed
    assert not unexpected, f"unexpected sprite families: {sorted(unexpected)}"
    assert seen == allowed, f"missing families: {sorted(allowed - seen)}"


def test_under_size_cap(served_manifest):
    """Owner decision: the vendored tree stays under the 100 MB cap."""
    assert served_manifest["total_bytes"] <= VENDOR.MAX_TOTAL_BYTES
    assert served_manifest["total_bytes"] < 50 * 1024 * 1024


def test_durable_public_tree_not_gitignored():
    """The durable tree must be committed so a rebuild does not drop the art.

    `frontend/qubit-dist/` is a regenerable build output (gitignored), so the
    git-tracked `frontend/qubit-web/public/plugins/...` tree is what survives; a
    rebuild copies it into dist. If THAT tree were ignored, the vendored art
    would vanish on the next `build_dashboard.sh`.
    """
    import subprocess

    durable = VENDOR.TARGETS[0]  # public/plugins/qubit-office/assets
    rel = durable.relative_to(REPO).as_posix()
    proc = subprocess.run(
        ["git", "check-ignore", "-q", f"{rel}/MANIFEST.json"],
        cwd=str(REPO),
        capture_output=True,
    )
    # git check-ignore -q exits 1 when NOT ignored (good), 0 when ignored (bad).
    assert proc.returncode != 0, f"{rel}/ is excluded by .gitignore"
