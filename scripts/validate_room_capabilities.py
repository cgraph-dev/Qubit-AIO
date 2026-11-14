#!/usr/bin/env python3
"""Validate that every shipped Office room maps to a real capability manifest.

This is the P3-02 gate. It builds the shipped role/floors registry, composes
one :class:`~voicebridge.business.room_capabilities.RoomCapabilityManifest` per
``(department, room kind)`` plus the Trading Department rooms, validates every
manifest against the role registry and the capability catalog, and then
validates coverage for every room in the shipped floors. A room with no
manifest is a typed failure: a furnished room is not a working room.

Usage:
    python scripts/validate_room_capabilities.py [--json]

Exit codes:
    0  every room covered and every manifest valid
    1  an uncovered/decorative room or an invalid manifest
    2  usage error (argparse) or unexpected internal failure
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.business.errors import BusinessError  # noqa: E402
from voicebridge.business.roles import RoleRegistry  # noqa: E402
from voicebridge.business.room_capabilities import (  # noqa: E402
    build_shipped_registry,
)


def _report(registry: Any, roles: RoleRegistry) -> dict[str, Any]:
    coverage = registry.validate_coverage(roles)
    return {
        "status": "PASS",
        "schema": "qubit.office.room_capabilities/1",
        "manifest_count": len(registry.manifests),
        "manifest_digest": registry.digest,
        "counts_by_department": registry.counts_by_department(),
        "counts_by_kind": registry.counts_by_kind(),
        "coverage": coverage.to_dict(),
        "manifests": [
            {
                "manifest_id": manifest.manifest_id,
                "workflow_id": manifest.workflow_id,
                "workflow_name": manifest.workflow_name,
                "room_kinds": list(manifest.room_kinds),
                "archetypes": list(manifest.archetypes),
                "departments": list(manifest.departments),
                "required_role_count": len(manifest.required_role_ids),
                "required_capability_count": len(
                    manifest.required_capabilities
                ),
                "approval_policy": manifest.approval_policy,
                "required_agent_count": manifest.required_agent_count,
                "digest": manifest.digest,
            }
            for manifest in registry.manifests
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_room_capabilities.py",
        description=(
            "Validate that every shipped Office room maps to a real workflow"
            " capability manifest."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the full report as JSON",
    )
    args = parser.parse_args(argv)

    roles = RoleRegistry.default()
    try:
        registry = build_shipped_registry(roles=roles)
        report = _report(registry, roles)
    except BusinessError as exc:
        failure = {
            "status": "FAIL",
            "schema": "qubit.office.room_capabilities/1",
            "code": exc.code,
            "detail": str(exc),
        }
        if args.json:
            json.dump(failure, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        else:
            print(f"FAIL {exc.code}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    print(
        f"manifests: {report['manifest_count']}"
        f" (digest {report['manifest_digest'][:16]}…)"
    )
    for manifest in report["manifests"]:
        print(
            f"  {manifest['manifest_id']}: {manifest['workflow_id']}"
            f" · {manifest['required_role_count']} roles"
            f" · {manifest['required_capability_count']} capabilities"
            f" · approval={manifest['approval_policy']}"
            f" · agents={manifest['required_agent_count']}"
        )
    print("counts by department:")
    for name, count in report["counts_by_department"].items():
        print(f"  {name}: {count}")
    print("counts by kind:")
    for name, count in report["counts_by_kind"].items():
        print(f"  {name}: {count}")
    coverage = report["coverage"]
    print(
        f"coverage: total={coverage['total_rooms']}"
        f" covered={coverage['covered_rooms']}"
        f" uncovered={coverage['uncovered_rooms']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
