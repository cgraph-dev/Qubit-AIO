"""W9 vendor-sync slice — ``gateway/**`` + ``tools/**`` held-back ledger.

This slice diffs the vendored ``qubit_runtime/gateway`` and
``qubit_runtime/tools`` against the pinned Hermes v0.21.4 source
(``d337b736aa1e8ebecfab043842d13e4a2d2f48a3``) and ports the genuinely-missing
files, applying the ``hermes_* -> qubit_*`` rename map. Exactly three pinned
files have no local counterpart, and each is **held back** because a
dependency it needs is absent locally — and that dependency lives outside the
slice's file-ownership scope (``qubit_runtime/gateway/**`` + ``tools/**``):

* ``gateway/slash_commands_login.py``  -> needs ``qubit_cli.anon_auth``
  (the anon-auth cluster is not vendored).
* ``tools/bot_live_delivery.py``       -> needs ``utils.fsync_directory`` and
  the 0.21.4 ``utils.atomic_write_text(..., mode=, fsync_dir=)`` /
  ``utils.atomic_json_write(..., fsync_dir=)`` parameters; the local
  ``qubit_runtime/utils.py`` predates them.
* ``tools/browser_vault_tool.py``      -> needs
  ``agent.redact.register_vault_redaction_value`` (the local vendored
  ``agent/redact.py`` predates the vault-redaction boundary).

Porting any of them without those dependencies would require inventing a
replacement for a durability/security primitive, which the slice's hard gate
forbids. This test pins the held-back set and its blocking reasons so the next
sync cannot silently drop a file or silently port one whose dependency is still
missing. It skips (does not fail) when the ephemeral pinned source is absent.
"""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "qubit_runtime"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

_PINNED_CANDIDATES = (
    Path("/tmp/opencode/pinned/hermes-agent-d337b736-git"),
    Path("/tmp/opencode/pinned/hermes-agent"),
)

HELD_BACK = {
    "gateway/slash_commands_login.py": "qubit_cli.anon_auth",
    "tools/bot_live_delivery.py": "utils.fsync_directory",
    "tools/browser_vault_tool.py": "agent.redact.register_vault_redaction_value",
}


def _pinned_root() -> Path:
    for candidate in _PINNED_CANDIDATES:
        if (candidate / "gateway").is_dir() and (candidate / "tools").is_dir():
            return candidate
    pytest.skip("pinned Hermes v0.21.4 source absent (ephemeral /tmp input)")


def _relative_py_files(root: Path, sub: str) -> set[str]:
    base = root / sub
    return {
        str(p.relative_to(root))
        for p in base.rglob("*.py")
        if "__pycache__" not in p.parts
    }


def test_pinned_only_gateway_tools_set_is_exactly_the_held_back_ledger():
    """The vendored tree is missing exactly the three documented files."""
    pinned = _pinned_root()
    missing: set[str] = set()
    for sub in ("gateway", "tools"):
        pinned_files = _relative_py_files(pinned, sub)
        local_files = _relative_py_files(VENDOR_ROOT, sub)
        missing |= pinned_files - local_files
    assert missing == set(HELD_BACK), (
        "held-back ledger drifted; update HELD_BACK and the slice report. "
        f"unexpected: {sorted(missing ^ set(HELD_BACK))}"
    )


def test_slash_commands_login_blocking_dependency_is_absent():
    assert not (VENDOR_ROOT / "qubit_cli" / "anon_auth.py").is_file()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("qubit_cli.anon_auth")


def test_bot_live_delivery_blocking_dependency_is_absent():
    utils = importlib.import_module("utils")
    assert not hasattr(utils, "fsync_directory")
    # The 0.21.4 atomic-write API the pinned module calls with.
    text_params = inspect.signature(utils.atomic_write_text).parameters
    assert "fsync_dir" not in text_params
    assert "mode" not in text_params
    json_params = inspect.signature(utils.atomic_json_write).parameters
    assert "fsync_dir" not in json_params


def test_browser_vault_blocking_dependency_is_absent():
    redact = importlib.import_module("agent.redact")
    assert not hasattr(redact, "register_vault_redaction_value")
    assert not hasattr(redact, "clear_vault_redaction_values")
    assert not hasattr(redact, "redact_registered_vault_values")


def test_preserved_qubit_adaptation_gateway_status_keys():
    """The slice must not regress the de-branded gateway status keys (N26)."""
    status = importlib.import_module("gateway.status")
    assert status._HOME_KEY == "qubit_home"
    assert status._LEGACY_HOME_KEY == "hermes_home"
    assert status._LEGACY_GATEWAY_KIND == "hermes-gateway"
    assert status.gateway_kind_matches("qubit-gateway")
    assert status.gateway_kind_matches("hermes-gateway")
