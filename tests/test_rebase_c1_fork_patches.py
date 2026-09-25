# tests/test_rebase_c1_fork_patches.py
"""C1 gate: the genuine Qubit fork patches in ``qubit_cli/`` re-applied on top
of the engine v0.21.5 reset (``FORK_PATCH_MATRIX.md`` §2.3/§4/§5).

The import-heavy probes run the runtime from its own tree under an isolated home
and never write a product tree.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"


@pytest.fixture
def runtime_on_path(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.syspath_prepend(str(RUNTIME))
    yield tmp_path


def _read(runtime_on_path, rel: str) -> str:
    return (RUNTIME / rel).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# §4.8 web_routers/status.py — additive ``offset`` log pagination
# --------------------------------------------------------------------------- #

def test_logs_offset_pagination(runtime_on_path, monkeypatch):
    status = importlib.import_module("qubit_cli.web_routers.status")
    home = runtime_on_path / "home"
    (home / "logs").mkdir(parents=True, exist_ok=True)
    lines = [f"line-{i:02d}" for i in range(1, 11)]
    (home / "logs" / "agent.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr(status, "get_qubit_home", lambda: home)
    monkeypatch.setattr(status, "_config_profile_scope", lambda profile: contextlib.nullcontext())

    page0 = asyncio.run(status.get_logs(file="agent", lines=3))
    assert [l.rstrip("\n") for l in page0["lines"]] == ["line-08", "line-09", "line-10"]
    assert (page0["offset"], page0["limit"], page0["returned"]) == (0, 3, 3)
    assert page0["has_more"] is True and page0["next_offset"] == 3

    page1 = asyncio.run(status.get_logs(file="agent", lines=3, offset=3))
    assert [l.rstrip("\n") for l in page1["lines"]] == ["line-05", "line-06", "line-07"]
    assert page1["next_offset"] == 6 and page1["has_more"] is True

    last = asyncio.run(status.get_logs(file="agent", lines=3, offset=8))
    assert [l.rstrip("\n") for l in last["lines"]] == ["line-01", "line-02"]
    assert last["has_more"] is False and last["next_offset"] is None

    # offset=0 with a large page is byte-identical to the pre-patch behaviour.
    whole = asyncio.run(status.get_logs(file="agent", lines=100))
    assert [l.rstrip("\n") for l in whole["lines"]] == lines
    assert whole["has_more"] is False

    missing = asyncio.run(status.get_logs(file="errors", lines=3))
    assert missing["lines"] == [] and missing["returned"] == 0


# --------------------------------------------------------------------------- #
# §2.3 #34 sessions_repair_profiles dependency — fork-only repair module
# --------------------------------------------------------------------------- #

def test_session_profile_repair_module_present(runtime_on_path):
    mod = importlib.import_module("qubit_cli.qubit_state_profile_repair")
    assert mod.session_key_profile("agent:default:abc") == "default"
    assert mod.session_key_profile("agent:work:abc") == "work"


# --------------------------------------------------------------------------- #
# §2.3 #18 main_tui_launch.py — QUBIT_TUI_FORCE_BUILD alias
# --------------------------------------------------------------------------- #

def test_tui_force_build_qubit_alias(runtime_on_path, monkeypatch):
    mod = importlib.import_module("qubit_cli.main_tui_launch")
    source_build = importlib.import_module("qubit_cli.source_build")
    monkeypatch.setattr(source_build, "source_product_current", lambda *a, **k: True)

    monkeypatch.delenv("QUBIT_TUI_FORCE_BUILD", raising=False)
    monkeypatch.delenv("HERMES_TUI_FORCE_BUILD", raising=False)
    assert mod._tui_need_rebuild(runtime_on_path / "ui-tui") is False

    monkeypatch.setenv("QUBIT_TUI_FORCE_BUILD", "1")
    assert mod._tui_need_rebuild(runtime_on_path / "ui-tui") is True

    monkeypatch.delenv("QUBIT_TUI_FORCE_BUILD")
    monkeypatch.setenv("HERMES_TUI_FORCE_BUILD", "true")
    assert mod._tui_need_rebuild(runtime_on_path / "ui-tui") is True


# --------------------------------------------------------------------------- #
# §2.3 #23/#29 de-branded source strings
# --------------------------------------------------------------------------- #

def test_plugins_skill_doc_debranded(runtime_on_path):
    src = _read(runtime_on_path, "qubit_cli/plugins.py")
    assert "~/.hermes/skills/" not in src
    assert "$QUBIT_HOME/skills/" in src
    assert "hermes-plugin-command-await" not in src
    assert "qubit-plugin-command-await" in src


def test_runtime_provider_backends_env_path_debranded(runtime_on_path):
    src = _read(runtime_on_path, "qubit_cli/runtime_provider_backends.py")
    assert "~/.hermes/.env" not in src
    assert "$QUBIT_HOME/.env" in src


def test_kanban_help_has_no_legacy_docs_url(runtime_on_path):
    src = _read(runtime_on_path, "qubit_cli/kanban_parser.py")
    assert "hermes-agent.nousresearch.com" not in src


# --------------------------------------------------------------------------- #
# §4.10 profiles.py — console-script identity + honcho read-old
# --------------------------------------------------------------------------- #

def test_profiles_console_script_identity(runtime_on_path):
    prof = importlib.import_module("qubit_cli.profiles")
    assert prof._QUBIT_CONSOLE_SCRIPT_NAMES == frozenset({"qubit", "qubit-agent", "qubit-acp"})
    assert prof._is_qubit_argv(["qubit"]) is True
    assert prof._is_qubit_argv(["hermes"]) is False
    assert prof._is_qubit_argv(["/usr/bin/python3", "qubit-agent"]) is True
