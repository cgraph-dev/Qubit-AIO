"""Test-session isolation for the 5C-compiled chat path (Packet 5C).

Every _user_content() call in tests uses a throwaway memory DB so the live
integration compiler never touches the user's real private state. Env
overrides also let tests force compiler mode (4b rollback path) and reset
the module-level compiler cache between cases.
"""
import atexit
import os
import shutil
import tempfile

import pytest

os.environ["VOICE_BRIDGE_MEMORY_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="vb-mem-"), "mem.db")

# Isolate this pytest process's tmp base from every other pytest process.
#
# pytest's default base root is shared by every concurrent run on the host
# (`<gettempdir>/pytest-of-<user>/pytest-<n>`). That shared root is subject to
# retention pruning (`make_numbered_dir_with_cleanup`, keep=3) and to external
# cleanup; when the running session's `pytest-<n>` directory disappears mid-run,
# every later `tmp_path` setup dies with
# `FileNotFoundError: .../pytest-of-<user>/pytest-<n>` -- the 47-setup-error
# shape seen in a chunked full-suite run whose base was removed out from under
# it. Give each session its own temp root so no other process can prune or
# delete it (the same fix the vendored parallel runner uses -- see
# `qubit_runtime/scripts/run_tests_parallel.py`). An operator/CI-provided
# `PYTEST_DEBUG_TEMPROOT` is respected.
if not os.environ.get("PYTEST_DEBUG_TEMPROOT"):
    _QUBIT_PYTEST_TEMPROOT = tempfile.mkdtemp(prefix="qubit-pytest-tmproot-")
    os.environ["PYTEST_DEBUG_TEMPROOT"] = _QUBIT_PYTEST_TEMPROOT
    atexit.register(shutil.rmtree, _QUBIT_PYTEST_TEMPROOT, ignore_errors=True)

# Hermetic STT: no test process may start a background CUDA recognizer load.
# sherpa-onnx aborts the interpreter natively ("Fatal Python error: Aborted")
# when an owned daemon thread is mid-load at process teardown; a test that
# starts a WebUI/Orchestrator (many do, with a fake orchestrator + default
# config) must never trigger it. Inherited by child/subprocess WebUIs. Tests
# that exercise the prewarm *function* directly are unaffected.
os.environ["QUBIT_STT_PREWARM"] = "0"


def pytest_runtest_setup(item):  # noqa: D103
    # Fresh compiler cache per test so mode/env changes apply immediately.
    import voicebridge.local_llm as llm
    llm._reset_compiler_cache()


@pytest.fixture(autouse=True)
def _isolate_qubit_runtime(tmp_path, monkeypatch):  # noqa: D103
    """PRB-03: no test may resolve the operator's real runtime store.

    ``voicebridge.qubit_runtime.default_home()`` derives the canonical
    ``state.db`` home from ``XDG_STATE_HOME``. Redirect it to a per-test temp
    dir so a test that forgets to build a Config cannot read or write
    ``~/.local/state/qubit-voicebridge/runtime``. Tests that set
    ``XDG_STATE_HOME`` themselves (most do, via ``monkeypatch``) override this.
    ``QUBIT_HOME`` is snapshotted/restored because runtime code assigns it
    directly, and a leaked value would otherwise bleed between files.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    # Fail closed: clear any ambient/leaked QUBIT_HOME so the store cannot
    # resolve an external or operator home. A test that needs an explicit home
    # sets it after this fixture (monkeypatch restores the original at teardown,
    # including a value assigned directly by runtime code during the test).
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_broker_forkserver():
    """Stop the process-wide broker forkserver between tests.

    The capability broker runs side-effecting callables in a ``forkserver``
    child. That server is a process-wide singleton whose environment is
    captured when it first starts, so a test that monkeypatches
    ``XDG_STATE_HOME`` / ``QUBIT_HOME`` would otherwise have later broker
    children resolve an earlier test's state tree. Stopping the singleton after
    each test lets the next broker child restart it and inherit the current
    environment. ``multiprocessing.forkserver`` starts the singleton at import;
    ``_stop`` clears its pid so the next ``ensure_running`` dial restarts it.
    """
    yield
    try:
        import multiprocessing.forkserver as _fs
    except Exception:  # pragma: no cover - platform guard
        return
    server = getattr(_fs, "_forkserver", None)
    if server is None:  # pragma: no cover - forkserver import always sets it
        return
    try:
        server._stop()
    except Exception:  # pragma: no cover - best-effort teardown
        pass

