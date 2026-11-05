"""Packaged TUI launch: bundled ``dist/entry.js`` and the product version env.

A fresh install ships ``_data/qubit_runtime/ui-tui/dist/entry.js`` without
``.git`` or ``scripts/build/tui.mjs``; the launcher must run that bundle and
never enter the source dependency/build path. A checkout that carries the
build script must still rebuild.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

from qubit_cli import main_tui_launch


def _write_entry(tui_dir: Path, text: str = "console.log('tui')") -> Path:
    entry = tui_dir / "dist" / "entry.js"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(text, encoding="utf-8")
    return entry


def _forbidden(*_args, **_kwargs):
    raise AssertionError("the packaged launch entered the source build path")


def test_tui_product_version_prefers_the_product_identity(monkeypatch):
    import voicebridge.product as product

    monkeypatch.setattr(product, "PRODUCT_VERSION", "9.9.9")
    assert main_tui_launch._tui_product_version() == "9.9.9"


def test_tui_product_version_falls_back_to_the_checkout_version(monkeypatch, tmp_path):
    import qubit_cli.main as main_mod

    monkeypatch.setitem(sys.modules, "voicebridge.product", None)
    runtime_root = tmp_path / "qubit_runtime"
    runtime_root.mkdir()
    (tmp_path / "VERSION").write_text("8.8.8\n", encoding="utf-8")
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", runtime_root)

    assert main_tui_launch._tui_product_version() == "8.8.8"


def test_tui_product_version_keeps_a_safe_placeholder(monkeypatch, tmp_path):
    import qubit_cli.main as main_mod

    monkeypatch.setitem(sys.modules, "voicebridge.product", None)
    runtime_root = tmp_path / "qubit_runtime"
    runtime_root.mkdir()
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", runtime_root)

    assert main_tui_launch._tui_product_version() == "0.0.1"


def test_packaged_data_layout_uses_the_bundled_entry_without_node_deps(monkeypatch, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")

    runtime_root = tmp_path / "_data" / "qubit_runtime"
    tui_dir = runtime_root / "ui-tui"
    entry = _write_entry(tui_dir, "console.log('packaged')")
    assert not (runtime_root / ".git").exists()
    assert not (runtime_root / "scripts" / "build" / "tui.mjs").exists()

    import qubit_cli.source_build as source_build

    for name in ("prepare_launch_dependencies", "prepare_source_dependencies", "build_source_tui"):
        monkeypatch.setattr(source_build, name, _forbidden)
    monkeypatch.setattr(main_tui_launch, "_ensure_tui_workspace", _forbidden)
    monkeypatch.setattr(main_tui_launch, "_tui_need_rebuild", _forbidden)
    monkeypatch.setattr(main_tui_launch, "_find_bundled_tui", lambda *_a, **_k: None)
    monkeypatch.delenv("HERMES_TUI_DIR", raising=False)
    monkeypatch.setenv("HERMES_NODE", node)

    argv, cwd = main_tui_launch._make_tui_argv(tui_dir, tui_dev=False)

    assert cwd == tui_dir
    assert argv == [node, "--expose-gc", str(entry)]
    assert "node-deps.mjs" not in " ".join(argv)


def test_checkout_with_the_build_script_still_rebuilds(monkeypatch, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")

    root = tmp_path / "checkout"
    tui_dir = root / "ui-tui"
    entry = _write_entry(tui_dir, "stale")
    build_script = root / "scripts" / "build" / "tui.mjs"
    build_script.parent.mkdir(parents=True)
    build_script.write_text("// source build entry\n", encoding="utf-8")
    assert not (root / ".git").exists()

    events = []
    import qubit_cli.source_build as source_build

    monkeypatch.setattr(source_build, "source_build_env", lambda **_k: {"PATH": os.environ["PATH"]})
    monkeypatch.setattr(
        source_build, "prepare_launch_dependencies",
        lambda project_root, env=None: events.append(("node-deps", project_root)))
    monkeypatch.setattr(
        source_build, "build_source_tui",
        lambda project_root, *, env: (events.append(("tui-build", project_root)),
                                      entry.write_text("fresh", encoding="utf-8")))
    monkeypatch.setattr(main_tui_launch, "_tui_need_rebuild", lambda _tui: True)
    monkeypatch.setattr(main_tui_launch, "_find_bundled_tui", lambda *_a, **_k: None)
    monkeypatch.delenv("HERMES_TUI_DIR", raising=False)
    monkeypatch.setenv("HERMES_NODE", node)

    argv, cwd = main_tui_launch._make_tui_argv(tui_dir, tui_dev=False)

    assert events == [("node-deps", root), ("tui-build", root)]
    assert entry.read_text(encoding="utf-8") == "fresh"
    assert argv[-1] == str(entry)
    assert cwd == tui_dir
