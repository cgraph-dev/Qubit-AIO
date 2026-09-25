"""Wave 20 — update surfaces are Qubit-branded, no legacy leakage.

Two independent checks:

1. ``python -m voicebridge.cli update --help`` (subprocess) exits 0 and prints
   no user-facing legacy token (``Hermes`` / ``OpenClaw`` / ``NousResearch`` /
   ``NousPortal`` / ``@NousResearch`` / ``nousresearch`` / ``hermes``,
   case-insensitive).
2. Every runtime update module plus ``docs/UPDATE.md`` carries a legacy token
   **only** inside the documented safety guards: the reference
   ``hermes-dashboard.service`` restart refusal, the legacy update-source-URL
   token guard, and the ``~/.hermes`` external-reference-home refusal. Anything
   in a user-facing string / log / error is a failure.

It also asserts ``docs/UPDATE.md`` exists and names the canonical backend
(``voicebridge/update_manager.py``) and the Qubit-only command surface.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

#: The legacy tokens forbidden on update product surfaces (case-insensitive).
LEGACY_RE = re.compile(
    r"hermes|openclaw|nousresearch|nousportal|nous\s+research", re.I)

SCANNED_FILES = (
    "voicebridge/update_manager.py",
    "voicebridge/update_channel.py",
    "voicebridge/update_lock.py",
    "voicebridge/update_receipt.py",
    "voicebridge/update_validation.py",
    "voicebridge/update_config_migration.py",
    "voicebridge/update_handoff.py",
    "docs/UPDATE.md",
)

#: Exact guard lines named in the task (fallback if a concurrent edit shifts
#: line numbers but leaves the guard text intact — content matching below is
#: the primary whitelist).
EXACT_LINE_WHITELIST = {
    "voicebridge/update_manager.py": {78, 936},
    "voicebridge/update_channel.py": {74, 152, 168},
    "docs/UPDATE.md": {68, 69},
}

#: The documented safety guards. A line carrying a legacy token is allowed only
#: when it is a comment / docstring line OR matches one of these guards.
GUARD_PATTERNS = {
    "voicebridge/update_manager.py": (
        r"hermes-dashboard\.service",       # foreign unit restart refusal
    ),
    "voicebridge/update_channel.py": (
        r"LEGACY_URL_TOKENS\s*=",           # legacy source-URL token guard
        r"Path\.home\(\)\s*/\s*['\"]\.hermes['\"]",  # external-home refusal
        r"external\s+``?~/\.hermes",        # its docstring
    ),
}

#: The section in docs/UPDATE.md that documents the legacy-source guard; token
#: mentions there are the allowed documented safety reference.
DOC_GUARD_SECTION_START = re.compile(r"^##\s*4\.\s*Legacy-source guard")
DOC_GUARD_SECTION_END = re.compile(r"^##\s*5\.")


# ---------------------------------------------------------------------------
# help output
# ---------------------------------------------------------------------------
def test_update_help_has_no_legacy_brand():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "voicebridge.cli", "update", "--help"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert "qubit update" in proc.stdout, proc.stdout
    leak = LEGACY_RE.search(proc.stdout)
    assert leak is None, (
        f"legacy token {leak.group(0)!r} leaked in `qubit update --help` "
        f"output: {proc.stdout}")


# ---------------------------------------------------------------------------
# source scan with tight guard whitelists
# ---------------------------------------------------------------------------
def _docstring_flags(lines):
    """Best-effort flags for lines inside a triple-quoted block."""
    flags = [False] * len(lines)
    in_doc = False
    for i, line in enumerate(lines):
        if in_doc:
            flags[i] = True
            if line.count('"""') % 2 == 1 or line.count("'''") % 2 == 1:
                in_doc = False
            continue
        if line.count('"""') % 2 == 1 or line.count("'''") % 2 == 1:
            in_doc = True
            flags[i] = True
    return flags


def _doc_guard_section(lines):
    """Line numbers inside the docs/UPDATE.md legacy-source guard section."""
    inside = set()
    active = False
    for i, line in enumerate(lines, start=1):
        if DOC_GUARD_SECTION_START.search(line):
            active = True
        elif active and DOC_GUARD_SECTION_END.search(line):
            active = False
        if active:
            inside.add(i)
    return inside


def _allowed(rel, lineno, line, doc_flags, doc_section):
    if lineno in EXACT_LINE_WHITELIST.get(rel, set()):
        return True
    if rel.endswith(".py"):
        if line.lstrip().startswith("#"):
            return True
        if doc_flags[lineno - 1]:
            return True
        for pat in GUARD_PATTERNS.get(rel, ()):
            if re.search(pat, line):
                return True
        return False
    if rel == "docs/UPDATE.md":
        return lineno in doc_section
    return False


def _scan(rel):
    """Return (leaks, allowed) for one scanned file.

    ``leaks`` are token lines that are neither comments/docstrings nor a
    documented guard — i.e. user-facing leakage.
    """
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    lines = text.splitlines()
    if rel.endswith(".py"):
        doc_flags = _docstring_flags(lines)
        doc_section = set()
    else:
        doc_flags = [False] * len(lines)
        doc_section = _doc_guard_section(lines)
    leaks, allowed = [], []
    for i, line in enumerate(lines, start=1):
        m = LEGACY_RE.search(line)
        if not m:
            continue
        if _allowed(rel, i, line, doc_flags, doc_section):
            allowed.append((i, line.strip()))
        else:
            leaks.append((i, line.strip()))
    return leaks, allowed


@pytest.mark.parametrize("rel", SCANNED_FILES)
def test_scanned_update_files_leak_no_user_facing_legacy(rel):
    leaks, _allowed_lines = _scan(rel)
    assert not leaks, (
        f"{rel}: user-facing legacy token(s) outside a documented guard:\n"
        + "\n".join(f"  {rel}:{n}: {l}" for n, l in leaks))


def test_legacy_reference_only_in_the_named_guards():
    """The only tokens anywhere in the scanned files are the guard lines."""
    total_allowed = []
    for rel in SCANNED_FILES:
        leaks, allowed = _scan(rel)
        assert not leaks, rel
        total_allowed.extend((rel, n) for n, _ in allowed)
    # Exactly the documented guards, and nothing else.
    named = {
        ("voicebridge/update_manager.py",),  # hermes-dashboard.service refusal
        ("voicebridge/update_channel.py",),  # URL token + ~/.hermes guards
        ("docs/UPDATE.md",),                 # documents the guard
    }
    seen_files = {f for f, _ in total_allowed}
    assert seen_files == {f for (f,) in named}, (
        f"unexpected files carry legacy tokens: {seen_files}")


def test_url_token_guard_still_exists():
    text = (REPO_ROOT / "voicebridge/update_channel.py").read_text(
        encoding="utf-8")
    assert re.search(r"LEGACY_URL_TOKENS\s*=\s*\([^)]*hermes[^)]*nousresearch",
                     text, re.I), (
        "the legacy update-source-URL token guard is missing")


def test_external_reference_home_refusal_still_exists():
    text = (REPO_ROOT / "voicebridge/update_channel.py").read_text(
        encoding="utf-8")
    assert re.search(r"Path\.home\(\)\s*/\s*['\"]\.hermes['\"]", text), (
        "the external ~/.hermes reference-home refusal is missing")


def test_reference_service_refusal_is_scoped_to_owned_units():
    text = (REPO_ROOT / "voicebridge/update_manager.py").read_text(
        encoding="utf-8")
    assert re.search(
        r"OWNED_SERVICE_UNITS\s*=\s*\([^)]*qubit-voicebridge\.service", text), (
        "the updater must only own/restart Qubit units")


# ---------------------------------------------------------------------------
# docs/UPDATE.md — canonical backend + Qubit-only command surface
# ---------------------------------------------------------------------------
def test_update_doc_exists_and_names_canonical_backend():
    doc = (REPO_ROOT / "docs/UPDATE.md").read_text(encoding="utf-8")
    assert "voicebridge/update_manager.py" in doc, (
        "docs/UPDATE.md must name the canonical update backend")
    assert "voicebridge/update_channel.py" in doc
    assert "qubit update" in doc
    assert "Qubit" in doc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
