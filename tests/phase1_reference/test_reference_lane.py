from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "phase1_reference_lane.py"


def _env(home: Path, tmp: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("QUBIT_HOME", None)
    env["HOME"] = str(home)
    env["TMPDIR"] = str(tmp)
    env["PYTHONPATH"] = str(ROOT)
    return env


def _run(work_root: Path, home: Path, tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--work-root", str(work_root)],
        cwd=ROOT, env=_env(home, tmp), capture_output=True, text=True,
        timeout=180,
    )


def _semantic(report: dict) -> str:
    picked = {
        "status": report["status"],
        "session_verified": report["session"]["verified"],
        "wrong_audience": report["session"]["wrong_audience_denied"],
        "a2a": {
            "created": report["a2a"]["created"],
            "delivery_state": report["a2a"]["delivery_state"],
            "acked_state": report["a2a"]["acked_state"],
        },
        "task_status": report["task"]["status"],
        "workspace_revision": report["task"]["workspace_revision"],
        "read": report["read"]["executed"],
        "approval": {
            "status": report["approval"]["status"],
            "confusion_denied": report["approval"]["confusion_denied"],
        },
        "write": report["write"],
        "artifact_sha": report["artifact"]["sha256"],
        "checkpoints": report["checkpoints"],
        "event_replayed": report["event"]["replayed"],
        "outbox_delivered": report["outbox"]["delivered"],
        "restart": report["restart"],
        "otel": report["otel"],
        "network_blocked": report["network_blocked"],
    }
    blob = json.dumps(picked, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def test_reference_lane_passes_and_is_hermetic(tmp_path: Path) -> None:
    work = tmp_path / "run"
    home = tmp_path / "home"
    home.mkdir()
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    result = _run(work, home, tmp)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)

    assert report["status"] == "PASS"
    assert report["session"]["verified"] is True
    assert report["session"]["wrong_audience_denied"] is True
    assert report["a2a"]["delivery_state"] == "delivered"
    assert report["a2a"]["acked_state"] == "acked"
    assert report["task"]["status"] == "completed"
    assert report["read"]["executed"] is True
    assert report["write"]["executed"] is True
    assert report["write"]["receipt"]["provider"] == "fake-etsy"
    assert report["write"]["receipt"]["status"] == "accepted"
    assert report["approval"]["status"] == "approved"
    assert report["approval"]["confusion_denied"] is True
    assert report["checkpoints"] == [1, 2]
    assert report["event"]["replayed"] == 1
    assert report["outbox"]["delivered"] is True
    assert report["restart"] == {
        "unknown_after_recover": True,
        "requeued": True,
        "final_status": "completed",
    }
    assert report["network_blocked"] is True
    assert report["otel"]["capture_content"] is False
    assert report["otel"]["traces"] == 1
    assert report["otel"]["spans"] >= 7
    assert "invoke_agent" in report["otel"]["span_names"]
    assert "execute_tool" in report["otel"]["span_names"]

    # The lane never writes into the operator's home.
    assert not any(home.iterdir())
    # No network: the process would have failed on a real connect.
    assert "QOFFICE_PHASE1_NETWORK_BLOCKED" not in result.stderr


def test_reference_lane_is_deterministic(tmp_path: Path) -> None:
    first = _run(tmp_path / "one", tmp_path / "home1", tmp_path / "tmp1")
    second = _run(tmp_path / "two", tmp_path / "home2", tmp_path / "tmp2")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert _semantic(json.loads(first.stdout)) == _semantic(
        json.loads(second.stdout))


def test_reference_lane_refuses_non_empty_work_root(tmp_path: Path) -> None:
    work = tmp_path / "busy"
    work.mkdir()
    (work / "sentinel").write_text("x", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    result = _run(work, home, tmp_path)
    assert result.returncode == 2
    assert "must be empty or absent" in result.stderr


def test_network_guard_blocks_socket_creation() -> None:
    spec = importlib.util.spec_from_file_location(
        "phase1_reference_lane_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import socket as real_socket

    original = real_socket.socket
    try:
        with module.network_block():
            try:
                real_socket.socket(real_socket.AF_INET, real_socket.SOCK_STREAM)
            except RuntimeError as exc:
                assert "QOFFICE_PHASE1_NETWORK_BLOCKED" in str(exc)
            else:  # pragma: no cover - the guard must fail closed
                raise AssertionError("socket creation was not blocked")
    finally:
        real_socket.socket = original
