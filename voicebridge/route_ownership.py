"""Explicit Qubit route-ownership matrix (owner architecture decision 2026-09-17).

The vendored Qubit runtime is the canonical implementation for every
Qubit-compatible route. Qubit owns the front door (branding, authentication
boundary, policy, broker, audit, e-stop, VoiceBridge, provider routing) and the
Qubit/Qubit additions. A Qubit wrapper is allowed only when it delegates to
the real Qubit operation or applies a documented policy boundary.

This module is the single classification authority:

* :data:`HERMES_RUNTIME`  -- safe Qubit functionality; the vendored runtime
  serves it. A Qubit compat handler must NOT shadow it.
* :data:`QUBIT_POLICY`    -- sensitive/operator-restricted functionality; Qubit
  serves it with a typed gate or a delegating/confined wrapper.
* :data:`QUBIT_ADDITION`  -- Qubit/Qubit capabilities the fork does not
  declare, plus the Qubit-owned dashboard/ops prefixes and the single-store
  read-authority quarantine. Qubit serves it.

The matrix is built from the *static* models (the vendored fork's declared
routes and the Qubit compat route table) so it needs no live backend, and
:func:`violations` fails when the served behaviour diverges from the declared
owner. ``scripts/route_ownership_matrix.py`` writes the committed JSON;
``tests/test_route_ownership_matrix.py`` guards it.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

QUBIT_RUNTIME = "qubit_runtime"
QUBIT_POLICY = "qubit_policy"
QUBIT_ADDITION = "qubit_addition"

OWNERS = (QUBIT_RUNTIME, QUBIT_POLICY, QUBIT_ADDITION)

#: HTTP verbs that mutate. Qubit never lets the fork serve a bare write: every
#: mutation stays on the Qubit permission/confirmation/audit path (or the
#: operator-allowlisted cutover write, which is empty by default).
MUTATING = ("POST", "PUT", "PATCH", "DELETE")
READING = ("GET", "HEAD")


def _under(path: str, prefixes: Iterable[str]) -> bool:
    clean = path.split("?", 1)[0]
    if len(clean) > 1:
        clean = clean.rstrip("/")
    for prefix in prefixes:
        if clean == prefix or clean.startswith(prefix + "/"):
            return True
    return False


def classify(method: str, path: str, *,
             disposition: str,
             fork_declares: bool,
             gated: bool,
             sensitive: bool,
             owned: bool,
             read_authority: bool,
             gate_table: bool = False,
             classic: bool = False) -> Tuple[str, str]:
    """Return ``(owner, reason)`` for one route.

    ``fork_declares``: the vendored runtime declares this method+path.
    ``gated``: Qubit registers a typed-gate disposition for it.
    ``gate_table``: the path is in the Qubit authority table
    (``qubit_compat.GATED``), which keeps it on the Qubit path whether the
    entry is a typed gate or a real Qubit handler.
    ``sensitive``/``owned``: the path is under a sensitive or Qubit-owned
    prefix. ``read_authority``: the path is in the narrow single-store
    quarantine (``webui.QUBIT_READ_AUTHORITY``).
    """
    method = method.upper()
    if method == "WEBSOCKET":
        return (QUBIT_POLICY,
                "WebSockets are refused with the typed WS_SINGLE_PATH policy; "
                "Qubit runs one SSE event path")
    if disposition == "gated":
        return QUBIT_POLICY, "Qubit typed gate (operator/sensitive boundary)"
    if disposition == "stream":
        return QUBIT_ADDITION, "Qubit-owned SSE stream (canonical chat path)"
    if method in MUTATING:
        return (QUBIT_POLICY,
                "mutations stay on the Qubit broker/confirmation/audit path")
    if sensitive:
        return (QUBIT_POLICY,
                "sensitive/confined prefix; Qubit serves a delegating or "
                "confined wrapper")
    if owned:
        return QUBIT_ADDITION, "Qubit-owned dashboard/ops prefix"
    if classic and not fork_declares:
        return (QUBIT_ADDITION,
                "classic Qubit dashboard dispatch serves this route and the "
                "fork does not declare it (webui.do_GET/do_POST)")
    if read_authority:
        return (QUBIT_ADDITION,
                "single-store read-authority quarantine (avoids split-brain "
                "against the matching Qubit write)")
    if gate_table:
        return (QUBIT_POLICY,
                "Qubit authority table (typed gate or Qubit handler); stays "
                "on the Qubit path by explicit policy")
    if fork_declares:
        return (QUBIT_RUNTIME,
                "safe vendor-compatible route; the vendored runtime is the "
                "canonical implementation")
    return QUBIT_ADDITION, "Qubit addition the foundation does not declare"


def build_matrix(fork_routes: Sequence[Dict[str, Any]],
                 compat_routes: Sequence[Dict[str, Any]],
                 *,
                 read_authority: Sequence[str],
                 sensitive_prefixes: Sequence[str],
                 owned_prefixes: Sequence[str],
                 gated_patterns: Sequence[Any],
                 fork_declares_fn,
                 matches_fn,
                 sample_path_fn,
                 gate_table_fn=None,
                 classic_declares_fn=None) -> List[Dict[str, Any]]:
    """Build one row per concrete (sampled) route.

    Rows are keyed by a concrete sample path so both the declared owner and the
    runtime dispatcher check operate on the same string. The original template
    or compat regex is kept under ``route``. ``fork_declares_fn(method, path)``
    and ``matches_fn(method, path)`` map a concrete path back to the static
    models; ``sample_path_fn`` produces the concrete path.
    """
    rows: List[Dict[str, Any]] = []
    seen: set = set()

    def _row(method: str, concrete: str, route: str, disposition: str,
             fork: bool, source: str, handler: str = "",
             sample_ok: bool = True) -> None:
        key = (method.upper(), concrete)
        if key in seen:
            return
        seen.add(key)
        gated_table = bool(gate_table_fn(method, concrete)) \
            if gate_table_fn is not None else False
        classic = bool(classic_declares_fn(method, concrete)) \
            if classic_declares_fn is not None else False
        owner, reason = classify(
            method, concrete, disposition=disposition, fork_declares=fork,
            gated=disposition == "gated", sensitive=_under(concrete, sensitive_prefixes),
            owned=_under(concrete, owned_prefixes),
            read_authority=_under(concrete, read_authority),
            gate_table=gated_table, classic=classic)
        rows.append({
            "method": method.upper(),
            "path": concrete,
            "route": route,
            "owner": owner,
            "reason": reason,
            "disposition": disposition,
            "fork_declares": bool(fork),
            "classic_served": bool(classic),
            "qubit_handler": handler,
            "source": source,
            "sample_ok": bool(sample_ok),
        })

    for route in fork_routes:
        method = str(route.get("method", ""))
        template = str(route.get("route", "") or route.get("pattern", ""))
        if not method or not template:
            continue
        concrete = sample_path_fn(template)
        if concrete is None:
            continue
        matched, _params = matches_fn(method, concrete)
        disp = str(matched.get("disposition", "implemented")) if matched \
            else "implemented"
        handler = str(matched.get("handler", "")) if matched else ""
        _row(method, concrete, template, disp, True, "fork", handler)

    for route in compat_routes:
        method = str(route.get("method", ""))
        pattern = str(route.get("pattern", ""))
        if not method or not pattern:
            continue
        disp = str(route.get("disposition", "implemented"))
        handler = str(route.get("handler", ""))
        concrete = sample_path_fn(pattern)
        sample_ok = concrete is not None
        if concrete is None:
            concrete = pattern
        fork = bool(fork_declares_fn(method, concrete)) if sample_ok else False
        _row(method, concrete, pattern, disp, fork, "compat", handler,
             sample_ok)

    rows.sort(key=lambda r: (r["owner"], r["method"], r["path"]))
    return rows


def violations(matrix: Sequence[Dict[str, Any]], *,
               qubit_authoritative_fn) -> List[Dict[str, str]]:
    """Served behaviour that contradicts the declared owner.

    ``qubit_authoritative_fn(method, path)`` must return True exactly when the
    production dispatcher keeps the request on the Qubit path (gated, owned
    prefix, read-authority quarantine, or any mutation). Two contradictions:

    * a ``qubit_runtime`` route that Qubit keeps on the compat path = a thin
      Qubit handler silently shadowing a working Qubit route.
    * a ``qubit_policy`` / ``qubit_addition`` route that proxies to the fork =
      the Qubit wrapper/store is dead and the served payload can diverge from
      the CLI/voice authority.
    """
    out: List[Dict[str, str]] = []
    for row in matrix:
        if not row.get("sample_ok", True):
            continue
        method = row["method"]
        path = row["path"]
        authoritative = bool(qubit_authoritative_fn(method, path))
        if row["owner"] == QUBIT_RUNTIME and authoritative:
            out.append({
                "method": method, "path": path,
                "issue": "vendor-owned route is Qubit-authoritative "
                         "(thin handler shadows the working vendor route)"})
        elif row["owner"] in (QUBIT_POLICY, QUBIT_ADDITION) and not authoritative:
            out.append({
                "method": method, "path": path,
                "issue": "Qubit-owned route is not authoritative "
                         "(fork proxy would serve a divergent payload)"})
    return out


__all__ = [
    "QUBIT_RUNTIME", "QUBIT_POLICY", "QUBIT_ADDITION", "OWNERS",
    "MUTATING", "READING", "classify", "build_matrix", "violations",
]
