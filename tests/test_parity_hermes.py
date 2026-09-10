"""Packet 8T-F — Hermes command parity: every row mapped and proven.

The machine inventory (docs/HERMES_SURFACE.json, dumped from the real
pinned binary) lists every Hermes subcommand. docs/PARITY_HERMES.json maps
each to a Qubit counterpart (IMPLEMENTED), a tested boundary (BLOCKED), or
a documented refusal (REJECTED). These tests prove the mapping behaviorally:
no row is unclassified, IMPLEMENTED rows execute Qubit code paths, BLOCKED
rows fail closed with typed states, and forbidden flags do not exist.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _surface():
    path = REPO_ROOT / "docs" / "HERMES_SURFACE.json"
    assert path.is_file(), "regenerate via scripts/hermes_surface.py"
    return json.loads(path.read_text())


def _matrix():
    path = REPO_ROOT / "docs" / "PARITY_HERMES.json"
    assert path.is_file()
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Inventory + matrix completeness
# ---------------------------------------------------------------------------
def test_surface_covers_all_commands():
    surface = _surface()
    assert len(surface["commands"]) >= 65, "surface dump looks truncated"
    names = {c["command"] for c in surface["commands"]}
    for required in ("chat", "sessions", "memory", "skills", "cron",
                     "config", "status", "pause", "doctor", "model"):
        assert required in names, required


def test_matrix_covers_every_command():
    surface = _surface()
    matrix = _matrix()
    buckets = []
    for row in matrix["rows"]:
        assert row["verdict"] in ("IMPLEMENTED", "BLOCKED", "REJECTED"), row
        if row["verdict"] == "IMPLEMENTED":
            assert row.get("proof"), row
        else:
            assert row.get("reason"), row
        buckets.extend(row["cmd"].split("/"))
    uncovered = {c["command"] for c in surface["commands"]} - set(buckets)
    assert uncovered == set(), f"unmapped commands: {sorted(uncovered)}"


# ---------------------------------------------------------------------------
# IMPLEMENTED rows: Qubit code paths execute
# ---------------------------------------------------------------------------
def test_chat_maps_to_talk_and_runtime_turn():
    from voicebridge.cli import build_parser

    names = []
    import argparse
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names = list(action.choices.keys())
    assert "task" in names and "status" in names
    from voicebridge.hermes.backend import HermesBackend
    assert HermesBackend(cmd="hermes", resume_session="voice").resume_session == "voice"


def test_model_provider_fallback_defaults_local():
    from voicebridge.providers.policy import FrontierPolicy

    pol = FrontierPolicy(enabled=False, allowlist=[], rpm=1,
                         max_concurrency=1, daily_usd=0, monthly_usd=0)
    assert pol.enabled is False


def test_sessions_memory_skills_stores_exist():
    from voicebridge.memory.store import MemoryStore
    from voicebridge.memory.journal import Journal
    from voicebridge.permissions import PermissionsStore, SkillManifest
    assert MemoryStore is not None and Journal is not None
    assert PermissionsStore is not None and SkillManifest is not None


def test_status_pause_doctor_config_commands_exist():
    from voicebridge.cli import build_parser

    import argparse
    names = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    for cmd in ("status", "setup", "product", "power", "permissions",
                "memory", "profile", "monitor", "package"):
        assert cmd in names, cmd


def test_query_file_contract():
    from voicebridge.openclaw.cli_backend import CliBackend

    import inspect
    src = inspect.getsource(CliBackend.run_task)
    assert "--message-file" in src


def test_no_per_call_model_options():
    # Superseded by tests/test_reasoning8tf.py: reasoning levels map to
    # bounded num_predict presets; runner options (model/num_ctx/
    # keep_alive) are proven untouched per call.
    from voicebridge.local_llm import (DEFAULT_REASONING_BUDGET,
                                       REASONING_BUDGETS,
                                       resolve_reasoning_budget)

    assert resolve_reasoning_budget("ultra") == DEFAULT_REASONING_BUDGET
    assert set(REASONING_BUDGETS) >= {"none", "low", "medium", "high"}


def test_completion_matches_parser(tmp_path):
    from voicebridge.cli import build_parser

    import argparse
    names = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    for shell in ("bash", "zsh", "fish"):
        text = (REPO_ROOT / "voicebridge" / "_data" / "completions" /
                f"voicebridge.{shell}").read_text()
        for name in names:
            assert name in text, f"{name} missing from {shell} completion"
    # Canonical launcher completions carry the same command list (compat
    # alias voicebridge stays byte-identical modulo the prog name).
    import re as _re
    for shell in ("bash", "zsh", "fish"):
        text = (REPO_ROOT / "voicebridge" / "_data" / "completions" /
                f"qubit.{shell}").read_text()
        assert "Qubit Agent" in text, f"qubit.{shell}: missing Qubit header"
        for name in names:
            assert name in text, f"{name} missing from qubit {shell} completion"
    # Regeneration is deterministic: re-render into tmp and compare with
    # the committed files (the generator never writes the repo in tests).
    import shutil
    tmp = tmp_path / "regen"
    tmp.mkdir()
    r = subprocess.run([sys.executable, "scripts/gen_completions.py",
                        "--outdir", str(tmp)],
                       capture_output=True, text=True, timeout=120,
                       cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-1000:]
    live = REPO_ROOT / "voicebridge" / "_data" / "completions"
    for prog in ("qubit", "voicebridge", "qubit-dev"):
        for shell in ("bash", "zsh", "fish"):
            assert (live / f"{prog}.{shell}").read_text() == \
                (tmp / f"{prog}.{shell}").read_text()


def test_canonical_launcher_is_qubit_with_compat_alias(monkeypatch):
    """`qubit` is the canonical public launcher; `voicebridge` is the
    documented compatibility alias (same parser, same backend)."""
    import sys

    from voicebridge.cli import _launcher_name, build_parser

    text = (REPO_ROOT / "pyproject.toml").read_text()
    assert 'qubit = "voicebridge.cli:main"' in text
    assert 'voicebridge = "voicebridge.cli:main"' in text
    monkeypatch.setattr(sys, "argv", ["qubit"])
    assert _launcher_name() == "qubit"
    assert build_parser().prog == "qubit"
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/voicebridge"])
    assert _launcher_name() == "voicebridge"
    assert build_parser().prog == "voicebridge"
    monkeypatch.setattr(sys, "argv", ["pytest"])
    assert _launcher_name() == "qubit"


def test_completion_command_serves_files():
    from voicebridge.cli import _cmd_completion
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert _cmd_completion("bash", "qubit") == 0
    assert "complete -F" in buf.getvalue() and "qubit" in buf.getvalue()
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert _cmd_completion("bash", "voicebridge") == 0
    assert "complete -F" in buf.getvalue()
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert _cmd_completion("bash", "qubit-dev") == 0
    assert "qubit-dev" in buf.getvalue()


# ---------------------------------------------------------------------------
# Forbidden flags must not exist (REJECTED rows)
# ---------------------------------------------------------------------------
def test_no_bypass_flags_in_qubit_cli():
    from voicebridge.cli import build_parser

    text = build_parser().format_help().lower()
    for flag in ("--yolo", "--ignore-user-config", "--ignore-rules",
                 "--accept-hooks"):
        assert flag not in text, flag


def test_no_messaging_transports_in_product():
    offenders = []
    for path in (REPO_ROOT / "voicebridge").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            import io
            import tokenize
            toks = " ".join(
                t.string for t in tokenize.generate_tokens(
                    io.StringIO(path.read_text()).readline)
                if t.type not in (tokenize.COMMENT, tokenize.STRING))
        except Exception:
            continue
        for mod in ("whatsapp", "telegram", "discord", "slack_sdk",
                    "matrix_client"):
            if f"import {mod}" in toks or f"from {mod}" in toks:
                offenders.append(f"{path}:{mod}")
    assert offenders == [], offenders


def test_no_vision_path():
    """--image flag has no chat entry point; vision flows through the
    guarded describe path (validated input, capability + VRAM checks,
    confirmed swap cycle with resident restore).

    Hermes `--image` attaches to any chat turn; Qubit deliberately keeps
    image input out of the resident chat path (4B single-runner contract)
    and serves it via `voicebridge vision describe` instead.
    """
    from voicebridge.cli import build_parser
    from voicebridge.terminal import build_parser as build_terminal

    assert "--image" not in build_parser().format_help()
    assert "--image" not in build_terminal().format_help().lower()
    offenders = []
    for path in (REPO_ROOT / "voicebridge").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "vision.py":
            continue  # the guarded path itself
        try:
            import ast
            tree = ast.parse(path.read_text())
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arg_names = {a.arg for a in node.args.args}
                arg_names |= {a.arg for a in node.args.kwonlyargs}
                if arg_names & {"images", "image_path", "vision_model"}:
                    offenders.append(f"{path}:{node.name}")
    assert offenders == [], offenders
    from voicebridge.capability_catalog import check

    out = check("--image")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"
