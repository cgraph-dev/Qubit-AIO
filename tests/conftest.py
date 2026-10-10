"""Test-session isolation for the 5C-compiled chat path (Packet 5C).

Every _user_content() call in tests uses a throwaway memory DB so the live
integration compiler never touches the user's real private state. Env
overrides also let tests force compiler mode (4b rollback path) and reset
the module-level compiler cache between cases.
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

import tmp_policy


def pytest_addoption(parser):  # noqa: D103
    group = parser.getgroup("qubit-tmp")
    group.addoption(
        "--tmp-free-min-mb",
        type=int,
        default=tmp_policy.DEFAULT_MIN_FREE_MB,
        help=(
            "Refuse to start the session when free space on the filesystem "
            "behind the test temp root is below this many MiB "
            "(default: %(default)s)."
        ),
    )
    group.addoption(
        "--tmp-keep",
        action="store_true",
        default=False,
        help="Keep the per-run test basetemp instead of removing it on exit.",
    )


def pytest_configure(config):  # noqa: D103
    """Default ``--basetemp`` to a cleaned-up per-run dir and set ``TMPDIR``.

    Runs before pytest's own ``tmpdir`` plugin builds its ``TempPathFactory``,
    so ``config.option.basetemp`` set here is the one the session uses.
    """
    requested = config.option.basetemp
    if requested:
        base = Path(requested).expanduser()
        config._qubit_owned_basetemp = False
    else:
        base = tmp_policy.per_run_dir(tmp_policy.temp_root())
        config.option.basetemp = str(base)
        config._qubit_owned_basetemp = True

    min_free = int(config.getoption("tmp_free_min_mb")) * tmp_policy.MIB
    try:
        tmp_policy.enforce_space_guard(base, min_free)
    except tmp_policy.TmpSpaceError as exc:
        raise pytest.UsageError(str(exc)) from exc

    base.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(base)
    tempfile.tempdir = str(base)
    config._qubit_tmp_dir = str(base)

    # Register the host-OS gating marker (ported from upstream's tests/conftest.py
    # alongside tests/_fixtures/platform_gating.py). Without this, a test marked
    # ``@pytest.mark.platforms("windows")`` raises UnknownMarkWarning, which this
    # repo's ``filterwarnings = error`` turns into a collection error.
    config.addinivalue_line(
        "markers",
        "platforms(*specs, arch=None, arch_negate=False): run only on hosts "
        "matching at least one spec — linux/macos/windows/posix/any, "
        "'not X' negation, optional arch filter (e.g. arch='arm64')",
    )
    config.addinivalue_line(
        "markers",
        "require_symlinks: skip the test if symbolic links cannot be created in "
        "the current environment (needs admin/developer mode on Windows).",
    )
    config.addinivalue_line(
        "markers",
        "live_system_guard_bypass: explicitly bypass the test-only live-system "
        "guard (a test whose contract races the guard's own subtree walk).",
    )


def pytest_collection_modifyitems(config, items):  # noqa: D103
    # Host-OS gating: skip items whose ``platforms(...)`` specs exclude this host.
    # Skips (not run-against-a-patched-host) so a Windows-only test never reports
    # green on Linux having exercised nothing underneath the branch.
    from tests._fixtures.platform_gating import (
        _platforms_gate_reason,
        _reject_contradictory_platform_marks,
    )

    _reject_contradictory_platform_marks(items)
    for item in items:
        reason = _platforms_gate_reason(item)
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))


def pytest_unconfigure(config):  # noqa: D103
    """Remove the per-run basetemp we created; never a user-supplied one."""
    if not getattr(config, "_qubit_owned_basetemp", False):
        return
    try:
        if config.getoption("tmp_keep"):
            return
    except Exception:  # pragma: no cover - option may be absent on early exit
        pass
    shutil.rmtree(getattr(config, "_qubit_tmp_dir", ""), ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _isolate_voice_memory_db(tmp_path_factory):
    """Scratch memory DB *inside* the cleaned basetemp, not ``/tmp/vb-mem-*``.

    Created via ``tmp_path_factory`` so pytest has already materialized the
    basetemp; the old module-import ``tempfile.mkdtemp`` left a ``/tmp/vb-mem-*``
    directory behind on every run.
    """
    mem_dir = tmp_path_factory.mktemp("vb-mem")
    os.environ["VOICE_BRIDGE_MEMORY_DB"] = str(mem_dir / "mem.db")
    yield


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
    # Also clear HERMES_HOME, the foreign alias of QUBIT_HOME declared in
    # agent_compat's env_aliases. EnvAliaser mirrors an unset QUBIT_HOME from a
    # set HERMES_HOME (framework.py `EnvAliaser.apply`), so a process launched by
    # the live gateway — which exports BOTH to the operator's real runtime home —
    # would otherwise re-import the operator home through the alias even after
    # QUBIT_HOME is deleted, tripping qubit_runtime.assert_safe_test_home.
    monkeypatch.delenv("HERMES_HOME", raising=False)
    # Also clear the OPENCLAW_* home aliases. The OpenClaw adapter declares
    # ``env_aliases={"OPENCLAW_HOME": "QUBIT_HOME", "OPENCLAW_STATE_DIR":
    # "QUBIT_HOME"}`` and ``EnvAliaser.apply`` mirrors a set foreign name back
    # onto an unset QUBIT_HOME. A gateway-launched test process exports BOTH
    # OPENCLAW_HOME and OPENCLAW_STATE_DIR at the operator's real runtime home,
    # so after the delenv above a mid-test alias apply would re-inject
    # QUBIT_HOME=<operator home>, tripping qubit_runtime.assert_safe_test_home
    # (or, worse, resolving the operator store) in a later test.
    monkeypatch.delenv("OPENCLAW_HOME", raising=False)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
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

