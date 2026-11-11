"""Live-write tripwire: a test/agent subprocess may not WRITE the live state.db.

Background (2026-11-10): a live audit ran as an agent subprocess and deleted a
profile on the operator home; the gateway's state.db generation guard latched.
The mutating audits are now flag-gated, but a spawned child that rebuilds its
environment loses ``PYTEST_*`` and ``QUBIT_HOME`` together and can still resolve
the operator's live home (#82770).  The existing guard refuses *all* opens from
a pytest context; this tripwire adds the missing write-only refusal for the
agent-subprocess shape and uses a signal that survives an env rebuild.

These tests drive the refusal seam directly (never open the live DB) and use a
throwaway ``HOME`` for the real child-process proof.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import qubit_state
import qubit_state_guard
from qubit_state import SessionDB
from qubit_state_guard import LiveStateDbWriteRefused

pytestmark = pytest.mark.allow_real_home_io

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_ROOT = qubit_state._real_platform_state_root()
if REAL_ROOT is None:  # pragma: no cover - no resolvable home on this platform
    pytest.skip("no real platform state root to assert against", allow_module_level=True)


def _force_signals(monkeypatch, *, test=False, agent=False, service=False):
    """Replace the imported signal seams on ``qubit_state`` with fixed answers."""
    monkeypatch.setattr(qubit_state, "_in_test_context", lambda: test)
    monkeypatch.setattr(qubit_state, "_in_agent_subprocess", lambda: agent)
    monkeypatch.setattr(qubit_state, "_looks_like_live_service", lambda: service)


def test_test_marked_write_to_live_home_refuses_typed():
    """Under pytest the write guard refuses the production DB with a typed error."""
    with pytest.raises(LiveStateDbWriteRefused) as excinfo:
        qubit_state._ensure_live_write_allowed(REAL_ROOT / "state.db")
    err = excinfo.value
    assert err.code == "LIVE_STATE_DB_WRITE_REFUSED"
    assert str(REAL_ROOT) in err.detail
    assert err.as_dict()["state"] == "REFUSED"
    # Historical handlers match this phrase; the typed subclass keeps it.
    assert "live-system guard" in str(err)


def test_profile_write_under_live_home_refuses():
    with pytest.raises(LiveStateDbWriteRefused):
        qubit_state._ensure_live_write_allowed(REAL_ROOT / "profiles" / "work" / "state.db")


def test_agent_subprocess_write_to_live_home_refuses(monkeypatch):
    """An agent-spawned child (not pytest) is refused too."""
    _force_signals(monkeypatch, test=False, agent=True)
    with pytest.raises(LiveStateDbWriteRefused):
        qubit_state._ensure_live_write_allowed(REAL_ROOT / "state.db")


def test_normal_process_is_unaffected(monkeypatch):
    """No test/agent signal => the live service and a user CLI are not blocked."""
    _force_signals(monkeypatch, test=False, agent=False, service=False)
    # Must not raise: this is the seam the live service and normal CLI hit.
    qubit_state._ensure_live_write_allowed(REAL_ROOT / "state.db")


def test_live_service_is_exempt_even_under_an_agent_ancestor(monkeypatch):
    """A dev service launched from an agent shell still owns its live writes."""
    _force_signals(monkeypatch, test=False, agent=True, service=True)
    qubit_state._ensure_live_write_allowed(REAL_ROOT / "state.db")


def test_bypass_env_disarms_the_write_tripwire(monkeypatch):
    _force_signals(monkeypatch, test=False, agent=True)
    monkeypatch.setenv(qubit_state_guard._STATE_DB_GUARD_BYPASS_ENV, "1")
    qubit_state._ensure_live_write_allowed(REAL_ROOT / "state.db")


def test_temp_home_write_is_allowed(tmp_path):
    """A legitimate test writing its own temporary store is not blocked."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        assert db.db_path == tmp_path / "state.db"
    finally:
        db.close()


def test_read_only_open_does_not_run_the_write_tripwire(tmp_path, monkeypatch):
    """The tripwire is write-scoped: a read-only open never calls it."""
    seed = SessionDB(db_path=tmp_path / "state.db")
    seed.close()

    def _boom(_path):  # pragma: no cover - must not be reached
        raise AssertionError("write tripwire ran for a read-only open")

    monkeypatch.setattr(qubit_state, "_ensure_live_write_allowed", _boom)
    db = SessionDB(db_path=tmp_path / "state.db", read_only=True)
    try:
        assert db.read_only is True
    finally:
        db.close()


_CHILD_PROBE = r"""
import json, os, sys
sys.path.insert(0, {repo!r})
import qubit_state
from qubit_state import SessionDB
from qubit_state_guard import LiveStateDbWriteRefused

root = qubit_state._real_platform_state_root()
out = {{"root": str(root)}}
try:
    SessionDB(root / "state.db")
except LiveStateDbWriteRefused as exc:
    out["live_write"] = "REFUSED"
    out["code"] = exc.code
else:
    out["live_write"] = "ALLOWED"
scratch = os.environ["SCRATCH_HOME"]
try:
    db = SessionDB(__import__("pathlib").Path(scratch) / "state.db")
    db.close()
    out["temp_write"] = "OK"
except Exception as exc:
    out["temp_write"] = type(exc).__name__
print(json.dumps(out))
"""


def _run_child(env: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_PROBE.format(repo=str(REPO_ROOT))],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _child_env(tmp_path: Path, marker: str) -> dict:
    """A rebuilt child env with a throwaway HOME and exactly one isolation signal."""
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("PYTEST_")
        and k not in ("QUBIT_HOME", "HERMES_TEST_ISOLATION",
                      qubit_state_guard._ROOT_TEST_ISOLATION_MARKER_ENV,
                      qubit_state_guard._AGENT_SUBPROCESS_MARKER_ENV,
                      *qubit_state_guard._EXTERNAL_AGENT_ENV_MARKERS)
    }
    env["HOME"] = str(tmp_path)
    env["SCRATCH_HOME"] = str(tmp_path / "scratch")
    env["PYTHONPATH"] = str(REPO_ROOT)
    (tmp_path / "scratch").mkdir(parents=True, exist_ok=True)
    env[marker] = "1"
    return env


@pytest.mark.parametrize("marker", [
    qubit_state_guard._TEST_ISOLATION_MARKER_ENV,
    qubit_state_guard._ROOT_TEST_ISOLATION_MARKER_ENV,
    qubit_state_guard._AGENT_SUBPROCESS_MARKER_ENV,
])
def test_child_with_marker_refuses_live_write_but_allows_temp(tmp_path, marker):
    """Real child: production root write refused, temp root write succeeds."""
    result = _run_child(_child_env(tmp_path, marker))
    assert result["root"] == str(tmp_path / ".qubit")
    assert result["live_write"] == "REFUSED"
    assert result["code"] == "LIVE_STATE_DB_WRITE_REFUSED"
    assert result["temp_write"] == "OK"


def test_service_detection_matches_modules_not_path_substrings(monkeypatch):
    """``pytest tests/test_voicebridge.py`` is not the live service."""
    def _cmdline(argv):
        return lambda: argv

    monkeypatch.setattr(qubit_state_guard, "_current_cmdline",
                        _cmdline(["/usr/bin/python", "-m", "pytest", "tests/test_voicebridge.py"]))
    assert qubit_state_guard._looks_like_live_service() is False

    monkeypatch.setattr(qubit_state_guard, "_current_cmdline",
                        _cmdline(["/usr/bin/python", "-m", "voicebridge", "run"]))
    assert qubit_state_guard._looks_like_live_service() is True

    monkeypatch.setattr(qubit_state_guard, "_current_cmdline",
                        _cmdline(["/usr/bin/python", "-m", "voicebridge.gateway_daemon"]))
    assert qubit_state_guard._looks_like_live_service() is True

    monkeypatch.setattr(qubit_state_guard, "_current_cmdline",
                        _cmdline(["/home/x/.local/bin/qubit", "serve"]))
    assert qubit_state_guard._looks_like_live_service() is True

    monkeypatch.setattr(qubit_state_guard, "_current_cmdline",
                        _cmdline(["/home/x/.local/bin/qubit", "agent"]))
    assert qubit_state_guard._looks_like_live_service() is True


def test_shell_wrapper_exports_the_agent_subprocess_marker():
    """The Qubit terminal tool stamps the marker into every command it spawns."""
    from tools.environments.base_session_env import _wrap_command_script

    script = _wrap_command_script(
        "true", quoted_cwd="/tmp", quoted_snap="/tmp/snap",
        snap_tmp_template="/tmp/snap.XXXXXX", passthrough_names=(),
        snapshot_ready=False, cwd_marker="CWD",
    )
    assert "QUBIT_AGENT_SUBPROCESS=1" in script
