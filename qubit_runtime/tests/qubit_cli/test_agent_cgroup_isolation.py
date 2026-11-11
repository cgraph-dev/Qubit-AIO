"""Agent/dashboard PTY children get their own transient systemd scope.

Incident 2026-11-10: a dashboard/agent turn ran inside ``qubit-voicebridge.service``'s
cgroup and its profile delete replaced the live ``state.db`` generation.  The fix wraps
the PTY child (and therefore every agent tool subprocess under it) in
``systemd-run --user --scope ... --slice=qubit-agents.slice``, with the existing direct
spawn as the fallback.  These tests pin the wrapper, its activation gates, and the
fallback without requiring a real systemd session (the one integration test skips when
``systemd-run --user --scope`` is unavailable).
"""

from __future__ import annotations

import os
import shutil
import sys
import time

import pytest

pytest.importorskip("ptyprocess", reason="ptyprocess not installed")

import qubit_cli.pty_bridge as pty_bridge
from qubit_cli.pty_bridge import PTY_HOST_DASHBOARD, PTY_HOST_ENV, PtyBridge
from tools import process_registry as pr

_AGENT_SLICE_PROBE = pr._agent_scope_slice_available

skip_on_windows = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="PTY bridge is POSIX-only"
)


@pytest.fixture(autouse=True)
def _fresh_scope_probe(monkeypatch):
    """Never inherit a cached systemd verdict or cgroup classification from another test."""
    monkeypatch.setattr(pr, "_SYSTEMD_SCOPE_AVAILABLE", None, raising=False)
    monkeypatch.setattr(pr, "_SYSTEMD_SCOPE_PROBED_AT", 0.0, raising=False)
    monkeypatch.setattr(pr, "_AGENT_SLICE_AVAILABLE", None, raising=False)
    monkeypatch.setattr(pr, "_AGENT_SLICE_PROBED_AT", 0.0, raising=False)
    monkeypatch.setattr(pr, "_agent_scope_slice_available", lambda: True, raising=False)
    monkeypatch.delenv("QUBIT_AGENT_CGROUP_ISOLATION", raising=False)


def _enable_service(monkeypatch):
    monkeypatch.setattr(pr, "_running_in_service_cgroup", lambda: True)


# ---------------------------------------------------------------------------
# Service-cgroup gate
# ---------------------------------------------------------------------------

class TestServiceCgroupGate:

    def test_service_leaf_is_isolated(self, monkeypatch):
        class _FakePath:
            def __init__(self, _):
                pass

            def read_text(self, encoding=None):
                return (
                    "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
                    "qubit-voicebridge.service\n"
                )

        monkeypatch.setattr(pr, "Path", _FakePath)
        assert pr._running_in_service_cgroup() is True

    def test_scope_leaf_is_not_isolated(self, monkeypatch):
        class _FakePath:
            def __init__(self, _):
                pass

            def read_text(self, encoding=None):
                return (
                    "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
                    "snap.code-123.scope\n"
                )

        monkeypatch.setattr(pr, "Path", _FakePath)
        assert pr._running_in_service_cgroup() is False

    def test_missing_proc_falls_back_to_invocation_id(self, monkeypatch):
        class _FakePath:
            def __init__(self, _):
                pass

            def read_text(self, encoding=None):
                raise OSError("no proc")

        monkeypatch.setattr(pr, "Path", _FakePath)
        monkeypatch.setenv("INVOCATION_ID", "test-invocation")
        assert pr._running_in_service_cgroup() is True


# ---------------------------------------------------------------------------
# Shared helper (tools.process_registry)
# ---------------------------------------------------------------------------

class TestBuildAgentScopeArgv:

    def test_wraps_in_agent_slice(self, monkeypatch):
        _enable_service(monkeypatch)
        monkeypatch.setattr(pr, "_IS_LINUX", True)
        monkeypatch.setattr(pr, "_systemd_run_user_scope_available", lambda: True)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemd-run")

        argv = pr.build_agent_scope_argv(["qubit", "--tui"], unit_suffix="abc123")

        assert argv is not None
        assert argv[0] == "/usr/bin/systemd-run"
        assert "--user" in argv
        assert "--scope" in argv
        assert "--quiet" in argv
        assert "--collect" in argv
        assert "--slice=qubit-agents.slice" in argv
        assert "--unit" in argv
        assert argv[argv.index("--unit") + 1] == "qubit-agent-abc123"
        # No --pty: the caller's controlling terminal must be preserved.
        assert "--pty" not in argv
        assert argv[-3:] == ["--", "qubit", "--tui"]

    def test_slice_probe_failure_disables_scope(self, monkeypatch):
        _enable_service(monkeypatch)
        monkeypatch.setattr(pr, "_IS_LINUX", True)
        monkeypatch.setattr(pr, "_systemd_run_user_scope_available", lambda: True)
        monkeypatch.setattr(pr, "_agent_scope_slice_available", lambda: False)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemd-run")

        assert pr.build_agent_scope_argv(["x"], unit_suffix="a") is None

    def test_slice_probe_uses_the_real_slice_flag(self, monkeypatch):
        monkeypatch.setattr(pr, "_IS_LINUX", True)
        monkeypatch.setattr(pr, "_SYSTEMD_RUN_NO_EXPAND", True)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemd-run")
        captured = {}

        class _Result:
            returncode = 0
            stderr = b""

        def _run(argv, **kwargs):
            captured["argv"] = argv
            return _Result()

        monkeypatch.setattr(pr.subprocess, "run", _run)
        monkeypatch.setattr(pr, "_agent_scope_slice_available", _AGENT_SLICE_PROBE, raising=False)
        monkeypatch.setattr(pr, "_AGENT_SLICE_AVAILABLE", None, raising=False)
        monkeypatch.setattr(pr, "_AGENT_SLICE_PROBED_AT", 0.0, raising=False)

        assert pr._agent_scope_slice_available() is True
        argv = captured["argv"]
        assert "--slice=qubit-agents.slice" in argv
        assert argv[-4:] == ["--", "/bin/sh", "-c", "exit 0"]
        assert argv[argv.index("--unit") + 1].startswith("qubit-agent-probe-")

    def test_disabled_knob_returns_none(self, monkeypatch):
        _enable_service(monkeypatch)
        monkeypatch.setattr(pr, "_IS_LINUX", True)
        monkeypatch.setattr(pr, "_systemd_run_user_scope_available", lambda: True)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemd-run")
        monkeypatch.setenv("QUBIT_AGENT_CGROUP_ISOLATION", "off")

        assert pr.build_agent_scope_argv(["x"], unit_suffix="a") is None

    def test_outside_a_systemd_service_returns_none(self, monkeypatch):
        # No service cgroup (a terminal/CI scope): keep today's direct spawn.
        monkeypatch.setattr(pr, "_running_in_service_cgroup", lambda: False)
        monkeypatch.setattr(pr, "_IS_LINUX", True)
        monkeypatch.setattr(pr, "_systemd_run_user_scope_available", lambda: True)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemd-run")

        assert pr.build_agent_scope_argv(["x"], unit_suffix="a") is None

    def test_non_linux_returns_none(self, monkeypatch):
        _enable_service(monkeypatch)
        monkeypatch.setattr(pr, "_IS_LINUX", False)
        monkeypatch.setattr(pr, "_systemd_run_user_scope_available", lambda: True)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemd-run")

        assert pr.build_agent_scope_argv(["x"], unit_suffix="a") is None

    def test_missing_systemd_run_returns_none(self, monkeypatch):
        _enable_service(monkeypatch)
        monkeypatch.setattr(pr, "_IS_LINUX", True)
        monkeypatch.setattr(pr, "_systemd_run_user_scope_available", lambda: True)
        monkeypatch.setattr(shutil, "which", lambda name: None)

        assert pr.build_agent_scope_argv(["x"], unit_suffix="a") is None

    def test_unavailable_user_scope_returns_none(self, monkeypatch):
        _enable_service(monkeypatch)
        monkeypatch.setattr(pr, "_IS_LINUX", True)
        monkeypatch.setattr(pr, "_systemd_run_user_scope_available", lambda: False)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemd-run")

        assert pr.build_agent_scope_argv(["x"], unit_suffix="a") is None


# ---------------------------------------------------------------------------
# PtyBridge.spawn wiring (mocked ptyprocess)
# ---------------------------------------------------------------------------

class _FakeProc:
    pid = 4321


class _FakeBridge(PtyBridge):
    """Avoids building a real PTY in the wiring tests."""

    def __init__(self, proc):
        self._proc = proc
        self._fd = -1
        self._size = None


def _capture_spawn(monkeypatch, *, fail_scoped: bool = False):
    calls: list[dict] = []

    def fake_spawn(argv, *, cwd=None, env=None, dimensions=None):
        calls.append({"argv": list(argv), "cwd": cwd, "env": env, "dimensions": dimensions})
        if fail_scoped and any("systemd-run" in part for part in argv):
            raise OSError("systemd-run could not create the scope")
        return _FakeProc()

    monkeypatch.setattr("ptyprocess.PtyProcess.spawn", staticmethod(fake_spawn))
    return calls


@skip_on_windows
class TestPtyBridgeScopeWiring:

    def test_scoped_argv_and_env_are_passed_through(self, monkeypatch):
        _enable_service(monkeypatch)
        calls = _capture_spawn(monkeypatch)
        scoped = ["/usr/bin/systemd-run", "--user", "--scope", "--", "/bin/echo", "hi"]
        monkeypatch.setattr(pty_bridge, "_agent_scope_argv", lambda argv: list(scoped))
        monkeypatch.setattr(pty_bridge, "_agent_scope_env", lambda env: {**env, "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"})

        _FakeBridge.spawn(["/bin/echo", "hi"], cwd="/tmp/work", env={"DISPLAY": ":0", "A": "1"})

        assert len(calls) == 1
        call = calls[0]
        assert call["argv"] == scoped
        assert call["cwd"] == "/tmp/work"
        assert call["env"]["A"] == "1"
        assert call["env"][PTY_HOST_ENV] == PTY_HOST_DASHBOARD
        assert call["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"

    def test_falls_back_to_direct_spawn_when_scoped_spawn_raises(self, monkeypatch):
        _enable_service(monkeypatch)
        calls = _capture_spawn(monkeypatch, fail_scoped=True)
        monkeypatch.setattr(pty_bridge, "_agent_scope_argv", lambda argv: ["/usr/bin/systemd-run", "--", *argv])
        monkeypatch.setattr(pty_bridge, "_agent_scope_env", lambda env: env)

        _FakeBridge.spawn(["/bin/echo", "hi"], env={"DISPLAY": ":0"})

        assert len(calls) == 2
        assert calls[0]["argv"][0] == "/usr/bin/systemd-run"
        assert calls[1]["argv"] == ["/bin/echo", "hi"]
        assert calls[1]["env"][PTY_HOST_ENV] == PTY_HOST_DASHBOARD

    def test_direct_spawn_when_isolation_unavailable(self, monkeypatch):
        calls = _capture_spawn(monkeypatch)
        monkeypatch.setattr(pty_bridge, "_agent_scope_argv", lambda argv: None)

        _FakeBridge.spawn(["/bin/echo", "hi"], env={"DISPLAY": ":0"})

        assert len(calls) == 1
        assert calls[0]["argv"] == ["/bin/echo", "hi"]

    def test_helper_import_failure_degrades_to_direct_spawn(self, monkeypatch):
        # pty_bridge's own wrapper must swallow an import/probe failure.
        import builtins

        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "tools.process_registry":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked_import)
        assert pty_bridge._agent_scope_argv(["/bin/echo"]) is None


# ---------------------------------------------------------------------------
# Real integration: the child is actually in qubit-agents.slice
# ---------------------------------------------------------------------------

def _read_until(bridge: PtyBridge, needle: bytes, timeout: float = 8.0) -> bytes:
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = bridge.read(timeout=0.2)
        if chunk is None:
            break
        buf.extend(chunk)
        if needle in buf:
            return bytes(buf)
    return bytes(buf)


@skip_on_windows
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="systemd scopes are Linux-only")
def test_real_pty_child_runs_in_agent_slice(monkeypatch):
    if not pr._systemd_run_user_scope_available():
        pytest.skip("systemd-run --user --scope is unavailable on this host")
    _enable_service(monkeypatch)

    bridge = PtyBridge.spawn(
        ["/bin/sh", "-c", "cat /proc/self/cgroup; sleep 0.3"],
        env=dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0")),
    )
    try:
        output = _read_until(bridge, b"qubit-agents.slice")
        assert b"qubit-agents.slice" in output, output
        assert b"qubit-voicebridge.service" not in output, output
    finally:
        bridge.close()
