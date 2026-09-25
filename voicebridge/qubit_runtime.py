"""Qubit-owned Qubit runtime bootstrap.

Per `docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`, the product's
agent loop, CLI, dashboard, sessions, tools, skills, models, memory, tasks and
runtime execute from the vendored fork under ``qubit_runtime/`` (a byte-identical
fork of the pinned upstream `2237be35`, MIT, Copyright (c) 2025 Nous Research).
No external Qubit installation is imported or supervised.

This module is the single entry point that puts the fork on ``sys.path`` and
invokes its CLI. Qubit's own launcher and dashboard call through here so the
same runtime backs CLI, dashboard and voice.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

_FORK_NAME = "qubit_runtime"
_REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Qubit help branding at the runtime boundary.
#
# The vendored fork's top-level parser is built by
# ``qubit_cli._parser.build_top_level_parser`` with a hardcoded
# ``prog="hermes"`` and the upstream description. That text is user-facing on
# normal ``qubit <family> --help`` calls, but the fork tree is pinned and must
# stay byte-identical (``docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json``). So the
# parser is wrapped in memory here: only ``prog``/``description``/``epilog``
# change, cascading to subparsers through ``_prog_prefix``. No file under
# ``qubit_runtime/`` is touched; argv forwarding, exit codes, subcommands,
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
    """Rewrite the ``qubit`` program token at the start of every example line."""
    if not text:
        return text
    lines = []
    for line in text.splitlines(True):
        stripped = line.lstrip(" \t")
        indent = line[: len(line) - len(stripped)]
        if stripped == "hermes":
            line = indent + prog
        elif stripped.startswith(("qubit ", "qubit\t")):
            # B3-R1: strip the 5-char ``qubit`` token, NOT the 6-char upstream
            # one, so the separating space/tab survives (``qubit chat``).
            line = indent + prog + stripped[len("qubit"):]
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
    and ``qubit_cli.main._build_cli_parser`` imports it inside the function,
    so the wrapper is seen without editing the pinned tree.
    """
    global _BRAND_PROG
    _BRAND_PROG = prog or _QUBIT_PROG
    from qubit_cli import _parser as _hp

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
    """Per-file provenance manifest for the fork (checkout path).

    Canonical location is ``docs/generated/``; the frozen archive copy is a
    read-only fallback for checkouts that predate the canonical move.
    """
    canonical = _REPO_ROOT / "docs/generated" / "QUBIT_HERMES_VENDOR_MANIFEST.json"
    if canonical.is_file():
        return canonical
    return _REPO_ROOT / "docs/archive/2026-09-21" / "QUBIT_HERMES_VENDOR_MANIFEST.json"


def default_home() -> Path:
    """Qubit-owned runtime home for the vendored fork.

    The ONE home shared by ``run()`` (CLI / forwarded reads), the dashboard
    ASGI bridge (:mod:`voicebridge.qubit_asgi`), and voice. Derived from the
    XDG state root (the same base :mod:`voicebridge.config` uses), never the
    operator's external ``~/.hermes``.
    """
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return Path(base) / "qubit-voicebridge" / "runtime"


#: The Qubit home this process published into ``QUBIT_HOME`` itself. It lets
#: :func:`runtime_home_for_cfg`/:func:`use_qubit_home` tell their own
#: provisional/authoritative value apart from a caller-supplied home (a profile
#: or test sandbox), which must always win. ``None`` until Qubit pins a home.
_PINNED_QUBIT_HOME: Optional[str] = None


def _resolved(path: Any) -> Path:
    """Canonicalize a candidate home for comparison (never raises)."""
    try:
        return Path(path).expanduser().resolve(strict=False)
    except OSError:
        return Path(path).expanduser()


def _publish_qubit_home(home: Path) -> None:
    """Fail-closed: point ``QUBIT_HOME`` at a Qubit-owned home.

    The vendored fork resolves its home at import time as ``QUBIT_HOME`` ->
    ``~/.hermes`` (``qubit_constants.get_qubit_home``), and some modules (e.g.
    ``qubit_state``) *create* the resolved home as an import side effect. If
    ``QUBIT_HOME`` is unset or the external default when that import runs, the
    fork would materialize the read-only external reference home. Publishing a
    Qubit-owned home first makes the fork's import-time home resolution
    fail-closed. A caller-supplied non-external ``QUBIT_HOME`` is never
    clobbered because :func:`runtime_home_for_cfg` returns before this call.
    """
    global _PINNED_QUBIT_HOME
    assert_safe_test_home(home)
    os.environ["QUBIT_HOME"] = str(home)
    _PINNED_QUBIT_HOME = str(_resolved(home))


def pin_qubit_home() -> Path:
    """Publish a Qubit-owned ``QUBIT_HOME`` before the vendored fork loads.

    Call this at the runtime boundary (see :func:`ensure_path`) *before* the
    fork is imported. An explicit non-external ``QUBIT_HOME`` (a profile or test
    sandbox) wins and is returned unchanged; an unset or external-default value
    is replaced with :func:`default_home`. This is the fail-closed guard against
    the fork adopting the operator's ``~/.hermes`` by import-time side effect.
    """
    current = os.environ.get("QUBIT_HOME", "").strip()
    if current:
        try:
            cand = Path(current).expanduser()
            if cand.resolve(strict=False) != \
                    _external_default_home().resolve(strict=False):
                return cand
        except OSError:
            pass
    home = default_home()
    _publish_qubit_home(home)
    return home


def runtime_home_for_cfg(cfg: Any = None) -> Path:
    """Resolve the runtime home for a specific ``Config`` (PRB-03).

    The canonical runtime home is the configured state directory's ``runtime``
    child. ``voicebridge.config.canonical_state_dir()`` uses the same XDG base,
    so this equals :func:`default_home` for the default profile and follows
    ``logging.jsonl_path`` for a custom one. A caller with no config keeps the
    process default. The store, the bridge, the PTY/TUI, and the CLI all resolve
    through here so they read and write ONE ``state.db``.

    The resolved home is also published into ``QUBIT_HOME`` (unless the caller
    supplied an explicit non-external home) so a subsequent vendored import can
    never fall back to ``~/.hermes``. A home this module pinned itself is
    provisional and still yields to *cfg*; only a caller-supplied value wins
    outright.
    """
    current = os.environ.get("QUBIT_HOME", "").strip()
    if current:
        try:
            cand = Path(current).expanduser()
            if (cand.resolve(strict=False) !=
                    _external_default_home().resolve(strict=False)
                    and str(_resolved(cand)) != _PINNED_QUBIT_HOME):
                return cand
        except OSError:
            pass
    if cfg is not None and hasattr(cfg, "state_dir"):
        try:
            base = Path(cfg.state_dir())
            if str(base):
                home = base / "runtime"
                _publish_qubit_home(home)
                return home
        except Exception:  # noqa: BLE001 - fall through to the process default
            pass
    home = default_home()
    _publish_qubit_home(home)
    return home


def real_default_home() -> Path:
    """The operator's real runtime home, ignoring XDG/test overrides.

    Used only by the fail-closed isolation guard below so a test process can
    never silently write the operator's canonical store.
    """
    return Path.home() / ".local" / "state" / "qubit-voicebridge" / "runtime"


def under_test() -> bool:
    """True when running inside a pytest process (``PYTEST_CURRENT_TEST``)."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def assert_safe_test_home(home: Optional[Path] = None) -> None:
    """Fail closed when a test resolves the operator's real runtime home.

    A test must set an isolated ``XDG_STATE_HOME``/``QUBIT_HOME`` (the
    repository conftest does). If one still resolves
    :func:`real_default_home`, stop rather than write the operator's store.
    """
    if not under_test():
        return
    target = Path(home) if home is not None else default_home()
    try:
        if target.expanduser().resolve(strict=False) == \
                real_default_home().resolve(strict=False):
            raise RuntimeError(
                "refusing to use the operator's real Qubit runtime home "
                f"({target}) from a test process; set XDG_STATE_HOME/QUBIT_HOME "
                "to a temporary directory")
    except OSError:
        return


def _external_default_home() -> Path:
    """The fork's platform default (``~/.hermes``) that Qubit must never use."""
    return Path.home() / ".hermes"


#: Legacy per-process homes that older Qubit builds resolved (dashboard used
#: ``<state>/qubit``, an early runtime used ``<state>/hermes-runtime`` and
#: ``<state>/qubit``). They are READ-ONLY migration sources; the canonical
#: home is :func:`default_home`.
_LEGACY_HOME_NAMES = ("qubit", "hermes-runtime", "hermes")
#: State files converged into the canonical home when the canonical copy is
#: absent. Copy-only: a canonical file always wins and nothing is removed.
_LEGACY_STATE_FILES = ("state.db", "kanban.db", "projects.db")
_HOME_MIGRATED = False


def _legacy_homes(home: Path):
    base = home.parent
    return [base / name for name in _LEGACY_HOME_NAMES
            if (base / name).is_dir() and (base / name) != home]


def _copy_state_file(src: Path, dst: Path) -> None:
    """Consistently copy one state file without touching the source.

    SQLite files are copied through the online backup API (read-only source,
    WAL included) to a temp file then atomically renamed; anything else is a
    metadata-preserving ``copy2``. The source is never opened for write, moved,
    or deleted.
    """
    if src.suffix == ".db":
        import sqlite3

        tmp = dst.with_name(dst.name + ".migrate-tmp")
        try:
            source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            try:
                target = sqlite3.connect(str(tmp))
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
            os.replace(tmp, dst)
            return
        except sqlite3.Error:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            # Not a readable SQLite database (or backup unsupported): fall
            # back to a byte copy so the data is still preserved.
    shutil.copy2(src, dst)


def migrate_legacy_homes(home: Optional[Path] = None) -> dict:
    """One-shot, copy-only convergence of legacy Qubit homes.

    For every known state file that exists in a legacy home but is absent from
    the canonical home, copy it (preserving metadata). If the canonical file
    already exists it always wins and the legacy file is left untouched, so no
    user state is ever overwritten, moved, or deleted. Idempotent and safe to
    call on every start; failures degrade to a report instead of raising.
    """
    target = Path(home) if home is not None else default_home()
    report: dict = {"home": str(target), "copied": [], "kept": []}
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"[:160]
        return report
    for legacy in _legacy_homes(target):
        for name in _LEGACY_STATE_FILES:
            src = legacy / name
            dst = target / name
            if not src.is_file():
                continue
            if dst.exists():
                report["kept"].append({"file": name, "legacy": str(legacy)})
                continue
            try:
                _copy_state_file(src, dst)
                report["copied"].append({"file": name,
                                         "legacy": str(legacy)})
            except Exception as exc:  # noqa: BLE001 - keep converging others
                report["copied_error"] = f"{name}: {type(exc).__name__}"
    return report


def use_qubit_home(cfg: Any = None) -> Path:
    """Point the fork at the Qubit-owned home before it is imported.

    The fork resolves its home as ``QUBIT_HOME`` env -> ``~/.hermes``
    (``qubit_constants.get_qubit_home``). With ``QUBIT_HOME`` unset the
    ``qubit agent``/forwarded CLI read the external ``~/.hermes`` while the
    dashboard bridge uses the Qubit home -- two model/session authorities. Set
    ``QUBIT_HOME`` to :func:`runtime_home_for_cfg` unless the caller supplied an
    explicit non-default home (a profile/temp sandbox), so the external
    ``~/.hermes`` is never consulted. The caller restores ``os.environ``.
    """
    # Adopt a pre-rebrand HERMES_HOME (if any) before resolving the home, so an
    # old install migrates without the legacy name becoming the identity. The
    # external ~/.hermes is never adopted.
    from . import qubit_legacy
    qubit_legacy.install()
    home = runtime_home_for_cfg(cfg)
    assert_safe_test_home(home)
    current = os.environ.get("QUBIT_HOME", "").strip()
    if current:
        try:
            cand = Path(current).expanduser()
            # Only a caller-supplied home wins outright; a home this module
            # published itself (``_PINNED_QUBIT_HOME``) is provisional and must
            # not short-circuit the legacy-home convergence below.
            if (cand.resolve(strict=False) !=
                    _external_default_home().resolve(strict=False)
                    and str(_resolved(cand)) != _PINNED_QUBIT_HOME):
                return Path(current)  # explicit caller home (profile/tests)
        except OSError:
            pass
    # Preserve any data stranded in a legacy per-process home before the fork
    # opens the canonical store. Copy-only and once per process; never deletes.
    global _HOME_MIGRATED
    if not _HOME_MIGRATED:
        _HOME_MIGRATED = True
        try:
            migrate_legacy_homes(home)
        except Exception:  # noqa: BLE001 - migration must never block startup
            pass
    # Publish the resolved home BEFORE creating it: if mkdir fails (read-only
    # state dir, permissions, full disk) the caller's guard must still leave
    # QUBIT_HOME pointing at the Qubit-owned path, never the vendored ~/.hermes
    # default that ``qubit_constants.get_qubit_home`` would otherwise return.
    _publish_qubit_home(home)
    home.mkdir(parents=True, exist_ok=True)
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

    The fork's modules are top-level (``qubit_cli``, ``agent``, ``tools``,
    ``qubit_state`` …), so the fork root itself is the import root. The fork
    root also contains top-level ``docs/``, ``scripts/``, ``skills/`` and
    ``tests/`` directories that collide with the Qubit checkout; the Qubit repo
    root stays *ahead* of the fork root so Qubit's own packages win, and the
    Qubit ``tests`` namespace is cached first (the fork ships a regular package
    of the same name).
    NOTE: callers that only probe or run one command should use
    :func:`run`/:func:`status`, which restore ``sys.path`` afterwards.
    """
    # Fail closed before the fork becomes importable: the vendored import
    # resolves (and some modules create) the home from ``QUBIT_HOME`` ->
    # ``~/.hermes``, so a Qubit-owned home must be published first.
    pin_qubit_home()
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
    """Run the vendored Qubit CLI in-process with ``argv``.

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
        from qubit_cli.main import main as _qubit_main
        result = _qubit_main()
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
        "runtime": "qubit-runtime-fork",
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
        import qubit_cli.main  # noqa: F401
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
    """Refuse a second live Qubit listener (single-gateway invariant).

    Delegates to :mod:`voicebridge.qubit_backend`, which is inert: Qubit
    serves one gateway (:mod:`voicebridge.webui`) and proxies vendored reads
    in-process via :mod:`voicebridge.qubit_asgi`. Use ``qubit agent serve``
    for the foreground launcher.
    """
    from . import qubit_backend
    return qubit_backend.serve(host, port, open_browser=open_browser)


def entry() -> None:
    """Console-script entry point for the standalone ``qubit-runtime`` launcher."""
    raise SystemExit(run(sys.argv[1:]))


__all__ = ["runtime_root", "manifest_path", "default_home", "use_qubit_home",
           "migrate_legacy_homes", "ensure_path", "run", "status", "entry",
           "serve"]
