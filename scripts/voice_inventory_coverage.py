#!/usr/bin/env python3
"""W7 voice-coverage report: validate every parity row's ``voice_intent``.

The parity inventory (``docs/generated/parity_inventory.json``) carries a
``voice_intent`` string for every row, but nothing resolves those strings when
the inventory is generated -- they are unvalidated candidates
(``voice_intent_verified`` is ``false`` for every row). This generator imports
the real voice registry (``voicebridge.voice_commands``) and resolves every
row's intent through the production ``resolve()`` pipeline, classifying it:

* ``real``       -- resolves to an entry whose ``backend`` is a bound
                    ``_BACKENDS`` callable that performs the capability;
* ``guard``      -- resolves to an audited refusal/guidance: the
                    ``voice_unsupported`` backend, a ``*_refusal`` entry whose
                    bound backend returns a typed refusal, a ``*_help`` /
                    ``*_guidance`` entry, or a ``guard_*`` entry. These execute
                    Qubit's safety policy, not the capability;
* ``unresolved`` -- no unique entry (unknown/ambiguous) or an entry bound to a
                    dead backend.

It also reports registry health (total entries, entries with a bound backend,
dead backends) so a new entry that resolves nowhere fails loudly.

Output is deterministic: ``sort_keys=True``, rows sorted by ``(surface, id)``,
no timestamps. Re-running produces byte-identical bytes.

Usage::

    python scripts/voice_inventory_coverage.py
    python scripts/voice_inventory_coverage.py --out docs/generated/voice_coverage.json
    python scripts/voice_inventory_coverage.py --check
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ``resolve()`` recompiles ~1.6k alias regexes per call; Python's default
# compiled-pattern cache (512) thrashes and makes a full sweep ~10x slower.
# Raising it is a script-local performance tweak -- it never changes results.
try:  # pragma: no cover - interpreter detail
    if getattr(re, "_MAXCACHE", 0) < 8192:
        re._MAXCACHE = 8192
except Exception:  # pragma: no cover - never fatal
    pass

DEFAULT_INVENTORY = "docs/generated/parity_inventory.json"
DEFAULT_OUT = "docs/generated/voice_coverage.json"
SCHEMA = "qubit-voice-coverage/1"

# parity-inventory ``surface`` value -> report key. Unknown -> "other".
_SURFACE_KEYS: Tuple[Tuple[str, str], ...] = (
    ("route", "routes"),
    ("cli", "cli"),
    ("control", "controls"),
    ("tool", "tools"),
)
_OTHER_KEY = "other"

_RESOLUTIONS = ("real", "guard", "unresolved")

# A resolved entry names a typed refusal/guidance -- not a working capability --
# when its id or its bound backend carries one of these tokens. ``*_refusal``
# entries (e.g. ``cli_import_refusal``) and ``*_help`` / ``*_guidance`` entries
# bind genuinely callable backends that only return a refusal/guidance payload,
# so the backend name alone is not enough.
_GUARD_TOKENS: Tuple[str, ...] = ("refusal", "unsupported", "help", "guidance")
_GUARD_ID_PREFIXES: Tuple[str, ...] = ("guard_", "cli_guard_")


def _is_guard(entry_id: Optional[str], backend: Optional[str]) -> bool:
    """True when the resolved entry is an audited refusal/guidance, not a
    working voice path."""
    if backend == "voice_unsupported":
        return True
    if any(token in (backend or "").lower() for token in _GUARD_TOKENS):
        return True
    low_id = (entry_id or "").lower()
    if low_id.startswith(_GUARD_ID_PREFIXES):
        return True
    return any(token in low_id for token in _GUARD_TOKENS)


def _surface_key(surface: str) -> str:
    for raw, key in _SURFACE_KEYS:
        if surface == raw:
            return key
    return _OTHER_KEY


def load_inventory(path: Path) -> Dict[str, Any]:
    """Read the parity inventory (read-only; never written by this script)."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _classify(VC: Any, intent: str) -> Tuple[str, Optional[str], str]:
    """Classify one intent -> ``(resolution, entry_id, resolve_kind)``.

    ``resolve()`` is pure (no side effects), so identical intents are memoised
    within a render for speed; the classification itself is unchanged.
    """
    res = VC.resolve(intent)
    entry = res.entry
    if entry is None:
        return "unresolved", None, res.kind
    backend = entry.backend
    if _is_guard(entry.id, backend):
        return "guard", entry.id, res.kind
    bound = bool(backend) and backend in VC._BACKENDS \
        and callable(VC._BACKENDS[backend])
    if bound:
        return "real", entry.id, res.kind
    # An entry bound to a dead backend is not a working voice path.
    return "unresolved", entry.id, res.kind


def _empty_bucket() -> Dict[str, Any]:
    bucket: Dict[str, Any] = {"total": 0}
    for name in _RESOLUTIONS:
        bucket[name] = 0
    bucket["real_pct"] = 0.0
    return bucket


def _pct(part: int, total: int) -> float:
    return round(100.0 * part / total, 2) if total else 0.0


def build_report(inventory: Dict[str, Any]) -> Dict[str, Any]:
    """Build the deterministic coverage report from an inventory mapping."""
    from voicebridge import voice_commands as VC

    rows = list(inventory.get("rows") or [])

    surfaces: Dict[str, Dict[str, Any]] = {
        key: _empty_bucket() for _, key in _SURFACE_KEYS}
    surfaces[_OTHER_KEY] = _empty_bucket()

    memo: Dict[str, Tuple[str, Optional[str], str]] = {}
    per_row: List[Dict[str, Any]] = []
    for row in rows:
        intent = str(row.get("voice_intent") or "")
        if intent not in memo:
            memo[intent] = _classify(VC, intent)
        resolution, entry_id, kind = memo[intent]
        surface = _surface_key(str(row.get("surface") or ""))
        bucket = surfaces[surface]
        bucket["total"] += 1
        bucket[resolution] += 1
        per_row.append({
            "id": row.get("id"),
            "surface": surface,
            "voice_intent": intent,
            "resolution": resolution,
            "entry": entry_id,
            "kind": kind,
        })

    for bucket in surfaces.values():
        bucket["real_pct"] = _pct(bucket["real"], bucket["total"])

    totals = _empty_bucket()
    for bucket in surfaces.values():
        for name in _RESOLUTIONS:
            totals[name] += bucket[name]
        totals["total"] += bucket["total"]
    totals["real_pct"] = _pct(totals["real"], totals["total"])

    entries = list(VC._iter_entries())
    dead_ids = sorted({e.id for e in entries
                       if not e.backend or e.backend not in VC._BACKENDS})
    bound = sum(1 for e in entries
                if e.backend and e.backend in VC._BACKENDS
                and callable(VC._BACKENDS[e.backend]))

    per_row.sort(key=lambda r: (r["surface"], str(r["id"])))

    return {
        "schema": SCHEMA,
        "source": DEFAULT_INVENTORY,
        "source_schema": inventory.get("schema"),
        "classification": {
            "real": ("resolves to an entry with a bound _BACKENDS backend that "
                     "is not a refusal/guidance"),
            "guard": ("resolves to an audited refusal/guidance: voice_unsupported, "
                      "a *_refusal entry, a *_help/*_guidance entry, or a guard_* "
                      "entry"),
            "unresolved": ("no unique entry, or an entry with a dead backend"),
        },
        "summary": {
            "rows": totals["total"],
            "real": totals["real"],
            "guard": totals["guard"],
            "unresolved": totals["unresolved"],
            "real_pct": totals["real_pct"],
            "distinct_intents": len(memo),
        },
        "surfaces": surfaces,
        "registry": {
            "total_entries": len(entries),
            "bound_backends": bound,
            "dead_backends": len(dead_ids),
            "dead_backend_ids": dead_ids,
        },
        "rows": per_row,
    }


def dumps(report: Dict[str, Any]) -> str:
    """Canonical serialisation: sorted keys, no timestamps, trailing newline."""
    return json.dumps(report, indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"


def render(inventory_path: Optional[Path] = None) -> Dict[str, Any]:
    """Render the report in memory (defaults to the repo's parity inventory)."""
    path = (Path(inventory_path) if inventory_path
            else REPO_ROOT / DEFAULT_INVENTORY)
    return build_report(load_inventory(path))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate the W7 voice-coverage report.")
    ap.add_argument("--inventory", default=str(REPO_ROOT / DEFAULT_INVENTORY),
                    help="parity inventory to read (read-only)")
    ap.add_argument("--out", default=None,
                    help=f"output path (default: {DEFAULT_OUT})")
    ap.add_argument("--check", action="store_true",
                    help="render in memory, compare with --out, write nothing")
    args = ap.parse_args(argv)

    report = build_report(load_inventory(Path(args.inventory)))
    payload = dumps(report)
    out = Path(args.out) if args.out else (REPO_ROOT / DEFAULT_OUT)

    if args.check:
        if not out.is_file():
            print(f"voice coverage artifact missing: {out}", file=sys.stderr)
            return 1
        if out.read_text(encoding="utf-8") != payload:
            print(f"voice coverage artifact stale: {out}", file=sys.stderr)
            return 1
        print(f"voice coverage artifact up to date: {out}")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    s = report["summary"]
    print(
        f"voice coverage -> {out} | rows={s['rows']} real={s['real']} "
        f"guard={s['guard']} unresolved={s['unresolved']} "
        f"real_pct={s['real_pct']} "
        f"dead_backends={report['registry']['dead_backends']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
