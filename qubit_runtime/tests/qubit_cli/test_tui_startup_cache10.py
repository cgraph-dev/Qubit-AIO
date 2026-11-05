"""TUI launch startup: Node compile cache, cached Node resolution, receipt Node drift.

The dashboard opens one Ink TUI per chat, so these seams sit directly on the
new/resumed-chat first-byte path. Each test stays hermetic: nothing spawns a
real TUI, and the compile-cache resolver writes no files (Node creates the
directory on first use).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from qubit_cli import main_tui_launch as launch


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_compile_cache_lands_under_the_home_and_writes_nothing(tmp_path):
    env = {"QUBIT_HOME": str(tmp_path)}
    launch._apply_tui_node_env(env)
    assert env["NODE_COMPILE_CACHE"] == str(tmp_path / "cache" / "node-compile-cache")
    # Node creates the directory (with parents) on first use; Python must not
    # leave a cache tree behind for a launch that never happens.
    assert not (tmp_path / "cache").exists()


def test_compile_cache_honours_an_operator_override(tmp_path):
    env = {"QUBIT_HOME": str(tmp_path), "NODE_COMPILE_CACHE": "/opt/cache"}
    launch._apply_tui_node_env(env)
    assert env["NODE_COMPILE_CACHE"] == "/opt/cache"


def test_compile_cache_reads_the_legacy_home_alias(tmp_path):
    env = {"HERMES_HOME": str(tmp_path)}
    launch._apply_tui_node_env(env)
    assert env["NODE_COMPILE_CACHE"] == str(tmp_path / "cache" / "node-compile-cache")


def test_tui_node_bin_reuses_one_pm_resolution(monkeypatch, tmp_path):
    import pm

    node = _executable(tmp_path / "node")
    calls: list[str] = []

    def ensure(name):
        calls.append(name)
        return type("Runner", (), {"env": {"PATH": "/pm/bin"}})()

    monkeypatch.setattr(pm, "ensure", ensure)
    monkeypatch.setattr(launch.shutil, "which", lambda name, path=None: str(node))
    monkeypatch.delenv("HERMES_NODE", raising=False)
    launch._reset_tui_node_bin_cache()

    assert launch._tui_node_bin("node") == str(node)
    assert launch._tui_node_bin("node") == str(node)
    assert calls == ["node"]

    # A removed tool path re-resolves instead of trusting the cached string.
    node.unlink()
    assert launch._tui_node_bin("node") == str(node)
    assert calls == ["node", "node"]


def test_explicit_hermes_node_wins_without_pm(monkeypatch, tmp_path):
    import pm

    node = _executable(tmp_path / "custom-node")
    called: list[str] = []
    monkeypatch.setattr(pm, "ensure", lambda name: called.append(name))
    monkeypatch.setenv("HERMES_NODE", str(node))
    launch._reset_tui_node_bin_cache()

    assert launch._tui_node_bin("node") == str(node)
    assert called == []


def test_dashboard_chat_env_carries_the_compile_cache(monkeypatch, tmp_path):
    from qubit_cli import web_server_chat

    monkeypatch.setenv("QUBIT_HOME", str(tmp_path))
    monkeypatch.setattr(launch, "_make_tui_argv", lambda *_a, **_k: (["node", "fake-tui.js"], tmp_path))
    monkeypatch.setattr(launch, "_apply_tui_python_env", lambda _env: None)

    _argv, _cwd, env = web_server_chat._resolve_chat_argv()
    assert env["NODE_COMPILE_CACHE"] == str(tmp_path / "cache" / "node-compile-cache")


def test_tui_receipt_survives_a_different_builder_node_but_web_does_not(tmp_path):
    """TUI is an esbuild bundle; a host Node upgrade must not force a rebuild.

    Desktop/web keep the exact-Node rule (their receipts bake renderer output
    that the running Node can influence).
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to exercise the freshness receipt")

    repo = Path(__file__).resolve().parents[2]
    freshness = (repo / "scripts" / "build" / "freshness.mjs").as_uri()
    source = tmp_path / "source"
    source.mkdir()
    tui_out = tmp_path / "tui"
    tui_out.mkdir()
    (tui_out / "entry.js").write_text("bundle", encoding="utf-8")
    web_out = tmp_path / "web"
    web_out.mkdir()
    (web_out / "index.html").write_text("bundle", encoding="utf-8")

    script = f"""
import {{ recordProduct, buildInputs, productCurrent }} from {json.dumps(freshness)};
import {{ readFileSync, writeFileSync }} from 'node:fs';
const source = {json.dumps(str(source))};
for (const [product, out] of [['tui', {json.dumps(str(tui_out))}], ['web', {json.dumps(str(web_out))}]]) {{
  recordProduct({{ source, product, out, inputs: buildInputs(source, product) }});
  const receipt = out + '/hermes-build.json';
  const saved = JSON.parse(readFileSync(receipt, 'utf8'));
  const current = () => productCurrent({{ source, product, out }});
  console.log(product, 'same', current());
  writeFileSync(receipt, JSON.stringify({{ ...saved, node: '0.0.0-other' }}));
  console.log(product, 'drift', current());
  writeFileSync(receipt, JSON.stringify(saved));
}}
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True, text=True, check=True,
    )
    verdicts = {
        tuple(line.rsplit(" ", 1))[0]: line.rsplit(" ", 1)[1] == "true"
        for line in result.stdout.splitlines() if line.strip()
    }
    assert verdicts["tui same"]
    assert verdicts["tui drift"]
    assert verdicts["web same"]
    assert not verdicts["web drift"]
