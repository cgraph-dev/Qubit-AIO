"""``_profile_default_cwd`` / ``_is_other_profiles_cwd`` are reached from the cwd resolution paths.

Both were defined and never called. ``_completion_cwd`` (and ``session.resume``'s fallback) resolve
through ``_profile_default_cwd``; ``_resumable_stored_cwd`` (and ``_hydrate_session_cwd``'s repair
arm) set a row aside when ``_is_other_profiles_cwd`` says it belongs to another profile. A named
profile resolves in ITS OWN scope, never the launch profile's env or ``terminal.cwd``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tui_gateway.server as srv


@pytest.fixture
def topology(tmp_path, monkeypatch):
    """Launch home with its own terminal.cwd; a real named profile ``box`` under its profiles root."""
    launch = tmp_path / "runtime"
    box = launch / "profiles" / "box"
    box.mkdir(parents=True)
    (launch / "config.yaml").write_text("terminal:\n  cwd: /tmp\n")
    (box / "config.yaml").write_text("terminal:\n  backend: local\n")
    monkeypatch.setenv("QUBIT_HOME", str(launch))
    monkeypatch.setenv("TERMINAL_CWD", "/tmp")
    monkeypatch.setattr(srv, "_qubit_home", launch, raising=False)
    return launch, box


def test_completion_cwd_calls_profile_default_cwd_and_lands_in_the_profile(topology, monkeypatch):
    launch, box = topology
    calls = []
    real = srv._profile_default_cwd

    def spy(profile_home):
        calls.append(profile_home)
        return real(profile_home)

    monkeypatch.setattr(srv, "_profile_default_cwd", spy)
    env_before = dict(srv.os.environ)

    landed = srv._completion_cwd({"profile": "box"})

    assert calls, "_completion_cwd must consult _profile_default_cwd"
    assert Path(landed) == box, "a box session lands in box's own home"
    assert Path(landed) != launch, "never the launch profile's workspace"
    assert dict(srv.os.environ) == env_before, "a resolution is not an env mutation"


def test_profile_default_cwd_binds_the_named_profile_scope(topology, monkeypatch):
    _, box = topology
    binds = []
    real = srv._profile_runtime_scope_tokens
    monkeypatch.setattr(srv, "_profile_runtime_scope_tokens",
                        lambda profile_home, **kw: (binds.append(str(profile_home)), real(profile_home, **kw))[1])

    assert Path(srv._profile_default_cwd("box")) == box
    assert binds == [str(box)], "the name→home resolution runs bound to that profile"


def test_resumable_stored_cwd_calls_is_other_profiles_cwd_topology(topology, monkeypatch):
    launch, box = topology
    calls = []
    real = srv._is_other_profiles_cwd

    def spy(cwd, profile_home):
        calls.append((cwd, profile_home))
        return real(cwd, profile_home)

    monkeypatch.setattr(srv, "_is_other_profiles_cwd", spy)

    assert srv._resumable_stored_cwd(str(box), box) == str(box)          # its own home stays
    assert srv._resumable_stored_cwd(str(launch), box) == ""             # the launch home is set aside
    assert srv._resumable_stored_cwd(str(launch / "profiles" / "other" / "p"), box) == ""   # a sibling's tree
    assert srv._resumable_stored_cwd("/tmp/proj", box) == "/tmp/proj"    # an ordinary folder stays
    assert len(calls) == 4


def test_is_other_profiles_cwd_is_launch_profile_noop(topology):
    """The launch profile (``profile_home=None``) has no other profile to confuse it with."""
    launch, _ = topology
    assert srv._is_other_profiles_cwd(str(launch), None) is False
    assert srv._is_other_profiles_cwd("/tmp", None) is False
