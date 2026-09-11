"""Qubit-owned Hermes backend is the actual runtime backend.

Owner directive step 1: the vendored Hermes FastAPI/uvicorn server executes from
the Qubit tree. These tests prove it imports from the fork, reports a real
route table, and actually serves HTTP from a temp HOME with no repository
checkout on PYTHONPATH.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORK = REPO / "qubit_hermes"
PIN = "2237be355906fbe6065ce1815711eee52b2d646e"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_backend_status_is_real_fork_app():
    from voicebridge import hermes_backend as hb

    st = hb.status()
    assert st["runtime"] == "qubit-hermes-backend"
    assert st["present"] is True
    assert st["importable"] is True, st.get("import_error")
    assert st["title"] == "Hermes Agent"
    assert st["routes"] > 0
    assert st["start_server"] is True
    assert st["upstream_revision"] == PIN


def test_backend_app_is_from_the_fork():
    from voicebridge import hermes_backend as hb

    ws = hb._web_server_module()
    assert Path(ws.__file__).resolve().is_relative_to(FORK)
    assert hb.app() is ws.app


def test_backend_actually_serves_http():
    """Start the real backend headless from a temp HOME and probe it."""
    port = _free_port()
    home = Path(tempfile.mkdtemp(prefix="hermes-backend-test-"))
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env.pop("PYTHONPATH", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "voicebridge", "agent", "serve",
         "--host", "127.0.0.1", "--port", str(port), "--skip-build"],
        cwd=str(REPO), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.time() + 60
        last = None
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise AssertionError(f"backend exited {proc.returncode}: {out[-800:]}")
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception as exc:  # not up yet
                last = exc
                time.sleep(0.5)
        else:
            raise AssertionError(f"backend never became healthy: {last}")
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/openapi.json", timeout=5) as r:
            spec = json.loads(r.read().decode("utf-8"))
        assert spec.get("info", {}).get("title") == "Hermes Agent"
        assert spec.get("paths"), "backend served an empty API"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        if proc.stdout is not None:
            proc.stdout.close()
        shutil.rmtree(home, ignore_errors=True)
