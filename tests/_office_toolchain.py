"""Where the office-game tests find `tsc`.

WHY THIS IS NOT JUST A PATH CONSTANT
------------------------------------
`scripts/build_dashboard.sh` builds the SPA and then DELETES
`frontend/qubit-web/node_modules` unless `KEEP_NODE_MODULES=1` is set. The
office-game tests compile TypeScript through the project's own `tsc`, so after a
dashboard build every one of them silently SKIPPED — the suite went green while
proving nothing, which is worse than failing.

Two things follow, and both are handled here:

1. Resolve `tsc` from a list of candidates, so the project's copy and a
   standalone tool install are both acceptable.
2. Never let a missing compiler read as a pass. `require_tsc()` FAILS with an
   actionable message; only `has_tsc()` is available for an explicit skip, and
   the tests that use it say so in their skip reason.

The project's own pin (`typescript: 6.0.3` in `frontend/qubit-web/package.json`)
does not resolve on the public registry, so a working compiler legitimately has
to come from outside the tree.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "frontend" / "qubit-web"

#: Where a working `tsc` is looked for, most-project-specific first.
CANDIDATES = (
    WEB / "node_modules" / "typescript" / "bin" / "tsc",
    REPO / "node_modules" / "typescript" / "bin" / "tsc",
    Path.home() / ".local" / "state" / "qubit-voicebridge" / "runtime"
    / "cache" / "scratch" / "ts-tool" / "node_modules" / "typescript"
    / "bin" / "tsc",
    Path("/usr/lib/node_modules/typescript/bin/tsc"),
    Path("/usr/local/lib/node_modules/typescript/bin/tsc"),
)


def tsc_path() -> Path | None:
    """The first usable `tsc`, or an env override."""
    override = os.environ.get("QUBIT_TSC")
    if override and Path(override).is_file():
        return Path(override)
    for candidate in CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which("tsc")
    return Path(found) if found else None


def node_path() -> str | None:
    return shutil.which("node")


def has_tsc() -> bool:
    return tsc_path() is not None and node_path() is not None


def require_tsc() -> tuple[str, Path]:
    """`(node, tsc)` or a hard failure.

    Deliberately NOT a skip: a compiler that vanished mid-session is a broken
    environment, and a skipped test that reports success is how a false green
    gets shipped.
    """
    node = node_path()
    tsc = tsc_path()
    if node and tsc:
        return node, tsc
    raise RuntimeError(
        "no usable TypeScript compiler for the office-game suites.\n"
        f"  node: {node or 'MISSING'}\n"
        f"  tsc:  {tsc or 'MISSING'} (looked in: "
        + ", ".join(str(c) for c in CANDIDATES)
        + ")\n"
        "Likely cause: `scripts/build_dashboard.sh` removed "
        "frontend/qubit-web/node_modules. Reinstall one with:\n"
        "  npm install --prefix "
        + str(CANDIDATES[2].parents[3])
        + " typescript@5.9.2\n"
        "or set QUBIT_TSC=/path/to/tsc."
    )


#: The skip reason used by suites that can tolerate a missing compiler. It names
#: the cause so a silent green is impossible to mistake for coverage.
SKIP_REASON = (
    "no usable tsc — run scripts/build_dashboard.sh with KEEP_NODE_MODULES=1, "
    "or set QUBIT_TSC"
)
