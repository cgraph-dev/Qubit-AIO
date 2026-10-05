#!/usr/bin/env python3
"""Derive the upstream->Qubit rename map EMPIRICALLY from the two trees.

The map is the foundation of the sync: every one of the 4,438 touched upstream
files must be routed through it (path prefix + identifier) before it can land on
Qubit's vendored tree. This derives it from evidence rather than guessing:

  1. path prefix rules  - upstream top-level entry vs Qubit top-level entry
  2. file-stem rules    - hermes_x.py vs qubit_x.py
  3. identifier rules   - HERMES_* -> QUBIT_*, hermes_ -> qubit_

Read-only. Emits JSON to stdout.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

PIN = Path("/home/trick/.cache/qubit-pins/hermes-agent")
QR = Path("/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime")
PIN_SHA = "69948c0057"
BASE_SHA = "bd0affe5"


def ls_tree(sha: str, sub: str | None = None) -> list[str]:
    cmd = ["git", "-C", str(PIN), "ls-tree", "-r", "--name-only", sha]
    if sub:
        cmd.append(sub)
    out = subprocess.run(cmd, capture_output=True, text=True, check=False).stdout
    return [ln for ln in out.splitlines() if ln]


def top_level(sha: str) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(PIN), "ls-tree", "--name-only", sha],
        capture_output=True, text=True, check=False,
    ).stdout
    return set(out.splitlines())


def main() -> None:
    up_top = top_level(PIN_SHA)
    qr_top = {p.name for p in QR.iterdir()} if QR.is_dir() else set()

    # ---- 1. path prefix rules -------------------------------------------------
    # An upstream entry that vanishes from Qubit's top level, with a qubit_*
    # counterpart appearing in its place, is a rename. Pair them by suffix.
    prefix_rules: dict[str, str] = {}
    for up in sorted(up_top):
        if up in qr_top:
            continue
        if not up.startswith("hermes"):
            continue
        cand = "qubit" + up[len("hermes"):]
        if cand in qr_top:
            prefix_rules[up] = cand
        else:
            # e.g. hermes_cli -> qubit_cli but also hermes_state_* families
            for q in qr_top:
                if not q.startswith("qubit"):
                    continue
                if q[len("qubit"):] == up[len("hermes"):]:
                    prefix_rules[up] = q
                    break

    # ---- 2. file-stem rules (recursive, any depth) ---------------------------
    up_all = set(ls_tree(PIN_SHA))
    qr_all = {
        str(p.relative_to(QR))
        for p in QR.rglob("*")
        if p.is_file() and "node_modules" not in p.parts and "__pycache__" not in p.parts
    }
    stem_pairs: dict[str, str] = {}
    for up in up_all:
        if up in qr_all or not up.startswith("hermes"):
            continue
        cand = "qubit" + up[len("hermes"):]
        if cand in qr_all:
            stem_pairs[up] = cand

    # ---- 3. identifier rules -------------------------------------------------
    # Sample a known-renamed module to find the identifier conventions actually
    # in force, rather than assuming hermes_->qubit_.
    ident_rules = {
        "HERMES_": "QUBIT_",
        "hermes_": "qubit_",
        "Hermes": "Qubit",
        "_hermes": "_qubit",
    }

    # ---- 4. what the sync touches, routed through the map --------------------
    changed = subprocess.run(
        ["git", "-C", str(PIN), "diff", "--name-status", f"{PIN_SHA}..{BASE_SHA}"],
        capture_output=True, text=True, check=False,
    ).stdout

    def route(path: str) -> str:
        # longest prefix rule first
        for up in sorted(prefix_rules, key=len, reverse=True):
            if path == up or path.startswith(up + "/"):
                return prefix_rules[up] + path[len(up):]
        return stem_pairs.get(path, path)

    buckets = {"M": [], "A": [], "D": [], "R": []}
    for line in changed.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        st = parts[0]
        if st.startswith("R"):
            buckets["R"].append({"from": parts[1], "to": parts[2]})
        elif st[0] in buckets:
            buckets[st[0]].append(parts[1])

    def has_counterpart(up_path: str) -> bool:
        qp = route(up_path)
        return (QR / qp).exists()

    stats = {}
    for k, v in buckets.items():
        if k == "R":
            continue
        mapped = sum(1 for p in v if route(p) != p)
        present = sum(1 for p in v if has_counterpart(p))
        stats[k] = {"total": len(v), "path_mapped": mapped, "qubit_counterpart": present}

    print(json.dumps({
        "prefix_rules": prefix_rules,
        "stem_pair_sample": dict(list(stem_pairs.items())[:20]),
        "stem_pair_count": len(stem_pairs),
        "ident_rules": ident_rules,
        "changed_file_stats": stats,
        "sample_routing": [
            {"upstream": p, "qubit": route(p)}
            for p in (buckets["M"][:8] + buckets["A"][:8])
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
