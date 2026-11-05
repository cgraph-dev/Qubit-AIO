"""Hermetic tests for ``scripts/verify_fresh_install.py``.

These cover the verifier's parsing/reporting contract: wheel version parsing,
probe-marker decoding, version identity, leak scanning, install-command
selection, typed preflight failures, and the CLI report. The one real
build/install run is opt-in (``QUBIT_FRESH_INSTALL_E2E=1``) because it needs a
wheel (or a build backend + network) and a Node runtime.

Nothing here builds or installs by default, and no test touches the live
service, port 8890, the operator's real HOME/state, or another worktree.
"""
from __future__ import annotations

import importlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _gate():
    return importlib.import_module("scripts.verify_fresh_install")


def _fake_wheel(path: Path, *, dashboard: bool = True,
                node_modules: bool = False, version: str = "1.2.3") -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("voicebridge/__init__.py", b"")
        z.writestr(f"qubit_voicebridge-{version}.dist-info/METADATA",
                   f"Metadata-Version: 2.1\nName: qubit-voicebridge\nVersion: {version}\n"
                   .encode("utf-8"))
        if dashboard:
            z.writestr("voicebridge/_data/frontend/qubit-dist/index.html",
                       b"<html>dashboard</html>")
        if node_modules:
            z.writestr("voicebridge/_data/frontend/qubit-web/node_modules/x.js", b"")
    return path


def _probe_payload(**overrides):
    payload = {
        "installed": {
            "voicebridge": "/tmp/venv/lib/python3.14/site-packages/voicebridge/__init__.py",
            "runtime_root": "/tmp/venv/lib/python3.14/site-packages/voicebridge/_data/qubit_runtime",
            "distribution_version": "1.2.3",
        },
        "health": {"status": 200, "ok": True, "state": "DEGRADED",
                   "version": "qubit-1.2.3"},
        "dashboard": {
            "port": 43210, "redirect": "/chat", "chat_status": 200,
            "spa_shell": True, "asset": "assets/app.js", "asset_status": 200,
            "asset_bytes": 5000,
            "plugins": ["qubit-ops", "qubit-devices", "qubit-memory",
                        "qubit-reports", "qubit-approvals"],
            "plugins_status": 200,
        },
        "tui": {
            "entry": "/tmp/venv/lib/python3.14/site-packages/voicebridge/_data/"
                     "qubit_runtime/ui-tui/dist/entry.js",
            "entry_under_venv": True, "source_git": False,
            "packaged_argv": True, "launch_rc": 0,
            "launch_output": "qubit-tui: no TTY",
            "argv": ["/usr/bin/node", "--expose-gc", "entry.js"],
            "cwd": "/tmp/venv/lib/python3.14/site-packages/voicebridge/_data/"
                   "qubit_runtime/ui-tui",
        },
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# pure parsing / reporting
# ---------------------------------------------------------------------------
def test_parse_wheel_version():
    gate = _gate()
    assert gate.parse_wheel_version("qubit_voicebridge-0.0.5-py3-none-any.whl") == "0.0.5"
    assert gate.parse_wheel_version("/tmp/x/qubit_voicebridge-1.2.3-py3-none-any.whl") == "1.2.3"
    assert gate.parse_wheel_version("not-a-wheel.tar.gz") is None
    assert gate.parse_wheel_version("broken.whl") is None


def test_parse_probe_output_uses_last_well_formed_marker():
    gate = _gate()
    text = "\n".join([
        "noise before",
        gate.PROBE_MARKER + json.dumps({"n": 1}),
        "noise after",
        gate.PROBE_MARKER + "{not json",
        gate.PROBE_MARKER + json.dumps({"n": 2, "tui": {"launch_rc": 0}}),
    ])
    payload = gate.parse_probe_output(text)
    assert payload == {"n": 2, "tui": {"launch_rc": 0}}
    assert gate.parse_probe_output("nothing here") is None


def test_version_identity_agrees_and_reports_drift():
    gate = _gate()
    good = gate.version_identity(
        "1.2.3", "1.2.3", "qubit 1.2.3 (abc)", {"ok": True, "state": "READY",
                                                "version": "qubit-1.2.3"})
    assert good["problems"] == []

    bad = gate.version_identity(
        "1.2.3", "1.2.4", "qubit 9.9.9", {"ok": False, "state": "",
                                          "version": "qubit-9.9.9"})
    assert any("wheel filename version" in p for p in bad["problems"])
    assert any("qubit --version" in p for p in bad["problems"])
    assert any("/api/health version" in p for p in bad["problems"])
    assert any("ok=true" in p for p in bad["problems"])
    assert any("did not report a state" in p for p in bad["problems"])


def test_leak_scan_finds_operator_home_and_checkout(tmp_path):
    gate = _gate()
    root = tmp_path / "site-packages" / "voicebridge"
    root.mkdir(parents=True)
    (root / "clean.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "leaky.py").write_text("HOME = '/home/trick'\n", encoding="utf-8")
    (root / "checkout.py").write_text("REPO = '/tmp/qubit-fix6-install'\n",
                                      encoding="utf-8")
    (root / "binary.pyc").write_bytes(b"/home/trick")

    hits = gate.leak_scan(root, ["/home/trick", "/tmp/qubit-fix6-install"])
    names = {h["path"] for h in hits}
    assert names == {"leaky.py", "checkout.py"}


def test_evaluate_probe_passes_complete_payload():
    gate = _gate()
    out = gate.evaluate_probe(
        _probe_payload(), wheel_version="1.2.3", cli_line="qubit 1.2.3",
        expected_plugins=["qubit-ops", "qubit-memory"])
    assert out["reasons"] == []
    assert all(c["state"] == "OK" for c in out["checks"])
    names = {c["name"] for c in out["checks"]}
    assert {"dashboard.ephemeral_port", "tui.packaged_argv",
            "tui.no_source_rebuild", "version.identity"} <= names


def test_evaluate_probe_rejects_live_port_source_rebuild_and_drift():
    gate = _gate()
    payload = _probe_payload()
    payload["dashboard"]["port"] = gate.LIVE_DASHBOARD_PORT
    payload["tui"]["packaged_argv"] = False
    payload["tui"]["launch_output"] = "node-deps: installing workspace dependencies"
    payload["tui"]["launch_rc"] = 1
    out = gate.evaluate_probe(payload, wheel_version="1.2.3",
                              cli_line="qubit 9.9.9")
    reasons = "\n".join(out["reasons"])
    assert "dashboard.ephemeral_port" in reasons
    assert "tui.packaged_argv" in reasons
    assert "tui.no_source_rebuild" in reasons
    assert "version" in reasons


def test_evaluate_probe_without_payload_fails():
    gate = _gate()
    out = gate.evaluate_probe(None, wheel_version="1.2.3", cli_line="qubit 1.2.3")
    assert out["checks"] == []
    assert out["reasons"]


def test_no_deps_install_commands_are_minimal(tmp_path, monkeypatch):
    gate = _gate()
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/uv"
                        if name == "uv" else None)
    cmds = gate._install_commands(tmp_path, "/usr/bin/python3", no_deps=True)
    assert "--system-site-packages" in cmds["venv"]
    assert "--no-deps" in cmds["install"]

    monkeypatch.setattr(gate.shutil, "which", lambda name: None)
    monkeypatch.setattr(gate.R, "_has_module", lambda name: True)
    cmds = gate._install_commands(tmp_path, "/usr/bin/python3", no_deps=True)
    assert "--system-site-packages" in cmds["venv"]
    assert "--no-deps" in cmds["install"]


# ---------------------------------------------------------------------------
# typed preflight + CLI report
# ---------------------------------------------------------------------------
def test_check_refuses_live_dashboard_port(tmp_path):
    gate = _gate()
    out = gate.check(REPO, work_root=tmp_path / "work", port=gate.LIVE_DASHBOARD_PORT)
    assert out["state"] == "FAIL"
    assert any("live dashboard" in r for r in out["reasons"])


def test_check_accepts_a_dashboard_wheel(tmp_path):
    gate = _gate()
    wheel = _fake_wheel(tmp_path / "qubit_voicebridge-1.2.3-py3-none-any.whl")
    out = gate.check(REPO, work_root=tmp_path / "work", wheel=wheel)
    assert out["state"] == "OK", out["reasons"]
    assert out["wheel"]["version"] == "1.2.3"
    assert out["wheel"]["dashboard"] is True
    assert out["isolation_findings"] == []


def test_check_rejects_dashboardless_and_node_modules_wheels(tmp_path):
    gate = _gate()
    no_dash = _fake_wheel(tmp_path / "qubit_voicebridge-1.2.3-py3-none-any.whl",
                          dashboard=False)
    out = gate.check(REPO, work_root=tmp_path / "work1", wheel=no_dash)
    assert out["state"] == "FAIL"
    assert any("dashboard-less" in r for r in out["reasons"])

    node_modules = _fake_wheel(tmp_path / "qubit_voicebridge-1.2.3-py3-none-any.whl",
                               node_modules=True)
    out = gate.check(REPO, work_root=tmp_path / "work2", wheel=node_modules)
    assert out["state"] == "FAIL"
    assert any("node_modules" in r for r in out["reasons"])


def test_cli_check_writes_typed_json_report(tmp_path):
    gate = _gate()
    wheel = _fake_wheel(tmp_path / "qubit_voicebridge-1.2.3-py3-none-any.whl")
    report = tmp_path / "report.json"
    rc = gate.main([
        "--check", "--wheel", str(wheel), "--work-root", str(tmp_path / "work"),
        "--report", str(report), "--json",
    ])
    assert rc == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["schema"] == gate.SCHEMA
    assert data["gate"] == "fresh-install"
    assert data["verdict"] == "PASS"
    assert data["wheel"]["version"] == "1.2.3"


# ---------------------------------------------------------------------------
# opt-in real run
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    __import__("os").environ.get("QUBIT_FRESH_INSTALL_E2E") != "1",
    reason="real wheel install/start is opt-in (QUBIT_FRESH_INSTALL_E2E=1)",
)
def test_real_fresh_install_e2e(tmp_path):
    import os

    gate = _gate()
    wheel = os.environ.get("QUBIT_FRESH_INSTALL_WHEEL")
    kwargs = {"build": True} if not wheel else {"wheel": Path(wheel)}
    out = gate.run(REPO, work_root=tmp_path / "work", **kwargs)
    assert out["state"] == "OK", out["reasons"]
    assert out["dashboard"]["chat_status"] == 200
    assert out["tui"]["packaged_argv"] is True
    assert "no tty" in out["tui"]["launch_output"].lower()
    assert out["leaks"] == []
