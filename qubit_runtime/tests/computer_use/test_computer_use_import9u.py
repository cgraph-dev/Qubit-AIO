"""Regression tests for the computer_use partial-sync import crash.

A sync merged the newer ``tools/computer_use/*`` sources that referenced a
provider-plugin package (``plugins.computer_use``) which was never brought over.
Importing the tool module was fine, but ``check_computer_use_requirements()``
raised ``ModuleNotFoundError`` on every call, so the registry's ``check_fn``
failed and the tool silently vanished from the schema each turn.

These assert the behavior contract — the check and the readiness probe resolve
without the missing package — not the module's source shape.
"""

from __future__ import annotations

import pytest

from tools.computer_use import backend as cu_backend
from tools.computer_use import permissions as cu_permissions
from tools.computer_use import tool as cu_tool


def test_check_requirements_does_not_raise():
    """The registry check_fn must return a bool, never raise ModuleNotFoundError."""
    assert isinstance(cu_tool.check_computer_use_requirements(), bool)


def test_tool_permissions_and_backend_import():
    """The three formerly-coupled modules import and expose their real entry points."""
    assert callable(cu_tool.handle_computer_use)
    assert callable(cu_permissions.computer_use_status)
    assert cu_backend.ComputerUseBackend is not None


def test_new_backend_honours_noop_override(monkeypatch):
    """``_new_backend`` still selects the test noop from the documented env override."""
    monkeypatch.setenv("HERMES_COMPUTER_USE_BACKEND", "noop")
    backend = cu_tool._new_backend("standard")
    try:
        assert isinstance(backend, cu_tool._NoopBackend)
    finally:
        backend.stop()


def test_permissions_status_resolves_without_missing_package(monkeypatch):
    """Readiness must answer (not crash) even when no driver resolves.

    Before the fix this call raised ``ModuleNotFoundError: plugins.computer_use``
    on its import; pinning the resolver to None also keeps it host-independent.
    """
    monkeypatch.setattr(
        "tools.computer_use.cua_backend_driver.resolve_cua_driver_cmd", lambda *a, **k: None
    )
    status = cu_permissions.computer_use_status()
    assert isinstance(status, dict)
    assert status["installed"] is False
    assert status["ready"] is None
