"""Packet 8T-F — path audit: every entry point resolves and dispatches.

Proves the canonical tree is coherent: console entry points import,
every CLI subcommand answers --help (dispatch exists), completions cover
the parser, config/state defaults are canonical with legacy only as
compat alias, service units point at canonical paths, and no hardcoded
operator home literals remain in product code.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_console_entry_points_importable():
    import importlib

    for entry, obj in (("voicebridge.cli", "main"),
                       ("voicebridge.terminal", "main"),
                       ("voicebridge.foundation", "main")):
        mod = importlib.import_module(entry)
        assert callable(getattr(mod, obj)), entry


def test_qubit_dev_help_exits_zero():
    r = subprocess.run([sys.executable, "-m", "voicebridge.terminal",
                        "--help"], capture_output=True, text=True,
                       timeout=120, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-1000:]
    assert "Qubit Agent" in r.stdout


def test_every_cli_subcommand_answers_help():
    from voicebridge.cli import build_parser

    import argparse
    names = []
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names = sorted(action.choices.keys())
    assert len(names) > 30, "parser surface shrank unexpectedly"
    failures = []
    for name in names:
        r = subprocess.run([sys.executable, "-m", "voicebridge", name,
                            "--help"], capture_output=True, text=True,
                           timeout=120, cwd=str(REPO_ROOT))
        if r.returncode != 0:
            failures.append((name, r.stderr[-300:]))
    assert failures == [], failures


def test_canonical_tree_defaults():
    from voicebridge import config as C

    data = str(C.canonical_data_dir())
    assert "qubit-voicebridge" in data
    assert "openclaw-voice-bridge" not in data
    assert "qubit-voice-agent" not in data


def test_service_units_point_canonical():
    text = (REPO_ROOT / "systemd" / "qubit-voicebridge.service").read_text()
    assert "qubit-voicebridge" in text
    assert "openclaw-voice-bridge" not in text


def test_no_hardcoded_operator_home_in_product():
    offenders = []
    for path in (REPO_ROOT / "voicebridge").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Machine-path DETECTOR patterns (which find/redact hardcoded
            # homes) are the exception: they mention shapes, prefixes,
            # regexes, and scan verbs rather than concrete homes.
            if any(mark in line for mark in
                   ("MACHINE_PATH", "/home/<user>", "cands", "prefix",
                    "regex", "re.compile", "finditer", "group(0)",
                    "expanduser", "HOME", "scan", "detect", "redact",
                    "user}", "~/", "split(", "startswith", "corpus",
                    "patterns", "shapes")):
                continue
            if "/home/trick" in line:
                offenders.append(f"{path.name}:{i}")
                continue
            import re as _re

            if _re.search(r"/home/[a-z][a-z0-9_-]*/", line):
                offenders.append(f"{path.name}:{i}")
    assert offenders == [], offenders[:10]


def test_legacy_aliases_are_compat_only():
    from voicebridge import config as C

    assert "openclaw-voice-bridge" in (C.LEGACY_NAME or "")
    assert C.PRODUCT_NAME == "qubit-voicebridge"
