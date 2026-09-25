"""Render the living ``QubitVSupstream.md`` report from the findings ledger.

The report is a *view*, not the source of truth: edit the ledger or triage
overlay, then re-render. It is intentionally machine-generated so it cannot
drift from the checks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .schema import SEVERITY_ORDER, STATUS_FIXED, summarize

IDENTITY = {
    "upstream": "https://github.com/NousResearch/hermes-agent",
    "qubit_remote": "https://github.com/cgraph-dev/openclaw-voice-bridge.git",
}


def _table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "_none_\n"
    out = ["| Sev | Status | Check | Finding | Location |",
           "|---|---|---|---|---|"]
    for r in rows:
        loc = r.get("path") or ""
        if r.get("line"):
            loc = f"{loc}:{r['line']}"
        title = r.get("title", "").replace("|", "\\|")
        out.append(
            f"| {r['severity']} | {r['status']} | `{r['check']}` | "
            f"{title} | `{loc}` |")
    return "\n".join(out) + "\n"


def render(rows: List[Dict[str, Any]], run_seq: int,
           check_catalog: List[Dict[str, Any]]) -> str:
    s = summarize(rows)
    open_rows = [r for r in rows if r["status"] not in (STATUS_FIXED,)
                 and r["status"] not in ("WONTFIX", "ACCEPTED")]
    open_rows.sort(key=lambda r: (SEVERITY_ORDER.get(r["severity"], 9),
                                  r["check"], r["fingerprint"]))
    resolved = [r for r in rows if r["status"] == STATUS_FIXED]
    triaged = [r for r in rows if r["status"] in ("WONTFIX", "ACCEPTED", "OPERATOR")]

    gating = s.get("gating", [])
    verdict = ("FAIL_GATE" if gating else "READY_FOR_REPAIR")
    if not gating and not open_rows:
        verdict = "NO_REPRODUCED_DIFFERENCE"

    lines: List[str] = []
    lines.append("# Qubit vs. upstream Hermes — living audit")
    lines.append("")
    lines.append("> **Generated** by `scripts/forensic_audit/run_audit.py`. "
                 "Do not hand-edit; edit the ledger or the triage overlay and "
                 "re-run. The deep one-shot forensic pass is preserved at "
                 "`docs/generated/audit/FORENSIC_BASELINE_2026-09-25.md`.")
    lines.append("")
    lines.append(f"- Ledger run: **{run_seq}**")
    lines.append(f"- Upstream reference: `{IDENTITY['upstream']}`")
    lines.append(f"- Qubit remote: `{IDENTITY['qubit_remote']}`")
    lines.append(f"- **Verdict: `{verdict}`** "
                 f"({len(open_rows)} open, {len(resolved)} fixed/tracked-resolved, "
                 f"{len(triaged)} triaged)")
    lines.append("")
    lines.append("## How to re-run")
    lines.append("")
    lines.append("```bash")
    lines.append("# deterministic checks; fail on NEW/REGRESSED HIGH|CRITICAL")
    lines.append(".venv/bin/python scripts/forensic_audit/run_audit.py --check")
    lines.append("")
    lines.append("# update the ledger + regenerate this report")
    lines.append(".venv/bin/python scripts/forensic_audit/run_audit.py")
    lines.append("")
    lines.append("# fast subset (skips slow generator/import checks)")
    lines.append(".venv/bin/python scripts/forensic_audit/run_audit.py --check --fast")
    lines.append("```")
    lines.append("")
    lines.append("## Coverage map (Tier 0 checks)")
    lines.append("")
    lines.append("| Check | Slow | What it verifies |")
    lines.append("|---|---|---|")
    for c in check_catalog:
        lines.append(f"| `{c['id']}` | {'yes' if c['slow'] else 'no'} | "
                     f"{c['description']} |")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- By severity: `{s['by_severity']}`")
    lines.append(f"- By status: `{s['by_status']}`")
    lines.append("")
    lines.append("## Open findings (severity-ranked)")
    lines.append("")
    lines.append(_table(open_rows))
    lines.append("")
    if gating:
        lines.append("## GATING: new or regressed HIGH/CRITICAL")
        lines.append("")
        for gid in gating:
            lines.append(f"- `{gid}`")
        lines.append("")
    lines.append("## Fixed / resolved (tracked)")
    lines.append("")
    lines.append(_table(resolved))
    lines.append("")
    lines.append("## Triaged (owner overlay)")
    lines.append("")
    lines.append(_table(triaged))
    lines.append("")
    lines.append("## Tier 1 — deep agent audit")
    lines.append("")
    lines.append("Tier 0 is deterministic and cheap. To re-derive the full "
                 "107-row forensic set (providers, toolsets, skills, plugins, "
                 "MCP, sessions, rebrand, future-breakage), run the five-agent "
                 "deep dive described in "
                 "`scripts/forensic_audit/TIER1_AGENT_PROMPTS.md` and ingest the "
                 "outputs:")
    lines.append("")
    lines.append("```bash")
    lines.append(".venv/bin/python scripts/forensic_audit/ingest_agent_findings.py \\")
    lines.append("    /tmp/qubit-hermes-forensic-audit-*/reports/A*.json")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def write(repo: Path, text: str) -> Path:
    out = repo / "docs" / "QubitVSupstream.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out
