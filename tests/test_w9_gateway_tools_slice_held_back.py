"""W9 vendor-sync slice — ``gateway/**`` + ``tools/**`` port completion.

This slice diffed the vendored ``qubit_runtime/gateway`` and
``qubit_runtime/tools`` against the pinned Hermes v0.21.4 source
(``d337b736aa1e8ebecfab043842d13e4a2d2f48a3``). Exactly three pinned files had
no local counterpart, each held back because a dependency it needed was absent
locally and lived outside the slice's file-ownership scope:

* ``gateway/slash_commands_login.py``  -> needed ``qubit_cli.anon_auth``
  (the anon-auth cluster was not vendored).
* ``tools/bot_live_delivery.py``       -> needed ``utils.fsync_directory`` and
  the 0.21.4 ``utils.atomic_write_text(..., mode=, fsync_dir=)`` /
  ``utils.atomic_json_write(..., fsync_dir=)`` parameters.
* ``tools/browser_vault_tool.py``      -> needed
  ``agent.redact.register_vault_redaction_value`` (the vault-redaction
  boundary).

All three blocking primitives were subsequently ported from the same pin
(``qubit_cli/anon_auth.py``, ``utils.fsync_directory`` + the ``fsync_dir`` /
``mode`` parameters, ``agent.redact`` vault registry), and the three files were
ported with the ``hermes_* -> qubit_*`` rename map. This test pins the new
state: the held-back ledger is **empty**, every primitive exists with the shape
the ported modules call it with, and the ported modules import. It skips (does
not fail) when the ephemeral pinned source is absent.
"""
from __future__ import annotations

import importlib
import inspect
import os
import sys
import tempfile
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

PORTED = (
    "gateway/slash_commands_login.py",
    "tools/bot_live_delivery.py",
    "tools/browser_vault_tool.py",
)


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


def test_pinned_only_gateway_tools_set_is_now_empty():
    """The vendored tree carries every pinned gateway/tools file (ledger closed)."""
    pinned = _pinned_root()
    missing: set[str] = set()
    for sub in ("gateway", "tools"):
        missing |= _relative_py_files(pinned, sub) - _relative_py_files(VENDOR_ROOT, sub)
    assert missing == set(), (
        "held-back ledger drifted; a previously ported file is missing again. "
        f"unexpected: {sorted(missing)}"
    )


@pytest.mark.parametrize("rel", PORTED)
def test_previously_held_back_files_are_present(rel: str):
    assert (VENDOR_ROOT / rel).is_file()


def test_slash_commands_login_imports_against_ported_anon_auth():
    module = importlib.import_module("gateway.slash_commands_login")
    assert hasattr(module, "GatewayLoginCommandsMixin")
    anon_auth = importlib.import_module("qubit_cli.anon_auth")
    # Every anon_auth symbol the mixin reads at runtime must resolve.
    for symbol in ("LOGIN_DM_ONLY", "LOGIN_NOT_ALLOWED", "LOGIN_BUSY_ELSEWHERE",
                   "UPGRADE_ALREADY_SIGNED_IN", "UPGRADE_START", "UPGRADE_NOT_COMPLETED",
                   "GUEST_MODEL", "Code", "Waiting", "Completed",
                   "current_nous_state", "is_guest_state", "run_sign_in"):
        assert hasattr(anon_auth, symbol), f"qubit_cli.anon_auth missing {symbol}"


def test_utils_exposes_fsync_and_atomic_write_parameters():
    utils = importlib.import_module("utils")
    assert callable(utils.fsync_directory)
    assert callable(utils.atomic_write_bytes)
    text_params = inspect.signature(utils.atomic_write_text).parameters
    assert {"fsync_dir", "mode"} <= set(text_params)
    json_params = inspect.signature(utils.atomic_json_write).parameters
    assert "fsync_dir" in json_params
    assert "mode" in json_params


def test_atomic_json_write_honours_fsync_dir_and_mode(tmp_path):
    """The exact call ``cron/bot_chat_delivery.py`` makes must work."""
    utils = importlib.import_module("utils")
    target = tmp_path / "record.json"
    utils.atomic_json_write(target, {"ok": True}, fsync_dir=True, mode=0o600)
    assert target.read_text() == '{\n  "ok": true\n}'
    assert (os.stat(target).st_mode & 0o777) == 0o600


def test_redact_vault_boundary_registers_and_scrubs():
    redact = importlib.import_module("agent.redact")
    assert callable(redact.register_vault_redaction_value)
    assert callable(redact.clear_vault_redaction_values)
    assert callable(redact.redact_registered_vault_values)
    secret = "unit-test-vault-secret-xyz"
    redact.register_vault_redaction_value(secret)
    try:
        assert secret not in redact.redact_registered_vault_values(f"pw={secret}")
        assert secret not in redact.redact_sensitive_text(f"pw={secret}")
    finally:
        redact.clear_vault_redaction_values()


def test_bot_live_delivery_and_browser_vault_import():
    assert importlib.import_module("tools.bot_live_delivery").DELIVERY_DIR_NAME == "bot_live_delivery"
    browser_vault = importlib.import_module("tools.browser_vault_tool")
    assert callable(browser_vault.browser_vault_fill)
    assert callable(browser_vault.browser_vault_list)


def test_preserved_qubit_adaptation_gateway_status_keys():
    """The slice must not regress the de-branded gateway status keys (N26)."""
    status = importlib.import_module("gateway.status")
    assert status._HOME_KEY == "qubit_home"
    assert status._LEGACY_HOME_KEY == "hermes_home"
    assert status._LEGACY_GATEWAY_KIND == "hermes-gateway"
    assert status.gateway_kind_matches("qubit-gateway")
    assert status.gateway_kind_matches("hermes-gateway")
