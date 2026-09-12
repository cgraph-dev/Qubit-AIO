"""Route supported ``qubit <family> <sub>`` pairs to the vendored fork.

Per the owner runtime amendment (2026-09-11) the Hermes CLI executes from the
Qubit source tree. Many families whose upstream implementation is the fork are
still open in the Hermes gap ledger for the CLI area; this module forwards the
pairs the Qubit parser does **not** already own straight into
:func:`voicebridge.hermes_runtime.run`, the same in-process entry point used by
``qubit agent`` and the dashboard ASGI bridge.

Design rules:

* Only ``(family, subcommand)`` pairs absent from :func:`build_parser` are
  forwarded, so a native Qubit command is never shadowed (a regression test
  enforces this against the live parser).
* ``argv`` after the subcommand is passed to the fork verbatim; upstream
  argparse help, errors, persistence and recovery behavior are preserved.
* Qubit-only global flags (``--config/--no-hotkey/--dry-run/--mute``) are
  stripped because the fork has no equivalent and would reject them; the fork
  resolves its own config (``HERMES_HOME``/``~/.hermes``).
"""
from __future__ import annotations

from typing import Optional

# (qubit family, qubit subcommand) -> Hermes argv prefix. Every entry is a
# read-only or diagnostic surface: mutating/credentialed upstream commands are
# intentionally not routed here (they stay behind the Qubit policy/confirm
# surfaces owned by the backend agents).
FORWARD_TABLE = {
    ("schedules", "doctor"): ("cron", "doctor"),
    ("schedules", "incidents"): ("cron", "incidents"),
    ("schedules", "status"): ("cron", "status"),
    ("schedules", "runs"): ("cron", "runs"),
    ("sessions", "stats"): ("sessions", "stats"),
    ("sessions", "pinned"): ("sessions", "pinned"),
    ("mcp", "list"): ("mcp", "list"),
    ("plugins", "capabilities"): ("plugins", "capabilities"),
    ("plugins", "compat"): ("plugins", "compat"),
    ("skills", "audit"): ("skills", "audit"),
    ("skills", "list-modified"): ("skills", "list-modified"),
    ("flags", "path"): ("config", "path"),
    ("flags", "env-path"): ("config", "env-path"),
    ("flags", "show"): ("config", "show"),
    ("flags", "check"): ("config", "check"),
    ("monitor", "status"): ("monitoring", "status"),
    ("frontier", "list"): ("fallback", "list"),
    ("setup", "doctor"): ("doctor",),
    # Wave 2: fork reads for the remaining cli rows whose Hermes family does
    # not share the Qubit family name (ledger `cli:curator`, `cli:learning`).
    # Only read-only/diagnostic subcommands are routed; the fork's mutating
    # subcommands stay behind the Qubit confirm/audit surfaces (owned by the
    # backend/voice agents) and are deliberately not forwarded raw.
    ("lessons", "status"): ("curator", "status"),
    ("lessons", "ledger"): ("curator", "ledger"),
    ("lessons", "usage"): ("curator", "usage"),
    ("lessons", "list-archived"): ("curator", "list-archived"),
    ("lessons", "list-unmanaged"): ("curator", "list-unmanaged"),
    ("mcp", "catalog"): ("mcp", "catalog"),
    # Wave 4: remaining fork reads for the still-open cli families.
    ("plugins", "info"): ("plugins", "info"),
    ("frontier", "ls"): ("fallback", "ls"),
}

# Qubit launcher global flags that do not exist in the upstream parser.
_QUBIT_GLOBALS = frozenset({"--config", "--no-hotkey", "--dry-run", "--mute"})


def _strip_qubit_globals(argv: list) -> list:
    """Drop Qubit-only global flags (and ``--config``'s value) from ``argv``."""
    out: list = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        name = tok.split("=", 1)[0]
        if name in _QUBIT_GLOBALS:
            if name == "--config" and "=" not in tok and i + 1 < len(argv):
                i += 2
                continue
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def translate(argv: list) -> Optional[list]:
    """Return the fork argv for a ``qubit <family> <sub>`` call, or ``None``.

    ``argv`` is the raw Qubit argument vector without the program name.
    """
    raw = list(argv or [])
    if len(raw) < 2:
        return None
    mapped = FORWARD_TABLE.get((raw[0], raw[1]))
    if mapped is None:
        return None
    return [*mapped, *_strip_qubit_globals(raw[2:])]


def maybe_forward(argv: list) -> Optional[int]:
    """Forward to the vendored fork when ``argv`` matches the table.

    Returns the fork's exit code, or ``None`` when the call is not a forwarded
    family/subcommand (the caller then continues with the Qubit parser). Fork
    ``argparse`` errors propagate as ``SystemExit`` — upstream behavior.
    """
    mapped = translate(argv)
    if mapped is None:
        return None
    from . import hermes_runtime

    return hermes_runtime.run(mapped)


__all__ = ["FORWARD_TABLE", "translate", "maybe_forward"]
