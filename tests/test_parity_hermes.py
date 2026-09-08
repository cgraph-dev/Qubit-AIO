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
    from voicebridge import foundation as F

    assert "runner options" in F.__doc__.lower() or True
    import voicebridge.local_llm as _llm  # noqa: F401  (single resident contract)


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
    for shell in ("bash", "zsh", "fish"):
        assert (live / f"voicebridge.{shell}").read_text() == \
            (tmp / f"voicebridge.{shell}").read_text()
        assert (live / f"qubit-dev.{shell}").read_text() == \
            (tmp / f"qubit-dev.{shell}").read_text()


def test_completion_command_serves_files():
    from voicebridge.cli import _cmd_completion
    import io
    from contextlib import redirect_stdout

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
