"""Qubit text sanitizing for model/tool output.

Strips terminal escape sequences, stray C0 controls, and invisible
Unicode tag characters before text reaches prompts, receipts, reports,
or speech — models otherwise copy escapes into file writes and tag
characters can smuggle hidden instructions past human review. Plain
text (including emoji, newlines, tabs) passes through untouched.
"""
from __future__ import annotations

import re

# ECMA-48 escape sequences: CSI ... final-byte, OSC ... BEL/ST,
# DCS/SOS/PM/APC ... ST, nF two-byte, Fp/Fe/Fs single-byte, plus the
# 8-bit C1 forms. Written from the ECMA-48 grammar (independent
# implementation), tested against adversarial fixtures in
# tests/test_textutil8u.py.
_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|[PX^_][^\x1b]*(?:\x1b\\)|[ -/]+[0-~]|[0-~])"
    r"|\x9b[0-?]*[ -/]*[@-~]"
    r"|\x9d[^\x07\x9c]*(?:\x07|\x9c)"
    r"|[\x80-\x9f]"
)
_HAS_ESCAPE = re.compile(r"[\x1b\x80-\x9f]")
_HAS_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Unicode TAG block (U+E0000–U+E007F): invisible to humans, visible to
# tokenizers. Emoji tag sequences (U+1F3F4 base + tags + U+E007F cancel,
# e.g. subdivision flags) are preserved; all other tag runs are cut.
_HAS_UNICODE_TAG = re.compile("[\U000E0000-\U000E007F]")
_TAG_SEQ_RE = re.compile(
    "(\U0001F3F4[\U000E0000-\U000E007F]*\U000E007F)"
    "|([\U000E0000-\U000E007F]+)")


def strip_ansi(text: str) -> str:
    """Remove terminal escape sequences, leaving other bytes untouched."""
    if not text or not _HAS_ESCAPE.search(text):
        return text or ""
    return _ESCAPE_RE.sub("", text)


def sanitize_display_text(text: str) -> str:
    """Sanitize stored/untrusted text before echoing it to a terminal:
    strips ANSI sequences AND bare control chars, keeping only
    newlines/tabs (CRs become newlines so ``\\r``-overwrite spoofing
    can't hide content)."""
    if not text or not _HAS_CONTROL.search(text):
        return text or ""
    out = strip_ansi(text)
    if "\r" in out:
        out = out.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL_RE.sub("", out)


def strip_unicode_tags(text: str) -> str:
    """Remove invisible Unicode tag runs, preserving flag sequences."""
    if not text or not _HAS_UNICODE_TAG.search(text):
        return text
    return _TAG_SEQ_RE.sub(lambda m: m.group(1) or "", text)


def sanitize(text: str) -> str:
    """Full output sanitizing: escapes, controls, and smuggled tags."""
    return strip_unicode_tags(sanitize_display_text(text or ""))
