"""W7 — regenerated voice-coverage report guard.

``scripts/voice_inventory_coverage.py`` resolves every parity row's
``voice_intent`` through the real ``voicebridge.voice_commands.resolve()``
pipeline and emits ``docs/generated/voice_coverage.json``. This test guards the
report's *relationships*, not exact counts (no change-detector):

* the report renders byte-identically across two in-process renders;
* a generated artifact matches a fresh render;
* no voice entry references a dead backend (``dead_backends == 0``);
* ``real + guard + unresolved == total`` per surface, overall, and per row.

Scope note: this file is intentionally distinct from the existing
``tests/test_voice_inventory_coverage.py`` (which asserts the W7 alias bindings);
this one only validates the regenerated artifact. ``docs/generated/`` is
gitignored, so a fresh worktree has no artifact: the parity rows are built by
the real ``scripts.parity_inventory.build_controls()`` and both the inventory
fixture and the coverage artifact are written under ``tmp_path``. Nothing in
the repository is written.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

RESOLUTIONS = ("real", "guard", "unresolved")


def _load():
    """Load the generator script by path under a unique module name."""
    name = "voice_inventory_coverage_w7"
    spec = importlib.util.spec_from_file_location(
        name, SCRIPTS / "voice_inventory_coverage.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


@pytest.fixture(scope="module")
def fixture_inventory(tmp_path_factory):
    """A real parity inventory rendered to a tmp path (no repo artifact)."""
    from scripts import parity_inventory as pi

    rows = pi.build_controls()
    assert rows, "parity inventory fixture is empty"
    path = tmp_path_factory.mktemp("generated") / "parity_inventory.json"
    path.write_text(
        json.dumps({"schema": pi.SCHEMA, "rows": rows},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def report(mod, fixture_inventory):
    return mod.render(fixture_inventory)


# ------------------------------------------------------- (a) determinism
def test_two_in_process_renders_are_byte_identical(mod, fixture_inventory):
    first = mod.dumps(mod.render(fixture_inventory))
    second = mod.dumps(mod.render(fixture_inventory))
    assert first == second, "voice coverage render is nondeterministic"


# --------------------------------------------- (b) generated artifact is fresh
def test_on_disk_artifact_matches_a_fresh_render(
        mod, report, fixture_inventory, tmp_path):
    out = tmp_path / "voice_coverage.json"
    rc = mod.main(["--inventory", str(fixture_inventory), "--out", str(out)])
    assert rc == 0
    assert out.is_file(), "generator did not write the coverage artifact"
    on_disk = out.read_text(encoding="utf-8")
    assert on_disk == mod.dumps(report), (
        "generated voice coverage artifact is stale")
    parsed = json.loads(on_disk)
    assert parsed["schema"] == mod.SCHEMA
    assert parsed["source"] == mod.DEFAULT_INVENTORY


# ------------------------------------------------- (c) no dead voice backends
def test_no_dead_voice_backends(report):
    registry = report["registry"]
    assert registry["dead_backends"] == 0, registry["dead_backend_ids"]
    assert registry["dead_backend_ids"] == []
    assert registry["bound_backends"] <= registry["total_entries"]


# --------------------------------- (d) resolution counts are consistent
def test_resolution_counts_are_internally_consistent(report):
    surfaces = report["surfaces"]
    summary = report["summary"]

    # Every surface bucket partitions exactly.
    for key, bucket in surfaces.items():
        assert sum(bucket[name] for name in RESOLUTIONS) == bucket["total"], (
            key, bucket)
        assert 0.0 <= bucket["real_pct"] <= 100.0, (key, bucket)

    # Overall partition, and overall == sum of surfaces.
    assert sum(summary[name] for name in RESOLUTIONS) == summary["rows"]
    for name in ("total", "real", "guard", "unresolved"):
        assert sum(b[name] for b in surfaces.values()) == summary[
            name if name != "total" else "rows"], name

    # Per-row classifications agree with the per-surface buckets.
    counts = Counter((row["surface"], row["resolution"])
                     for row in report["rows"])
    for key, bucket in surfaces.items():
        for name in RESOLUTIONS:
            assert counts[(key, name)] == bucket[name], (key, name, bucket)
    assert len(report["rows"]) == summary["rows"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
