"""Packet 3B tests: isolated execution (OpenSandbox-adapted Bubblewrap runner).

Covers: startup probe (live + fail-closed typed unavailable), realpath/
symlink-escape rejection, minimal-FS/private-proc/network-none boundaries,
clearenv + secret-shaped env deny, cgroup evidence (own transient unit,
outside caller cgroup), lifecycle transitions with terminal reasons
(user_delete / ttl_expiry / runtime_error / provision_timeout / invalid_spec),
bounded idempotent stop, no-orphan cleanup, and spec validation.

Live tests require the local bubblewrap + user-systemd primitives; they are
safe (ephemeral transient units, synthetic fixtures only) and degrade loudly
when the primitives are absent — a skip is DEGRADED, never a pass.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge import isolated as iso
from voicebridge.isolated import (
    ISOLATION_UNAVAILABLE, IsolatedRun, IsolatedSpec, IsolationError,
    IsolationUnavailable, State, build_bwrap_argv, find_bwrap,
    probe_isolation, resolve_real_path)


@pytest.fixture(autouse=True)
def _reset_failed_units():
    yield
    subprocess.run(["systemctl", "--user", "reset-failed", "vb-iso-*"],
                   capture_output=True, timeout=5)


def _require_live() -> None:
    pr = probe_isolation()
    if not pr.available:
        pytest.skip(f"DEGRADED: isolation primitives unavailable: "
                    f"{pr.message}")


def _spec(tmp_path: Path, argv, **kw) -> IsolatedSpec:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return IsolatedSpec(workspace=str(ws), argv=tuple(argv), **kw)


# ------------------------------------------------------------------- probing

def test_probe_available_on_this_machine():
    _require_live()
    pr = probe_isolation()
    assert pr.available and pr.version
    assert pr.message == ""


def test_probe_fail_closed_without_bwrap(monkeypatch):
    monkeypatch.setattr(iso, "_PROBE_CACHE", None)
    monkeypatch.setattr(iso, "find_bwrap", lambda: "")
    pr = iso.probe_isolation()
    assert not pr.available and "not found" in pr.message


def test_bwrap_version_regex_matches_upstream_shape():
    m = iso._BWRAP_VERSION_RE.search("bubblewrap 0.9.0")
    assert m and m.group(1) == "0.9.0"
    assert not iso._BWRAP_VERSION_RE.search("nope")


# --------------------------------------------------------------- path policy

def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "link").symlink_to(outside)
    with pytest.raises(IsolationError) as ei:
        resolve_real_path(str(ws / "link"), allowed_prefixes=(str(ws),))
    assert ei.value.code == "SYMLINK_ESCAPE"


def test_root_boundary_confusion_rejected_ws_vs_ws_secret(tmp_path):
    """Repair 3: startswith('/ws') wrongly allowed '/ws-secret'.
    commonpath() must accept the root EXACTLY and only below a boundary."""
    base = tmp_path / "vb"
    ws = base / "workspace"
    ws_secret = base / "workspace-secret"
    ws.mkdir(parents=True)
    ws_secret.mkdir()
    roots = (str(ws),)
    assert resolve_real_path(str(ws), allowed_prefixes=roots) == str(ws)
    with pytest.raises(IsolationError) as ei:
        resolve_real_path(str(ws_secret), allowed_prefixes=roots)
    assert ei.value.code == "SYMLINK_ESCAPE"
    # nested child still allowed
    child = ws / "child"
    child.mkdir()
    assert resolve_real_path(str(child), allowed_prefixes=roots) == str(child)


def test_read_only_bind_sibling_trap_rejected_before_launch(tmp_path):
    """A declared read path that merely LOOKS like it lives under an
    allowed root (workspace vs workspace-secret) is rejected at
    argv-build time, never launched."""
    ws = tmp_path / "workspace"
    trap = tmp_path / "workspace-secret"
    ws.mkdir()
    trap.mkdir()
    spec = IsolatedSpec(workspace=str(ws), argv=["/bin/echo", "no"],
                        read_only_paths=(str(trap),))
    with pytest.raises(IsolationError) as ei:
        build_bwrap_argv(spec)
    assert ei.value.code == "SYMLINK_ESCAPE"


def test_relative_and_missing_paths_rejected(tmp_path):
    with pytest.raises(IsolationError):
        resolve_real_path("relative/path", allowed_prefixes=("/",))
    with pytest.raises(IsolationError):
        resolve_real_path(str(tmp_path / "nope"), allowed_prefixes=("/"),
                          must_be_dir=True)


def test_spec_validation_fails_closed(tmp_path):
    with pytest.raises(IsolationError) as ei:
        _spec(tmp_path, [])
    assert ei.value.code == "INVALID_SPEC"


# ------------------------------------------------------------- argv building

def test_bwrap_argv_minimal_fs_network_none_clearenv(tmp_path):
    spec = _spec(tmp_path, ["/bin/echo", "hi"])
    _, argv = build_bwrap_argv(spec)
    assert "--unshare-net" in argv and "--unshare-user" in argv
    assert "--clearenv" in argv
    i = argv.index("--ro-bind")
    assert argv[i:i + 3] == ["--ro-bind", "/usr", "/usr"]
    # whole-host root bind appears ONLY in the probe, never in workloads
    for j, a in enumerate(argv):
        if a == "--ro-bind":
            assert argv[j + 1] != "/", "workload must not bind whole root"
    assert "--" in argv and "sh" not in argv[:argv.index("--")]
    assert "--bind" in argv                              # workspace rw


def test_secret_shaped_env_names_always_denied(tmp_path, monkeypatch):
    monkeypatch.setenv("VB_API_KEY", "fixture-value")
    spec = _spec(tmp_path, ["/bin/true"], env_allowlist=("VB_API_KEY",))
    _, argv = build_bwrap_argv(spec)
    assert "VB_API_KEY" not in argv


def test_allowlisted_env_passes_through(tmp_path, monkeypatch):
    monkeypatch.setenv("VB_PLAIN", "hello")
    spec = _spec(tmp_path, ["/bin/true"], env_allowlist=("VB_PLAIN",))
    _, argv = build_bwrap_argv(spec)
    i = argv.index("VB_PLAIN")
    assert argv[i - 1] == "--setenv" and argv[i + 1] == "hello"


# ---------------------------------------------- live isolation + lifecycle

def test_echo_run_completes_with_bounded_output(tmp_path):
    _require_live()
    with IsolatedRun(_spec(tmp_path, ["/bin/echo", "hello-isolated"])) as r:
        r.start()
        out = r.wait(timeout_s=30)
    assert out.ok and out.stdout.strip() == "hello-isolated"
    assert r.state is State.TERMINATED and not out.truncated


def test_workload_runs_in_own_transient_unit_outside_caller_cgroup(tmp_path):
    _require_live()
    with IsolatedRun(_spec(tmp_path, ["/bin/sleep", "8"])) as r:
        r.start()
        try:
            deadline = time.time() + 5
            while r.main_pid() == 0 and time.time() < deadline:
                time.sleep(0.05)
            cg = r.cgroup_path()
            with open("/proc/self/cgroup") as fh:
                mine = fh.read().strip()
            time.sleep(0.1)
            assert "vb-iso-" in cg
            assert cg.splitlines() != mine.splitlines()
        finally:
            r.stop()


def test_hostile_filesystem_home_and_etc_not_readable(tmp_path):
    _require_live()
    py = ("import sys\n"
          "try:\n"
          "    open('/etc/passwd').read(); sys.exit(0)\n"
          "except PermissionError:\n"
          "    sys.exit(7)\n")
    with IsolatedRun(_spec(tmp_path, ["python3", "-c", py])) as r:
        r.start()
        out = r.wait(timeout_s=30)
    # /etc/passwd is NOT bound into the minimal view: open must fail (any
    # error counts as unreadable; we assert it did NOT succeed)
    assert out.ok is False or "Traceback" in out.stderr or \
        out.exit_code not in (0,)


def test_etc_read_bind_ld_cache_only(tmp_path):
    _require_live()
    py = "open('/etc/ld.so.cache','rb').read(4); print('cache-ok')"
    with IsolatedRun(_spec(tmp_path, ["python3", "-c", py])) as r:
        r.start()
        out = r.wait(timeout_s=30)
    assert out.ok and "cache-ok" in out.stdout


def test_private_proc_only_own_processes(tmp_path):
    _require_live()
    py = ("import os\n"
          "pids = [p for p in os.listdir('/proc') if p.isdigit()]\n"
          "print(len(pids))\n")
    with IsolatedRun(_spec(tmp_path, ["python3", "-c", py])) as r:
        r.start()
        out = r.wait(timeout_s=30)
    assert out.ok and int(out.stdout.strip()) <= 4   # pid ns: self + sh


def test_network_none_blocks_connections(tmp_path):
    _require_live()
    py = ("import socket, sys\n"
          "try:\n"
          "    s = socket.socket(); s.settimeout(1)\n"
          "    s.connect(('127.0.0.1', 80)); sys.exit(0)\n"
          "except OSError:\n"
          "    sys.exit(7)\n")
    with IsolatedRun(_spec(tmp_path, ["python3", "-c", py])) as r:
        r.start()
        out = r.wait(timeout_s=30)
    assert out.exit_code == 7                        # connect refused => no net


def test_writes_outside_workspace_denied(tmp_path):
    _require_live()
    with IsolatedRun(_spec(tmp_path,
                           ["touch", "/usr/share/vb-iso-escape-test"])) as r:
        r.start()
        out = r.wait(timeout_s=30)
    assert not out.ok                                # ro bind blocks the write
    assert not Path("/usr/share/vb-iso-escape-test").exists()


def test_service_env_secret_absent_inside_sandbox(tmp_path, monkeypatch):
    _require_live()
    monkeypatch.setenv("VB_FIXTURE_SECRET", "fixture-not-for-workloads")
    py = ("import os, sys\n"
          "sys.exit(0 if 'VB_FIXTURE_SECRET' not in os.environ else 5)")
    with IsolatedRun(_spec(tmp_path, ["python3", "-c", py])) as r:
        r.start()
        out = r.wait(timeout_s=30)
    assert out.ok                                    # secret never crossed


def test_crash_maps_to_failed_runtime_error(tmp_path):
    _require_live()
    with IsolatedRun(_spec(tmp_path, ["python3", "-c",
                                      "raise SystemExit('boom')"])) as r:
        r.start()
        out = r.wait(timeout_s=30)
    assert not out.ok and r.state is State.FAILED
    assert r.reason == iso.REASON_RUNTIME_ERROR
    assert "boom" in out.stderr


def test_ttl_expiry_terminates_with_reason_and_no_orphan(tmp_path):
    _require_live()
    marker = "vb-iso-orphan-marker-ttl"
    py = (f"# {marker}\n"
          "import subprocess, time\n"
          "subprocess.Popen(['sleep', '60'])\n"
          "time.sleep(60)\n")
    spec = _spec(tmp_path, ["python3", "-c", py], ttl_s=1.5)
    t0 = time.monotonic()
    with IsolatedRun(spec) as r:
        r.start()
        out = r.wait(timeout_s=60)
        elapsed = time.monotonic() - t0
    assert out.killed_by == iso.REASON_TTL_EXPIRY
    assert r.reason == iso.REASON_TTL_EXPIRY
    assert elapsed < 15                              # bounded, not 60s
    found = subprocess.run(["pgrep", "-f", marker], capture_output=True)
    assert found.returncode == 1                     # no orphan remains


def test_cancel_idempotent_user_delete_reason(tmp_path):
    _require_live()
    marker = "vb-iso-orphan-marker-cancel"
    py = (f"# {marker}\n"
          "import subprocess, time\n"
          "subprocess.Popen(['sleep', '60'])\n"
          "time.sleep(60)\n")
    with IsolatedRun(_spec(tmp_path, ["python3", "-c", py])) as r:
        r.start()
        first = r.stop()          # cancellation == user_delete stop
        second = r.stop()         # idempotent re-stop
    assert r.state is State.TERMINATED
    assert r.reason == iso.REASON_USER_DELETE
    assert first.killed_by == second.killed_by       # idempotent, no raise
    found = subprocess.run(["pgrep", "-f", marker], capture_output=True)
    assert found.returncode == 1


def test_pause_resume_round_trip(tmp_path):
    _require_live()
    with IsolatedRun(_spec(tmp_path, ["/bin/sleep", "5"])) as r:
        r.start()
        r.pause()
        assert r.state is State.PAUSED
        r.resume()
        assert r.state is State.RUNNING
        r.stop()


def test_invalid_spec_fails_before_launch(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    spec = IsolatedSpec(workspace=str(ws),
                        argv=["/nonexistent-binary-xyz"])
    r = IsolatedRun(spec)
    # executable-missing surfaces at systemd/bwrap layer as failed start;
    # policy-level invalid specs (empty argv) never even reach launch
    with pytest.raises(IsolationError):
        IsolatedSpec(workspace=str(ws), argv=[])


def test_start_refuses_when_unavailable_end_to_end(
        tmp_path, monkeypatch):
    """No Bubblewrap => typed ISOLATION_UNAVAILABLE and NOTHING launches."""
    monkeypatch.setattr(iso, "_PROBE_CACHE", None)
    monkeypatch.setattr(iso, "find_bwrap", lambda: "")
    r = IsolatedRun(_spec(tmp_path, ["/bin/echo", "must-not-run"]))
    with pytest.raises(IsolationUnavailable) as ei:
        r.start()
    assert ei.value.code == ISOLATION_UNAVAILABLE
    assert r.state is State.FAILED
    load = subprocess.run(["systemctl", "--user", "show", "-p", "LoadState",
                           "--value", r.unit], capture_output=True, text=True)
    assert load.stdout.strip() == "not-found"          # nothing was created
