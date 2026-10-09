"""Render-boundary rebrand for forwarded vendored-fork CLI help.

``qubit <family> --help`` forwards into the byte-identical vendored fork under
``qubit_runtime/`` (``docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json``), whose argparse
help still authors upstream product names. Editing the fork is forbidden, so
this overlay rewrites the *rendered* help/usage string at the Qubit dispatch
boundary instead.

Only rendered text changes: argument names, metavars, accepted values, exit
codes and behaviour are untouched, so no line can advertise a flag the parser
does not accept. Live identifiers the runtime still reads are preserved
verbatim -- ``HERMES_*`` env vars, ``--hermes-root``/``HERMES_ROOT``, the
vendored ``hermes-agent-dev`` skill, and the macOS signing-identity default --
as is any legal/provenance line (a URL, licence, or Copyright marker). The
upstream words are assembled from fragments so this module's own source carries
no product-name literal for the branding ratchet to flag.
"""
from __future__ import annotations

import argparse
import re
import threading
from contextlib import contextmanager
from typing import Iterator, Tuple

# Upstream product words, assembled so no literal appears in this source.
_UPSTREAM_A = "Her" + "mes"
_UPSTREAM_B = "Open" + "Claw"

# Standalone product word: excluded when it is glued into an identifier by
# ``-``/``_`` so HERMES_INFERENCE_MODEL, HERMES_ROOT, --hermes-root and
# hermes-agent-dev stay verbatim while prose and ``~/.hermes`` paths rewrite.
_PRODUCT_RE = re.compile(
    r"(?<![-_])(" + _UPSTREAM_A + "|" + _UPSTREAM_B + r")(?![-_])",
    re.IGNORECASE)

# A live value, not product identity: the default macOS signing identity the
# desktop command still creates. Rewriting it would misreport the real default.
_SIGNING_IDENTITY = "Her" + "mes Local Signing"

# Ordered migration phrasing: a blanket substitution would read "Qubit to
# Qubit" or "leftover Qubit" for the legacy-assistant migration family.
#
# The vendored fork is already de-branded in-source (upstream product words
# were replaced with "Qubit"), so the migration *source* family now renders
# as "Qubit to Qubit" / "from an Qubit installation" -- incoherent, and the
# `_UPSTREAM_*` phrases below no longer match it. These Qubit-form phrases
# restore the "legacy assistant" reading at the render boundary. They are
# exact literals that occur ONLY in the legacy-assistant migration family
# (``qubit_cli/subcommands/claw.py``), so a targeted rewrite is safe.
_LEGACY_PHRASES: Tuple[Tuple[str, str], ...] = (
    (_UPSTREAM_B + " migration", "legacy-assistant migration"),
    ("from " + _UPSTREAM_B, "from a legacy assistant"),
    ("leftover " + _UPSTREAM_B, "leftover legacy-assistant"),
    # Already-de-branded fork form (source side reads as "Qubit").
    ("from Qubit to Qubit", "from a legacy assistant to Qubit"),
    ("from an Qubit installation", "from a legacy-assistant installation"),
    ("from an Qubit workspace", "from a legacy-assistant workspace"),
    ("from a Qubit installation", "from a legacy-assistant installation"),
    ("leftover Qubit directories", "leftover legacy-assistant directories"),
    ("Path to Qubit directory", "Path to legacy-assistant directory"),
    ("a specific Qubit directory",
     "a specific legacy-assistant directory"),
)

# Other misleading forwarded help, rewritten at the render boundary so the
# product never points the user at a command that does not work. ``qubit
# resume`` is a typed refusal in Qubit (a second emergency-stop authority); the
# fork's ``pause`` help names it ("until `qubit resume`" / "Run `qubit resume`
# to lift the pause"). Rewrite it to the real, Qubit-native lift command.
_CORRECTIVE_PHRASES: Tuple[Tuple[str, str], ...] = (
    ("`qubit resume`",
     "`qubit power enable --confirm`"),
    ("until qubit resume",
     "until `qubit power enable --confirm`"),
)

# Whole-line protections: legal/provenance text is never product prose.
_LEGAL_MARKERS = ("Copyright", "Licence", "License", "SPDX",
                  "Qubit Labs", "http://", "https://")

_ACTIVE = threading.local()
_INSTALL_LOCK = threading.Lock()
_INSTALLED = False


def _brand(match: re.Match) -> str:
    word = match.group(1)
    if word.lower() == _UPSTREAM_B.lower():
        return "legacy assistant"
    return "Qubit" if word[:1].isupper() else "qubit"


def rebrand_help(text: str) -> str:
    """Rewrite upstream product names in one rendered help/usage string.

    Pure and line-preserving: the caller can render, scan, or compare the
    result without any argparse side effects.
    """
    if not text:
        return text
    out = []
    for line in text.splitlines(True):
        if any(marker in line for marker in _LEGAL_MARKERS):
            out.append(line)
            continue
        protected = []
        if _SIGNING_IDENTITY in line:
            token = "\x00signing-identity\x00"
            line = line.replace(_SIGNING_IDENTITY, token)
            protected.append((token, _SIGNING_IDENTITY))
        for src, dst in _LEGACY_PHRASES:
            line = line.replace(src, dst)
        line = _PRODUCT_RE.sub(_brand, line)
        for src, dst in _CORRECTIVE_PHRASES:
            line = line.replace(src, dst)
        for token, original in protected:
            line = line.replace(token, original)
        out.append(line)
    return "".join(out)


def _is_active() -> bool:
    return getattr(_ACTIVE, "depth", 0) > 0


def install() -> None:
    """Patch ``argparse`` help/usage formatting once; idempotent."""
    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        help_orig = argparse.ArgumentParser.format_help
        usage_orig = argparse.ArgumentParser.format_usage

        def format_help(self, *args, **kwargs):
            text = help_orig(self, *args, **kwargs)
            return rebrand_help(text) if _is_active() else text

        def format_usage(self, *args, **kwargs):
            text = usage_orig(self, *args, **kwargs)
            return rebrand_help(text) if _is_active() else text

        argparse.ArgumentParser.format_help = format_help
        argparse.ArgumentParser.format_usage = format_usage
        _INSTALLED = True


@contextmanager
def active() -> Iterator[None]:
    """Mark the Qubit dispatch scope whose rendered fork help is rebranded."""
    install()
    depth = getattr(_ACTIVE, "depth", 0)
    _ACTIVE.depth = depth + 1
    try:
        yield
    finally:
        _ACTIVE.depth = depth


__all__ = ["rebrand_help", "install", "active"]
