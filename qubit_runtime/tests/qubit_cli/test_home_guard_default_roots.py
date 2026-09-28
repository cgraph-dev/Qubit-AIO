"""The real-home guard must know the Qubit-owned default (``.qubit``), not only
the legacy ``.hermes``.

Regression for the harness artifact: presetting ``QUBIT_HOME=$HOME/.qubit`` made
``conftest`` treat the throwaway default as a *genuinely custom* home, so the
session both ran against it AND registered it as a guarded "real" root — every
read under it tripped ``TEST BUG: file I/O against the REAL hermes home`` (606
failures across the rebased suite). The canonical default is production-shaped
and must be sandboxed. The legacy external ``.hermes`` must stay refused.
"""
from __future__ import annotations

from tests import conftest


def _fake_defaults(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(
        conftest, "_get_platform_default_qubit_home", lambda: fake_home / ".qubit"
    )
    monkeypatch.setattr(
        conftest, "_legacy_platform_default_qubit_home", lambda: fake_home / ".hermes"
    )
    return fake_home


def test_both_platform_defaults_are_production_shaped(tmp_path, monkeypatch):
    fake_home = _fake_defaults(monkeypatch, tmp_path)
    assert conftest._qubit_home_points_at_production(str(fake_home / ".qubit"))
    assert conftest._qubit_home_points_at_production(str(fake_home / ".hermes"))
    assert conftest._qubit_home_points_at_production(
        str(fake_home / ".qubit" / "profiles" / "default")
    )
    assert not conftest._qubit_home_points_at_production(str(fake_home / "custom"))
    # An unset value is treated as production (the conftest sandboxes it anyway).
    assert conftest._qubit_home_points_at_production("")


def test_capture_guards_the_canonical_and_legacy_defaults(tmp_path, monkeypatch):
    fake_home = _fake_defaults(monkeypatch, tmp_path)
    monkeypatch.setattr(conftest, "_PRE_SANDBOX_HERMES_HOME", "")
    roots = conftest._capture_real_qubit_root()
    assert (fake_home / ".qubit").resolve() in roots
    assert (fake_home / ".hermes").resolve() in roots
