"""Generate the committed Qubit route-ownership matrix.

Owner architecture decision 2026-09-17: the vendored upstream runtime is the
canonical implementation for upstream-compatible routes; Qubit owns the front
door, policy boundaries, and Qubit/upstream additions. This script statically
classifies every route the vendored fork declares and every route the Qubit
compat table registers, and fails (exit 1) on a contradiction, so ownership can
never silently drift.

Usage:
    python scripts/route_ownership_matrix.py --out docs/generated/QUBIT_ROUTE_OWNERSHIP.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[1]
for extra in (str(REPO), str(REPO / "scripts")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

from voicebridge import qubit_asgi as HA  # noqa: E402
from voicebridge import qubit_compat as HC  # noqa: E402
from voicebridge import route_ownership as RO  # noqa: E402
from voicebridge import webui as W  # noqa: E402
import source_inventory as SI  # noqa: E402

HERMES_TREE = REPO / "qubit_runtime"


def _template_regex(template: str) -> re.Pattern:
    """Compile a fork route template (``{p}`` / ``{p:path}``) to a full regex."""
    out = []
    for seg in template.strip("/").split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            inner = seg[1:-1]
            out.append(".+" if inner.endswith(":path") else "[^/]+")
        else:
            out.append(re.escape(seg))
    return re.compile("/" + "/".join(out) + "/?")


def _is_catch_all(template: str) -> bool:
    """True for a pure SPA/static wildcard route (e.g. ``/{full_path:path}``).

    Such a route matches every path; it declares no specific API ownership and
    must never make ``fork_declares_fn`` report that the fork owns an unrelated
    Qubit route. Excluding it is what makes the ownership matrix sound.
    """
    stripped = template.strip("/")
    if not stripped:
        return False
    segs = [s for s in stripped.split("/") if s]
    if len(segs) != 1:
        return False
    seg = segs[0]
    return seg.startswith("{") and seg.endswith("}") and ":path" in seg


def _classic_webui_routes() -> List[Tuple[str, str, str]]:
    """Extract the classic ``webui.do_GET``/``do_POST`` route matchers.

    The product SPA addresses the backend through ``/agent/api/*``; classic
    ``/api/*`` is served by the big ``elif`` chain in ``webui.py``. That chain
    is a second served authority for any path the vendored fork also declares,
    so the matrix must model it. Returns ``(kind, method, value)`` where kind is
    ``exact`` / ``prefix`` / ``regex``.
    """
    import ast
    src = (REPO / "voicebridge" / "webui.py").read_text(encoding="utf-8")
    out: List[Tuple[str, str, str]] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.FunctionDef) \
                or node.name not in ("do_GET", "do_POST"):
            continue
        method = "GET" if node.name == "do_GET" else "POST"
        for n in ast.walk(node):
            if isinstance(n, ast.Compare) \
                    and isinstance(n.left, ast.Name) and n.left.id == "path":
                for op, comp in zip(n.ops, n.comparators):
                    if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) \
                            and isinstance(comp.value, str):
                        out.append(("exact", method, comp.value))
            if isinstance(n, ast.Call) \
                    and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "startswith" \
                    and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id == "path" and n.args \
                    and isinstance(n.args[0], ast.Constant) \
                    and isinstance(n.args[0].value, str):
                out.append(("prefix", method, n.args[0].value))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr in ("match", "fullmatch") \
                    and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id == "re" and n.args \
                    and isinstance(n.args[0], ast.Constant) \
                    and isinstance(n.args[0].value, str):
                out.append(("regex", method, n.args[0].value))
    return out


_GROUP = re.compile(r"\(\?P<[^>]+>([^)]*)\)")
_ANON = re.compile(r"\(([^()]*)\)")
_UNSAFE = re.compile(r"[()\[\]?*+|]")


def _sample(template: str):
    """A representative concrete path, or None when the pattern is regex-heavy.

    Handles fork templates (``{p}`` / ``{p:path}``) and compat regex patterns
    (named/anonymous groups, ``[^/]+``, ``.+``). Returns None when any regex
    metacharacter survives, so the matrix never asserts on a fabricated path.
    """
    t = _GROUP.sub("x1", template)
    t = _ANON.sub("x1", t)
    t = t.replace("[^/]+", "x1").replace(".+", "x1").replace(".*", "x1")
    t = t.replace("^", "").replace("$", "")
    segs: List[str] = []
    for seg in t.strip("/").split("/"):
        if not seg:
            continue
        if seg.startswith("{") and seg.endswith("}"):
            inner = seg[1:-1]
            segs.append("a/b" if inner.endswith(":path") else "x1")
        else:
            segs.append(seg)
    path = "/" + "/".join(segs)
    if _UNSAFE.search(path):
        return None
    return path


def build() -> Dict[str, Any]:
    all_fork_routes = SI.extract_hermes_routes(HERMES_TREE)
    # A pure SPA/static catch-all (`/{full_path:path}`) fullmatches every path;
    # it does NOT declare API ownership. Excluding it is the core soundness fix.
    fork_routes = [r for r in all_fork_routes
                   if not _is_catch_all(str(r.get("route", "")))]
    dropped_catch_alls = len(all_fork_routes) - len(fork_routes)
    fork_res = [(_template_regex(r["route"]), r["method"]) for r in fork_routes]

    def fork_declares_fn(method: str, path: str) -> bool:
        clean = path.split("?", 1)[0]
        return any(m == method.upper() and rx.fullmatch(clean)
                   for rx, m in fork_res)

    classic = _classic_webui_routes()
    classic_res = [(re.compile(v), m) for k, m, v in classic if k == "regex"]
    classic_exact = {(m, v) for k, m, v in classic if k == "exact"}
    classic_prefix = [(m, v) for k, m, v in classic if k == "prefix"]

    def classic_declares_fn(method: str, path: str) -> bool:
        """True when the classic webui elif chain serves this method+path."""
        clean = path.split("?", 1)[0]
        m = method.upper()
        if (m, clean) in classic_exact:
            return True
        for pm, pref in classic_prefix:
            if pm == m and clean.startswith(pref):
                return True
        for rx, pm in classic_res:
            if pm == m and rx.fullmatch(clean):
                return True
        return False

    def matches_fn(method: str, path: str):
        return HC.match(method, path)

    def gate_table_fn(method: str, path: str) -> bool:
        """True when the Qubit authority table (``GATED``) keeps this path on
        the Qubit side — a typed gate entry (dict) or a real Qubit handler
        entry (handler-name string)."""
        clean = path.split("?", 1)[0]
        return any(g[0].upper() == method.upper()
                   and re.fullmatch(g[1], clean) for g in HC.GATED)

    # Classic webui branches that serve the NATIVE Qubit contract directly and
    # must be classified Qubit-owned even though the fork also declares the
    # path. ``/api/status`` is the B-01R case: classic returns the native
    # bridge/route/busy/uptime document; the upstream-compatible document is
    # served on the SPA prefix (``/agent/api/status``). Modeling it here keeps
    # the matrix honest about who serves the classic path.
    native_classic_reads = tuple(W.QUBIT_READ_AUTHORITY) + ("/api/status",)
    matrix = RO.build_matrix(
        fork_routes, HC.ROUTES,
        read_authority=native_classic_reads,
        sensitive_prefixes=HA.SENSITIVE_READ_PREFIXES,
        owned_prefixes=HA.QUBIT_OWNED_PREFIXES,
        gated_patterns=[g[1] for g in HC.GATED],
        fork_declares_fn=fork_declares_fn,
        matches_fn=matches_fn,
        sample_path_fn=_sample,
        gate_table_fn=gate_table_fn,
        classic_declares_fn=classic_declares_fn)

    def qubit_authoritative_fn(method: str, path: str) -> bool:
        if method.upper() not in RO.READING:
            return True
        clean = path.split("?", 1)[0]
        if RO._under(clean, native_classic_reads) or HA.gated_path(clean):
            return True
        if RO._under(clean, HA.QUBIT_OWNED_PREFIXES):
            return True
        # A fork-declared read is fork-authoritative even when the classic
        # webui chain also names it (the classic branch delegates to the fork
        # for these — owner decision 2026-09-17). Only routes the fork does
        # NOT declare stay Qubit-authoritative.
        return not fork_declares_fn(method, _sample(clean))

    bad = RO.violations(matrix, qubit_authoritative_fn=qubit_authoritative_fn)
    counts: Dict[str, int] = {}
    for row in matrix:
        counts[row["owner"]] = counts.get(row["owner"], 0) + 1
    # Qubit compat handlers that are registered but non-authoritative because
    # the vendored runtime owns the route. They are migration-only dead code and
    # must never be treated as the served implementation.
    non_authoritative = sorted(
        f"{r['method']} {r['path']} -> {r['qubit_handler']}"
        for r in matrix
        if r["owner"] == RO.QUBIT_RUNTIME and r["qubit_handler"])
    return {
        "schema": "qubit-route-ownership/1",
        "decision": "owner architecture decision 2026-09-17: vendored Hermes "
                    "runtime is canonical for Hermes-compatible routes; Qubit "
                    "owns the front door, policy boundaries, and additions",
        "fork_routes": len(fork_routes),
        "catch_all_routes_excluded": dropped_catch_alls,
        "classic_dispatch_routes": len(classic),
        "compat_routes": len(HC.ROUTES),
        "counts": counts,
        "non_authoritative_handler_count": len(non_authoritative),
        "non_authoritative_handlers": non_authoritative,
        "violations": bad,
        "rows": matrix,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="docs/generated/QUBIT_ROUTE_OWNERSHIP.json")
    ap.add_argument("--check", action="store_true",
                    help="fail unless the on-disk matrix is current")
    args = ap.parse_args(argv)
    doc = build()
    out = (REPO / args.out)
    text = json.dumps(doc, indent=1, sort_keys=False) + "\n"
    print(f"rows={len(doc['rows'])} counts={doc['counts']} "
          f"violations={len(doc['violations'])} "
          f"non_authoritative_handlers={doc['non_authoritative_handler_count']}")
    for v in doc["violations"][:20]:
        print("  VIOLATION", v["method"], v["path"], "-", v["issue"])
    if args.check:
        if not out.is_file() or out.read_text(encoding="utf-8") != text:
            print(f"STALE: {out} is not current", file=sys.stderr)
            return 1
        print(f"current: {out}")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    try:
        shown = out.relative_to(REPO)
    except ValueError:
        shown = out
    print(f"wrote {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
