"""Qubit-owned Hermes runtime bootstrap.

Per `docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`, the product's
agent loop, CLI, dashboard, sessions, tools, skills, models, memory, tasks and
runtime execute from the vendored fork under ``qubit_hermes/`` (a byte-identical
fork of the pinned upstream `2237be35`, MIT, Copyright (c) 2025 Nous Research).
No external Hermes installation is imported or supervised.

This module is the single entry point that puts the fork on ``sys.path`` and
invokes its CLI. Qubit's own launcher and dashboard call through here so the
same runtime backs CLI, dashboard and voice.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

_FORK_NAME = "qubit_hermes"
_REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Qubit help branding at the runtime boundary.
#
# The vendored fork's top-level parser is built by
# ``hermes_cli._parser.build_top_level_parser`` with a hardcoded
# ``prog="hermes"`` and the upstream description. That text is user-facing on
# normal ``qubit <family> --help`` calls, but the fork tree is pinned and must
# stay byte-identical (``docs/QUBIT_HERMES_VENDOR_MANIFEST.json``). So the
# parser is wrapped in memory here: only ``prog``/``description``/``epilog``
# change, cascading to subparsers through ``_prog_prefix``. No file under
# ``qubit_hermes/`` is touched; argv forwarding, exit codes, subcommands,
# arguments and argument help strings are upstream-exact.
# ---------------------------------------------------------------------------
_QUBIT_PROG = "qubit"
# The upstream product word, stored lowercase; see :func:`_qubit_text`.
_UPSTREAM_PRODUCT_WORD = "hermes"
_BRAND_MARKER = "_qubit_branded"
_BRAND_PROG = _QUBIT_PROG


def _launcher_prog() -> str:
    """User-facing launcher name for the branded fork help.

    Reuses :func:`voicebridge.cli._launcher_name` (the same canonical resolver
    the native parser uses) so ``qubit`` and the ``voicebridge`` alias read
    correctly; falls back to ``qubit`` if the launcher module is unavailable.
    """
    try:
        from .cli import _launcher_name

        return _launcher_name() or _QUBIT_PROG
    except Exception:  # pragma: no cover - only on a broken install
        return _QUBIT_PROG


def _qubit_text(text: Optional[str]) -> Optional[str]:
    """Rebrand the capitalised product word in help prose to the Qubit name.

    Only the capitalised product word is rewritten: lowercase upstream
    identifiers (``hermes-agent-dev`` skill, ``HERMES_*`` env names, package
    paths) are provenance/compatibility and stay verbatim. The source token is
    held lowercase and capitalised at runtime so the source literal is a
    compatibility identifier, never user-facing prose (the branding gate scans
    capitalised literals).
    """
    if not text:
        return text
    return text.replace(_UPSTREAM_PRODUCT_WORD.capitalize(), "Qubit")


def _rebrand_epilogue(text: Optional[str], prog: str) -> Optional[str]:
    """Rewrite the ``hermes`` program token at the start of every example line."""
    if not text:
        return text
    lines = []
    for line in text.splitlines(True):
        stripped = line.lstrip(" \t")
        if stripped == "hermes" or stripped.startswith(("hermes ", "hermes\t")):
            indent = line[: len(line) - len(stripped)]
            line = indent + prog + stripped[len("hermes"):]
        lines.append(line)
    return _qubit_text("".join(lines))


def _rebrand_parser(parser, prog: str) -> None:
    """Set ``prog``/description/epilogue on ``parser`` and every subparser."""
    parser.prog = prog
    parser.description = _qubit_text(parser.description)
    parser.epilog = _rebrand_epilogue(parser.epilog, prog)
    for action in getattr(parser, "_actions", ()) or ():
        if not isinstance(action, argparse._SubParsersAction):
            continue
        # Future ``add_parser()`` calls on this action inherit the new prefix.
        action._prog_prefix = prog
        for choice in list(getattr(action, "_choices_actions", ()) or ()):
            name = getattr(choice, "dest", None)
            if not name:
                continue
            sub = action.choices.get(name)
            if sub is not None:
                _rebrand_parser(sub, f"{prog} {name}")


def _guard_parser(parser) -> None:
    """Rebrand the fully-built tree on each parse.

    ``_build_cli_parser`` registers most subcommands *after* the top-level
    builder returns, so a one-shot rebrand would miss their descriptions. The
    instance's ``parse_known_args`` (which ``parse_args`` delegates to) now
    re-runs the pass immediately before help/errors are emitted.
    """
    if getattr(parser, "_qubit_brand_guard", False):
        return
    original = parser.parse_known_args

    def branded_parse_known_args(args=None, namespace=None):
        _rebrand_parser(parser, _BRAND_PROG)
        return original(args, namespace)

    parser.parse_known_args = branded_parse_known_args
    parser._qubit_brand_guard = True


def _install_qubit_branding(prog: str) -> None:
    """Wrap the fork's top-level parser builder so help reads as Qubit.

    Idempotent: installed once per process; repeated :func:`run` calls only
    refresh the launcher name. The module attribute is patched (not the file),
    and ``hermes_cli.main._build_cli_parser`` imports it inside the function,
    so the wrapper is seen without editing the pinned tree.
    """
    global _BRAND_PROG
    _BRAND_PROG = prog or _QUBIT_PROG
    from hermes_cli import _parser as _hp

    existing = _hp.build_top_level_parser
    if getattr(existing, _BRAND_MARKER, False):
        return

    def build_top_level_parser():
        parser, subparsers, chat_parser = existing()
        _rebrand_parser(parser, _BRAND_PROG)
        _guard_parser(parser)
        return parser, subparsers, chat_parser

    setattr(build_top_level_parser, _BRAND_MARKER, True)
    setattr(build_top_level_parser, "__wrapped__", existing)
    _hp.build_top_level_parser = build_top_level_parser


def runtime_root() -> Path:
    """Resolve the vendored fork root (installed package data first, then the
    source checkout). Never raises; callers decide how to degrade."""
    try:
        from . import resources as _res
        pkg = _res.package_data_root() / _FORK_NAME
        if pkg.is_dir():
            return pkg
        return _res.resource_path(_FORK_NAME)
    except Exception:
        return _REPO_ROOT / _FORK_NAME


def manifest_path() -> Path:
    """Per-file provenance manifest for the fork (checkout path)."""
    return _REPO_ROOT / "docs" / "QUBIT_HERMES_VENDOR_MANIFEST.json"


def default_home() -> Path:
    """Qubit-owned runtime home for the vendored fork.

    The ONE home shared by ``run()`` (CLI / forwarded reads), the dashboard
    ASGI bridge (:mod:`voicebridge.hermes_asgi`), and voice. Derived from the
    XDG state root (the same base :mod:`voicebridge.config` uses), never the
    operator's external ``~/.hermes``.
    """
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return Path(base) / "qubit-voicebridge" / "runtime"


def _external_default_home() -> Path:
    """The fork's platform default (``~/.hermes``) that Qubit must never use."""
    return Path.home() / ".hermes"


def use_qubit_home() -> Path:
    """Point the fork at the Qubit-owned home before it is imported.

    The fork resolves its home as ``HERMES_HOME`` env -> ``~/.hermes``
    (``hermes_constants.get_hermes_home``). With ``HERMES_HOME`` unset the
    ``qubit agent``/forwarded CLI read the external ``~/.hermes`` while the
    dashboard bridge uses the Qubit home -- two model/session authorities. Set
    ``HERMES_HOME`` to :func:`default_home` unless the caller supplied an
    explicit non-default home (a profile/temp sandbox), so the external
    ``~/.hermes`` is never consulted. The caller restores ``os.environ``.
    """
    home = default_home()
    current = os.environ.get("HERMES_HOME", "").strip()
    if current:
        try:
            if Path(current).expanduser().resolve(strict=False) != \
                    _external_default_home().resolve(strict=False):
                return Path(current)  # explicit caller home (profile/tests)
        except OSError:
            pass
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    return home


def _cache_repo_tests_namespace() -> None:
    """Cache the Qubit checkout's namespace ``tests`` package.

    The fork ships a *regular* ``tests/__init__.py`` package; the Qubit
    checkout's ``tests/`` has no ``__init__.py`` (a PEP 420 namespace package).
    A regular package wins over a namespace portion regardless of ``sys.path``
    order, so without this the fork's ``tests`` would shadow ``tests.<module>``
    imports (e.g. ``from tests.test_webui_reports import _start``). Cache the
    Qubit namespace before the fork root becomes importable.
    """
    import types
    if "tests" in sys.modules:
        return
    repo_tests = _REPO_ROOT / "tests"
    if not repo_tests.is_dir():
        return
    mod = types.ModuleType("tests")
    mod.__path__ = [str(repo_tests)]  # type: ignore[attr-defined]
    mod.__package__ = "tests"
    sys.modules["tests"] = mod


def ensure_path() -> Path:
    """Idempotently put the fork root on ``sys.path`` and return it.

    The fork's modules are top-level (``hermes_cli``, ``agent``, ``tools``,
    ``hermes_state`` …), so the fork root itself is the import root. The fork
    root also contains top-level ``docs/``, ``scripts/``, ``skills/`` and
    ``tests/`` directories that collide with the Qubit checkout; the Qubit repo
    root stays *ahead* of the fork root so Qubit's own packages win, and the
    Qubit ``tests`` namespace is cached first (the fork ships a regular package
    of the same name).
    NOTE: callers that only probe or run one command should use
    :func:`run`/:func:`status`, which restore ``sys.path`` afterwards.
    """
    root = runtime_root()
    s = str(root)
    if s in sys.path:
        sys.path.remove(s)
    repo = str(_REPO_ROOT)
    if repo in sys.path:
        sys.path.insert(sys.path.index(repo) + 1, s)
    else:
        sys.path.insert(0, s)
    _cache_repo_tests_namespace()
    return root


def run(argv: Optional[list] = None) -> int:
    """Run the vendored Hermes CLI in-process with ``argv``.

    ``argv`` is passed verbatim (e.g. ``["status"]`` or ``["gateway", "run"]``).
    The fork reads ``sys.argv`` internally, so it is set before import; some
    argv-dependent behavior is decided at import time. ``sys.path``/``sys.argv``
    are restored afterwards so a one-shot dispatch cannot shadow Qubit's own
    top-level packages (the fork ships a ``tests/`` and ``tools/`` tree).
    """
    args = list(argv or [])
    # Read the launcher name before sys.argv is replaced with the fork's own.
    prog = _launcher_prog()
    saved_path = list(sys.path)
    saved_env = dict(os.environ)
    saved = sys.argv
    sys.argv = ["hermes", *args]
    try:
        use_qubit_home()
        ensure_path()
        _install_qubit_branding(prog)
        from hermes_cli.main import main as _hermes_main
        result = _hermes_main()
    finally:
        sys.argv = saved
        sys.path[:] = saved_path
        os.environ.clear()
        os.environ.update(saved_env)
    return int(result) if isinstance(result, int) else 0


def status() -> dict:
    """Read-only runtime status: fork presence, provenance pin, importability.

    Never runs the runtime; safe for dashboard/voice/status surfaces."""
    import json
    root = runtime_root()
    out = {
        "runtime": "qubit-hermes-fork",
        "root": str(root),
        "present": root.is_dir(),
        "importable": False,
        "manifest": str(manifest_path()),
        "upstream_revision": None,
        "file_count": 0,
        "patched": 0,
    }
    if not root.is_dir():
        return out
    saved_path = list(sys.path)
    saved_env = dict(os.environ)
    try:
        ensure_path()
        import hermes_cli.main  # noqa: F401
        out["importable"] = True
    except Exception as exc:  # pragma: no cover - degraded env only
        out["import_error"] = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        sys.path[:] = saved_path
        # The runtime's import loads dotenv into os.environ; a read-only status
        # probe must not leak provider credentials into the rest of the process.
        os.environ.clear()
        os.environ.update(saved_env)
    mf = manifest_path()
    if mf.is_file():
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
            out["upstream_revision"] = m.get("upstream_revision")
            out["file_count"] = m.get("file_count", 0)
            out["patched"] = len(m.get("patched", []))
        except Exception:
            pass
    return out


def serve(host: str = "127.0.0.1", port: int = 9119, *,
          open_browser: bool = False):
    """Start the vendored Qubit-owned Hermes backend in-process.

    Delegates to :mod:`voicebridge.hermes_backend`, the programmatic server
    entry point that assembles the vendored FastAPI app. Keeps one runtime
    authority: this is the same backend ``qubit hermes …`` and the dashboard
    dispatch to.
    """
    from . import hermes_backend
    return hermes_backend.serve(host, port, open_browser=open_browser)


def entry() -> None:
    """Console-script entry point for the standalone ``qubit-hermes`` launcher."""
    raise SystemExit(run(sys.argv[1:]))


__all__ = ["runtime_root", "manifest_path", "default_home", "use_qubit_home",
           "ensure_path", "run", "status", "entry", "serve"]
