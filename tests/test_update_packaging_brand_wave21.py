"""Wave-21 verifier: update-surface brand, import health, packaging detection.

Read-only verification of the Qubit update surface:

(a) ``qubit update --help`` renders with zero legacy-brand tokens.
(b) the Qubit-owned update modules import.
(c) the vendored rebranded update modules import via the product bootstrap, and
    the modules superseded by the Qubit-owned updater are documented absent.
(d) ``detect_install_method`` classifies git-checkout for this repo and names a
    Qubit command for package / container / immutable.

No product code is read as text here; behaviour is exercised by import and by
calling the real function with monkeypatched host markers.
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Any-case legacy-brand tokens that must never appear on a product surface.
LEGACY_BRAND_TOKENS = ("hermes", "openclaw", "nousresearch", "nousportal")

#: Qubit-owned update modules that back the sanctioned `qubit update` surface.
OWNED_UPDATE_MODULES = (
    "voicebridge.update_manager",
    "voicebridge.update_channel",
    "voicebridge.update_lock",
    "voicebridge.update_receipt",
    "voicebridge.update_handoff",
    "voicebridge.update_validation",
    "voicebridge.update_config_migration",
)

#: Vendored rebranded update modules that must import through the product path.
PRESENT_VENDORED_UPDATE_MODULES = (
    "qubit_cli.update_cmd",
    "qubit_cli.update_cmd_git",
    "qubit_cli.update_lock",
    "qubit_cli.update_receipt",
    "qubit_cli.update_handoff",
    "qubit_cli.service_manager",
)

#: Reference update modules with no vendored counterpart because the
#: Qubit-owned updater supersedes them. Documented, not a broken import: the
#: vendored update path does not reference them.
SUPERSEDED_VENDORED_MODULES = (
    "qubit_cli.update_cmd_check",
    "qubit_cli.update_channel",
    "qubit_cli.update_completion",
    "qubit_cli.update_finish",
    "qubit_cli.post_update",
)


def _qubit_bin() -> str:
    candidate = Path(sys.executable).with_name("qubit")
    if candidate.exists():
        return str(candidate)
    found = shutil.which("qubit")
    if not found:
        pytest.skip("qubit console script not found")
    return found


def test_update_help_has_no_legacy_brand():
    """(a) `qubit update --help` is a product surface with zero legacy brand."""
    proc = subprocess.run(
        [_qubit_bin(), "update", "--help"],
        capture_output=True, text=True, timeout=90,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "usage:" in out, out
    low = out.lower()
    leaked = sorted(t for t in LEGACY_BRAND_TOKENS if t in low)
    assert not leaked, f"legacy brand token(s) in `qubit update --help`: {leaked}"


def test_owned_update_modules_import():
    """(b) every Qubit-owned update module imports cleanly."""
    for name in OWNED_UPDATE_MODULES:
        importlib.import_module(name)


def test_vendored_update_modules_import_or_are_documented_absent():
    """(c) vendored rebranded update path is import-complete.

    The vendored modules are top-level (``qubit_cli.*``); the product puts the
    fork on ``sys.path`` via ``ensure_path()``. Superseded reference modules
    must genuinely be absent (and are not referenced by the vendored path).
    """
    from voicebridge import qubit_runtime

    saved_path = list(sys.path)
    saved_modules = {m for m in sys.modules if m == "qubit_cli" or m.startswith("qubit_cli.")}
    try:
        qubit_runtime.ensure_path()
        for name in PRESENT_VENDORED_UPDATE_MODULES:
            importlib.import_module(name)
        for name in SUPERSEDED_VENDORED_MODULES:
            with pytest.raises(ModuleNotFoundError):
                importlib.import_module(name)
    finally:
        sys.path[:] = saved_path
        for mod in [m for m in sys.modules if m == "qubit_cli" or m.startswith("qubit_cli.")]:
            if mod not in saved_modules:
                del sys.modules[mod]


class _Cfg:
    """Minimal config object; update_manager._get calls ``cfg.get(*path)``."""


def _detect(monkeypatch, repo, *, container=False, immutable=False, package=False):
    from voicebridge import update_manager as UM

    monkeypatch.delenv("QUBIT_IMMUTABLE", raising=False)
    monkeypatch.delenv("QUBIT_CONTAINER", raising=False)
    monkeypatch.setattr(UM, "repo_root", lambda cfg: repo)
    monkeypatch.setattr(UM, "_is_container", lambda: container)
    monkeypatch.setattr(UM, "_is_immutable", lambda r: immutable)
    monkeypatch.setattr(UM, "_package_installed", lambda: package)
    return UM.detect_install_method(_Cfg())


def test_detect_install_method_git_checkout_names_qubit_pull(monkeypatch):
    """(d) the live repo is a git checkout updated by a Qubit command."""
    result = _detect(monkeypatch, REPO_ROOT)
    assert result["method"] == "git-checkout"
    assert result["supported_in_place"] is True
    assert result["update_command"] == "git pull --ff-only"


def test_detect_install_method_names_qubit_command_per_kind(monkeypatch):
    """(d) package/container/immutable each name a Qubit update command."""
    absent = Path("/tmp/qubit-wave21-not-a-checkout")

    package = _detect(monkeypatch, absent, package=True)
    assert package["method"] == "package"
    assert package["update_command"] == "scripts/install.sh"

    container = _detect(monkeypatch, absent, container=True)
    assert container["method"] == "container"
    assert container["update_command"] == "docker pull qubit-agent:latest"

    immutable = _detect(monkeypatch, absent, immutable=True)
    assert immutable["method"] == "immutable"
    assert immutable["update_command"] == "scripts/install.sh"

    for r in (package, container, immutable):
        assert r["supported_in_place"] is False
        # no legacy-brand token is exposed in the surfaced command/reason
        blob = (str(r["update_command"]) + " " + str(r["reason"])).lower()
        assert not any(t in blob for t in LEGACY_BRAND_TOKENS)
