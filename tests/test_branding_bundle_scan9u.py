"""Packet 9U Phase-5 — shipped-bundle + CLI-help public-branding scan.

Enforces ``docs/archive/2026-09-22-pre-reset/QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md``
(sections 1.2, 1.3, 2.1) on the *shipped* SPA bundle and on the rendered CLI
help:

* ``frontend/qubit-dist/assets/*.js`` — the dashboard SPA chunks; and
* ``frontend/qubit-dist/plugins/**/*.js`` — the shipped dashboard plugins
  (kanban, achievements), which the existing fail-closed scan
  (``tests/test_qbpv2_rebrand_public_surface.py``) never walks; and
* the rendered ``--help`` text of the CLI, including the vendored families the
  Qubit dispatch forwards into.

Three classes of public brand token are checked, matching the policy:

1. upstream URLs (``nousresearch.com`` / ``hermes-agent.`` host segments);
2. the ``@NousResearch`` social handle; and
3. standalone prose product words (``Hermes`` / ``Nous`` / ``OpenClaw``) —
   *not* the same words glued into wire identifiers, DOM ids, storage keys,
   CSS classes, env names, plugin slugs or URLs, which the policy preserves.

Known frozen-allowlist conflict (reported, not resolved here):
``docs/archive/2026-09-21/QUBIT_REBRAND_ALLOWLIST.md`` records the
DocsPage/kanban upstream docs links and the achievements ``@NousResearch``
handle as attribution/provenance, while the 2026-09-21 policy forbids
upstream names as URLs/labels on public surfaces and its section 3.1 records
only the three wire tokens as allowlisted. This test enforces the newer
policy, so while the conflict stands it fails on the shipped occurrences and
names the exact file plus the minimal de-brand fix.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "frontend" / "qubit-dist"

# Upstream URL host segments that must never ship on a public surface.
_UPSTREAM_URL = re.compile(
    r"https?://[^\s\"'`<>)\]]*(?:nousresearch\.com|hermes-agent\.)"
    r"[^\s\"'`<>)\]]*",
    re.IGNORECASE,
)
# Social handle used as attribution in the achievements plugin share text.
_UPSTREAM_HANDLE = re.compile(r"@NousResearch\b")
# Standalone prose product word: excluded when glued into an identifier by
# ``-``, ``_``, ``.``, ``/`` or ``@`` (so ``hermes-update``, ``NOUS_``,
# ``__QUBIT_BASE_PATH__``, ``hermes-kanban-*``, ``nous-blue`` and the
# ``@nous-research/ui`` package id do not trip it).
_PROSE = re.compile(
    r"(?<![A-Za-z0-9_\-./@])(Hermes|Nous|OpenClaw)(?![A-Za-z0-9_\-./@])"
)

# CLI families whose help is rendered through the vendored fork / Qubit
# dispatch. The top-level help is always included.
_CLI_FAMILIES = (
    "agent", "desktop", "gui", "worktree", "claw", "kanban",
    "portal", "setup", "auth", "channels", "webhook", "mcp", "plugins",
)


def _hits(text: str) -> list[str]:
    """Return every public brand token occurrence in ``text`` (deduped)."""
    out: list[str] = []
    for rx in (_UPSTREAM_URL, _UPSTREAM_HANDLE):
        out.extend(m.group(0) for m in rx.finditer(text))
    for m in _PROSE.finditer(text):
        start, end = max(0, m.start() - 40), min(len(text), m.end() + 40)
        out.append(f"{m.group(0)} ... {' '.join(text[start:end].split())} ...")
    return out


def _bundle_files() -> list[Path]:
    return sorted(DIST.glob("assets/*.js")) + sorted(DIST.glob("plugins/**/*.js"))


def test_shipped_bundle_has_no_public_brand_tokens():
    """The shipped SPA (assets + plugins) must carry no public brand token.

    Skips with a clear reason when the dashboard has not been built.
    """
    if not (DIST / "index.html").is_file():
        pytest.skip("frontend/qubit-dist not built in this checkout")
    files = _bundle_files()
    assert files, f"no shipped bundle JS under {DIST}"

    offenders: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for hit in _hits(text):
            offenders.append(f"{path.relative_to(REPO)}: {hit}")

    assert not offenders, (
        "shipped SPA still exposes upstream branding on a public surface "
        "(policy sections 1.2/2.1); de-brand the rendered string or, only if "
        "the frozen allowlist truly covers it, reconcile the allowlist:\n  "
        + "\n  ".join(offenders)
    )


def test_cli_help_has_no_public_brand_tokens():
    """Rendered CLI ``--help`` must carry no public brand token."""
    commands = [("--help",)] + [(fam, "--help") for fam in _CLI_FAMILIES]

    offenders: list[str] = []
    for argv in commands:
        proc = subprocess.run(
            [sys.executable, "-m", "voicebridge.cli", *argv],
            capture_output=True, text=True, timeout=90,
        )
        text = proc.stdout + proc.stderr
        label = " ".join(argv)
        for hit in _hits(text):
            offenders.append(f"qubit {label}: {hit}")

    assert not offenders, (
        "CLI help still prints upstream branding (policy sections 1.2/1.3):\n  "
        + "\n  ".join(offenders)
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
