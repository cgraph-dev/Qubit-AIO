#!/usr/bin/env python3
"""Exhaustive entry-point resolution sweep for the vendored Qubit runtime.

WHY THIS EXISTS
---------------
``scripts/sync_import_gate.py`` probes a CURATED ~28-module list by actually
importing each one. That catches import-time breakage for those 28 modules and
nothing else. Two real production incidents came from a symbol being dropped by
an upstream rename / partial port while its call site survived:

  * ``tui_gateway/server.py`` lost ``from tui_gateway.turn_marker import
    marker_writer_state`` -> ``NameError`` on every resume of a crash-interrupted
    session.
  * ``qubit_cli/plugins.py`` lost ``publish_gateway_message_host`` /
    ``clear_published_gateway_message_host`` / ``_attach_published_gateway_host``
    -> ``ImportError`` that killed the gateway daemon at startup.

Both are the same shape: a *symbol* disappeared from a module while a caller kept
referencing it. A named-import probe that imports the *importing* module only
fails when the module is on the curated list. This script closes the class
tree-wide by statically resolving every ``from X import Y`` and every ``X.Y``
attribute access on a *genuinely module-bound* name, for EVERY module in both
vendored roots.

WHAT IT DOES
------------
For each ``.py`` file under the vendored roots it:

  1. Parses the file with ``ast`` (never imports it -- no side effects).
  2. Indexes every module in the tree (module name -> source file) so ``from X
     import Y`` can be resolved to X's source without executing anything.
  3. Collects X's public surface statically: module-level ``def`` / ``class`` /
     assignments / annotated assignments / imports (re-exports) / ``__all__`` /
     PEP 562 ``__getattr__`` (permissive) / names assigned inside functions via
     ``global`` / conditional (``try``, ``if``) definitions.
  4. Reports every imported name Y that is not present in X's static surface, and
     every ``mod.ATTR`` on a true module-alias binding whose ATTR is not present.

Resolution mirrors ``sync_import_gate.py``'s interpreter/PYTHONPATH model:
``qubit_runtime/`` and the repo root are the two import roots, so a bare
``import agent.foo`` resolves to ``qubit_runtime/agent/foo.py``.

FALSE-POSITIVE HANDLING
-----------------------
 (a) ``from __future__ import ...``     -- skipped entirely; ``__future__`` is a
     stdlib pseudo-module and its names always resolve.
 (b) ``from X import *``                -- cannot be resolved statically without
     executing X; the star import is recorded as SKIPPED and no name from it is
     ever reported as missing. Target modules that DO a star import are also
     marked dynamic (their surface is unknowable).
 (c) conditional / lazy imports inside functions -- STILL CHECKED (they are real
     call sites and are exactly the class of bug this exists to catch). They are
     flagged ``lazy`` in the output so a reader knows they only fire at call
     time, and imports wrapped in ``try/except ImportError`` are additionally
     flagged ``optional``.
 (d) ``try/except ImportError`` fallbacks -- still reported, but marked
     ``optional`` and demoted to the SOFT bucket, because a missing name there is
     handled by design and is not a crash.
 (e) re-exports via ``__all__`` / compat shims -- names listed in a module's
     ``__all__`` are treated as present, and any name bound by an ``import``
     inside the module counts as a re-export. Covers the ``compat_manifest.json``
     facade-shim pattern.
 (f) ``TYPE_CHECKING``-only imports -- reported but marked ``typechecking`` and
     demoted to SOFT; they never execute at runtime.
 (g) string annotations -- not resolved at all (they are string literals); only
     real ``Name``/``Attribute`` AST nodes are checked, so forward references in
     quotes cause no false positives.

Additional precision rules (each was added after a noisy run to kill a specific
false-positive class):
  * Module attribute reads (``X.Y``) are checked ONLY when ``X`` is a true module
    binding in its lexical scope: ``import X`` / ``import X.Y as Z`` / ``from P
    import submod`` (submod resolves to a module file). ``from M import sym``
    followed by ``sym.attr`` is an *object* attribute read, never a module read.
  * Alias bindings respect lexical scope: a function parameter or local of the
    same name shadows a module alias (``def body(server)`` / ``def f(config)``).
    Without this, ~40% of findings were phantom.
  * A module is treated as DYNAMIC (surface unknowable -> never reported against)
    if it has a PEP 562 ``__getattr__``, a module-level ``from x import *``, an
    ``_OriginProxy``-style attribute proxy, a ``globals().update(...)`` /
    ``registry.export(globals())`` / ``setattr(sys.modules[__name__], ...)``
    injection, or a ``...register(sys.modules[__name__])`` call — the
    ``tui_gateway/server.py`` pattern that rebinds ~40 split handler modules.
  * Module surface includes names assigned inside top-level ``if``/``try``/
    ``with`` blocks and ``global``-declared names.
  * Bare single-segment names resolve against the importing file's own directory
    first ONLY when the name is not a real tree module (skills/optional-skills
    scripts import their siblings that way); the ``voicebridge`` tree never
    provides bare aliases.

Output has THREE buckets:
  * HARD            -- a real in-tree miss that would raise at runtime.
  * OPTIONAL        -- the import is guarded by try/except (ImportError,
                       ModuleNotFoundError, Exception, BaseException, or bare) or
                       sits in an except-handler fallback, or is TYPE_CHECKING-
                       only. Handled by design; not a crash.
  * UNVERIFIED      -- a name/module we could not resolve and could NOT prove is
                       in-tree (third-party or repo-root). Reported for
                       completeness, never counted as a failure.

USAGE
-----
    python3 scripts/check_entry_point_resolution.py               # human report
    python3 scripts/check_entry_point_resolution.py --json out.json
    python3 scripts/check_entry_point_resolution.py --include-tests

Exit codes: 0 = no HARD findings, 1 = HARD findings present, 2 = usage error.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Vendored roots, in sys.path order (mirrors sync_import_gate.py's
# PYTHONPATH="{repo}/qubit_runtime:{repo}"). The FIRST root wins on a name clash.
ROOTS = ("qubit_runtime", "voicebridge")

SKIP_DIRS = {
    "__pycache__", "node_modules", ".git", ".venv", "venv", ".mypy_cache",
    ".ruff_cache", ".pytest_cache", "ui-tui", "website", "dist", "build",
    ".eggs", ".hermes",
}

TEST_DIR_NAMES = {"tests", "test", "tests-js", "testing"}
TEST_FILE_PREFIXES = ("test_",)
TEST_FILE_SUFFIXES = ("_test.py",)

# Stdlib + very common third-party roots. Not resolved, never reported.
KNOWN_EXTERNAL_PREFIXES = {
    "__future__", "abc", "argparse", "ast", "asyncio", "atexit", "base64",
    "binascii", "bisect", "builtins", "bz2", "calendar", "cmath", "collections",
    "concurrent", "contextlib", "contextvars", "copy", "csv", "ctypes",
    "dataclasses", "datetime", "decimal", "difflib", "dis", "email", "enum",
    "errno", "faulthandler", "fcntl", "fnmatch", "fractions", "ftplib",
    "functools", "gc", "getpass", "gettext", "glob", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "imaplib", "importlib", "inspect", "io", "ipaddress",
    "itertools", "json", "keyword", "linecache", "locale", "logging", "lzma",
    "mailbox", "math", "mimetypes", "multiprocessing", "netrc", "numbers",
    "operator", "os", "pathlib", "pickle", "pkgutil", "platform", "plistlib",
    "poplib", "posixpath", "pprint", "profile", "pty", "pwd", "py_compile",
    "queue", "random", "re", "readline", "resource", "sched", "secrets",
    "select", "selectors", "shlex", "shutil", "signal", "site", "smtplib",
    "socket", "socketserver", "sqlite3", "ssl", "stat", "statistics", "string",
    "struct", "subprocess", "sys", "sysconfig", "tarfile", "tempfile",
    "termios", "textwrap", "threading", "time", "timeit", "token", "tokenize",
    "tomllib", "traceback", "tracemalloc", "types", "typing", "unicodedata",
    "unittest", "urllib", "uuid", "venv", "warnings", "wave", "weakref",
    "webbrowser", "xml", "xmlrpc", "zipfile", "zlib",
    # third-party widely used here
    "pytest", "yaml", "numpy", "requests", "httpx", "aiohttp", "pydantic",
    "rich", "click", "typer", "openai", "anthropic", "tiktoken", "PIL",
    "psutil", "setuptools", "pkg_resources", "packaging", "ruamel", "tomli",
    "tomli_w", "fastapi", "starlette", "uvicorn", "websockets", "sounddevice",
    "soundfile", "dotenv", "jinja2", "markdown", "bs4", "lxml", "pygments",
    "anyio", "sniffio", "certifi", "charset_normalizer", "idna", "h11",
    "httpcore", "socksio", "wsproto", "multipart", "starlette", "watchfiles",
    "uvloop", "pyyaml", "_pytest", "coverage", "mypy_extensions", "typing_extensions",
}


def _is_external(top: str) -> bool:
    return top in KNOWN_EXTERNAL_PREFIXES


# In-tree namespaces: a *missing* module whose top segment is one of these is a
# genuine dropped-module finding. Anything else that we cannot resolve is an
# unknown third-party name -> recorded as UNVERIFIED, never a hard failure.
IN_TREE_ROOTS = {
    "agent", "tools", "gateway", "qubit_cli", "tui_gateway", "cron", "pm",
    "plugins", "voicebridge", "qubit_platform", "acp_adapter", "native",
    "providers", "apps", "evals", "model_tools", "batch_runner", "utils",
    "toolsets", "toolset_distributions", "registration_lifecycle",
    "run_agent", "qubit_bootstrap", "qubit_logging", "qubit_time", "qubit_yaml",
    "qubit_startup_watchdog", "mcp_serve", "mini_swe_runner",
    "trajectory_compressor", "cli", "config", "tools_registry",
}


def _is_in_tree_namespace(top: str) -> bool:
    return top in IN_TREE_ROOTS or top.startswith("qubit_")


def _is_test_path(rel: Path) -> bool:
    if set(rel.parts) & TEST_DIR_NAMES:
        return True
    name = rel.name
    return name.startswith(TEST_FILE_PREFIXES) or name.endswith(TEST_FILE_SUFFIXES)


class ModuleIndex:
    """Maps a dotted module name to its source file, per import root."""

    def __init__(self, repo: Path):
        self.modules: dict[str, Path] = {}
        self.root_dirs: list[Path] = []
        self._build(repo)

    def _build(self, repo: Path) -> None:
        for root_name in ROOTS:
            root = repo / root_name
            if not root.is_dir():
                continue
            self.root_dirs.append(root)
            for p in root.rglob("*.py"):
                if any(part in SKIP_DIRS for part in p.parts):
                    continue
                rel = p.relative_to(root)
                parts = list(rel.with_suffix("").parts)
                if parts and parts[-1] == "__init__":
                    parts = parts[:-1]
                modname = ".".join(parts) if parts else root_name
                if root_name == "voicebridge":
                    # voicebridge is imported ONLY as `voicebridge.<x>` -- it is a
                    # package, not a sys.path root, so it never gets a bare alias.
                    key = "voicebridge." + ".".join(parts) if parts else "voicebridge"
                else:
                    key = ".".join(parts) if parts else root_name
                self.modules.setdefault(key, p)

    def find_module(self, dotted: str, near: Path | None = None) -> Path | None:
        # Global index first (tree modules and packages).
        p = self.modules.get(dotted)
        if p is not None:
            return p
        # Root-scan fallback for namespace packages / dirs without __init__.
        # Only the true sys.path root (qubit_runtime) participates in bare-name
        # resolution -- voicebridge is a package and is reachable solely via its
        # `voicebridge.`-prefixed keys in the index.
        for root in self.root_dirs:
            if root.name == "voicebridge" and "." not in dotted:
                continue
            cand = root / Path(*dotted.split("."))
            if cand.is_dir():
                return cand
            if cand.with_suffix(".py").exists():
                return cand.with_suffix(".py")
        # Fallback for single-segment names: standalone scripts (skills/
        # optional-skills) import their siblings by bare name because the
        # script's own directory is on sys.path. Only consulted when the name is
        # NOT a tree module at all (a dotted name is never flattened).
        if near is not None and "." not in dotted:
            cand = near.parent / dotted
            if cand.with_suffix(".py").exists():
                return cand.with_suffix(".py")
            if cand.is_dir():
                return cand
        return None


class Surface:
    __slots__ = ("names", "all_names", "dynamic", "ok", "is_package", "submodules")

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.all_names: set[str] = set()
        self.dynamic = False
        self.ok = False
        self.is_package = False
        self.submodules: set[str] = set()


def _collect_surface(path: Path, cache: dict[Path, Surface], seen: set[str] | None = None) -> Surface:
    """Statically collect a module's surface. Never imports the module."""
    if path is None:
        s = Surface(); s.ok = True; s.dynamic = True
        return s
    if path in cache:
        return cache[path]
    surf = Surface()
    surf.is_package = path.is_dir()
    src_file = path / "__init__.py" if path.is_dir() else path
    if path.is_dir():
        for child in path.iterdir():
            if child.name.endswith(".py") and child.name != "__init__.py":
                surf.submodules.add(child.stem)
            elif child.is_dir() and child.name not in SKIP_DIRS:
                surf.submodules.add(child.name)
        surf.names |= surf.submodules
    if not src_file.exists():
        surf.ok = True  # namespace package
        cache[path] = surf
        return surf
    try:
        tree = ast.parse(src_file.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        surf.ok = False
        cache[path] = surf
        return surf
    surf.ok = True

    def bind_target(t: ast.AST) -> None:
        if isinstance(t, ast.Name):
            surf.names.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                bind_target(e)

    # module-level bindings, descending into top-level conditional blocks
    # (`if`/`try`/`with`/`for`) so a name assigned inside a module-level
    # try/except (e.g. `_HAS_NUMPY = True` / `= False`) is part of the surface.
    def collect_block(stmts) -> None:
        for node in stmts:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                surf.names.add(node.name)
                if node.name == "__getattr__" and not isinstance(node, ast.ClassDef):
                    surf.dynamic = True
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    bind_target(t)
            elif isinstance(node, ast.AnnAssign):
                bind_target(node.target)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    if a.name == "*":
                        surf.dynamic = True
                    else:
                        surf.names.add(a.asname or a.name.split(".")[0])
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith,
                                   ast.For, ast.AsyncFor, ast.While)):
                collect_block(getattr(node, "body", []))
                collect_block(getattr(node, "orelse", []))
                collect_block(getattr(node, "finalbody", []))
                for h in getattr(node, "handlers", []) or []:
                    collect_block(h.body)

    collect_block(tree.body)
    # also pick up `global`-declared names
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            for nm in node.names:
                surf.names.add(nm)

    # __all__
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    for elt in ast.walk(node.value):
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            surf.all_names.add(elt.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "__all__" and node.value is not None:
            for elt in ast.walk(node.value):
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    surf.all_names.add(elt.value)
    surf.names |= surf.all_names

    # _OriginProxy-style dynamic facade: `X(...)` assigned at module level where
    # X defines __getattr__ -> surface unknowable.
    proxy_classes = {
        n.name for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef)
        and any(isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                and b.name == "__getattr__" for b in n.body)
    }
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            cname = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else None)
            if cname in proxy_classes:
                surf.dynamic = True

    # Dynamic namespace rebinding: `for m in (...): m.register(sys.modules[__name__])`
    # or `globals().update(...)` / `setattr(sys.modules[__name__], ...)` attaches
    # arbitrary names to this module at import time (tui_gateway/server.py pulls
    # ~40 split handler modules' globals onto its own namespace this way). The
    # static surface cannot enumerate those; treat the module as dynamic.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else None)
            if fname == "register" and any(_refs_own_module(a) for a in node.args):
                surf.dynamic = True
            elif fname == "setattr" and node.args and _refs_own_module(node.args[0]):
                surf.dynamic = True
            elif isinstance(fn, ast.Attribute) and fn.attr == "update" and _refs_globals(fn.value):
                surf.dynamic = True
            elif isinstance(fn, ast.Attribute) and fn.attr in ("export", "install", "bind"):
                # `registry.export(globals())` injects names into this module.
                if any(_refs_globals(a) for a in node.args):
                    surf.dynamic = True

    cache[path] = surf
    return surf


def _refs_own_module(node) -> bool:
    """True for `sys.modules[__name__]`."""
    return (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
            and node.value.attr == "modules"
            and isinstance(node.slice, ast.Name) and node.slice.id == "__name__")


def _refs_globals(node) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "globals"


class Finding:
    __slots__ = ("path", "lineno", "kind", "module", "symbol", "flags")

    def __init__(self, path, lineno, kind, module, symbol, flags):
        self.path = path; self.lineno = lineno; self.kind = kind
        self.module = module; self.symbol = symbol; self.flags = flags

    @property
    def hard(self) -> bool:
        return not ({"optional", "typechecking", "unverified"} & set(self.flags))


def _resolve_relative(modname: str, is_pkg: bool, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    parts = modname.split(".") if modname else []
    if not is_pkg and parts:
        parts = parts[:-1]
    up = node.level - 1
    if up > len(parts):
        return None
    base = parts[: len(parts) - up]
    if node.module:
        base = base + node.module.split(".")
    return ".".join(base) if base else None


def analyze_file(path: Path, rel: Path, modname: str, index: ModuleIndex,
                 cache: dict[Path, Surface]) -> list[Finding]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return []
    is_pkg = path.name == "__init__.py"
    findings: list[Finding] = []

    # context tags
    ctx: dict[int, set[str]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Try):
            # An import guarded by try/except is "handled by design". A narrow
            # `except ImportError` is the classic optional-dependency guard, but
            # a broad `except Exception`/`except BaseException`/bare `except`
            # swallows ImportError too, so treat those as optional as well.
            def _is_import_guard(h: ast.ExceptHandler) -> bool:
                if h.type is None:  # bare except:
                    return True
                names = []
                if isinstance(h.type, ast.Name):
                    names = [h.type.id]
                elif isinstance(h.type, ast.Tuple):
                    names = [e.id for e in h.type.elts if isinstance(e, ast.Name)]
                return any(x in ("ImportError", "ModuleNotFoundError", "Exception",
                                 "BaseException") for x in names)

            has_ie = any(_is_import_guard(h) for h in n.handlers)
            if has_ie:
                for sub in n.body:
                    for d in ast.walk(sub):
                        ctx.setdefault(id(d), set()).add("optional")
            # Imports inside the except-handler body are the fallback path; they
            # only run when the primary import already failed.
            for h in n.handlers:
                for sub in h.body:
                    for d in ast.walk(sub):
                        ctx.setdefault(id(d), set()).add("optional")
        if isinstance(n, ast.If):
            t = n.test
            tn = t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else None)
            if tn == "TYPE_CHECKING":
                for sub in n.body:
                    for d in ast.walk(sub):
                        ctx.setdefault(id(d), set()).add("typechecking")
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in n.body:
                for d in ast.walk(sub):
                    ctx.setdefault(id(d), set()).add("lazy")

    # (1) Scope-aware module bindings. A name is only a module alias at a given
    #     Attribute site if the import that binds it is visible there AND the
    #     name is not shadowed by a parameter / local / loop / with / comprehension
    #     target / assignment in an enclosing or intervening scope. Getting this
    #     wrong is the single biggest false-positive source (e.g. `def body(server)`
    #     shadowing the module `server`, or `def _dict_at(config: dict)` shadowing
    #     the module `config`).
    module_alias_at: dict[int, str] = {}   # id(Attribute node) -> dotted module

    def _imports_in(stmts) -> dict[str, str]:
        """Map name -> dotted module for Import/ImportFrom in these statements."""
        out: dict[str, str] = {}
        for st in stmts:
            if isinstance(st, ast.Import):
                for a in st.names:
                    out[a.asname or a.name.split(".")[0]] = a.name
            elif isinstance(st, ast.ImportFrom):
                if st.module == "__future__" and st.level == 0:
                    continue
                res = _resolve_relative(modname, is_pkg, st) if st.level else st.module
                if not res:
                    continue
                for a in st.names:
                    if a.name == "*":
                        continue
                    if index.find_module(f"{res}.{a.name}", near=path) is not None:
                        out[a.asname or a.name] = f"{res}.{a.name}"
        return out

    def _local_bindings(fn) -> set[str]:
        """Names bound locally anywhere in a function (params, stores, imports,
        for/with/comprehension targets, except-as)."""
        names: set[str] = set()
        a = fn.args
        for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
            names.add(arg.arg)
        if a.vararg:
            names.add(a.vararg.arg)
        if a.kwarg:
            names.add(a.kwarg.arg)
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                names.add(sub.id)
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                for nm in sub.names:
                    if nm.name != "*":
                        names.add(nm.asname or nm.name.split(".")[0])
            elif isinstance(sub, ast.ExceptHandler) and sub.name:
                names.add(sub.name)
        return names

    def _scan(stmts, visible: dict[str, str]) -> None:
        """Linear top-down walk. `visible` maps name -> module for the current
        scope (already minus shadowed names). Records Attribute sites."""
        for st in stmts:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local = _local_bindings(st)
                inner = {k: v for k, v in visible.items() if k not in local}
                _scan(st.body, inner)
                # decorators / default values / annotations: enclosing scope.
                # They contain no module-attr reads worth flagging; skip.
                continue
            if isinstance(st, ast.ClassDef):
                cls_names = {b.name for b in st.body
                             if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
                _scan(st.body, {k: v for k, v in visible.items() if k not in cls_names})
                continue
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                continue
            # record attribute sites in THIS statement, not descending into
            # nested defs/classes (handled above), and recurse into sub-blocks.
            for n in ast.walk(st):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                    if n.value.id in visible:
                        module_alias_at[id(n)] = visible[n.value.id]
            for field in ("body", "orelse", "finalbody"):
                block = getattr(st, field, None)
                if isinstance(block, list) and not isinstance(
                        st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    _scan(block, visible)
            if isinstance(st, ast.Try):
                for h in st.handlers:
                    _scan(h.body, visible)
        return

    _scan(tree.body, _imports_in(tree.body))

    # (2) from-import checks
    for n in ast.walk(tree):
        if not isinstance(n, ast.ImportFrom):
            continue
        base_flags = set(ctx.get(id(n), set()))
        if n.module == "__future__" and n.level == 0:
            continue
        resolved = _resolve_relative(modname, is_pkg, n) if n.level else n.module
        if not resolved:
            continue
        top = resolved.split(".")[0]
        for a in n.names:
            if a.name == "*":
                continue  # (b) star import: skipped
            sub_mod = f"{resolved}.{a.name}"
            sub_path = index.find_module(sub_mod, near=path)
            if sub_path is not None:
                continue  # `from pkg import submodule` -> module binding, handled above
            if _is_external(top):
                continue
            tpath = index.find_module(resolved, near=path)
            if tpath is None:
                # Module not found. Only a name whose top segment is a known
                # in-tree namespace is a real dropped-module finding; anything
                # else is an unresolvable (likely third-party) name that we mark
                # UNVERIFIED rather than crying wolf.
                if _is_in_tree_namespace(top):
                    findings.append(Finding(str(rel), n.lineno, "unresolved-module",
                                            resolved, a.name, frozenset(base_flags)))
                else:
                    findings.append(Finding(str(rel), n.lineno, "unknown-module",
                                            resolved, a.name,
                                            frozenset(base_flags | {"unverified"})))
                continue
            surf = _collect_surface(tpath, cache)
            if not surf.ok or surf.dynamic:
                continue
            if a.name in surf.names:
                continue
            # A name missing from a module that is only reachable via the
            # sibling fallback and whose top segment is not an in-tree namespace
            # is almost certainly a third-party package shadowed by a same-named
            # local file (e.g. `from daytona import Daytona` inside daytona.py).
            # Mark UNVERIFIED rather than asserting a dropped symbol.
            if not _is_in_tree_namespace(top) and top not in index.modules:
                findings.append(Finding(str(rel), n.lineno, "unknown-name",
                                        resolved, a.name,
                                        frozenset(base_flags | {"unverified"})))
                continue
            findings.append(Finding(str(rel), n.lineno, "missing-name",
                                    resolved, a.name, frozenset(base_flags)))

    # (3) alias.ATTR checks -- only scope-valid module aliases
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            dotted = module_alias_at.get(id(n))
            if not dotted:
                continue
            top = dotted.split(".")[0]
            if _is_external(top):
                continue
            tpath = index.find_module(dotted, near=path)
            if tpath is None:
                continue
            surf = _collect_surface(tpath, cache)
            if not surf.ok or surf.dynamic:
                continue
            if n.attr.startswith("__") and n.attr.endswith("__"):
                continue
            if n.attr in surf.names:
                continue
            # Third-party-shadowing guard: a bare single-segment module whose top
            # segment is not an in-tree namespace and is not a real tree module
            # is almost certainly an external package shadowed by a same-named
            # local file -> UNVERIFIED, not a hard finding.
            if "." not in dotted and not _is_in_tree_namespace(top) and top not in index.modules:
                findings.append(Finding(str(rel), n.lineno, "unknown-attr",
                                        dotted, n.attr,
                                        frozenset(set(ctx.get(id(n), set())) | {"unverified"})))
                continue
            flags = set(ctx.get(id(n), set()))
            findings.append(Finding(str(rel), n.lineno, "missing-attr",
                                    dotted, n.attr, frozenset(flags)))

    return findings


def iter_targets(repo: Path, include_tests: bool):
    seen: set[Path] = set()
    out = []
    for root_name in ROOTS:
        root = repo / root_name
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if p in seen:
                continue
            seen.add(p)
            rel_root = p.relative_to(root)
            if not include_tests and _is_test_path(rel_root):
                continue
            parts = list(rel_root.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if root_name == "voicebridge":
                # voicebridge files are imported with the package prefix
                # (voicebridge.foo); qubit_runtime files are imported flat (foo).
                modname = "voicebridge." + ".".join(parts) if parts else "voicebridge"
            else:
                modname = ".".join(parts) if parts else root_name
            out.append((p, p.relative_to(repo), modname))
    return out


def _group(findings):
    by = defaultdict(list)
    for f in findings:
        by[f.path].append(f)
    return by


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--json", help="write full findings JSON here")
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--max-per-file", type=int, default=0)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    t0 = time.time()
    index = ModuleIndex(repo)
    cache: dict[Path, Surface] = {}

    prod, tests = [], []
    targets = iter_targets(repo, include_tests=True)
    for path, rel, modname in targets:
        rel_root = path.relative_to(repo / ROOTS[0]) if str(path).startswith(str(repo / ROOTS[0])) \
            else path.relative_to(repo)
        is_test = _is_test_path(rel_root)
        if is_test and not args.include_tests:
            continue
        if rel_root.parts and rel_root.parts[0] == "scripts":
            continue
        f = analyze_file(path, rel_root, modname, index, cache)
        (tests if is_test else prod).extend(f)

    elapsed = time.time() - t0

    # Provenance: a bare number caused real confusion. Five agents were told the
    # baseline was prod_hard=0; the COMMITTED tree measured 19 at the same commit,
    # and 0 was only true of a checkout carrying ~79 uncommitted files. Print what
    # was actually measured so the number can never be read out of context.
    try:
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=20).stdout.count("\n")
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        dirty, head = -1, "?"

    prod_hard = [f for f in prod if f.hard]
    prod_soft = [f for f in prod if not f.hard]
    prod_unverified = [f for f in prod_soft if "unverified" in f.flags]
    prod_optional = [f for f in prod_soft if "unverified" not in f.flags]
    test_hard = [f for f in tests if f.hard]

    print("# Entry-point resolution sweep")
    print(f"# roots: {', '.join(ROOTS)}")
    print(f"# measured tree: HEAD {head}  uncommitted files: {dirty}"
          + ("   <-- RESULTS REFLECT UNCOMMITTED WORK, NOT A COMMITTED BASELINE"
             if dirty > 0 else "   (clean: results are reproducible from this commit)"))
    print(f"# modules scanned: {len(targets)}  time: {elapsed:.1f}s")
    print(f"# HARD (production): {len(prod_hard)}")
    print(f"# OPTIONAL/TYPECHECKING (production): {len(prod_optional)}")
    print(f"# UNVERIFIED unresolved third-party-looking modules (production): {len(prod_unverified)}")
    print(f"# HARD (tests): {len(test_hard)}")
    print()

    def dump(label, findings):
        if not findings:
            return
        print(f"## {label}")
        for path, fl in sorted(_group(findings).items(), key=lambda kv: (-len(kv[1]), kv[0])):
            print(f"  {path}  ({len(fl)})")
            items = fl if args.max_per_file <= 0 else fl[: args.max_per_file]
            for fb in items:
                fl_s = f" [{','.join(sorted(fb.flags))}]" if fb.flags else ""
                print(f"    L{fb.lineno}: {fb.kind}: {fb.module}.{fb.symbol}{fl_s}")
        print()

    dump("PRODUCTION (HARD)", prod_hard)
    dump("PRODUCTION (OPTIONAL/TYPECHECKING)", prod_optional)
    dump("UNVERIFIED (unresolved non-in-tree module names)", prod_unverified)
    if args.include_tests:
        dump("TESTS (HARD)", test_hard)

    if args.json:
        payload = {
            "roots": list(ROOTS), "scanned": len(targets), "elapsed_s": round(elapsed, 1),
            "counts": {"prod_hard": len(prod_hard), "prod_soft": len(prod_soft),
                       "prod_unverified": len(prod_unverified),
                       "test_hard": len(test_hard)},
            "findings": [
                {"file": f.path, "line": f.lineno, "kind": f.kind, "module": f.module,
                 "symbol": f.symbol, "flags": sorted(f.flags), "hard": f.hard,
                 "scope": "test" if _is_test_path(Path(f.path)) else "prod"}
                for f in (prod + tests)
            ],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2))
        print(f"JSON written: {args.json}")

    return 1 if prod_hard else 0


if __name__ == "__main__":
    sys.exit(main())
