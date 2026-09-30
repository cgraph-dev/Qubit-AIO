#!/usr/bin/env python3
"""Deterministic reconciliation of the upstream and Qubit runtime catalogs.

Input (evidence, captured from the live upstream dashboard handlers):
    docs/QUBIT_HERMES_RUNTIME_CATALOG_2026-09-22.json
        GET /api/tools/toolsets  -> 29 toolsets
        GET /api/skills          -> 160 skills

Qubit side is computed in-process from the rebranded source:
    qubit_cli.tools_config._get_effective_configurable_toolsets()  (catalog)
    qubit_runtime/toolsets.py TOOLSETS                              (registry)
    voicebridge.skill_catalog.catalog_entries({})                   (catalog)
    qubit_runtime/**/SKILL.md                                       (on-disk)

Output: docs/QUBIT_RUNTIME_CATALOG_RECONCILIATION_2026-09-22.json
Same inputs -> byte-identical output (sorted keys, no timestamps).

This is an INVENTORY, not a parity claim. Name equality is not behavior.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

GENERATED = "2026-09-22"
SCHEMA = "qubit.runtime_catalog_reconciliation/1"

# Curated dispositions for identifiers that need explanation beyond "present".
# status: PRESENT | PRESENT_UNREGISTERED | PRESENT_UNINDEXED | MISSING | NOT_A_TOOLSET
TOOLSET_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "kanban": {
        "reason": (
            "Restored to CONFIGURABLE_TOOLSETS + _DEFAULT_OFF_TOOLSETS with the "
            "read-time platform opt-in preserved (qubit_cli/tools_config.py), and "
            "_profile_has_kanban_toolset ported to honour a saved platform list. "
            "Activating the kanban environment also un-hides sdlc-review and the "
            "kanban skills."
        ),
    },
    "connections": {
        "reason": (
            "Absent from Qubit CONFIGURABLE_TOOLSETS and static TOOLSETS. "
            "Local-MCP half (install/enable/authorize) exists under renamed "
            "surfaces (/api/mcp/*, McpPage.tsx, qubit_cli/mcp_*.py, desktop "
            "setup_mcp); managed-connector half (status/connect/reconnect) "
            "requires the external tool gateway (OPERATOR_ONLY)."
        ),
    },
}

SKILL_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "sdlc-review": {
        "qubit_names": ["qubit_runtime/skills/devops/sdlc-review/SKILL.md"],
        "reason": "Qubit-rebranded skill; visible once the kanban environment is active.",
    },
    "kanban-orchestrator": {
        "qubit_names": ["qubit_runtime/skills/devops/kanban-orchestrator/SKILL.md"],
        "reason": "Ported into the Qubit-owned tree with metadata.qubit; wire identifiers ($HERMES_KANBAN_*) preserved.",
    },
    "kanban-worker": {
        "qubit_names": ["qubit_runtime/skills/devops/kanban-worker/SKILL.md"],
        "reason": "Ported into the Qubit-owned tree with metadata.qubit; wire identifiers ($HERMES_KANBAN_*) preserved.",
    },
}


def _ts_keys(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "TOOLSETS" for t in node.targets
        ) and isinstance(node.value, ast.Dict):
            return sorted(k.value for k in node.value.keys if isinstance(k, ast.Constant))
    return []


def _qubit_toolsets(repo: Path) -> Dict[str, Any]:
    sys.path.insert(0, str(repo / "qubit_runtime"))
    from qubit_cli.tools_config import _get_effective_configurable_toolsets  # type: ignore

    catalog = {
        str(name): {"label": str(label or name), "description": str(desc or "")}
        for name, label, desc in _get_effective_configurable_toolsets()
    }
    registry = _ts_keys(repo / "qubit_runtime" / "toolsets.py")
    return {"catalog": catalog, "registry": registry}


def _qubit_skills(repo: Path) -> Dict[str, Any]:
    sys.path.insert(0, str(repo))
    from voicebridge import skill_catalog  # type: ignore

    catalog = {e["name"]: e for e in skill_catalog.catalog_entries({})}
    on_disk = {}
    for root in (repo / "qubit_runtime" / "skills", repo / "qubit_runtime" / "optional-skills"):
        if not root.exists():
            continue
        for skill_md in sorted(root.rglob("SKILL.md")):
            on_disk[skill_md.parent.name] = str(skill_md.relative_to(repo))
    return {"catalog": catalog, "on_disk": on_disk}


def build(repo: Path, hermes_snapshot: Path) -> Dict[str, Any]:
    snap = json.loads(hermes_snapshot.read_text(encoding="utf-8"))
    q_ts = _qubit_toolsets(repo)
    q_sk = _qubit_skills(repo)

    toolsets: List[Dict[str, Any]] = []
    for row in snap["toolsets"]:
        name = row["name"]
        in_catalog = name in q_ts["catalog"]
        in_registry = name in q_ts["registry"]
        override = TOOLSET_OVERRIDES.get(name)
        if in_catalog:
            status, qubit_names, reason = "PRESENT", [name], "shown in Qubit configurable catalog"
        elif in_registry:
            status, qubit_names, reason = "PRESENT_UNREGISTERED", [name], "in registry, not in catalog"
        else:
            status, qubit_names, reason = "MISSING", [], "absent from Qubit catalog and registry"
        if override:
            reason = override.get("reason", reason)
            qubit_names = override.get("qubit_names") or qubit_names
        toolsets.append({
            "hermes_id": name,
            "qubit_id": name if (in_catalog or in_registry) else None,
            "display_name": row.get("label", ""),
            "description": row.get("description", ""),
            "member_tools": row.get("tools", []),
            "hermes_enabled": bool(row.get("enabled")),
            "hermes_available": bool(row.get("available")),
            "hermes_configured": bool(row.get("configured")),
            "qubit_catalog_present": in_catalog,
            "qubit_registry_present": in_registry,
            "qubit_names": qubit_names,
            "status": status,
            "reason": reason,
        })

    skills: List[Dict[str, Any]] = []
    for row in snap["skills"]:
        name = row["name"]
        in_catalog = name in q_sk["catalog"]
        on_disk = q_sk["on_disk"].get(name)
        override = SKILL_OVERRIDES.get(name)
        if in_catalog:
            status, qubit_names, reason = "PRESENT", [name], "shown in Qubit skills catalog"
        elif on_disk:
            status, qubit_names, reason = "PRESENT_UNINDEXED", [on_disk], "on disk but not in Qubit catalog"
        else:
            status, qubit_names, reason = "MISSING", [], "absent from Qubit tree and catalog"
        if override:
            reason = override.get("reason", reason)
            qubit_names = override.get("qubit_names") or qubit_names
        skills.append({
            "hermes_id": name,
            "display_name": name,
            "description": row.get("description", ""),
            "category": row.get("category", ""),
            "hermes_enabled": bool(row.get("enabled")),
            "hermes_provenance": row.get("provenance", ""),
            "qubit_catalog_present": in_catalog,
            "qubit_on_disk": on_disk,
            "qubit_names": qubit_names,
            "status": status,
            "reason": reason,
            # not machine-derivable here; must not be fabricated
            "dashboard_exposure": "UNVERIFIED",
            "cli_exposure": "UNVERIFIED",
            "chat_exposure": "UNVERIFIED",
            "qubit_chat_exposure": "UNVERIFIED",
            "voice_exposure": "UNVERIFIED",
        })

    def tally(items: List[Dict[str, Any]]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for it in items:
            out[it["status"]] = out.get(it["status"], 0) + 1
        return dict(sorted(out.items()))

    return {
        "schema": SCHEMA,
        "generated": GENERATED,
        "note": "INVENTORY ONLY. Name match is not behavioral parity; exposure fields marked UNVERIFIED are not proven.",
        "hermes_source": snap.get("source", ""),
        "counts": {
            "hermes_toolsets": len(toolsets),
            "qubit_toolsets_catalog": len(q_ts["catalog"]),
            "qubit_toolsets_registry": len(q_ts["registry"]),
            "hermes_skills": len(skills),
            "qubit_skills_catalog": len(q_sk["catalog"]),
            "qubit_skills_on_disk": len(q_sk["on_disk"]),
        },
        "toolset_status": tally(toolsets),
        "skill_status": tally(skills),
        "toolsets": toolsets,
        "skills": skills,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--hermes-snapshot", type=Path,
                    default=Path("docs/QUBIT_HERMES_RUNTIME_CATALOG_2026-09-22.json"))
    ap.add_argument("--out", type=Path,
                    default=Path("docs/QUBIT_RUNTIME_CATALOG_RECONCILIATION_2026-09-22.json"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    report = build(args.repo, args.hermes_snapshot)
    payload = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    if args.check:
        if not args.out.exists() or args.out.read_text(encoding="utf-8") != payload:
            print(f"FAIL: {args.out} is out of date", file=sys.stderr)
            return 1
        print(f"OK: {args.out} matches")
        return 0
    args.out.write_text(payload, encoding="utf-8")
    print("wrote", args.out)
    print("toolsets:", report["counts"]["hermes_toolsets"], "->", report["toolset_status"])
    print("skills:", report["counts"]["hermes_skills"], "->", report["skill_status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
