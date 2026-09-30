#!/usr/bin/env python3
"""Packet 8T-F Phase A — generate machine-readable upstream inventories.

Walks a pinned clone and emits per-area file/size records with a
PRESERVE/INTEGRATE/ADAPT/BLOCKED/REJECTED verdict per area. Read-only;
never modifies the clone. Re-run to re-verify (hashes detect drift).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# Area verdicts for hermes-agent (see docs/archive/2026-09-21/MATRIX_8T_F.json for rationale).
HERMES_AREAS = {
    "agent": "ADAPT", "qubit_cli": "ADAPT", "tools": "ADAPT",
    "qubit_state_sessions.py": "BLOCKED", "qubit_state": "BLOCKED",
    "gateway": "BLOCKED", "cron": "BLOCKED", "tui_gateway": "BLOCKED",
    "web": "REJECTED", "ui-tui": "REJECTED", "apps": "REJECTED",
    "providers": "ADAPT", "plugins": "BLOCKED", "skills": "BLOCKED",
    "optional-skills": "BLOCKED", "acp_adapter": "ADAPT",
    "acp_registry": "ADAPT", "batch_runner.py": "BLOCKED",
    "docker": "REJECTED", "nix": "REJECTED",
}
OPENCLAW_AREAS = {
    "packages/gateway-protocol": "INTEGRATE", "packages/gateway-client": "INTEGRATE",
    "src/gateway": "INTEGRATE", "src/secrets": "INTEGRATE",
    "src/llm": "INTEGRATE", "src/audit": "INTEGRATE", "src/cron": "INTEGRATE",
    "src/sessions": "INTEGRATE", "src/memory": "INTEGRATE",
    "extensions/browser": "ADAPT", "src/skills": "ADAPT",
    "src/plugins": "BLOCKED", "apps": "REJECTED", "deploy": "REJECTED",
}
SKIP_DIRS = {"tests", "__pycache__", ".git", "website", "docs", "node_modules",
             ".venv", "landingpage", "evals"}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _area(rel: str, table: dict, default: str = "BLOCKED") -> str:
    for prefix, verdict in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if rel == prefix or rel.startswith(prefix + "/"):
            return verdict
    return default


def inventory(clone: Path, table: dict, commit: str,
              suffixes: tuple = (".py",)) -> dict:
    files = []
    for path in sorted(clone.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(seg in SKIP_DIRS or seg.startswith(".") and seg != ".env.example"
               for seg in path.parts):
            continue
        rel = str(path.relative_to(clone))
        files.append({"path": rel, "bytes": path.stat().st_size,
                      "sha256": _sha(path), "area_verdict": _area(rel, table)})
    return {"commit": commit, "files": files, "count": len(files),
            "bytes": sum(f["bytes"] for f in files)}


def main() -> int:
    if len(sys.argv) != 5:
        print("usage: inventory_upstream.py <clone> <kind> <commit> <out>",
              file=sys.stderr)
        return 2
    clone, kind, commit, out = (Path(sys.argv[1]), sys.argv[2],
                                sys.argv[3], Path(sys.argv[4]))
    table = HERMES_AREAS if kind == "hermes" else OPENCLAW_AREAS
    suffixes = (".py",) if kind == "hermes" else (".ts", ".tsx", ".js", ".mjs")
    out.write_text(json.dumps(inventory(clone, table, commit, suffixes), indent=1))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
