"""Packet 8T-F — unified capability catalog (CLI/dashboard truth source).

Every Qubit command and catalog capability resolves to exactly one typed
state through Qubit contracts: IMPLEMENTED (routes to a real Qubit op),
BLOCKED (typed refusal with reason, owner, and rollback behavior), or
REJECTED (refused by policy with reason). Nothing is silently omitted:
unknown names fail closed as UNKNOWN_CAPABILITY. No output here grants
authority — this surface is read-only and descriptive.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .resources import resource_path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> Dict[str, Any]:
    candidates = (resource_path(f"docs/{name}"),
                  REPO_ROOT / "docs" / "archive" / "2026-09-21" / name)
    for path in candidates:
        if path.is_file():
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    raise FileNotFoundError(f"docs/{name}")


_IMPLEMENTED_VERDICTS = {"IMPLEMENTED", "REUSE", "ADAPT"}


def _state_of(row: Dict[str, Any]) -> str:
    # Honesty rule (Qubit audit §4.1): a row's own explicit ``status``
    # field is authoritative. ``verdict`` is only a fallback for rows that
    # predate that field. In particular ``MISSING`` no longer silently means
    # IMPLEMENTED: it resolves to NOT_UPSTREAM (a Qubit-owned capability with
    # no upstream ancestry), so a future genuine gap row entered as MISSING
    # cannot be reported as a completed inherited capability.
    status = str(row.get("status", "")).strip().upper()
    if status in ("IMPLEMENTED", "BLOCKED", "REJECTED"):
        return status
    verdict = str(row.get("verdict", "")).strip().upper()
    if verdict in _IMPLEMENTED_VERDICTS:
        return "IMPLEMENTED"
    if verdict == "MISSING":
        return "NOT_UPSTREAM"
    return verdict if verdict in ("BLOCKED", "REJECTED") else "UNKNOWN"


def _rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    parity = _load("PARITY_HERMES.json")
    for row in parity.get("rows", []):
        for token in str(row.get("cmd", "")).split("/"):
            token = token.strip()
            if token:
                rows.append({"id": token, "kind": "hermes-command", **row})
    matrix = _load("MATRIX_8T_F.json")
    for row in matrix.get("rows", []):
        rows.append({"id": str(row.get("id", "")), "kind": "capability",
                     **{k: v for k, v in row.items() if k != "id"}})
    return rows


def check(name: str) -> Dict[str, Any]:
    """Resolve one capability name to its typed state (fail closed).

    Every id in the source matrices is unique, so the first hit is the
    only hit; unknown or empty names fail closed as UNKNOWN_CAPABILITY.
    No output here grants authority — this surface is read-only."""
    key = (name or "").strip().lower().lstrip("-")
    if not key:
        return {"state": "UNKNOWN_CAPABILITY", "code": "UNKNOWN_CAPABILITY",
                "explanation": "Empty capability name.",
                "owner": "qubit-policy", "rollback": "nothing to roll back"}
    # Renamed Qubit commands resolve to their catalog row (the row keeps
    # the upstream command token as its id for differential coverage).
    key = {"worker": "claw"}.get(key, key)
    hits = [r for r in _rows()
            if r["id"].strip().lower().lstrip("-") == key]
    if not hits:
        try:
            from .redaction import redact_text as _redact
            shown = _redact(name)[:60]
        except Exception:
            shown = "?"
        return {"state": "UNKNOWN_CAPABILITY", "code": "UNKNOWN_CAPABILITY",
                "explanation": f"Qubit knows no capability {shown!r}.",
                "owner": "qubit-policy", "rollback": "nothing to roll back"}
    row = hits[0]
    state = _state_of(row)
    base = {"id": row["id"], "kind": row.get("kind", ""),
            "owner": str(row.get("owner", "qubit-policy")),
            "rollback": str(row.get("rollback", "nothing to roll back"))}
    if state == "IMPLEMENTED":
        return {**base, "state": "IMPLEMENTED", "code": "OK",
                "explanation": str(row.get("qubit") or row.get("proof") or
                                   "Available through Qubit.")[:300],
                "proof": str(row.get("proof", ""))[:300]}
    if state == "NOT_UPSTREAM":
        # Qubit-owned capability with no upstream ancestry. It is still a
        # real, proved Qubit path; the distinct state prevents conflating
        # "not inherited from upstream" with "upstream gap closed".
        return {**base, "state": "NOT_UPSTREAM", "code": "NOT_UPSTREAM",
                "explanation": str(row.get("user_explanation") or
                                   row.get("proof") or
                                   "Qubit-owned capability.")[:400],
                "proof": str(row.get("proof", ""))[:300]}
    code = str(row.get("code", "") or
               ("BLOCKED_" + str(row.get("id", "unknown")).upper()
                .replace("-", "_").replace("/", "_")[:48]
                if state == "BLOCKED" else "REJECTED_POLICY"))
    return {**base, "state": state, "code": code,
            "explanation": str(row.get("user_explanation") or row.get("reason")
                               or "Not available.")[:400]}


def summary() -> Dict[str, Any]:
    """Counts by state across the whole catalog."""
    counts: Dict[str, int] = {}
    for row in _rows():
        state = _state_of(row)
        counts[state] = counts.get(state, 0) + 1
    return {"states": counts, "total": sum(counts.values())}


def list_names(state: Optional[str] = None) -> List[str]:
    """Capability ids, optionally filtered to one state."""
    out = []
    for row in _rows():
        row_state = _state_of(row)
        if state is None or row_state == state.upper():
            out.append(row["id"])
    return sorted(set(out))
