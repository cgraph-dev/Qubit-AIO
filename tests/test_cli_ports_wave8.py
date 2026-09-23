"""Packet W4 CLI-port wave 8 — ``plugins validate``, ported from the read-only
reference 0.21.4 onto the VENDORED 0.21.1 model (no stubs, no catalog model
invented):

* parser  — reference ``hermes_cli/subcommands/plugins.py:54``;
  vendored ``qubit_cli/subcommands/plugins.py`` (``validate <path>`` with
  ``--install-deps`` + ``--json``);
* handler — reference ``hermes_cli/plugins_cmd_catalog.py::cmd_validate``;
  vendored ``qubit_cli/plugins_cmd.py::cmd_validate_plugin`` →
  ``qubit_cli/plugin_validate.py::validate_plugin_dir``;
* desktop lint — reference ``hermes_cli/plugin_validate_desktop.py`` →
  vendored ``qubit_cli/plugin_validate_desktop.py``.

The proof is behavioral and hermetic. Real execution runs through
``python -m voicebridge`` in a sandboxed subprocess (temp ``HOME`` /
``QUBIT_HOME``) so the vendored tree and the operator's live home are never
touched; the parser/validation tests import the vendored modules directly with
``QUBIT_HOME`` redirected to a temp dir.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "qubit_runtime"

BASE_MANIFEST = {
    "name": "fixture-plugin",
    "version": "1.0.0",
    "description": "A fixture plugin.",
}


# ---------------------------------------------------------------------------
# Sandbox helpers (mirror tests/test_cli_ports_wave6.py / wave7.py)
# ---------------------------------------------------------------------------
def _sandbox_env(home: Path, qubit_home: Path) -> dict:
    for d in (home, qubit_home):
        d.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = home / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        env[var] = str(target)
    env["QUBIT_HOME"] = str(qubit_home)
    env.pop("PYTHONPATH", None)
    return env


def _run_agent(tmp_path, argv, *, home=None, qubit_home=None, timeout=180):
    """Run the production ``qubit agent <argv>`` passthrough in a scratch HOME."""
    home = home or (tmp_path / "home")
    qubit_home = qubit_home or (home / ".hermes")
    return subprocess.run(
        [sys.executable, "-m", "voicebridge", "agent", *argv],
        cwd=str(REPO_ROOT), env=_sandbox_env(home, qubit_home),
        capture_output=True, text=True, timeout=timeout)


@pytest.fixture(autouse=True)
def _vendored_import(tmp_path, monkeypatch):
    """Put the vendored fork on sys.path and redirect QUBIT_HOME to a temp dir."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qh"))
    if str(RUNTIME_ROOT) not in sys.path:
        sys.path.insert(0, str(RUNTIME_ROOT))
    yield


def _make_plugin(tmp_path: Path, *, manifest: dict, init_py: str | None = "def register(ctx):\n    pass\n") -> Path:
    d = tmp_path / manifest.get("name", "fixture-plugin")
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    if init_py is not None:
        (d / "__init__.py").write_text(init_py, encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# 1. Parser surface (production passthrough)
# ---------------------------------------------------------------------------
def test_plugins_help_lists_validate(tmp_path):
    r = _run_agent(tmp_path, ["plugins", "--help"])
    assert r.returncode == 0, r.stderr[-600:]
    assert "validate" in r.stdout, r.stdout[:800]


def test_plugins_validate_help_is_a_real_parser_choice(tmp_path):
    r = _run_agent(tmp_path, ["plugins", "validate", "--help"])
    assert r.returncode == 0, (r.returncode, r.stderr[-600:])
    combined = r.stdout + r.stderr
    assert "invalid choice" not in combined, combined[-400:]
    assert "usage: qubit plugins validate" in r.stdout, r.stdout[:400]
    assert "--json" in r.stdout and "--install-deps" in r.stdout, r.stdout[:600]


# ---------------------------------------------------------------------------
# 2. validate_plugin_dir — real behavior on TEMP plugin dirs
# ---------------------------------------------------------------------------
def test_valid_plugin_dir_passes(tmp_path):
    from qubit_cli.plugin_validate import validate_plugin_dir

    manifest = dict(BASE_MANIFEST, provides_tools=["good_tool"])
    d = _make_plugin(
        tmp_path, manifest=manifest,
        init_py=("def register(ctx):\n"
                 "    ctx.register_tool('good_tool', schema={}, handler=lambda **kw: None)\n"),
    )
    report = validate_plugin_dir(d)
    assert report.ok, report.failures
    assert ("manifest fields", True, "name, version, description present") in report.checks


def test_malformed_manifest_fails_with_exact_error(tmp_path):
    from qubit_cli.plugin_validate import validate_plugin_dir

    d = _make_plugin(
        tmp_path,
        manifest={"name": "bad-plugin", "version": "1.0.0", "requires_env": ["lowercase_name"]},
        init_py=None,
    )
    report = validate_plugin_dir(d)
    assert not report.ok
    assert any(
        f == "plugin.yaml missing required field(s): description" for f in report.failures
    ), report.failures
    assert any("lowercase_name" in f and "UPPER_SNAKE" in f for f in report.failures), report.failures
    assert report.exit_code == 1


def test_json_shape_is_stable(tmp_path):
    from qubit_cli.plugin_validate import validate_plugin_dir

    d = _make_plugin(tmp_path, manifest=dict(BASE_MANIFEST))
    payload = validate_plugin_dir(d).to_dict()
    assert set(payload) == {"ok", "checks", "warnings"}
    assert isinstance(payload["ok"], bool)
    assert payload["checks"] and all(set(c) == {"name", "ok", "detail"} for c in payload["checks"])
    assert isinstance(payload["warnings"], list)


def test_undeclared_tool_registration_fails(tmp_path):
    from qubit_cli.plugin_validate import validate_plugin_dir

    d = _make_plugin(
        tmp_path, manifest=dict(BASE_MANIFEST, name="sneaky"),
        init_py=("def register(ctx):\n"
                 "    ctx.register_tool('sneaky_tool', schema={}, handler=lambda **kw: None)\n"),
    )
    report = validate_plugin_dir(d)
    assert not report.ok
    joined = " ".join(report.failures)
    assert "sneaky_tool" in joined and "undeclared" in joined.lower()


def test_crashing_register_is_contained(tmp_path):
    from qubit_cli.plugin_validate import validate_plugin_dir

    d = _make_plugin(
        tmp_path, manifest=dict(BASE_MANIFEST, name="crash"),
        init_py="def register(ctx):\n    raise RuntimeError('boom')\n")
    report = validate_plugin_dir(d)  # must not raise
    assert not report.ok
    assert any("boom" in f or "register()" in f for f in report.failures), report.failures


def test_desktop_surface_violation_fails(tmp_path):
    from qubit_cli.plugin_validate import validate_plugin_dir
    from qubit_cli.plugin_validate_desktop import desktop_surface_hits, is_desktop_surface

    d = _make_plugin(tmp_path, manifest=dict(BASE_MANIFEST, name="desk"), init_py=None)
    (d / "desktop").mkdir()
    (d / "desktop" / "plugin.js").write_text(
        "Storage.prototype.setItem = function () {}\n", encoding="utf-8")
    report = validate_plugin_dir(d)
    failed = {name: detail for name, ok, detail in report.checks if not ok}
    assert "desktop surface" in failed
    assert "prototype patching (desktop/plugin.js:1)" in failed["desktop surface"]
    assert desktop_surface_hits(d) == ["prototype patching (desktop/plugin.js:1)"]
    assert is_desktop_surface("desktop/plugin.js") and not is_desktop_surface("sidecar/x.mjs")


# ---------------------------------------------------------------------------
# 3. End-to-end through the production CLI (rc + exact error text)
# ---------------------------------------------------------------------------
def test_cli_validates_a_temp_plugin_rc0(tmp_path):
    home = tmp_path / "home"
    plugin = _make_plugin(
        tmp_path, manifest=dict(BASE_MANIFEST, name="cli-good", provides_tools=["good_tool"]),
        init_py=("def register(ctx):\n"
                 "    ctx.register_tool('good_tool', schema={}, handler=lambda **kw: None)\n"))
    r = _run_agent(tmp_path, ["plugins", "validate", str(plugin)],
                   home=home, qubit_home=home / "qh")
    assert r.returncode == 0, (r.returncode, r.stdout[-500:], r.stderr[-400:])
    assert "Validation passed." in r.stdout, r.stdout[-500:]


def test_cli_rejects_a_malformed_manifest_rc1(tmp_path):
    home = tmp_path / "home"
    plugin = _make_plugin(
        tmp_path,
        manifest={"name": "cli-bad", "version": "1.0.0", "requires_env": ["lowercase_name"]},
        init_py=None)
    r = _run_agent(tmp_path, ["plugins", "validate", str(plugin)],
                   home=home, qubit_home=home / "qh")
    assert r.returncode == 1, (r.returncode, r.stdout[-500:], r.stderr[-400:])
    combined = r.stdout + r.stderr
    assert "plugin.yaml missing required field(s): description" in combined, combined[-600:]
    assert "UPPER_SNAKE_CASE" in combined, combined[-600:]
    assert "Validation failed." in combined, combined[-400:]


def test_cli_json_output_is_stable_and_exits_nonzero(tmp_path):
    home = tmp_path / "home"
    plugin = _make_plugin(
        tmp_path,
        manifest={"name": "cli-bad-json", "version": "1.0.0"},
        init_py=None)
    r = _run_agent(tmp_path, ["plugins", "validate", str(plugin), "--json"],
                   home=home, qubit_home=home / "qh")
    assert r.returncode == 1, (r.returncode, r.stdout[-400:], r.stderr[-400:])
    payload = json.loads(r.stdout)
    assert set(payload) == {"ok", "checks", "warnings"}
    assert payload["ok"] is False
    assert all(set(c) == {"name", "ok", "detail"} for c in payload["checks"])
    assert any(c["name"] == "manifest fields" and not c["ok"] for c in payload["checks"])
