"""Focused tests for the bounded/guarded test temp policy (Task S4).

They assert the live session wiring (``--basetemp`` is a bounded per-run dir
and ``TMPDIR`` points at it) plus the pure free-space guard logic that must fail
a session early rather than let scratch fill ``/tmp``.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import tmp_policy


def test_live_basetemp_is_bounded_and_owns_tmpdir(pytestconfig, tmp_path):
    basetemp = pytestconfig.option.basetemp
    assert basetemp, "the session must default --basetemp, never leave it unset"
    base = Path(basetemp).resolve()

    # Never the accumulating pytest default.
    assert "pytest-of-" not in str(base)

    # TMPDIR (and the stdlib resolution) must point at the run dir.
    assert os.environ["TMPDIR"] == str(base)
    assert Path(tempfile.gettempdir()).resolve() == base

    # The per-test tmp_path really is governed by that basetemp.
    assert Path(tmp_path).resolve().is_relative_to(base)

    # And it is a one-run directory under the configured root.
    root = tmp_policy.temp_root()
    assert base.is_relative_to(root) or os.environ.get("QUBIT_PYTEST_TMPROOT")
    if getattr(pytestconfig, "_qubit_owned_basetemp", False):
        assert base.parent.resolve() == root
        assert base.name.startswith(tmp_policy.RUN_PREFIX)


def test_temp_root_honors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(tmp_policy.ROOT_ENV, str(tmp_path / "scratch"))
    assert tmp_policy.temp_root() == (tmp_path / "scratch").resolve()


def test_per_run_dir_is_unique_and_prefixed(tmp_path):
    a = tmp_policy.per_run_dir(tmp_path, pid=1234, token="aaaa")
    b = tmp_policy.per_run_dir(tmp_path, pid=1234, token="bbbb")
    assert a != b
    assert a.name == f"{tmp_policy.RUN_PREFIX}-1234-aaaa"
    assert a.parent == tmp_path


def test_space_guard_ok_and_low(tmp_path):
    ok, available = tmp_policy.space_guard(tmp_path, 2 * tmp_policy.MIB,
                                           free=lambda _p: 5 * tmp_policy.MIB)
    assert ok is True and available == 5 * tmp_policy.MIB

    ok, available = tmp_policy.space_guard(tmp_path, 2 * tmp_policy.MIB,
                                           free=lambda _p: 1 * tmp_policy.MIB)
    assert ok is False and available == 1 * tmp_policy.MIB


def test_enforce_space_guard_raises_actionable_error(tmp_path):
    with pytest.raises(tmp_policy.TmpSpaceError) as excinfo:
        tmp_policy.enforce_space_guard(
            tmp_path, 2 * tmp_policy.MIB, free=lambda _p: 1 * tmp_policy.MIB)
    message = str(excinfo.value)
    assert "tmp_reclaim.sh" in message
    assert tmp_policy.ROOT_ENV in message
    assert str(tmp_path) in message


def test_enforce_space_guard_passes_when_room(tmp_path):
    tmp_policy.enforce_space_guard(
        tmp_path, tmp_policy.MIB, free=lambda _p: 4 * tmp_policy.MIB)


def test_free_bytes_walks_up_to_existing_anchor(tmp_path):
    missing = tmp_path / "does" / "not" / "exist"
    seen: list[str] = []

    def fake_usage(path):
        seen.append(path)
        return type("U", (), {"free": 123})()

    assert tmp_policy.free_bytes(missing, usage=fake_usage) == 123
    # Stat anchored on the nearest existing ancestor, not the missing leaf.
    assert seen and Path(seen[0]) == tmp_path
