"""Wave 24 — version / help / parser rebrand regressions (forensic HIGH findings).

Locks in three previously-flagged defects:

1. the vendored subproject version must match the pin's upstream tag
   (``v0.21.5``) in ``qubit_runtime/pyproject.toml`` and never drift back to the
   stale ``0.21.1`` the ``qubit agent --version`` banner used to advertise;
2. ``voicebridge.qubit_runtime._rebrand_epilogue`` must strip the 5-char
   ``qubit`` token (not the 6-char ``hermes`` one), preserving the separator —
   ``qubit chat``, never ``qubitchat``;
3. ``--ignore-user-config`` help must name the Qubit home's ``config.yaml``, not
   ``~/.hermes/config.yaml``.

Hermetic: no network, no model, no service. Reads only in-repo files and builds
the vendored top-level parser in-process.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"
VENDORED_PYPROJECT = RUNTIME / "pyproject.toml"
PRODUCT_PYPROJECT = REPO / "pyproject.toml"
CLI_INIT = RUNTIME / "qubit_cli" / "__init__.py"
VENDOR_MANIFEST = REPO / "scripts" / "hermes_vendor_manifest.py"

# The v0.21.5 pin (``69948c0057``) as the repo records it.
PIN_TAG = "v0.21.5"
PIN_VERSION = "0.21.5"
PIN_RELEASE_DATE = "2026.9.24"
STALE_RELEASE_DATE = "2026.9.7"

# The off-by-one bug concatenated the program token with the next argument.
_BROKEN_TOKENS = ("qubitchat", "qubit--tui", "qubit-c", "qubitsetup", "qubitgateway")


def _load_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _import_runtime_module(name: str):
    """Import a module from the vendored ``qubit_runtime`` tree."""
    if str(RUNTIME) not in sys.path:
        sys.path.insert(0, str(RUNTIME))
    return __import__(name, fromlist=["*"])


# --------------------------------------------------------------------------- #
# item 1 — the vendored version matches the pin's tag
# --------------------------------------------------------------------------- #
def test_vendored_version_matches_the_pin_tag():
    project = _load_toml(VENDORED_PYPROJECT)["project"]
    assert project["name"] == "qubit-agent"
    assert project["version"] == PIN_VERSION

    manifest = VENDOR_MANIFEST.read_text(encoding="utf-8")
    tag = re.search(r'^UPSTREAM_TAG = "([^"]+)"', manifest, re.MULTILINE)
    assert tag is not None, "UPSTREAM_TAG not found in the vendor manifest generator"
    assert tag.group(1) == PIN_TAG
    # The declared version is the pin's tag minus the leading ``v``.
    assert project["version"] == tag.group(1).lstrip("v")


def test_vendored_release_date_matches_the_pin():
    source = CLI_INIT.read_text(encoding="utf-8")
    assert f'__release_date__ = "{PIN_RELEASE_DATE}"' in source
    assert STALE_RELEASE_DATE not in source


def test_version_bump_left_the_product_version_alone():
    assert _load_toml(PRODUCT_PYPROJECT)["project"]["version"] == "1.0.0"


# --------------------------------------------------------------------------- #
# item 2 — the help epilogue keeps the separator after the program token
# --------------------------------------------------------------------------- #
def test_rebrand_epilogue_preserves_separator_on_flagged_samples():
    from voicebridge.qubit_runtime import _rebrand_epilogue

    text = "\n".join(
        [
            "qubit chat                   Chat with the agent",
            "qubit --tui                  Launch the modern TUI",
            "qubit -c                     Resume a session",
            "qubit setup                  Run setup wizard",
            "qubit gateway install        Install gateway background service",
        ]
    ) + "\n"
    out = _rebrand_epilogue(text, "qubit")
    assert out is not None
    for good in ("qubit chat", "qubit --tui", "qubit -c", "qubit setup", "qubit gateway install"):
        assert good in out
    for broken in _BROKEN_TOKENS:
        assert broken not in out


def test_rebrand_epilogue_over_the_real_vendored_epilogue():
    parser_mod = _import_runtime_module("qubit_cli._parser")
    from voicebridge.qubit_runtime import _rebrand_epilogue

    out = _rebrand_epilogue(parser_mod._EPILOGUE, "qubit")
    assert out is not None
    for broken in _BROKEN_TOKENS:
        assert broken not in out
    for good in ("qubit chat", "qubit --tui", "qubit setup", "qubit gateway install"):
        assert good in out


# --------------------------------------------------------------------------- #
# item 3 — ``--ignore-user-config`` help names the Qubit home, not ``~/.hermes``
# --------------------------------------------------------------------------- #
@pytest.fixture
def qubit_home_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    qubit_home = home / ".qubit"
    qubit_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(qubit_home))
    return qubit_home


def test_ignore_user_config_help_names_the_qubit_home(qubit_home_env):
    parser_mod = _import_runtime_module("qubit_cli._parser")
    top, _subparsers, chat_parser = parser_mod.build_top_level_parser()

    helps: list[str] = []
    for parser in (top, chat_parser):
        for action in parser._actions:
            if "--ignore-user-config" in action.option_strings:
                helps.append(action.help or "")
    assert helps, "--ignore-user-config help not found on the top-level/chat parser"
    for help_text in helps:
        assert "~/.hermes" not in help_text
        assert ".hermes" not in help_text
        assert "config.yaml" in help_text


def test_cfg_path_renders_the_qubit_home(qubit_home_env):
    parser_mod = _import_runtime_module("qubit_cli._parser")
    rendered = parser_mod._cfg_path()
    assert rendered.endswith("/config.yaml")
    assert ".hermes" not in rendered
    assert rendered.startswith("~/.qubit")
