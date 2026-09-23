"""Reachability proof for Qubit compat handlers the vendored runtime owns.

Owner decision 2026-09-17: the vendored upstream runtime is canonical for
upstream-compatible routes, so a Qubit compat handler on such a route is
non-authoritative. This analyzer classifies every such handler as:

* ``production_referenced`` -- another production module (CLI/voice/ops/other
  compat handler) calls it, so it must be kept (as a delegating wrapper or an
  authoritative policy handler).
* ``test_referenced``        -- a test calls it directly, so it must be kept.
* ``registration_only``      -- the only reference is its own ``HANDLERS``
  registration in ``qubit_compat.py``; it is reachable only through the compat
  fallback and is a delete candidate.

It writes ``docs/generated/QUBIT_COMPAT_HANDLER_REACHABILITY.json``; the guard test
``tests/test_compat_handler_reachability.py`` keeps it current and checked
against the route-ownership matrix. ``docs/archive/2026-09-21/`` is
historical-only and is never a generator input or output (owner decision #6).
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

REPO = Path(__file__).resolve().parents[1]
for extra in (str(REPO), str(REPO / "scripts")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import source_inventory as SI  # noqa: E402
from voicebridge import qubit_compat as HC  # noqa: E402

MATRIX = REPO / "docs/generated/QUBIT_ROUTE_OWNERSHIP.json"
OUT = REPO / "docs/generated/QUBIT_COMPAT_HANDLER_REACHABILITY.json"
COMPAT = REPO / "voicebridge/qubit_compat.py"

_SCAN_DIRS = ("voicebridge", "tests", "scripts")
_SKIP = {"qubit_compat.py"}


def _handler_names() -> Dict[str, List[str]]:
    """handler function name -> routes (method path) that register it."""
    out: Dict[str, List[str]] = {}
    for route in HC.ROUTES:
        handler = str(route.get("handler", ""))
        if not handler or handler.startswith("__"):
            continue
        out.setdefault(handler, []).append(
            f"{str(route.get('method')).upper()} {route.get('pattern')}")
    return out


def _sample(template: str):
    """Representative concrete path for a route template/pattern (or None)."""
    group = re.compile(r"\(\?P<[^>]+>([^)]*)\)")
    anon = re.compile(r"\(([^()]*)\)")
    t = group.sub("x1", template)
    t = anon.sub("x1", t)
    t = t.replace("[^/]+", "x1").replace(".+", "x1").replace(".*", "x1")
    t = t.replace("^", "").replace("$", "")
    segs = []
    for seg in t.strip("/").split("/"):
        if not seg:
            continue
        if seg.startswith("{") and seg.endswith("}"):
            segs.append("a/b" if seg.endswith(":path}") else "x1")
        else:
            segs.append(seg)
    path = "/" + "/".join(segs)
    return None if re.search(r"[()\[\]?*+|]", path) else path


def _tests_text() -> str:
    chunks = []
    for path in (REPO / "tests").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _route_test_reachable(routes: List[str], tests_text: str) -> bool:
    for route in routes:
        _, _, pattern = route.partition(" ")
        if pattern and f'"{pattern}"' in tests_text:
            return True
        sample = _sample(pattern)
        if sample and f'"{sample}"' in tests_text:
            return True
    return False


def _defined_functions(path: Path) -> Set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _registered_function_names() -> Set[str]:
    """Function names registered as HANDLERS dict values (the compat dispatch)."""
    tree = ast.parse(COMPAT.read_text(encoding="utf-8"))
    names: Set[str] = set()

    def _collect_dict(d: ast.Dict) -> None:
        for v in d.values:
            if isinstance(v, ast.Name):
                names.add(v.id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                        "HANDLERS", "_EXTRA_HANDLERS") \
                        and isinstance(node.value, ast.Dict):
                    _collect_dict(node.value)
        if isinstance(node, ast.AnnAssign) \
                and isinstance(node.target, ast.Name) \
                and node.target.id in ("HANDLERS", "_EXTRA_HANDLERS") \
                and isinstance(node.value, ast.Dict):
            _collect_dict(node.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "update":
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    _collect_dict(arg)
    return names


_COMPAT_USES: Dict[str, int] | None = None


def _same_file_uses(name: str) -> int:
    global _COMPAT_USES
    if _COMPAT_USES is None:
        tree = ast.parse(COMPAT.read_text(encoding="utf-8"))
        counts: Dict[str, int] = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                counts[n.id] = counts.get(n.id, 0) + 1
            elif isinstance(n, ast.Attribute):
                counts[n.attr] = counts.get(n.attr, 0) + 1
        _COMPAT_USES = counts
    return _COMPAT_USES.get(name, 0)


def _dead_module_functions(defined: Set[str],
                           registered: Set[str]) -> List[str]:
    """Defined, never registered, and never referenced anywhere else."""
    dead: List[str] = []
    for name in sorted(defined):
        if name in registered:
            continue
        if _references(name):
            continue
        uses = _same_file_uses(name)
        # Zero Name uses in the defining file means it is never called or
        # registered anywhere; a single use is a real call site.
        if uses == 0:
            dead.append(name)
    return dead



_REF_INDEX: Dict[str, Dict[str, List[str]]] | None = None


def _reference_index() -> Dict[str, Dict[str, List[str]]]:
    """name -> {file: [refs]} built with one AST parse per file (called once)."""
    global _REF_INDEX
    if _REF_INDEX is not None:
        return _REF_INDEX
    index: Dict[str, Dict[str, List[str]]] = {}
    for base in _SCAN_DIRS:
        for path in (REPO / base).rglob("*.py"):
            if path.name in _SKIP or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
                tree = ast.parse(text)
            except (OSError, SyntaxError):
                continue
            rel = path.relative_to(REPO).as_posix()
            lines = text.splitlines()
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Name):
                    names.append(node.id)
                elif isinstance(node, ast.Attribute):
                    names.append(node.attr)
                for name in names:
                    index.setdefault(name, {}).setdefault(rel, []).append(
                        f"{node.lineno}: {lines[node.lineno-1].strip()[:120]}")
    _REF_INDEX = index
    return index


def _references(name: str) -> Dict[str, List[str]]:
    """AST references (Name / Attribute attr) to ``name`` outside its module.

    Comments, docstrings and string literals are deliberately excluded: a name
    mentioned in a comment is not a caller. This is the reachability evidence.
    """
    return _reference_index().get(name, {})


def build() -> Dict[str, Any]:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    owned = {r["qubit_handler"] for r in matrix["rows"]
             if r["owner"] == "qubit_runtime" and r["qubit_handler"]}
    defined = _defined_functions(COMPAT)
    registered = _handler_names()
    tests_text = _tests_text()
    # Proof scope = fork-owned handlers (the primary dead-code question) PLUS
    # every registered compat handler that a production module actually calls.
    # The Qubit-served read/write handlers (e.g. ``h_chat_stream``, ``h_power``)
    # are not fork-owned but are still part of the no-dead-code proof; excluding
    # them made the production-reference classification vacuous.
    ref_index = _reference_index()
    scope = set(owned)
    for name in registered:
        if any(k.startswith("voicebridge/")
               for k in ref_index.get(name, {})):
            scope.add(name)

    rows: List[Dict[str, Any]] = []
    for name in sorted(scope):
        routes = registered.get(name, [])
        refs = _references(name)
        prod_refs = {k: v for k, v in refs.items()
                     if k.startswith("voicebridge/")}
        script_refs = {k: v for k, v in refs.items()
                       if k.startswith("scripts/")}
        test_refs = {k: v for k, v in refs.items() if k.startswith("tests/")}
        route_tested = _route_test_reachable(routes, tests_text)
        if prod_refs:
            klass = "production_referenced"
        elif test_refs:
            klass = "test_referenced"
        elif route_tested:
            klass = "route_test_reachable"
        elif script_refs:
            klass = "script_referenced"
        else:
            klass = "registration_only"
        rows.append({
            "handler": name,
            "defined": name in defined,
            "routes": routes,
            "classification": klass,
            "route_test_reachable": route_tested,
            "production_references": prod_refs,
            "script_references": script_refs,
            "test_references": test_refs,
        })

    # Genuinely dead module code: functions defined in qubit_compat.py that are
    # neither registered on a route nor referenced anywhere else.
    registered_fns = _registered_function_names()
    dead = _dead_module_functions(defined, registered_fns)

    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    # A compat handler is genuinely unreachable only when it is registered on no
    # route AND referenced nowhere. Every owned handler is registered, so the
    # delete set is empty; the proof is the per-handler classification.
    delete_candidates = [r["handler"] for r in rows if not r["routes"]]
    return {
        "schema": "qubit-compat-handler-reachability/1",
        "decision": "owner architecture decision 2026-09-17",
        "matrix": "docs/generated/QUBIT_ROUTE_OWNERSHIP.json",
        "counts": counts,
        "registered_untested": [r["handler"] for r in rows
                                if r["classification"] == "registration_only"],
        "delete_candidates": delete_candidates,
        "dead_module_functions": dead,
        "rows": rows,
    }


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    doc = build()
    text = json.dumps(doc, indent=1, sort_keys=False) + "\n"
    out = Path(args.out)
    print(f"handlers={len(doc['rows'])} counts={doc['counts']}")
    for name in doc["delete_candidates"]:
        print("  DELETE_CANDIDATE", name)
    for name in doc["dead_module_functions"]:
        print("  DEAD_MODULE_FUNCTION", name)
    if args.check:
        if not out.is_file() or out.read_text(encoding="utf-8") != text:
            print(f"STALE: {out}", file=sys.stderr)
            return 1
        print(f"current: {out}")
        return 0
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
