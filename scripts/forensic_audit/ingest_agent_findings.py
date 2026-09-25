#!/usr/bin/env python3
"""Ingest Tier-1 agent deep-audit outputs into the forensic ledger.

Tier 0 (``run_audit.py``) is deterministic and cheap. The Tier-1 deep dive --
the 5-agent provider/tools/skills/plugins/MCP/sessions/rebrand pass -- produces
richer findings on demand; this script normalizes any of its JSON shapes into
the same ledger schema so Tier-1 and Tier-0 findings merge and diff identically.

Inputs accepted:
* ``CAPABILITY_COMPARISON.json`` (``{"findings": [...]}``)
* an agent report ``A*.json`` (``{"findings": [...]}``)
* ``REBRAND_LEAKS.json`` (``{"unapproved_public_leaks": [...]}``)

Usage::

    python scripts/forensic_audit/ingest_agent_findings.py <file> [<file> ...]
    python scripts/forensic_audit/ingest_agent_findings.py --render
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from forensic_audit import report as report_mod  # noqa: E402
from forensic_audit import schema  # noqa: E402

REPO = HERE.parents[1]
LEDGER = REPO / "docs" / "generated" / "audit" / "ledger.json"
TRIAGE = REPO / "docs" / "generated" / "audit" / "triage.json"


def _rows(payload) -> list:
    if isinstance(payload, dict):
        for key in ("findings", "unapproved_public_leaks"):
            if isinstance(payload.get(key), list):
                return payload[key]
    if isinstance(payload, list):
        return payload
    return []


def _to_finding(item: dict) -> dict | None:
    import hashlib
    src = item.get("agent") or item.get("src") or item.get("id", "T1")
    aid = str(item.get("id") or item.get("kind") or item.get("title", ""))[:40]
    path = (item.get("qubit_file_line") or item.get("qubit") or item.get("file")
            or item.get("qubit_path") or "")
    title = item.get("title") or item.get("kind") or item.get("item") or aid
    # ids like A1's non-unique ``kind`` collide; disambiguate with a stable hash
    # of the distinguishing content (never line numbers), so reruns keep the
    # same fingerprint.
    hpath = str(item.get("hermes_file_line") or item.get("hermes") or "")
    uniq = hashlib.sha256(
        f"{aid}|{title}|{path}|{hpath}".encode("utf-8")).hexdigest()[:10]
    check = f"tier1_{str(src).lower()}"
    try:
        row = schema.finding(
            check,
            f"{aid}-{uniq}".replace("/", "_").replace(" ", "_")[:64],
            str(title)[:110],
            path=str(path),
            subsystem=str(item.get("area") or item.get("subsystem") or src),
            detail=str(item.get("difference") or item.get("evidence") or "")[:400],
            repro=str(item.get("repro") or ""),
            severity=(item.get("severity") or "MEDIUM").upper(),
            classification=(item.get("classification") or "UNKNOWN_REQUIRES_REPRODUCTION"),
            cause_type=str(item.get("cause_type") or "tier1"),
            tier=1,
        )
    except ValueError:
        return None
    row["tier"] = 1
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ingest Tier-1 findings.")
    ap.add_argument("files", nargs="*", help="agent JSON files")
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--triage", default=str(TRIAGE))
    ap.add_argument("--render", action="store_true",
                    help="only re-render the report from the ledger")
    args = ap.parse_args(argv)

    ledger_path = Path(args.ledger)
    ledger = schema.load_ledger(ledger_path)

    imported = 0
    if not args.render:
        merged = dict(ledger.get("findings", {}))
        for f in args.files:
            import json
            try:
                payload = json.loads(Path(f).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"skip {f}: {exc}", file=sys.stderr)
                continue
            for item in _rows(payload):
                if not isinstance(item, dict):
                    continue
                row = _to_finding(item)
                if row is None:
                    continue
                key = row["fingerprint"]
                if key not in merged:
                    row["status"] = schema.STATUS_OPEN
                    merged[key] = row
                    imported += 1
        run_seq = int(ledger.get("run_seq", 0)) + 1
        rows = list(merged.values())
        ledger_path.write_text(schema.dumps(
            schema.ledger_payload(rows, run_seq)), encoding="utf-8")
    else:
        rows = list(ledger.get("findings", {}).values())
        run_seq = int(ledger.get("run_seq", 0))

    text = report_mod.render(rows, run_seq, [])
    report_mod.write(REPO, text)
    print(f"ingested {imported} tier-1 findings; ledger -> {ledger_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
