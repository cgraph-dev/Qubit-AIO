"""Qubit-owned Hermes backend is the actual runtime backend.

Owner directive step 1: the vendored Hermes FastAPI/uvicorn server executes from
the Qubit tree. These tests prove it imports from the fork, reports a real
route table, and actually serves HTTP from a temp HOME with no repository
checkout on PYTHONPATH.

All fork imports run in subprocesses: importing the vendored backend starts
upstream background threads, and the hermetic Qubit suite must not inherit them.
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
FORK = REPO / "qubit_runtime"
# The vendored pin is owned by the manifest, not the test: read the revision
# from the canonical provenance manifest (mirroring
# voicebridge.qubit_runtime.manifest_path(), with the frozen archive as the
# read-only fallback). Never hardcode the pin here — a pin move (owner
# decisions #14/#15) must not require editing this test.
_MANIFEST = REPO / "docs" / "generated" / "QUBIT_HERMES_VENDOR_MANIFEST.json"
_ARCHIVE_MANIFEST = (REPO / "docs" / "archive" / "2026-09-21"
                     / "QUBIT_HERMES_VENDOR_MANIFEST.json")


def _manifest_revision() -> str:
    mf = _MANIFEST if _MANIFEST.is_file() else _ARCHIVE_MANIFEST
    return json.loads(mf.read_text(encoding="utf-8"))["upstream_revision"]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _child_env(home: Path) -> dict:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env.pop("PYTHONPATH", None)
    return env


def _run_py(body: str) -> dict:
    """Run a snippet in a child process and parse its last stdout line as JSON."""
    home = Path(tempfile.mkdtemp(prefix="hermes-be-"))
    code = "import json\n" + body
    try:
        p = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                           env=_child_env(home), capture_output=True,
                           text=True, timeout=180)
        assert p.returncode == 0, p.stderr[-1000:]
        return json.loads(p.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_backend_status_is_real_fork_app():
    out = _run_py(
        "from voicebridge import qubit_backend as hb\n"
        "print(json.dumps(hb.status()))\n")
    assert out["runtime"] == "qubit-runtime-backend"
    assert out["present"] is True
    assert out["importable"] is True, out.get("import_error")
    assert out["title"] == "Qubit Agent"
    assert out["routes"] > 0
    assert out["start_server"] is True
    assert out["upstream_revision"] == _manifest_revision()
    assert out["upstream_revision"], "manifest must record the vendored pin"


def test_backend_app_is_from_the_fork():
    out = _run_py(
        "from voicebridge import qubit_backend as hb\n"
        "ws = hb._web_server_module()\n"
        "print(json.dumps({'file': str(ws.__file__), "
        "'same': hb.app() is ws.app}))\n")
    assert Path(out["file"]).resolve().is_relative_to(FORK)
    assert out["same"] is True


def test_backend_actually_serves_http():
    """Start the real backend headless from a temp HOME and probe it."""
    port = _free_port()
    home = Path(tempfile.mkdtemp(prefix="hermes-backend-test-"))
    env = _child_env(home)
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
        assert spec.get("info", {}).get("title") == "Qubit Agent"
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
