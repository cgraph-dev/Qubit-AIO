"""Where the office-game tests find `tsc`.

WHY THIS IS NOT JUST A PATH CONSTANT
------------------------------------
`scripts/build_dashboard.sh` builds the SPA and then DELETES
`frontend/qubit-web/node_modules` unless `KEEP_NODE_MODULES=1` is set. The
office-game tests compile TypeScript through the project's own `tsc`, so after a
dashboard build every one of them silently SKIPPED — the suite went green while
proving nothing, which is worse than failing.

The same deletion is caused one level up by `NODE_ENV=production` in the shell:
`npm ci` defaults to `omit=dev`, installs no devDependencies, and `node_modules`
ends up without `typescript` or `vite` — so the office suites skip and the
packaging suite skips, and the run still reports success.

Three things follow, and all are handled here:

1. Resolve `tsc` from a list of candidates, so the project's copy and a
   standalone tool install are both acceptable.
2. SELF-HEAL: `ensure_tsc()` installs `typescript` into a scratch dir OUTSIDE
   the tree when nothing resolves, so a dashboard build's `rm -rf node_modules`
   cannot take the compiler away for good. Tests RUN instead of skipping.
3. Never let a missing compiler read as a pass. `toolchain_problem()` returns
   the actionable message; every office-game suite calls
   `require_toolchain_or_fail()` at import time so a missing compiler is a LOUD
   collection error naming the install command, and `require_tsc()` raises the
   same message for in-test callers. A silent `N skipped` green is no longer
   possible for these suites — the whole point of the exercise.

The project's own pin (`typescript: 6.0.3` in `frontend/qubit-web/package.json`)
does not resolve on the public registry, so a working compiler legitimately has
to come from outside the tree (`TOOL_VERSION` = 5.9.2).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "frontend" / "qubit-web"

#: A self-healing fallback install lives outside the tree so
#: `scripts/build_dashboard.sh` deleting `frontend/qubit-web/node_modules`
#: (or `NODE_ENV=production` making `npm ci` omit devDependencies) cannot take
#: the compiler away from the tests for good. See `ensure_tsc()`.
TOOL_DIR = (
    Path.home() / ".local" / "state" / "qubit-voicebridge" / "runtime"
    / "cache" / "scratch" / "ts-tool"
)
TOOL_TSC = TOOL_DIR / "node_modules" / "typescript" / "bin" / "tsc"
#: The version the tool dir is pinned to. 6.0.3 is what
#: `frontend/qubit-web/package.json` pins, but it does not resolve on the
#: public registry, so a working compiler legitimately comes from outside.
TOOL_VERSION = "5.9.2"

#: Where a working `tsc` is looked for, most-project-specific first.
CANDIDATES = (
    WEB / "node_modules" / "typescript" / "bin" / "tsc",
    REPO / "node_modules" / "typescript" / "bin" / "tsc",
    TOOL_TSC,
    Path("/usr/lib/node_modules/typescript/bin/tsc"),
    Path("/usr/local/lib/node_modules/typescript/bin/tsc"),
)

#: Set to "0" to forbid the network install (pure read-only resolution).
#: Any suite that then cannot find `tsc` FAILS — it never silently skips.
SELF_HEAL = os.environ.get("QUBIT_TSC_SELF_HEAL", "1") != "0"

#: Cached result of the last `ensure_tsc()` so the network is not hit per test.
_HEAL_ATTEMPTED = False
_HEAL_DIAGNOSIS = ""


def tsc_path() -> Path | None:
    """The first usable `tsc`, or an env override. Read-only: no install.

    Use `ensure_tsc()` when you need the compiler to exist.
    """
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


def _install_tsc() -> str:
    """`npm install typescript` into `TOOL_DIR`. Returns "" on success.

    Failures are returned, not raised, so callers can fold the reason into one
    actionable message. Never raises on a missing npm — that is a diagnosis too.
    """
    npm = shutil.which("npm")
    if not npm:
        return "npm is not on PATH, so the compiler cannot be self-installed"
    TOOL_DIR.mkdir(parents=True, exist_ok=True)
    # Force devDependencies on: an ambient NODE_ENV=production makes npm default
    # to `--omit=dev`, which would install nothing. This is the trap that took
    # the compiler away in the first place.
    env = dict(os.environ)
    env["NODE_ENV"] = "development"
    env.pop("npm_config_omit", None)
    try:
        proc = subprocess.run(
            [npm, "install", "--include=dev", "--no-audit", "--no-fund",
             f"typescript@{TOOL_VERSION}"],
            cwd=str(TOOL_DIR), env=env,
            capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"npm install raised {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return "npm install failed (rc={}): {}".format(
            proc.returncode, tail[-1] if tail else "no output")
    if not TOOL_TSC.is_file():
        return f"npm install reported success but {TOOL_TSC} is still absent"
    return ""


def ensure_tsc() -> Path | None:
    """`tsc_path()`, installing the compiler once if nothing resolves.

    Self-healing, not a skip: a missing compiler is an environment fault this
    suite repairs if it can (into `TOOL_DIR`, outside the tree so a dashboard
    build cannot delete it again) and reports loudly if it cannot.

    Cached: the install is attempted at most once per process.
    """
    found = tsc_path()
    if found is not None:
        return found
    if not SELF_HEAL:
        return None

    global _HEAL_ATTEMPTED, _HEAL_DIAGNOSIS
    if not _HEAL_ATTEMPTED:
        _HEAL_ATTEMPTED = True
        _HEAL_DIAGNOSIS = _install_tsc()
        if not _HEAL_DIAGNOSIS:
            print(
                f"[_office_toolchain] self-healed: installed typescript"
                f"@{TOOL_VERSION} into {TOOL_DIR}",
                file=sys.stderr,
            )
    return tsc_path()


def has_tsc() -> bool:
    """True when a compiler can be obtained (self-healing before it answers)."""
    return ensure_tsc() is not None and node_path() is not None


def toolchain_problem() -> str:
    """`""` when the toolchain is usable, else an actionable multi-line reason.

    Single source of truth for both the hard failure and the message wording,
    so the loud path and the diagnosis cannot drift apart.
    """
    node = node_path()
    tsc = ensure_tsc()
    if node and tsc:
        return ""
    lines = [
        "NO USABLE TypeScript compiler for the office-game suites — this is a"
        " FAILURE, not a skip (a skipped test that reports success is how a"
        " false green gets shipped).",
        f"  node: {node or 'MISSING'}",
        f"  tsc:  {tsc or 'MISSING'} (looked in: "
        + ", ".join(str(c) for c in CANDIDATES) + ")",
    ]
    if _HEAL_DIAGNOSIS:
        lines.append(f"  self-heal attempt failed: {_HEAL_DIAGNOSIS}")
    lines += [
        "Recovery (any one):",
        f"  npm install --include=dev --prefix {TOOL_DIR}"
        f" typescript@{TOOL_VERSION}",
        "  scripts/build_dashboard.sh with KEEP_NODE_MODULES=1",
        "  export QUBIT_TSC=/path/to/tsc",
        "Root cause to check first: NODE_ENV=production makes `npm ci` omit"
        " devDependencies (no tsc/vite in node_modules). See docs/OPEN_THREADS.md"
        " § 4.15.",
    ]
    return "\n".join(lines)


def require_tsc() -> tuple[str, Path]:
    """`(node, tsc)` or a hard failure.

    Deliberately NOT a skip: a compiler that vanished mid-session is a broken
    environment, and a skipped test that reports success is how a false green
    gets shipped.
    """
    problem = toolchain_problem()
    if not problem:
        return node_path(), ensure_tsc()  # type: ignore[return-value]
    raise RuntimeError(problem)


#: The skip reason kept for callers that genuinely tolerate a missing compiler.
#: It names the cause so a silent green is impossible to mistake for coverage —
#: but note the office-game suites no longer USE this: they hard-fail via
#: `require_toolchain_or_fail()`, because a skip here is exactly the false green
#: the module docstring warns about. Retained only for out-of-tree callers.
SKIP_REASON = (
    "no usable tsc — run scripts/build_dashboard.sh with KEEP_NODE_MODULES=1, "
    "or set QUBIT_TSC"
)


def require_toolchain_or_fail() -> None:
    """Raise at import time when the office suites cannot compile.

    Called at module scope by every office-game suite in place of the old
    ``pytest.mark.skipif(not has_tsc())``. A skip reports success while proving
    nothing — the exact false green the module docstring warns about — so a
    missing compiler must be a LOUD collection error naming the install command.

    Raises ``RuntimeError`` (pytest turns a module-scope raise into a collection
    error) with the actionable message from `toolchain_problem()`. `has_tsc()`
    has already self-healed by the time this runs, so it only fires when the
    compiler is genuinely unobtainable.
    """
    problem = toolchain_problem()
    if problem:
        raise RuntimeError(problem)
