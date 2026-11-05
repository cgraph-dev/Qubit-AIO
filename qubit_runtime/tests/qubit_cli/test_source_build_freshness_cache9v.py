"""The TUI freshness verdict is reused while its inputs are unchanged.

Opening a dashboard chat (new or resumed) resolves the TUI argv on the request
path. That used to spawn Node to hash the compiler inputs every time; the
in-process cache now reuses the compiler's answer while a cheap fingerprint of
the watched sources and the receipt is unchanged, and still asks the compiler
whenever something is actually newer than the receipt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from qubit_cli import main_tui_launch, source_build


def _checkout(tmp_path: Path) -> Path:
    """A source checkout that looks buildable (so it is not a packaged bundle)."""
    root = tmp_path / "checkout"
    (root / "scripts" / "build").mkdir(parents=True)
    (root / "scripts" / "build" / "tui.mjs").write_text("// build\n", encoding="utf-8")
    (root / "scripts" / "build" / "freshness.mjs").write_text("// probe\n", encoding="utf-8")
    src = root / "ui-tui" / "src"
    src.mkdir(parents=True)
    (src / "entry.tsx").write_text("console.log('src')\n", encoding="utf-8")
    dist = root / "ui-tui" / "dist"
    dist.mkdir(parents=True)
    (dist / "entry.js").write_text("console.log('built')\n", encoding="utf-8")
    return root


def _receipt(root: Path, *, node: str = "22.0.0") -> Path:
    dist = root / "ui-tui" / "dist"
    receipt = dist / "hermes-build.json"
    receipt.write_text(json.dumps({
        "schema": 1, "product": "tui", "platform": "linux", "arch": "x64", "node": node,
        "inputs": {"sourceHash": "0" * 64, "prepared": []}, "outputHash": "0" * 64,
    }) + "\n", encoding="utf-8")
    return receipt


def _older_than_receipt(root: Path) -> None:
    """Make every watched source older than the receipt (the steady state)."""
    receipt = root / "ui-tui" / "dist" / "hermes-build.json"
    stamp = receipt.stat().st_mtime - 10
    for path in (root / "ui-tui" / "src").rglob("*"):
        if path.is_file():
            os.utime(path, (stamp, stamp))
    entry = root / "ui-tui" / "dist" / "entry.js"
    os.utime(entry, (stamp, stamp))


@pytest.fixture
def probes(monkeypatch):
    """Record every authoritative compiler probe and let the test set its answer."""
    calls: list[str] = []
    answer = {"current": True}

    def fake_probe(project_root, product, out):
        calls.append(product)
        return answer["current"]

    monkeypatch.setattr(source_build, "_probe_source_product_current", fake_probe)
    monkeypatch.delenv("QUBIT_SOURCE_FRESHNESS_TTL_S", raising=False)
    monkeypatch.delenv("HERMES_SOURCE_FRESHNESS_TTL_S", raising=False)
    # Isolate the cache between tests; the module is process-global.
    source_build._freshness_cache.clear()
    yield calls, answer
    source_build._freshness_cache.clear()


def test_a_current_receipt_is_reused_without_spawning_the_probe(tmp_path, probes):
    calls, _answer = probes
    root = _checkout(tmp_path)
    _receipt(root)
    _older_than_receipt(root)
    dist = root / "ui-tui" / "dist"

    assert source_build.source_product_current(root, "tui", dist) is True
    assert calls == ["tui"], "the first verdict must come from the compiler"

    # Same receipt, same watched inputs: the compiler is not asked again.
    for _ in range(5):
        assert source_build.source_product_current(root, "tui", dist) is True
    assert calls == ["tui"]


def test_a_newer_source_file_still_asks_the_compiler(tmp_path, probes):
    calls, answer = probes
    root = _checkout(tmp_path)
    _receipt(root)
    _older_than_receipt(root)
    dist = root / "ui-tui" / "dist"

    assert source_build.source_product_current(root, "tui", dist) is True
    assert calls == ["tui"]

    # A source edit changes the fingerprint: the compiler gets the final say.
    source = root / "ui-tui" / "src" / "entry.tsx"
    source.write_text("console.log('edited')\n", encoding="utf-8")
    answer["current"] = False
    assert source_build.source_product_current(root, "tui", dist) is False
    assert calls == ["tui", "tui"]


def test_a_rebuilt_receipt_is_never_served_from_the_old_verdict(tmp_path, probes):
    calls, answer = probes
    root = _checkout(tmp_path)
    _receipt(root)
    _older_than_receipt(root)
    dist = root / "ui-tui" / "dist"

    assert source_build.source_product_current(root, "tui", dist) is True
    answer["current"] = False
    # A build rewrote the receipt (new mtime/size) but the sources are unchanged.
    _receipt(root, node="22.0.0")
    os.utime(root / "ui-tui" / "dist" / "hermes-build.json", None)
    assert source_build.source_product_current(root, "tui", dist) is False
    assert calls == ["tui", "tui"]


def test_cache_can_be_disabled_with_the_environment(tmp_path, probes, monkeypatch):
    calls, _answer = probes
    monkeypatch.setenv("QUBIT_SOURCE_FRESHNESS_TTL_S", "0")
    root = _checkout(tmp_path)
    _receipt(root)
    _older_than_receipt(root)
    dist = root / "ui-tui" / "dist"

    assert source_build.source_product_current(root, "tui", dist) is True
    assert source_build.source_product_current(root, "tui", dist) is True
    assert calls == ["tui", "tui"]


def test_current_tui_launch_skips_dependency_prep_and_build(tmp_path, probes, monkeypatch):
    calls, _answer = probes
    root = _checkout(tmp_path)
    _receipt(root)
    _older_than_receipt(root)
    tui_dir = root / "ui-tui"

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("a current TUI must not prepare dependencies or rebuild")

    monkeypatch.setattr(source_build, "prepare_launch_dependencies", _forbidden)
    monkeypatch.setattr(source_build, "build_source_tui", _forbidden)
    monkeypatch.setattr(main_tui_launch, "_find_bundled_tui", lambda *_a, **_k: None)
    monkeypatch.setattr(main_tui_launch, "_tui_node_bin", lambda _bin: "node")
    monkeypatch.delenv("HERMES_TUI_DIR", raising=False)
    monkeypatch.delenv("HERMES_TUI_FORCE_BUILD", raising=False)

    argv, cwd = main_tui_launch._make_tui_argv(tui_dir, tui_dev=False)

    assert cwd == tui_dir
    assert argv == ["node", "--expose-gc", str(tui_dir / "dist" / "entry.js")]
    assert calls == ["tui"]


def test_stale_tui_launch_still_prepares_and_rebuilds(tmp_path, probes, monkeypatch):
    calls, answer = probes
    root = _checkout(tmp_path)
    _receipt(root)
    _older_than_receipt(root)
    tui_dir = root / "ui-tui"
    dist = tui_dir / "dist"
    events: list[str] = []

    # Warm the cache with a current verdict, then edit a source file. The edit
    # must miss the cache and let the compiler report the dist stale.
    assert source_build.source_product_current(root, "tui", dist) is True
    (root / "ui-tui" / "src" / "entry.tsx").write_text("console.log('edited')\n", encoding="utf-8")
    answer["current"] = False
    monkeypatch.setattr(source_build, "source_build_env", lambda **_k: {"PATH": os.environ["PATH"]})
    monkeypatch.setattr(
        source_build, "prepare_launch_dependencies",
        lambda project_root, env=None: events.append("deps"))
    monkeypatch.setattr(
        source_build, "build_source_tui",
        lambda project_root, *, env: (events.append("tui"),
                                      (dist / "entry.js").write_text("fresh\n", encoding="utf-8")))
    monkeypatch.setattr(main_tui_launch, "_find_bundled_tui", lambda *_a, **_k: None)
    monkeypatch.setattr(main_tui_launch, "_tui_node_bin", lambda _bin: "node")
    monkeypatch.delenv("HERMES_TUI_DIR", raising=False)
    monkeypatch.delenv("HERMES_TUI_FORCE_BUILD", raising=False)

    argv, cwd = main_tui_launch._make_tui_argv(tui_dir, tui_dev=False)

    assert events == ["deps", "tui"]
    assert (dist / "entry.js").read_text(encoding="utf-8") == "fresh\n"
    assert argv[-1] == str(dist / "entry.js")
    assert "--expose-gc" in argv
    assert calls == ["tui", "tui"]
