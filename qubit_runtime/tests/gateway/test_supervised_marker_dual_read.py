"""The supervised-child marker family must survive the brand rename.

The launcher marker is WRITTEN into generated systemd units, launchd plists and Windows
Scheduled Tasks. Machines already have those units installed, so a rename that only
changes the *written* name would make every installed unit invisible to the new reader —
the same half-migration bug being fixed, just inverted. The contract is therefore:

* WRITE the canonical ``QUBIT_`` spelling (the repo's zero-legacy-brand rule).
* READ BOTH spellings everywhere, so an installed unit written by an older build is still
  recognised.

These tests pin both halves. ``test_*_legacy_*`` are the installed-service cases; the
generator assertions are the convergence cases. They fail on the unmodified tree because
the writers emit ``HERMES_`` and the readers know only ``HERMES_``.
"""
import os
from pathlib import Path

import pytest

from gateway.restart import (
    EXTERNAL_GATEWAY_SUPERVISOR_ENV,
    EXTERNAL_GATEWAY_SUPERVISOR_ENV_ALIASES,
    S6_SUPERVISED_CHILD_ENV,
    S6_SUPERVISED_CHILD_ENV_ALIASES,
    SUPERVISED_CHILD_ENV,
    SUPERVISED_CHILD_ENV_ALIASES,
    is_gateway_supervisor_process,
    is_supervised_gateway_launch,
)

# The marker family under test: (canonical write name, legacy read-only alias).
_CANONICAL_SUPERVISED = SUPERVISED_CHILD_ENV == "QUBIT_SUPERVISED_CHILD"
_LEGACY_SUPERVISED = SUPERVISED_CHILD_ENV_ALIASES == ("HERMES_SUPERVISED_CHILD",)
_CANONICAL_S6 = S6_SUPERVISED_CHILD_ENV == "QUBIT_S6_SUPERVISED_CHILD"
_LEGACY_S6 = S6_SUPERVISED_CHILD_ENV_ALIASES == ("HERMES_S6_SUPERVISED_CHILD",)
_CANONICAL_EXTERNAL = EXTERNAL_GATEWAY_SUPERVISOR_ENV == "QUBIT_GATEWAY_EXTERNAL_SUPERVISOR"
_LEGACY_EXTERNAL = EXTERNAL_GATEWAY_SUPERVISOR_ENV_ALIASES == (
    "HERMES_GATEWAY_EXTERNAL_SUPERVISOR",
)


def _clear_markers(monkeypatch):
    for name in (
        SUPERVISED_CHILD_ENV,
        *SUPERVISED_CHILD_ENV_ALIASES,
        S6_SUPERVISED_CHILD_ENV,
        *S6_SUPERVISED_CHILD_ENV_ALIASES,
        EXTERNAL_GATEWAY_SUPERVISOR_ENV,
        *EXTERNAL_GATEWAY_SUPERVISOR_ENV_ALIASES,
        "INVOCATION_ID",
        "XPC_SERVICE_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


class TestMarkerNamesAreCanonicalised:
    """The constants name the canonical brand, with the legacy name kept as a read alias."""

    def test_supervised_child_constants(self):
        assert _CANONICAL_SUPERVISED, SUPERVISED_CHILD_ENV
        assert _LEGACY_SUPERVISED, SUPERVISED_CHILD_ENV_ALIASES

    def test_s6_supervised_child_constants(self):
        assert _CANONICAL_S6, S6_SUPERVISED_CHILD_ENV
        assert _LEGACY_S6, S6_SUPERVISED_CHILD_ENV_ALIASES

    def test_external_supervisor_constants(self):
        assert _CANONICAL_EXTERNAL, EXTERNAL_GATEWAY_SUPERVISOR_ENV
        assert _LEGACY_EXTERNAL, EXTERNAL_GATEWAY_SUPERVISOR_ENV_ALIASES


class TestReaderAcceptsBothSpellings:
    """An installed unit written by either build must be recognised (the dual read)."""

    @pytest.mark.parametrize("spelling", ["QUBIT_SUPERVISED_CHILD", "HERMES_SUPERVISED_CHILD"])
    def test_supervised_launch_recognises_either_spelling(self, spelling):
        """A Windows Scheduled Task / systemd unit installed by an older build exports only the
        legacy name; the self-kill guards (#113667) must still treat it as a supervised launch."""
        assert is_supervised_gateway_launch({spelling: "1"}) is True

    @pytest.mark.parametrize("spelling", ["QUBIT_S6_SUPERVISED_CHILD", "HERMES_S6_SUPERVISED_CHILD"])
    def test_s6_sentinel_recognised_under_either_spelling(self, spelling):
        """The s6 run script of an already-built image still exports the legacy s6 sentinel."""
        assert is_gateway_supervisor_process({spelling: "1"}) is True
        assert is_supervised_gateway_launch({spelling: "1"}) is True

    @pytest.mark.parametrize(
        "spelling",
        ["QUBIT_GATEWAY_EXTERNAL_SUPERVISOR", "HERMES_GATEWAY_EXTERNAL_SUPERVISOR"],
    )
    def test_external_supervisor_opt_in_recognised_under_either_spelling(self, spelling):
        assert is_gateway_supervisor_process({spelling: "1"}) is True

    def test_supervised_child_is_not_a_restart_route(self):
        """Neither spelling selects the exit-75 restart route: the Scheduled Task cannot honour it
        (#113670), so the marker must stay a launch-only identity."""
        for spelling in ("QUBIT_SUPERVISED_CHILD", "HERMES_SUPERVISED_CHILD"):
            assert is_gateway_supervisor_process({spelling: "1"}) is False

    def test_no_marker_means_not_supervised(self):
        assert is_supervised_gateway_launch({}) is False
        assert is_gateway_supervisor_process({}) is False


class TestApplyProfileOverrideReadsBothSpellings:
    """A supervised gateway installed before the rename must keep its fixed profile identity.

    ``_apply_profile_override`` skips the sticky ``active_profile`` for supervised children;
    if it read only the new spelling, an installed unit would be silently re-homed into that
    profile — the #74872 identity-assumption vector.
    """

    @pytest.mark.parametrize("spelling", ["QUBIT_SUPERVISED_CHILD", "HERMES_SUPERVISED_CHILD"])
    def test_supervised_child_skips_active_profile(self, tmp_path, monkeypatch, spelling):
        _clear_markers(monkeypatch)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        root = tmp_path / ".qubit"
        (root / "profiles" / "cand").mkdir(parents=True)
        (root / "profiles" / "cand" / "config.yaml").write_text("{}\n", encoding="utf-8")
        (root / "active_profile").write_text("cand", encoding="utf-8")
        monkeypatch.setenv("QUBIT_HOME", str(root))
        monkeypatch.setenv(spelling, "1")

        import sys as _sys
        from qubit_cli.main import _apply_profile_override

        monkeypatch.setattr(_sys, "argv", ["qubit", "gateway", "run"])
        _apply_profile_override()
        assert os.environ.get("QUBIT_HOME") == str(root), (
            f"a supervised child marked {spelling} was redirected to the active profile"
        )

    @pytest.mark.parametrize("spelling", ["QUBIT_S6_SUPERVISED_CHILD", "HERMES_S6_SUPERVISED_CHILD"])
    def test_s6_sentinel_skips_active_profile(self, tmp_path, monkeypatch, spelling):
        _clear_markers(monkeypatch)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        root = tmp_path / ".qubit"
        (root / "profiles" / "cand").mkdir(parents=True)
        (root / "profiles" / "cand" / "config.yaml").write_text("{}\n", encoding="utf-8")
        (root / "active_profile").write_text("cand", encoding="utf-8")
        monkeypatch.setenv("QUBIT_HOME", str(root))
        monkeypatch.setenv(spelling, "1")

        import sys as _sys
        from qubit_cli.main import _apply_profile_override

        monkeypatch.setattr(_sys, "argv", ["qubit", "gateway", "run"])
        _apply_profile_override()
        assert os.environ.get("QUBIT_HOME") == str(root)

    @pytest.mark.parametrize(
        "spelling",
        ["QUBIT_GATEWAY_EXTERNAL_SUPERVISOR", "HERMES_GATEWAY_EXTERNAL_SUPERVISOR"],
    )
    def test_external_supervisor_skips_active_profile(self, tmp_path, monkeypatch, spelling):
        _clear_markers(monkeypatch)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        root = tmp_path / ".qubit"
        (root / "profiles" / "cand").mkdir(parents=True)
        (root / "profiles" / "cand" / "config.yaml").write_text("{}\n", encoding="utf-8")
        (root / "active_profile").write_text("cand", encoding="utf-8")
        monkeypatch.setenv("QUBIT_HOME", str(root))
        monkeypatch.setenv(spelling, "1")

        import sys as _sys
        from qubit_cli.main import _apply_profile_override

        monkeypatch.setattr(_sys, "argv", ["qubit", "gateway", "run"])
        _apply_profile_override()
        assert os.environ.get("QUBIT_HOME") == str(root)


class TestGeneratedLaunchersWriteCanonicalSpelling:
    """Regenerated units carry the canonical name so installs converge on it."""

    def test_systemd_unit_writes_canonical_marker(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        from qubit_cli.gateway import generate_systemd_unit

        unit = generate_systemd_unit()
        assert 'Environment="QUBIT_SUPERVISED_CHILD=1"' in unit
        assert "HERMES_SUPERVISED_CHILD" not in unit

    def test_launchd_plist_writes_canonical_marker(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        from qubit_cli.gateway import generate_launchd_plist

        plist = generate_launchd_plist()
        assert "<key>QUBIT_SUPERVISED_CHILD</key>" in plist
        assert "HERMES_SUPERVISED_CHILD" not in plist

    def test_windows_task_env_writes_canonical_marker(self):
        from qubit_cli.gateway_windows import _GATEWAY_ENV

        task_env = dict(_GATEWAY_ENV)
        assert task_env["QUBIT_SUPERVISED_CHILD"] == "1"
        assert "HERMES_SUPERVISED_CHILD" not in task_env

    def test_s6_run_script_writes_canonical_markers(self):
        """The s6 run script exports BOTH the s6 sentinel and the generalized marker, both
        canonical after the rename."""
        import inspect

        from qubit_cli import service_manager

        source = inspect.getsource(service_manager)
        assert "export QUBIT_S6_SUPERVISED_CHILD=1" in source
        assert "export QUBIT_SUPERVISED_CHILD=1" in source
        assert "export HERMES_S6_SUPERVISED_CHILD=1" not in source
        assert "export HERMES_SUPERVISED_CHILD=1" not in source


class TestVenvSyncTailReadsBothSpellings:
    """``venv_sync._supervised_child`` decides whether a spawned tail still owes a repair.

    It was the half-migrated reader (only ``QUBIT_``); a launcher-marked child from an
    installed unit must not be mistaken for an interactive command.
    """

    def test_s6_spelling_is_not_a_restart_route_but_is_a_marker(self):
        from gateway.restart import s6_supervised_child_marker_present

        assert s6_supervised_child_marker_present({"HERMES_S6_SUPERVISED_CHILD": "1"}) is True
        assert s6_supervised_child_marker_present({"QUBIT_S6_SUPERVISED_CHILD": "1"}) is True
        assert s6_supervised_child_marker_present({}) is False
