"""Branding workstream (2026-09-21) — frontend classification guard.

Complements ``tests/test_branding_bundle_scan9u.py`` (which fail-closes on the
*shipped* bundle and CLI help) by classifying every residual upstream token in
the editable SPA **source** tree (``frontend/qubit-web/src/**``). The source
tree is where a leak is introduced; the bundle scan only sees it after a build.

The known residuals are exactly:

* allowlisted wire identifiers (policy §2.2 / allowlist row 75):
  ``X-Qubit-Session-Token``, ``__QUBIT_SESSION_TOKEN__``,
  ``__QUBIT_BASE_PATH__``;
* the DocsPage provenance URL constant (allowlist row 72, owner ruling
  requested — see ``docs/archive/2026-09-22-pre-reset/QUBIT_BRANDING_RULING_REQUEST_2026-09-21.md`` §1a).

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


def test_frontend_source_has_no_unclassified_upstream_tokens():
    """Only the documented wire tokens and the DocsPage provenance URL remain."""
    files = _source_files()
    assert files, f"no SPA source files under {SRC}"

    offenders: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in _WIRE_TOKENS:
            text = text.replace(token, "")
        text = text.replace(_DOCS_PROVENANCE_URL, "")
        rel = path.relative_to(REPO).as_posix()

        for rx in (_UPSTREAM_URL, _HANDLE):
            for m in rx.finditer(text):
                offenders.append(f"{rel}: {m.group(0)!r} (upstream URL/handle)")
        for m in _PROSE.finditer(text):
            offenders.append(
                f"{rel}: {m.group(0)!r} in source text (classify or allowlist)"
            )

    assert offenders == [], (
        "unclassified upstream token(s) in the SPA source tree; either "
        "de-brand them or add them to the classification in "
        "docs/archive/2026-09-22-pre-reset/QUBIT_BRANDING_RULING_REQUEST_2026-09-21.md:\n  "
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
