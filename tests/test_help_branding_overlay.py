"""Wave 17 — forwarded-fork CLI help is Qubit-branded at the dispatch boundary.

The vendored fork under ``qubit_runtime/`` stays byte-identical, so its argparse
help is rebranded by :mod:`voicebridge.branding.help_overlay` when
``qubit agent``/``qubit <family> --help`` dispatches through
:func:`voicebridge.qubit_runtime.run`. This scan fails on any user-facing
upstream product name left in ``qubit --help`` or the forwarded family help,
allowing only live identifiers the runtime still reads (env vars, the
``--hermes-root`` option, the vendored ``hermes-agent-dev`` skill, and the
macOS signing-identity default) plus legal/provenance lines.
"""
from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_UPSTREAM = re.compile(r"hermes|openclaw", re.IGNORECASE)

# Live identifiers/value the runtime still reads verbatim; rewriting them in
# help would misreport a value the parser or user must actually use.
ALLOWED_IDENTIFIERS = (
    "HERMES_INFERENCE_MODEL",
    "HERMES_ACCEPT_HOOKS",
    "HERMES_DESKTOP_HERMES_ROOT",
    "HERMES_DESKTOP_CWD",
    "HERMES_ROOT",
    "--hermes-root",
    "hermes-agent-dev",
    "Hermes Local Signing",
)

# family -> argv passed to qubit_runtime.run (the exact pre-parse route in
# voicebridge/cli.py::main for each forwarded surface).
FORWARDED_FAMILIES = {
    "agent": ["--help"],
    "hermes": ["--help"],
    "bundles": ["bundles", "--help"],
    "claw": ["claw", "--help"],
    "console": ["console", "--help"],
    "debug": ["debug", "--help"],
    "desktop": ["desktop", "--help"],
    "dump": ["dump", "--help"],
    "gui": ["gui", "--help"],
    "memory-graph": ["memory-graph", "--help"],
    "moa": ["moa", "--help"],
    "pause": ["pause", "--help"],
    "portal": ["portal", "--help"],
    "prompt-size": ["prompt-size", "--help"],
    "worktree": ["worktree", "--help"],
}

# Legal/provenance lines (a licence/notice URL) are allowed by the frozen
# contract and are never rewritten by the overlay either.
_LEGAL_MARKERS = ("Copyright", "Licence", "License", "SPDX",
                  "Nous Research", "http://", "https://")


def _residual(text: str) -> list:
    for token in ALLOWED_IDENTIFIERS:
        text = text.replace(token, "")
    text = "\n".join(
        line for line in text.splitlines()
        if not any(marker in line for marker in _LEGAL_MARKERS))
    return [m.group(0) for m in _UPSTREAM.finditer(text)]


def _fork_help(argv: list) -> str:
    from voicebridge import qubit_runtime
    from voicebridge.branding import help_overlay

    if not qubit_runtime.runtime_root().is_dir():
        pytest.skip("vendored runtime absent from this checkout")
    help_overlay.install()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), help_overlay.active():
        try:
            qubit_runtime.run(list(argv))
        except SystemExit:
            pass
    return buf.getvalue()


def test_rebrand_help_rewrites_prose_and_preserves_identifiers():
    from voicebridge.branding.help_overlay import rebrand_help

    out = rebrand_help(
        "Update Hermes Agent to the latest version\n"
        "peer Hermes gateways\n"
        "Inspect / prune / clear ~/.hermes/checkpoints/\n"
        "Use 'hermes debug share' here\n"
        "Migrate from OpenClaw to Hermes\n"
        "Migrate settings ... from OpenClaw to Qubit\n"
        "Archive leftover OpenClaw directories after migration\n"
        "settable via HERMES_INFERENCE_MODEL and HERMES_ACCEPT_HOOKS\n"
        "qubit -s hermes-agent-dev,github-auth\n"
        "Add --hermes-root HERMES_ROOT (sets HERMES_DESKTOP_HERMES_ROOT)\n"
        "Initial project dir (sets HERMES_DESKTOP_CWD)\n"
        "default: Hermes Local Signing\n"
    )
    assert "Update Qubit Agent to the latest version" in out
    assert "peer Qubit gateways" in out
    assert "~/.qubit/checkpoints/" in out
    assert "'qubit debug share'" in out
    assert "from a legacy assistant to Qubit" in out
    assert "leftover legacy-assistant directories" in out
    for token in ALLOWED_IDENTIFIERS:
        assert token in out, token


def test_rebrand_help_leaves_legal_provenance_lines_untouched():
    from voicebridge.branding.help_overlay import rebrand_help

    legal = "Copyright (c) 2025 Nous Research — MIT; see LICENSE\n"
    out = rebrand_help(legal + "Update Hermes Agent\n")
    assert out.startswith(legal)
    assert "Update Qubit Agent" in out


def test_top_level_qubit_help_is_clean():
    from voicebridge.cli import build_parser

    text = build_parser().format_help()
    residual = _residual(text)
    assert not residual, f"qubit --help exposes upstream names: {residual}"


def test_forwarded_family_help_has_no_user_facing_upstream_names():
    offenders = []
    for family, argv in FORWARDED_FAMILIES.items():
        text = _fork_help(argv)
        assert text.strip(), f"{family}: no help captured"
        residual = _residual(text)
        if residual:
            offenders.append(f"{family} {argv}: {residual}")
    assert not offenders, (
        "forwarded family help still exposes upstream names: " + "; ".join(
            offenders))
