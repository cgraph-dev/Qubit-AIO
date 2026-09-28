"""Owner Decision #16 — `qubit gateway run|install` are real Qubit surfaces.

``run`` is no longer a typed refusal: it runs the Qubit-owned messaging-gateway
daemon in the foreground (``--check`` is a read-only preflight), with the daemon
launch injected so no real process is ever started. ``install`` writes the
systemd user unit through the shared installer into a TEMP unit dir, gated by
the one authority path (e-stop → confirmation → audit).

Hermetic: no host unit is installed, no service is started/stopped, no real
daemon process is launched.
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

UNIT_NAME = "qubit-gateway.service"


def _run_cli(argv, cfg_path, monkeypatch, tmp_path):
    """Invoke the real CLI ``main`` against a hermetic config; capture stdout."""
    for var, sub in (("XDG_CONFIG_HOME", "c"), ("XDG_STATE_HOME", "s"),
                     ("XDG_DATA_HOME", "d")):
        d = tmp_path / sub
        d.mkdir(exist_ok=True)
        monkeypatch.setenv(var, str(d))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  host: 127.0.0.1\n"
                 "  port: 48999\n")
    buf = io.StringIO()
    from voicebridge import cli as CLI

    with redirect_stdout(buf):
        rc = CLI.main([*argv, "--config", str(p)])
    return rc, buf.getvalue()


# --------------------------------------------------------------- run (un-refused)

def test_gateway_run_is_no_longer_a_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    launched = []

    def _fake_run_gateway(argv=None):
        launched.append(list(argv or []))
        return 0

    from voicebridge import gateway_daemon as GD

    monkeypatch.setattr(GD, "run_gateway", _fake_run_gateway)
    rc, out = _run_cli(["gateway", "run"], None, monkeypatch, tmp_path)
    assert rc == 0, out
    assert launched == [[]], launched
    assert "GATEWAY_NO_SUPERVISION" not in out


def test_gateway_run_check_is_read_only_preflight(tmp_path, monkeypatch):
    from voicebridge import gateway_daemon as GD

    called = {"run": 0}
    monkeypatch.setattr(GD, "run_gateway",
                        lambda argv=None: called.__setitem__("run", 1))
    rc, out = _run_cli(["gateway", "run", "--check", "--json"], None,
                       monkeypatch, tmp_path)
    assert rc == 0, out
    report = json.loads(out)
    assert report["state"] == "OK" and report["service_unit"] == UNIT_NAME
    assert called["run"] == 0, "preflight must never start the daemon loop"


# ----------------------------------------------------------- install (gated)

def test_gateway_install_without_confirm_is_refused(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    calls = []
    monkeypatch.setattr(GS, "install_unit",
                        lambda *a, **k: calls.append(1) or {"ok": True})
    rc, out = _run_cli(["gateway", "install"], None, monkeypatch, tmp_path)
    assert rc == 3, out
    body = json.loads(out)
    assert body["code"] == GS.CONFIRM_CODE and body["state"] == "CONFIRM_REQUIRED"
    assert calls == [], "unconfirmed install must not run the installer"


def test_gateway_install_confirmed_writes_into_temp_unit_dir(tmp_path, monkeypatch):
    """The confirmed install reaches the REAL installer, rendered into a temp dir.

    ``SYSTEMD_UNIT_DIR`` (the installer's documented override) redirects the
    render into a temporary dir, so the operator's real user-unit dir is never
    written.
    """
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    monkeypatch.setenv("SYSTEMD_UNIT_DIR", str(unit_dir))
    rc, out = _run_cli(["gateway", "install", "--confirm"], None, monkeypatch,
                       tmp_path)
    assert rc == 0, out
    body = json.loads(out)
    assert body["ok"] is True and body["unit"] == UNIT_NAME, body
    installed = unit_dir / UNIT_NAME
    assert installed.exists(), out
    rendered = installed.read_text(encoding="utf-8")
    assert "__PROJECT_DIR__" not in rendered
    assert "voicebridge.gateway_daemon" in rendered
    # The operator's real user-unit dir was not written.
    assert not (Path.home() / ".config" / "systemd" / "user" / UNIT_NAME).exists()


def test_gateway_install_estop_refuses(tmp_path, monkeypatch):
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(GS, "_estop", lambda _cfg: True)
    calls = []
    monkeypatch.setattr(GS, "install_unit",
                        lambda *a, **k: calls.append(1) or {"ok": True})
    rc, out = _run_cli(["gateway", "install", "--confirm"], None, monkeypatch,
                       tmp_path)
    assert rc == 3, out
    assert json.loads(out)["code"] == GS.ESTOP_CODE
    assert calls == [], "a latched e-stop must refuse before installing"


# ------------------------------------------------------------- independence

def test_daemon_import_never_touches_the_reference_home(tmp_path):
    """The daemon boundary pins a Qubit home; ``~/.hermes`` is never opened.

    Uses a scratch ``HOME`` so the external default is a throwaway path; asserts
    the pinned home is Qubit-owned and the external home is never materialized.
    """
    from voicebridge import qubit_runtime as QR

    scratch = tmp_path / "operator"
    scratch.mkdir()
    old_home = os.environ.get("HOME")
    old_qh = os.environ.pop("QUBIT_HOME", None)
    old_state = os.environ.get("XDG_STATE_HOME")
    os.environ["HOME"] = str(scratch)
    os.environ["XDG_STATE_HOME"] = str(tmp_path / "xdg")
    try:
        external = QR._external_default_home()
        assert str(external).startswith(str(scratch)), external
        home = QR.pin_qubit_home()
        assert QR._resolved(home) != QR._resolved(external)
        assert QR._resolved(home).is_relative_to(tmp_path)
        assert not external.exists(), "the reference home must never be created"
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        if old_qh is not None:
            os.environ["QUBIT_HOME"] = old_qh
        if old_state is not None:
            os.environ["XDG_STATE_HOME"] = old_state


@pytest.mark.skipif(not __import__("shutil").which("strace"),
                    reason="strace not available")
def test_daemon_boundary_strace_shows_zero_reference_home_opens(tmp_path):
    """strace: importing the daemon boundary opens no path under ~/.hermes.

    The reference home is a scratch path (``HOME`` redirected), so the real
    operator ``~/.hermes`` is never consulted regardless of the result.
    """
    scratch = tmp_path / "operator"
    scratch.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(scratch)
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg")
    env.pop("QUBIT_HOME", None)
    trace_out = tmp_path / "trace.txt"
    code = (
        "from voicebridge import qubit_runtime as QR;"
        "QR.pin_qubit_home(); QR.ensure_path();"
        "import importlib.util;"
        "assert importlib.util.find_spec('gateway.status') is not None"
    )
    r = __import__("subprocess").run(
        ["strace", "-f", "-e", "trace=openat,open,statx", "-o", str(trace_out),
         sys.executable, "-c", code],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=120)
    assert r.returncode == 0, (r.returncode, r.stderr[-400:])
    joined = trace_out.read_text(encoding="utf-8", errors="ignore")
    assert "/.hermes" not in joined, "the reference home was opened"
