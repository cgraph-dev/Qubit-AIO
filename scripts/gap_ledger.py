#!/usr/bin/env python3
"""Qubit gap ledger: validate, merge, and report (stdlib only).

Reads the per-surface ledger sections

    docs/QUBIT_GAP_LEDGER.hermes.json
    docs/QUBIT_GAP_LEDGER.openclaw.json
    docs/QUBIT_GAP_LEDGER.qubit.json

validates their schema, merges every row, computes counts by
source/status/area, writes the human report ``docs/QUBIT_GAP_LEDGER.md``,
and prints a summary. Missing sections are treated as empty with a note;
the script still runs. It NEVER writes back to the input JSON files.

Exits non-zero (1) on a schema violation (bad JSON, missing schema keys,
missing row keys, unknown status, unknown needs).

Usage:
    python scripts/gap_ledger.py
    python scripts/gap_ledger.py --docs-dir docs --json
    python scripts/gap_ledger.py --require-all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SECTIONS = ("hermes", "openclaw", "qubit")

SECTION_KEYS = ("schema", "source", "rows")

ROW_KEYS = (
    "id",
    "area",
    "capability",
    "upstream_ref",
    "qubit_backend",
    "cli",
    "dashboard",
    "voice",
    "status",
    "proving_test",
    "note",
    "needs",
)

STATUS_VALUES = (
    "IMPLEMENTED",
    "PARTIAL",
    "CLI_ONLY",
    "DASHBOARD_ONLY",
    "VOICE_ONLY",
    "GATED",
    "MISSING",
    "UNVERIFIED",
)

NEEDS_VALUES = ("none", "creds", "hardware", "owner")

COMPLETION_GATE = "NOT COMPLETE while any row has status != IMPLEMENTED"


class SchemaError(Exception):
    """Raised when a ledger section violates the fixed schema."""


def _as_str(value) -> str:
    return value if isinstance(value, str) else ""


def validate_section(name: str, data) -> dict:
    """Validate one parsed section; return a normalized copy."""
    if not isinstance(data, dict):
        raise SchemaError(f"{name}: section must be a JSON object")
    for key in SECTION_KEYS:
        if key not in data:
            raise SchemaError(f"{name}: missing section key {key!r}")
    if not isinstance(data["schema"], str) or not data["schema"]:
        raise SchemaError(f"{name}: 'schema' must be a non-empty string")
    if not isinstance(data["source"], str) or not data["source"]:
        raise SchemaError(f"{name}: 'source' must be a non-empty string")
    rows = data["rows"]
    if not isinstance(rows, list):
        raise SchemaError(f"{name}: 'rows' must be a list")

    normalized_rows = []
    for index, row in enumerate(rows):
        where = f"{name}: rows[{index}]"
        if not isinstance(row, dict):
            raise SchemaError(f"{where}: row must be a JSON object")
        missing = [k for k in ROW_KEYS if k not in row]
        if missing:
            raise SchemaError(f"{where}: missing row keys {sorted(missing)}")
        for key in ROW_KEYS:
            if not isinstance(row[key], str):
                raise SchemaError(f"{where}: row key {key!r} must be a string")
        status = row["status"]
        if status not in STATUS_VALUES:
            raise SchemaError(
                f"{where}: unknown status {status!r}; "
                f"allowed {list(STATUS_VALUES)}"
            )
        needs = row["needs"]
        if needs not in NEEDS_VALUES:
            raise SchemaError(
                f"{where}: unknown needs {needs!r}; "
                f"allowed {list(NEEDS_VALUES)}"
            )
        normalized = {key: row[key] for key in ROW_KEYS}
        normalized["_source"] = name
        normalized["_section_source"] = data["source"]
        normalized_rows.append(normalized)

    meta = {
        key: value
        for key, value in data.items()
        if key not in ("schema", "source", "rows")
    }
    return {
        "file_section": name,
        "schema": data["schema"],
        "source": data["source"],
        "generated_from": data.get("generated_from", {}),
        "provenance": data.get("provenance", {}),
        "meta": meta,
        "input_counts": {
            "by_status": data.get("counts_by_status", {}),
            "by_area": data.get("counts_by_area", {}),
            "by_source": data.get("counts_by_source", {}),
            "tri_surface": data.get("tri_surface", {}),
        },
        "rows": normalized_rows,
    }


def load_sections(docs_dir: Path) -> tuple[list, list, list]:
    """Return (sections, missing, warnings)."""
    sections, missing, warnings = [], [], []
    for name in SECTIONS:
        path = docs_dir / f"QUBIT_GAP_LEDGER.{name}.json"
        if not path.exists():
            missing.append(name)
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SchemaError(f"{name}: invalid JSON in {path.name}: {exc}")
        section = validate_section(name, raw)
        section["path"] = path
        extra_row_keys = set()
        for row in raw["rows"]:
            extra_row_keys |= set(row) - set(ROW_KEYS)
        if extra_row_keys:
            warnings.append(
                f"{name}: extra row keys ignored: {sorted(extra_row_keys)}"
            )
        sections.append(section)
    return sections, missing, warnings


def counts_by(rows, key):
    counts = {}
    for row in rows:
        value = row.get(key, "")
        counts[value] = counts.get(value, 0) + 1
    return {k: counts[k] for k in sorted(counts)}


def inventory_reports(sections) -> list:
    """Per-section ``inventory`` provenance blocks (machine readable)."""
    reports = []
    for section in sections:
        block = (section.get("meta") or {}).get("inventory")
        if isinstance(block, dict):
            reports.append({"section": section["file_section"], **block})
    return reports


def inventory_omissions(sections) -> dict:
    """Machine-readable summary of unexplained source-inventory omissions."""
    reports = inventory_reports(sections)
    omitted = []
    for report in reports:
        for item_id in report.get("omitted_ids") or []:
            omitted.append({"section": report.get("section"), "id": item_id})
    errors = [str(report.get("error")) for report in reports
              if report.get("error")]
    return {
        "present": any(bool(report.get("present")) for report in reports),
        "reports": reports,
        "unexplained_omissions": sum(
            int(report.get("unexplained_omissions") or 0)
            for report in reports),
        "omitted": omitted,
        "errors": errors,
    }


def inventory_complete(sections) -> tuple:
    """Return ``(ok, reason)`` for ``--require-inventory-complete``."""
    data = inventory_omissions(sections)
    if not data["reports"]:
        return False, "no source-inventory report in any ledger section"
    if not data["present"]:
        return False, "source inventory absent"
    for report in data["reports"]:
        if report.get("error"):
            return False, (f"{report.get('section')}: {report['error']}")
    if data["unexplained_omissions"]:
        return False, (f"{data['unexplained_omissions']} unexplained source "
                       "inventory omission(s)")
    return True, ""


def build_summary(sections, missing) -> dict:
    merged = []
    for section in sections:
        merged.extend(section["rows"])
    by_status = counts_by(merged, "status")
    by_source = counts_by(merged, "_source")
    by_area = counts_by(merged, "area")
    total = len(merged)
    open_rows = [r for r in merged if r["status"] != "IMPLEMENTED"]
    return {
        "sections_present": [s["file_section"] for s in sections],
        "sections_missing": list(missing),
        "total_rows": total,
        "by_status": by_status,
        "by_source": by_source,
        "by_area": by_area,
        "open_rows": len(open_rows),
        "complete": not open_rows,
        "inventory": inventory_omissions(sections),
    }


def render_markdown(sections, missing, summary) -> str:
    merged = []
    for section in sections:
        merged.extend(section["rows"])

    lines = []
    lines.append("# Qubit gap ledger")
    lines.append("")
    lines.append(
        "Machine-generated from `docs/QUBIT_GAP_LEDGER.*.json`. "
        "This merged report is the authoritative view; the input "
        "JSON sections are never modified."
    )
    lines.append("")
    lines.append(f"> **COMPLETION GATE:** {COMPLETION_GATE}.")
    lines.append("")

    # ---- provenance pins
    lines.append("## Provenance pins")
    lines.append("")
    for section in sections:
        lines.append(
            f"- `{section['path']}` — schema `{section['schema']}`, "
            f"source `{section['source']}`, {len(section['rows'])} rows"
        )
        generated = section.get("generated_from") or {}
        if generated:
            pins = ", ".join(f"{k}={v}" for k, v in sorted(generated.items()))
            lines.append(f"  - generated_from: {pins}")
        for key, value in sorted((section.get("provenance") or {}).items()):
            lines.append(f"  - {key}: {value}")
        for key, value in sorted((section.get("meta") or {}).items()):
            if key in ("generated_from", "provenance", "counts_by_status",
                       "counts_by_area", "counts_by_source", "tri_surface"):
                continue
            rendered = json.dumps(value, sort_keys=True) if isinstance(
                value, (dict, list)) else str(value)
            lines.append(f"  - {key}: {rendered[:400].rstrip()}")
    if missing:
        lines.append("")
        lines.append(
            "Missing sections (treated as empty; no rows contributed): "
            + ", ".join(f"`{name}`" for name in missing)
        )
    lines.append("")

    # ---- counts
    lines.append("## Counts")
    lines.append("")
    lines.append(f"- Total rows: **{summary['total_rows']}**")
    lines.append(f"- Open rows (status != IMPLEMENTED): **{summary['open_rows']}**")
    lines.append("")
    lines.append("### By status")
    lines.append("")
    for key, value in summary["by_status"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("### By source")
    lines.append("")
    for key, value in summary["by_source"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("### By area")
    lines.append("")
    for key, value in summary["by_area"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")

    # ---- direct source-inventory reconciliation
    inventory = summary.get("inventory") or {}
    if inventory.get("reports"):
        lines.append("## Source-inventory reconciliation")
        lines.append("")
        lines.append(f"- Inventory present: **{inventory.get('present')}**")
        lines.append(
            f"- Unexplained omissions: "
            f"**{inventory.get('unexplained_omissions', 0)}**")
        if inventory.get("errors"):
            lines.append(
                f"- Inventory errors: {inventory['errors']}")
        for report in inventory["reports"]:
            lines.append(
                f"- `{report.get('section')}`: present="
                f"{report.get('present')}, "
                f"items={report.get('items_total', 0)}, "
                f"matched={report.get('matched', 0)}, "
                f"unmatched={report.get('unmatched', 0)}, "
                f"unexplained={report.get('unexplained_omissions', 0)}")
        lines.append("")

    # ---- open rows grouped by source then area
    lines.append("## Open rows (status != IMPLEMENTED)")
    lines.append("")
    sources = sorted({r["_source"] for r in merged})
    any_open = False
    for source in sources:
        source_rows = [r for r in merged if r["_source"] == source]
        open_rows = [r for r in source_rows if r["status"] != "IMPLEMENTED"]
        if not open_rows:
            continue
        any_open = True
        lines.append(f"### source: {source} ({len(open_rows)} open)")
        lines.append("")
        areas = sorted({r["area"] for r in open_rows})
        for area in areas:
            area_rows = [r for r in open_rows if r["area"] == area]
            lines.append(f"#### {source} / {area} ({len(area_rows)})")
            lines.append("")
            lines.append("| id | status | needs | capability | proving test |")
            lines.append("| --- | --- | --- | --- | --- |")
            for row in sorted(area_rows, key=lambda r: r["id"]):
                capability = row["capability"].replace("|", "\\|")[:100]
                proving = row["proving_test"] or "—"
                lines.append(
                    f"| `{row['id']}` | {row['status']} | {row['needs']} | "
                    f"{capability} | {proving} |"
                )
            lines.append("")
    if not any_open:
        lines.append("No open rows: every row is IMPLEMENTED.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate, merge, and report the Qubit gap ledgers."
    )
    parser.add_argument(
        "--docs-dir", default="docs",
        help="directory holding the ledger JSON sections (default: docs)",
    )
    parser.add_argument(
        "--out", default=None,
        help="output Markdown path (default: <docs-dir>/QUBIT_GAP_LEDGER.md)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="also print the merged summary as JSON",
    )
    parser.add_argument(
        "--require-all", action="store_true",
        help="fail (exit 1) if any of the three sections is missing",
    )
    parser.add_argument(
        "--require-inventory-complete", action="store_true",
        dest="require_inventory_complete",
        help=("fail (exit 1) unless the direct source inventory is present, "
              "readable, and reports zero unexplained omissions"),
    )
    parser.add_argument(
        "--omissions-out", default=None,
        help=("write the machine-readable source-inventory omissions summary "
              "to this path"),
    )
    args = parser.parse_args(argv)

    docs_dir = Path(args.docs_dir)
    out_path = Path(args.out) if args.out else docs_dir / "QUBIT_GAP_LEDGER.md"

    try:
        sections, missing, warnings = load_sections(docs_dir)
    except SchemaError as exc:
        print(f"SCHEMA VIOLATION: {exc}", file=sys.stderr)
        return 1

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if args.require_all and missing:
        print(
            "SCHEMA VIOLATION: missing required sections: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1

    summary = build_summary(sections, missing)
    omissions = summary["inventory"]

    if args.omissions_out:
        omissions_path = Path(args.omissions_out)
        omissions_path.parent.mkdir(parents=True, exist_ok=True)
        omissions_path.write_text(
            json.dumps(omissions, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"gap ledger: wrote omissions summary {omissions_path}")

    if args.require_inventory_complete:
        ok, reason = inventory_complete(sections)
        if not ok:
            print(f"INVENTORY INCOMPLETE: {reason}", file=sys.stderr)
            print(json.dumps(omissions, indent=2, sort_keys=True),
                  file=sys.stderr)
            return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(sections, missing, summary),
                        encoding="utf-8")

    print(f"gap ledger: wrote {out_path}")
    print(f"  sections present: {summary['sections_present'] or 'none'}")
    print(f"  sections missing: {summary['sections_missing'] or 'none'}")
    print(f"  total rows: {summary['total_rows']}")
    print(f"  by status: {summary['by_status']}")
    print(f"  by source: {summary['by_source']}")
    print(f"  open rows: {summary['open_rows']}")
    print(f"  completion gate: {COMPLETION_GATE}")
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
