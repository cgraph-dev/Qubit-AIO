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

from typing import NamedTuple, Optional

# (qubit family, qubit subcommand) -> Hermes argv prefix. Every entry is a
# read-only or diagnostic surface: mutating/credentialed upstream commands are
# intentionally not routed here (they stay behind the Qubit policy/confirm
# surfaces owned by the backend agents, or the CONFIRM_TABLE below).
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
    # Wave 1 Hermes CLI parity: read-only fork subcommands whose Qubit family
    # does not expose a native subcommand.
    ("tools", "list"): ("tools", "list"),
}

# Subcommand sentinel meaning "every value of this axis".
ANY = "*"

# Whole Qubit families backed by the vendored fork:
#   family -> (fork_family, readonly, confirm)
# ``readonly`` subcommands forward verbatim (the empty string is the bare
# family: help/default output). ``confirm`` subcommands forward only with
# Qubit's explicit ``--confirm`` (the flag is stripped before the fork sees it).
# A subcommand in neither set is a typed refusal, so no mutating upstream
# surface is reachable through an unrestricted bypass.
FAMILY_FORWARD = {
    "bundles": ("bundles", frozenset({"list", "show", ""}),
                frozenset({"create", "delete", "reload"})),
    "claw": ("claw", frozenset({""}),
             frozenset({"migrate", "cleanup", "clean"})),
    "console": ("console", frozenset(), frozenset({ANY})),
    "debug": ("debug", frozenset({""}), frozenset({"share", "delete"})),
    "desktop": ("desktop", frozenset(), frozenset({ANY})),
    "dump": ("dump", frozenset({ANY}), frozenset()),
    "gui": ("gui", frozenset(), frozenset({ANY})),
    "memory-graph": ("memory-graph", frozenset({"list", ""}),
                     frozenset({"edit", "delete"})),
    "moa": ("moa", frozenset({"list", "ls", ""}),
            frozenset({"configure", "config", "delete", "rm"})),
    "pause": ("pause", frozenset(), frozenset()),
    "portal": ("portal", frozenset({""}),
               frozenset({"login", "open", "tools", "info", "status"})),
    "prompt-size": ("prompt-size", frozenset({ANY}), frozenset()),
    "worktree": ("worktree", frozenset({"list", "ls", "audit", ""}),
                 frozenset({"prune"})),
}

# Mutating/credentialed (qubit family, sub) -> fork argv prefix. These are NOT
# in FORWARD_TABLE: they require the caller's explicit ``--confirm`` before the
# fork runs, so the unsafe upstream surface is never an unrestricted bypass.
CONFIRM_TABLE = {
    ("flags", "edit"): ("config", "edit"),
    ("schedules", "edit"): ("cron", "edit"),
    ("schedules", "run"): ("cron", "run"),
    ("schedules", "tick"): ("cron", "tick"),
    ("mcp", "test"): ("mcp", "test"),
    ("plugins", "enable"): ("plugins", "enable"),
    ("plugins", "remove"): ("plugins", "remove"),
    ("sessions", "clean-markers"): ("sessions", "clean-markers"),
    ("sessions", "export"): ("sessions", "export"),
    ("sessions", "optimize-storage"): ("sessions", "optimize-storage"),
    ("sessions", "pin"): ("sessions", "pin"),
    ("tools", "disable"): ("tools", "disable"),
    ("tools", "enable"): ("tools", "enable"),
    ("tools", "post-setup"): ("tools", "post-setup"),
}


class FamilyRoute(NamedTuple):
    """One resolved route for a ``qubit <family> [...]`` call."""

    kind: str            # "forward" | "needs-confirm" | "refused"
    argv: list           # fork argv when kind == "forward"; else the gated argv
    code: str = ""
    detail: str = ""


# Qubit launcher global flags that do not exist in the upstream parser.
# ``--dry-run`` is deliberately NOT stripped: the fork's own mutating commands
# (claw migrate, worktree prune) use it, and dropping it could turn a preview
# into a real apply.
_QUBIT_GLOBALS = frozenset({"--config", "--no-hotkey", "--dry-run", "--mute"})
_FAMILY_GLOBALS = frozenset({"--config", "--no-hotkey", "--mute", "--confirm"})


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


def _strip_family_globals(argv: list) -> list:
    """Drop Qubit-only family flags (``--confirm``/``--config``/…) from argv."""
    out: list = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        name = tok.split("=", 1)[0]
        if name in _FAMILY_GLOBALS:
            if name == "--config" and "=" not in tok and i + 1 < len(argv):
                i += 2
                continue
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def route_family(argv: list) -> Optional[FamilyRoute]:
    """Resolve a whole-family ``qubit <family> [...]`` call, or ``None``.

    Read-only subcommands/help forward to the fork verbatim; mutating
    subcommands become ``needs-confirm`` without Qubit's ``--confirm`` and
    forward (with the flag stripped) when it is present; everything else is a
    typed refusal.
    """
    raw = list(argv or [])
    if not raw or raw[0] not in FAMILY_FORWARD:
        return None
    family = raw[0]
    fork_family, readonly, confirm = FAMILY_FORWARD[family]
    tail = raw[1:]
    # Upstream help is a read-only surface and is always preserved.
    if any(tok in ("-h", "--help") for tok in tail):
        return FamilyRoute("forward",
                           [fork_family, *_strip_family_globals(tail)])
    if family == "pause":
        # The fork's ESTOP is a second emergency-stop authority; Qubit owns one.
        # With --confirm this defers to Qubit's native `qubit pause` handler
        # (the shared PowerControl latch); a bare `qubit pause` stays a typed
        # refusal so the fork sentinel is never engaged.
        if "--confirm" in raw:
            return None
        return FamilyRoute(
            "refused", [], code="ESTOP_QUBIT_OWNED",
            detail=("qubit pause maps to the Qubit emergency stop, not a second "
                    "fork sentinel; use `qubit pause --confirm` (or "
                    "`qubit power emergency-stop --confirm`)"))
    sub = tail[0] if tail and not tail[0].startswith("-") else ""
    if family == "worktree" and sub in ("add", "remove", "switch") \
            and "--confirm" in raw:
        # Qubit-native, confirmation-gated git worktree lifecycle; the pinned
        # fork only owns list/prune. Unconfirmed calls still hit the typed
        # refusal below, so no unconfirmed write ever runs.
        return None
    label = f"qubit {family} {sub}".strip()
    if ANY in readonly or sub in readonly:
        return FamilyRoute("forward",
                           [fork_family, *_strip_family_globals(tail)])
    if ANY in confirm or sub in confirm:
        args = _strip_family_globals(tail)
        if "--confirm" not in raw:
            return FamilyRoute(
                "needs-confirm", [fork_family, *args], code="CONFIRM_REQUIRED",
                detail=f"{label} requires --confirm (explicit human authorization)")
        return FamilyRoute("forward", [fork_family, *args])
    return FamilyRoute(
        "refused", [], code="FORK_SUB_UNCLASSIFIED",
        detail=(f"{label} is not a classified read-only surface; a mutating "
                "fork subcommand is never exposed as an unrestricted bypass"))


def confirm_pair(argv: list) -> Optional[FamilyRoute]:
    """Resolve a ``CONFIRM_TABLE`` pair, or ``None``.

    Without Qubit's ``--confirm`` the fork is never invoked; the caller gets a
    typed ``needs-confirm`` envelope carrying the exact argv that would run.
    """
    raw = list(argv or [])
    if len(raw) < 2:
        return None
    mapped = CONFIRM_TABLE.get((raw[0], raw[1]))
    if mapped is None:
        return None
    args = _strip_family_globals(raw[2:])
    label = f"qubit {raw[0]} {raw[1]}"
    if "--confirm" not in raw:
        return FamilyRoute(
            "needs-confirm", [*mapped, *args], code="CONFIRM_REQUIRED",
            detail=f"{label} requires --confirm (explicit human authorization)")
    return FamilyRoute("forward", [*mapped, *args])


def route(argv: list) -> Optional[FamilyRoute]:
    """Resolve any fork-backed read-only/confirm-gated Qubit CLI call.

    Returns ``None`` when the Qubit parser owns the call.
    """
    resolved = route_family(argv)
    if resolved is not None:
        return resolved
    resolved = confirm_pair(argv)
    if resolved is not None:
        return resolved
    mapped = translate(argv)
    if mapped is not None:
        return FamilyRoute("forward", list(mapped))
    return None


__all__ = ["FORWARD_TABLE", "FAMILY_FORWARD", "CONFIRM_TABLE", "FamilyRoute",
           "translate", "maybe_forward", "route", "route_family", "confirm_pair"]
