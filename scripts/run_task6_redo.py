#!/usr/bin/env python3
"""Task 6 REDO (R17) — corrected-base fork-patch re-application.

Method (owner R17): start from OURS = engine(v0.21.5) wholesale; re-apply the
Qubit overlay (out of scope here) and the *genuine* Qubit fork patches.  The
fork patches are the diff ``theirs - base`` where ``base`` is the fork's true
ancestor ``engine(0.21.1)`` — never the rejected ``engine(0.21.4)`` base, which
was a descendant of ``theirs`` and turned upstream lag into false reversions.

Resolution rule (default OURS, per the analyst reports' TAKE_OURS majority):

* only in OURS               -> OURS (0.21.5 upstream)
* only in THEIRS             -> THEIRS (fork-only module; kept, then reviewed)
* only in BASE (deleted both)-> absent
* identical                  -> that content
* differing / conflict       -> ``git merge-file -p ours base theirs``; when the
  merge has real conflict markers the OURS side wins by default and the path is
  recorded in the human-judgment list.  Explicit overrides (``--overrides``)
  take a named side (ours/theirs) instead.

The script never writes the live product tree.  ``--analyze`` reports counts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from merge_v0215.merge import MergeConflict, merge_file  # noqa: E402

CONFLICT_RE = b"<<<<<<< "

BUCKETS = (
    ("qubit_state", lambda rel: Path(rel).name.startswith("qubit_state")),
    ("plugins", lambda rel: rel.startswith("plugins/")),
    ("tui_gateway", lambda rel: rel.startswith("tui_gateway/")),
    ("qubit_cli", lambda rel: rel.startswith("qubit_cli/")),
    ("tools", lambda rel: rel.startswith("tools/")),
    ("providers", lambda rel: rel.startswith("providers/")),
    ("gateway", lambda rel: rel.startswith("gateway/")),
    ("agent", lambda rel: rel.startswith("agent/")),
)


def bucket_of(rel: str) -> str:
    for name, pred in BUCKETS:
        if pred(rel):
            return name
    return "remaining"


def sha(b: bytes | None) -> str | None:
    return hashlib.sha256(b).hexdigest() if b is not None else None


def index(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith("__pycache__/") or rel.endswith(".pyc"):
            continue
        files[rel] = p.read_bytes()
    return files


def load_overrides(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    doc = json.loads(path.read_text())
    return {k: v["side"] if isinstance(v, dict) else v for k, v in doc.get("overrides", doc).items()}


def three_way(base_p: Path, ours_b: bytes, theirs_b: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="t6redo-") as tmp:
        t = Path(tmp)
        (t / "ours").write_bytes(ours_b)
        (t / "theirs").write_bytes(theirs_b)
        (t / "base").write_bytes(base_p.read_bytes())
        res = subprocess.run(
            ["git", "merge-file", "-p", str(t / "ours"), str(t / "base"), str(t / "theirs")],
            capture_output=True,
        )
        return res.stdout


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--ours", required=True)
    ap.add_argument("--theirs", required=True)
    ap.add_argument("--target", required=True, help="product tree to write")
    ap.add_argument("--audit", required=True)
    ap.add_argument("--overrides", type=Path)
    ap.add_argument("--overrides-out", type=Path)
    ap.add_argument("--analyze", action="store_true")
    args = ap.parse_args(argv)

    base_root = Path(args.base)
    ours_root = Path(args.ours)
    theirs_root = Path(args.theirs)
    target = Path(args.target)
    ov = load_overrides(args.overrides)

    print("indexing trees ...", file=sys.stderr)
    base = index(base_root)
    ours = index(ours_root)
    theirs = index(theirs_root)
    print(f"  base={len(base)} ours={len(ours)} theirs={len(theirs)}", file=sys.stderr)

    paths = sorted(set(ours) | set(theirs) | set(base))
    records = []
    totals = {"ours_only": 0, "theirs_only": 0, "identical": 0, "merged": 0,
              "conflict_ours": 0, "conflict_theirs": 0, "override": 0, "both_absent": 0}
    writes: dict[str, bytes] = {}
    deletes: list[str] = []

    for rel in paths:
        ob = ours.get(rel)
        tb = theirs.get(rel)
        bb = base.get(rel)
        side = ov.get(rel)

        if ob is None and tb is None:
            totals["both_absent"] += 1
            continue
        if ob is None and tb is not None:
            if side == "ours":
                deletes.append(rel)
                res_side = "ours"
            else:
                writes[rel] = tb
                res_side = "theirs"
            totals["theirs_only"] += 1
            records.append({"path": rel, "bucket": bucket_of(rel), "class": "THEIRS_ONLY",
                            "resolution": res_side, "ours": None, "base": sha(bb),
                            "theirs": sha(tb), "result": sha(writes.get(rel))})
            continue
        if ob is not None and tb is None:
            if side == "theirs":
                deletes.append(rel)
                writes.pop(rel, None)
                totals["override"] += 1
                records.append({"path": rel, "bucket": bucket_of(rel), "class": "OVERRIDE",
                                "resolution": "drop_ours", "ours": sha(ob), "base": sha(bb),
                                "theirs": None, "result": None})
                continue
            writes[rel] = ob
            totals["ours_only"] += 1
            records.append({"path": rel, "bucket": bucket_of(rel), "class": "OURS_ONLY",
                            "resolution": "ours", "ours": sha(ob), "base": sha(bb),
                            "theirs": None, "result": sha(ob)})
            continue
        if ob == tb:
            writes[rel] = ob
            totals["identical"] += 1
            records.append({"path": rel, "bucket": bucket_of(rel), "class": "IDENTICAL",
                            "resolution": "same", "ours": sha(ob), "base": sha(bb),
                            "theirs": sha(tb), "result": sha(ob)})
            continue

        base_p = base_root / rel
        if not base_p.is_file():
            base_p = None  # add/add with no ancestor -> conflict
        if side == "theirs":
            merged = tb
            klass, resolution = "OVERRIDE", "theirs"
            totals["override"] += 1
        elif side == "ours":
            merged = ob
            klass, resolution = "OVERRIDE", "ours"
            totals["override"] += 1
        else:
            if base_p is None:
                merged = ob
                klass, resolution = "CONFLICT", "ours"
                totals["conflict_ours"] += 1
            else:
                try:
                    merged = merge_file(base_root, ours_root, theirs_root, rel)
                    klass, resolution = "MERGED", "merge"
                    totals["merged"] += 1
                except MergeConflict as exc:
                    merged = ob if CONFLICT_RE not in ob else exc.merged
                    # default: keep OURS (analyst TAKE_OURS majority)
                    merged = ob
                    klass, resolution = "CONFLICT", "ours"
                    totals["conflict_ours"] += 1
        writes[rel] = merged
        records.append({"path": rel, "bucket": bucket_of(rel), "class": klass,
                        "resolution": resolution, "ours": sha(ob), "base": sha(bb),
                        "theirs": sha(tb), "result": sha(merged)})

    # conflicts for human judgment
    judgment = [r for r in records if r["class"] == "CONFLICT"]
    theirs_only = [r for r in records if r["class"] == "THEIRS_ONLY"]
    print(f"totals: {totals}", file=sys.stderr)
    print(f"conflicts(default ours)={len(judgment)} theirs_only={len(theirs_only)}",
          file=sys.stderr)

    audit = {
        "schema": "qubit-v0215-task6redo-audit/1",
        "base": str(base_root), "ours": str(ours_root), "theirs": str(theirs_root),
        "base_files": len(base), "ours_files": len(ours), "theirs_files": len(theirs),
        "totals": totals,
        "conflicts": [r["path"] for r in judgment],
        "theirs_only": [r["path"] for r in theirs_only],
        "records": records,
    }
    audit_path = Path(args.audit)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=1, sort_keys=True) + "\n")

    if args.analyze:
        return 0

    # write the target tree
    if target.exists():
        for p in sorted(target.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
    target.mkdir(parents=True, exist_ok=True)
    for rel, data in writes.items():
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    for rel in deletes:
        dest = target / rel
        if dest.exists():
            dest.unlink()
    print(f"wrote {len(writes)} files, deleted {len(deletes)}", file=sys.stderr)

    if args.overrides_out:
        doc = {
            "schema": "qubit-v0215-task6redo-overrides/1",
            "overrides": {r["path"]: {"side": r["resolution"]} for r in records
                          if r["class"] == "CONFLICT"},
        }
        Path(args.overrides_out).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
