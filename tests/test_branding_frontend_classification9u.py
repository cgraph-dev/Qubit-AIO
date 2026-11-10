"""Branding workstream (2026-09-21) — frontend classification guard.

Complements ``tests/test_branding_bundle_scan9u.py`` (which fail-closes on the
*shipped* bundle and CLI help) by classifying every residual upstream token in
the editable SPA **source** tree (``frontend/qubit-web/src/**``). The source
tree is where a leak is introduced; the bundle scan only sees it after a build.

The known residuals are exactly:

* allowlisted wire identifiers (policy §2.2 / allowlist row 75):
  ``X-Qubit-Session-Token``, ``__QUBIT_SESSION_TOKEN__``,
  ``__QUBIT_BASE_PATH__``;
* upstream **comments** — attribution notices ("© 2025 Nous Research", the
  MIT-licensed HermesTV provenance header) and parity notes explaining a
  ported behaviour. Comments never reach the shipped bundle (the bundler
  strips them), and ``docs/BRAND.md`` §2 makes legal attribution the required
  exception, so they are not a public-surface leak; the *shipped* scan
  (``test_branding_bundle_scan9u.py``) still fail-closes on the built output;
* **test fixtures** that assert the de-brand transform itself — e.g.
  ``PluginsPage.pinnedIndex.test.tsx`` feeds ``brandText("Run Hermes Agent")``
  and requires ``"Run Qubit Agent"``. The upstream word there is the INPUT
  the rebrander must rewrite; deleting it would delete the proof.

Any *other* standalone ``Hermes`` / ``Nous`` / ``OpenClaw`` prose word in a
source string or JSX text fails this test. It does not resolve the allowlist-vs-
policy conflict; it prevents a new, unclassified leak from being introduced
while the ruling is open. No claim of verification is made.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "frontend" / "qubit-web" / "src"

# Allowlisted wire identifiers that legitimately remain in source (policy
# §2.2; allowlist row 75).
_WIRE_TOKENS = (
    "X-Qubit-Session-Token",
    "__QUBIT_SESSION_TOKEN__",
    "__QUBIT_BASE_PATH__",
)

# The single documented provenance URL constant (allowlist row 72; owner
# ruling requested). Any new URL host of this shape must be classified.
_DOCS_PROVENANCE_URL = "https://hermes-agent.nousresearch.com/docs/"

# Standalone prose product word: excluded when glued into an identifier by
# ``-``, ``_``, ``.``, ``/`` or ``@`` (mirrors the shipped-bundle scanner).
_PROSE = re.compile(
    r"(?<![A-Za-z0-9_\-./@])(Hermes|Nous|OpenClaw)(?![A-Za-z0-9_\-./@])"
)
_UPSTREAM_URL = re.compile(
    r"https?://[^\s\"'`<>)\]]*(?:nousresearch\.com|hermes-agent\.)"
    r"[^\s\"'`<>)\]]*",
    re.IGNORECASE,
)
_HANDLE = re.compile(r"@(?:NousResearch|nousresearch)\b", re.IGNORECASE)


def _source_files() -> list[Path]:
    return [
        p
        for p in sorted(SRC.rglob("*"))
        if p.is_file() and p.suffix in (".ts", ".tsx")
    ]


_STR_OR_COMMENT = re.compile(
    r"""//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`""",
    re.S,
)


def _strip_comments(text: str) -> str:
    """Blank out ``//`` and ``/* */`` comments, leaving string literals intact.

    A token inside a comment never ships (the bundler drops comments) and the
    attribution headers in this tree are legally required, so comments are not
    a public-surface leak. Quoted regions are preserved verbatim — a brand word
    in a shipped string or JSX text is exactly what this guard exists to catch.
    """
    def _repl(m: "re.Match[str]") -> str:
        tok = m.group(0)
        return tok if tok[0] in "'\"`" else " "

    return _STR_OR_COMMENT.sub(_repl, text)


def test_frontend_source_has_no_unclassified_upstream_tokens():
    """Only the documented wire tokens remain outside comments and fixtures."""
    files = _source_files()
    assert files, f"no SPA source files under {SRC}"

    offenders: list[str] = []
    for path in files:
        rel = path.relative_to(REPO).as_posix()
        # A *.test.ts(x) fixture is the proof of the de-brand transform: it
        # feeds an upstream label to brandText() and requires the Qubit one.
        # The upstream word is that test's INPUT — never a shipped string.
        if ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in _WIRE_TOKENS:
            text = text.replace(token, "")
        text = text.replace(_DOCS_PROVENANCE_URL, "")
        text = _strip_comments(text)

        for rx in (_UPSTREAM_URL, _HANDLE):
            for m in rx.finditer(text):
                offenders.append(f"{rel}: {m.group(0)!r} (upstream URL/handle)")
        for m in _PROSE.finditer(text):
            offenders.append(
                f"{rel}: {m.group(0)!r} in source text (classify or allowlist)"
            )

    assert offenders == [], (
        "unclassified upstream token(s) in the SPA source tree outside "
        "comments and test fixtures; either de-brand them or classify them in "
        "this module's docstring:\n  "
        + "\n  ".join(offenders)
    )


def test_docs_provenance_constant_removed_after_ruling():
    """Ruling 1 → Option A applied: the upstream docs URL is gone from DocsPage.

    The provenance URL was an owner-ruling item (allowlist row 72). The owner
    ruled Option A (de-brand the product surface); it must now be absent.
    """
    docs = SRC / "pages" / "DocsPage.tsx"
    text = docs.read_text(encoding="utf-8")
    assert _DOCS_PROVENANCE_URL not in text, (
        "DocsPage still renders the upstream docs URL after Ruling 1 → Option A"
    )
    assert "nousresearch.com" not in text.lower(), text
