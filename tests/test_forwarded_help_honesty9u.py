"""Forwarded/native help text matches reality (audit fix-channels-2, 2026-10-10).

The product was telling the user things that were not true:

* ``qubit pause --help`` named ``qubit resume`` -- a command Qubit deliberately
  refuses (a second emergency-stop authority). The real lift command is
  ``qubit power enable --confirm``;
* ``qubit memory-graph --help`` printed ``usage: qubit journey`` and
  ``qubit gui --help`` printed ``usage: qubit desktop`` -- the fork registers
  these as argparse *aliases* of the primary parser, so the invoked name was
  lost;
* ``qubit claw ... --help`` read "from Qubit to Qubit" / "leftover Qubit
  directories" -- the de-branded migration prose was incoherent;
* ``qubit receipts`` family help read like a ``verify`` subverb that does not
  exist.

All fixes are at the render boundary (the vendored fork stays byte-identical).
Hermetic: pure ``rebrand_help`` checks plus in-process fork-help capture.
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _fork_help(argv):
    from voicebridge import qubit_runtime
    from voicebridge.branding import help_overlay

    if not qubit_runtime.runtime_root().is_dir():
        pytest.skip("vendored runtime absent from this checkout")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), help_overlay.active():
        try:
            qubit_runtime.run(list(argv))
        except SystemExit:
            pass
    return buf.getvalue()


# ------------------------------------------------------------- pause/resume
def test_pause_help_names_a_real_resume_command():
    text = _fork_help(["pause", "--help"])
    assert "qubit resume" not in text, text
    assert "qubit power enable --confirm" in text, text


# ------------------------------------------------------------- alias prog
def test_memory_graph_help_uses_invoked_name():
    text = _fork_help(["memory-graph", "--help"])
    assert text.splitlines()[0].startswith("usage: qubit memory-graph"), text
    assert "usage: qubit journey" not in text, text


def test_gui_help_uses_invoked_name():
    text = _fork_help(["gui", "--help"])
    assert text.splitlines()[0].startswith("usage: qubit gui"), text
    assert "usage: qubit desktop" not in text, text


def test_canonical_journey_prog_still_journey():
    text = _fork_help(["journey", "--help"])
    assert text.splitlines()[0].startswith("usage: qubit journey"), text


# ------------------------------------------------------------- claw migration
def test_claw_is_install_gated_when_its_plugin_is_absent():
    """`qubit claw` left core for the agent-importers plugin. The Qubit surface
    must name the install command instead of advertising a command that cannot
    run (the old forwarded help was captured from a plugin that is not shipped)."""
    from voicebridge import cli_qubit_forward as HF

    route = HF.family_refusal(["claw"])
    assert route is not None
    assert route.code == "QUBIT_CLAW_PLUGIN_REQUIRED"
    assert "qubit plugins install agent-importers" in route.detail
    assert "qubit import-agent" in route.detail


def test_rebrand_pure_migration_phrases():
    from voicebridge.branding.help_overlay import rebrand_help

    assert ("from a legacy assistant to Qubit"
            in rebrand_help("Migrate from Qubit to Qubit"))
    assert ("leftover legacy-assistant directories"
            in rebrand_help("Archive leftover Qubit directories"))


# ------------------------------------------------------------- receipts
def test_receipts_family_help_does_not_imply_verify_subverb():
    from voicebridge.cli import build_parser

    text = build_parser().format_help()
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.strip().startswith("receipts "))
    # The entry may wrap onto following, more-indented lines; join them.
    indent = len(lines[start]) - len(lines[start].lstrip())
    entry = lines[start]
    i = start + 1
    while i < len(lines):
        nxt = lines[i]
        if not nxt.strip():
            break
        nindent = len(nxt) - len(nxt.lstrip())
        if nindent <= indent:
            break
        entry += " " + nxt.strip()
        i += 1
    entry = " ".join(entry.split())
    assert "no subcommands" in entry, entry


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
