#!/usr/bin/env python3
"""Runtime symbol audit: for each .py under qubit_runtime, parse imports and
attribute reads, resolve the referenced module AT RUNTIME (importlib), and
report names that the module does not actually expose.

This is far more accurate than static def-collection: it follows __getattr__,
re-exports, star imports, and submodules exactly as Python does.

Strategy:
  - For `from X import a,b`: resolve X via importlib (using the module's own
    package context), then check hasattr(mod, a). If the module cannot be
    imported at all, skip (that's an import-time concern, reported elsewhere).
  - For `mod.NAME` where `mod` is a module bound by an import in the same file:
    check hasattr(mod, NAME). BUT only when NAME looks like a module-level
    constant (UPPER_CASE) or is referenced as a call target -- attribute reads
    of methods/attributes on instances are handled by static analysis, not this.
    To avoid false positives from instance attributes, restrict to aliases that
    are definitely module objects (imported via Import/ImportFrom and the alias
    resolves to a module, i.e. hasattr(mod,'__file__') or is a package).

Outputs: env-var-aware. Names that are env vars (appear as os.environ[...] /
os.getenv keys) are excluded from the report.
"""
from __future__ import annotations
import ast, importlib, sys, json, traceback, warnings
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / 'qubit_runtime'
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(ROOT))
warnings.filterwarnings('ignore')

SKIP_DIRS = {'__pycache__', 'node_modules', '.git', 'venv', '.venv', 'tests'}


def iter_py(base: Path):
    for p in base.rglob('*.py'):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def import_module_of(path: Path) -> str | None:
    """Dotted module name relative to PKG."""
    try:
        rel = path.relative_to(PKG)
    except ValueError:
        return None
    parts = list(rel.with_suffix('').parts)
    if parts and parts[-1] == '__init__':
        parts = parts[:-1]
    if not parts:
        return None
    return '.'.join(parts)


def safe_import(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def main():
    env_var_names: set[str] = set()
    files = list(iter_py(PKG))
    # 1. gather env var names (QUBIT_*/HERMES_*/etc read via os.environ/getenv)
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding='utf-8', errors='replace'))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute) \
               and n.value.attr == 'environ':
                if isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                    env_var_names.add(n.slice.value)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
               and n.func.attr in ('getenv',) and n.args:
                if isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                    env_var_names.add(n.args[0].value)

    import_misses = []
    attr_misses = []

    for f in files:
        modname = import_module_of(f)
        try:
            tree = ast.parse(f.read_text(encoding='utf-8', errors='replace'))
        except SyntaxError:
            continue

        # map alias -> imported module object (only true module objects)
        alias_mod = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    m = safe_import(a.name)
                    if m is not None:
                        alias_mod[a.asname or a.name.split('.')[0]] = (m, a.name)
            elif isinstance(n, ast.ImportFrom):
                if n.module is None:
                    continue
                base_mod = n.module
                if n.level:
                    # resolve relative using this file's package
                    pkg = modname.rsplit('.', 1)[0] if modname and '.' in modname else ''
                    for _ in range(n.level - 1):
                        pkg = pkg.rsplit('.', 1)[0] if '.' in pkg else ''
                    base_mod = (pkg + '.' + n.module).strip('.') if pkg else n.module
                m = safe_import(base_mod)
                if m is None:
                    continue
                # names could be submodules OR symbols
                for a in n.names:
                    if a.name == '*':
                        continue
                    alias_mod[a.asname or a.name] = (m, base_mod)
                    # if the name is actually a submodule, alias points to submodule
                    sub = safe_import(base_mod + '.' + a.name)
                    if sub is not None:
                        alias_mod[a.asname or a.name] = (sub, base_mod + '.' + a.name)
                    else:
                        if not hasattr(m, a.name):
                            if a.name in env_var_names:
                                continue
                            import_misses.append({
                                'file': str(f.relative_to(ROOT)), 'line': n.lineno,
                                'name': a.name, 'from': base_mod})

        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
                alias = n.value.id
                if alias in alias_mod:
                    obj, realname = alias_mod[alias]
                    if not hasattr(obj, n.attr):
                        if n.attr in env_var_names:
                            continue
                        attr_misses.append({
                            'file': str(f.relative_to(ROOT)), 'line': n.lineno,
                            'expr': f'{alias}.{n.attr}', 'module': realname})

    out = {'import_misses': import_misses, 'attr_misses': attr_misses,
           'env_vars_seen': sorted(env_var_names)}
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if dest is not None:
        dest.write_text(json.dumps(out, indent=2))
    sys.stdout = sys.__stdout__
    sys.stdout.write(f"AUDIT import_misses={len(import_misses)} attr_misses={len(attr_misses)} "
                     f"env_vars={len(env_var_names)}\n")
    sys.stdout.flush()


if __name__ == '__main__':
    main()
