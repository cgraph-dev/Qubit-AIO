#!/usr/bin/env python3
"""Deterministic reconciliation of the upstream and Qubit toolset registries.

Parses the static ``TOOLSETS`` dict out of each side's ``toolsets.py`` using
``ast`` only (no import, no execution, no config/secrets access), then emits a
sorted JSON report. Same inputs -> byte-identical output.

Usage:
    scripts/toolset_reconciliation.py \
        --hermes <path-to-hermes-agent/toolsets.py> \
        --qubit  qubit_runtime/toolsets.py \
        --out    docs/QUBIT_TOOLSET_RECONCILIATION_2026-09-22.json
    scripts/toolset_reconciliation.py --check ...   # fail if artifact drifted

The mechanical diff is computed; the per-identifier *dispositions* for names
that differ (``connections``) and for non-toolset identifiers present in upstream
platform config (``moa``) are curated editorial annotations with cited evidence.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

GENERATED = "2026-09-22"
SCHEMA = "qubit.toolset_reconciliation/1"


def _str_const(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _list_const(node: ast.AST) -> List[str]:
    out: List[str] = []
    if isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            value = _str_const(elt)
            if value is not None:
                out.append(value)
    return out


def _extract_ts_call(node: ast.AST) -> Dict[str, Any]:
    """``_ts(desc)`` / ``_ts(desc, tools)`` / ``_ts(desc, tools, includes)``."""
    entry: Dict[str, Any] = {"description": "", "tools": [], "includes": []}
    if not isinstance(node, ast.Call):
        return entry
    args = node.args
    if len(args) >= 1:
        desc = _str_const(args[0])
        if desc is not None:
            entry["description"] = desc
    if len(args) >= 2:
        entry["tools"] = _list_const(args[1])
    if len(args) >= 3:
        entry["includes"] = _list_const(args[2])
    return entry


def extract_toolsets(path: Path) -> Dict[str, Dict[str, Any]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    toolsets: Dict[str, Dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "TOOLSETS" not in names or not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            name = _str_const(key)
            if name is None:
                continue
            toolsets[name] = _extract_ts_call(value)
    if not toolsets:
        raise SystemExit(f"no static TOOLSETS dict found in {path}")
    return toolsets


def _names(toolsets: Dict[str, Dict[str, Any]]) -> List[str]:
    return sorted(toolsets)


def _slim(toolsets: Dict[str, Dict[str, Any]], name: str) -> Dict[str, Any]:
    entry = toolsets[name]
    return {
        "description": entry["description"],
        "tools": sorted(entry["tools"]),
        "includes": sorted(entry["includes"]),
        "tool_count": len(entry["tools"]),
    }


# --- Curated dispositions for identifiers that are NOT a 1:1 name match. -----
# Status vocabulary: SAFE_EQUIVALENT | OPERATOR_ONLY | OPEN_PARTIAL | NOT_A_TOOLSET
DISPOSITIONS: Dict[str, Dict[str, Any]] = {
    "connections": {
        "kind": "toolset",
        "upstream_name": "connections",
        "qubit_name": None,
        "status": "OPEN_PARTIAL",
        "upstream_definition": {
            "file": "hermes-agent/toolsets.py",
            "tools": ["manage_connections"],
            "description": "Remote connector discovery, execution, and account authorization",
            "actions_managed": ["status", "connect", "reconnect"],
            "actions_mcp": ["install", "enable", "authorize"],
            "backing_module": "hermes-agent/tools/connectors/",
        },
        "qubit_mapping": {
            "local_mcp_half": {
                "status": "SAFE_EQUIVALENT",
                "qubit_names": [
                    "McpPage.tsx",
                    "voicebridge/qubit_compat.py: h_mcp_servers / mcp_create / mcp_set_enabled / mcp_auth / h_mcp_catalog_live",
                    "qubit_runtime/qubit_cli/mcp_config.py",
                    "qubit_runtime/qubit_cli/mcp_catalog.py",
                    "qubit_runtime/tools/setup_mcp_tool.py (setup_mcp, desktop only)",
                ],
                "note": (
                    "install/enable/authorize are implemented canonically for CLI and "
                    "dashboard (confirm-gated /api/mcp/*), and as the desktop-only "
                    "setup_mcp tool. Not exposed as a model toolset for non-desktop "
                    "sessions, so a migrated platform_toolsets entry named "
                    "'connections' resolves zero tools."
                ),
            },
            "managed_connector_half": {
                "status": "OPERATOR_ONLY",
                "reason": (
                    "status/connect/reconnect require the upstream tool gateway "
                    "(gateway.sock / portal account authorization) that Qubit "
                    "deliberately does not run; no local counterpart exists."
                ),
                "prerequisite": "external Qubit/Hermes tool-gateway service + account auth",
            },
        },
    },
    "moa": {
        "kind": "not_a_toolset",
        "upstream_name": "moa",
        "qubit_name": "moa",
        "status": "NOT_A_TOOLSET",
        "note": (
            "'moa' appears in the live ~/.hermes platform_toolsets.cli list but is "
            "absent from both TOOLSETS dicts; Hermes _get_platform_tools('cli') drops "
            "it. It is the virtual Mixture-of-Agents provider, not a toolset."
        ),
        "upstream_evidence": [
            "hermes-agent/hermes_cli/providers.py: 'moa': HermesOverlay(auth_type='virtual', base_url_override='moa://local')",
            "hermes-agent/hermes_cli/commands.py: CommandDef('moa', ...)",
            "hermes-agent/hermes_cli/tools_config.py: no 'moa' entry in CONFIGURABLE_TOOLSETS",
        ],
        "qubit_mapping": {
            "status": "SAFE_EQUIVALENT",
            "qubit_names": [
                "qubit_runtime/qubit_cli/moa_config.py",
                "qubit_runtime/qubit_cli/providers.py (moa virtual provider)",
            ],
        },
        "config_only": True,
    },
}


def build(hermes_path: Path, qubit_path: Path) -> Dict[str, Any]:
    hermes = extract_toolsets(hermes_path)
    qubit = extract_toolsets(qubit_path)
    h_names, q_names = set(hermes), set(qubit)
    shared = sorted(h_names & q_names)
    return {
        "schema": SCHEMA,
        "generated": GENERATED,
        "sources": {
            "hermes": "hermes-agent/toolsets.py (static TOOLSETS)",
            "qubit": "qubit_runtime/toolsets.py (static TOOLSETS)",
        },
        "counts": {"hermes": len(h_names), "qubit": len(q_names), "shared": len(shared)},
        "hermes_only": sorted(h_names - q_names),
        "qubit_only": sorted(q_names - h_names),
        "shared_names": shared,
        "hermes_toolset_detail": {n: _slim(hermes, n) for n in _names(hermes)},
        "qubit_toolset_detail": {n: _slim(qubit, n) for n in _names(qubit)},
        "dispositions": DISPOSITIONS,
        "notes": [
            "Mechanical diff only; name equality is not behavioral parity.",
            "Dispositions are curated editorial annotations with cited evidence.",
            "No Hermes config secrets are read or emitted by this generator.",
        ],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hermes", required=True, type=Path)
    ap.add_argument("--qubit", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    report = build(args.hermes, args.qubit)
    payload = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    if args.check:
        if not args.out or not args.out.exists():
            print("FAIL: --check requires --out pointing at the committed artifact", file=sys.stderr)
            return 1
        current = args.out.read_text(encoding="utf-8")
        if current != payload:
            print(f"FAIL: {args.out} is out of date; regenerate it", file=sys.stderr)
            return 1
        print(f"OK: {args.out} matches the generated report")
        return 0

    if not args.out:
        sys.stdout.write(payload)
        return 0
    args.out.write_text(payload, encoding="utf-8")
    print(f"wrote {args.out} ({report['counts']['hermes']} hermes / "
          f"{report['counts']['qubit']} qubit / shared {report['counts']['shared']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
