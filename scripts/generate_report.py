#!/usr/bin/env python3
"""Generate a professional two-layer report deterministically.
Usage:
  python scripts/generate_report.py --task "fix login bug" \
    --project ~/Projects/Repo --status completed \
    --summary "Fixed auth, 2 files changed, verified with tests." \
    --actions "Patched auth.py; Added tests" \
    --files "auth.py:Modified:fixed token check; tests/:Added:new cases" \
    --verify "pytest 12 passed" \
    --next "deploy to staging"

Also supports reading raw agent output and auto-extracting:
  python scripts/generate_report.py --raw output.txt --task "deploy API"

Outputs a markdown report to stdout and to <state_dir>/reports/<task>.md.
The spoken summary is ALWAYS the last paragraph (≤40w).
"""
import argparse, re, json, sys, os, pathlib, datetime, textwrap, subprocess

def clamp_words(text, max_words=40):
    w=text.split()
    if len(w)<=max_words:
        return text
    return " ".join(w[:max_words]).rstrip(" ,;:.")+"."

def strip_md(text):
    text=re.sub(r"```.*?```"," ",text,flags=re.S)
    text=re.sub(r"`([^`]*)`",r"\1",text)
    text=re.sub(r"[*_#>{}\[\]]+"," ",text)
    return re.sub(r"\s+"," ",text).strip()

def extract_files(raw):
    # naive: find file-like mentions
    candidates=re.findall(r"(?:^|\s)([A-Za-z0-9._/\-]+\.(?:py|ts|tsx|js|md|yaml|yml|json|html|css|sh))", raw or "")
    seen=[]
    for c in candidates:
        if c not in seen:
            seen.append(c)
    return seen[:12]

def auto_build(raw, args):
    raw=(raw or "").strip()
    task=args.task or "Task"
    project=args.project or ""
    status=args.status or "completed"
    elapsed=args.elapsed or ""
    # files
    files_arg=args.files or ""
    files=[]
    if files_arg:
        for entry in files_arg.split(";"):
            entry=entry.strip()
            if not entry: continue
            parts=[p.strip() for p in entry.split(":")]
            path=parts[0] if parts else entry
            change=parts[1] if len(parts)>1 else "Modified"
            desc=parts[2] if len(parts)>2 else ""
            files.append((path, change, desc))
    else:
        for f in extract_files(raw):
            files.append((f, "Modified", ""))
    # actions
    actions=[]
    if args.actions:
        actions=[a.strip() for a in args.actions.split(";") if a.strip()]
    # spoken summary: prefer explicit --summary, else last 40w of raw
    spoken=args.summary or ""
    if not spoken:
        spoken=clamp_words(strip_md(raw) or task, 40) if raw else task
    else:
        spoken=clamp_words(spoken.strip(), 40)

    date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    verify=args.verify or "—"
    risks=args.risks or "None"
    next_steps=[s.strip() for s in (args.next or "").split(";") if s.strip()]
    questions=args.questions or ""

    lines=[]
    lines.append(f"# Task Execution Report — {task}")
    lines.append("")
    lines.append(f"**Task:** {task}  ")
    lines.append(f"**Date:** {date}  ")
    if project: lines.append(f"**Project:** {project}  ")
    lines.append(f"**Status:** {status}  ")
    if elapsed: lines.append(f"**Elapsed:** {elapsed}  ")
    lines.append("")
    lines.append("## Executive Summary")
    if raw and len(raw)>60:
        # first 2 sentences of raw as executive summary fallback
        sents=re.split(r"(?<=[.!?])\s+", strip_md(raw))
        for s in sents[:3]:
            if s.strip(): lines.append(f"- {s.strip()}")
    else:
        lines.append(f"- {spoken}")
    lines.append("")
    if args.objectives:
        lines.append("## Objectives")
        for o in [x.strip() for x in args.objectives.split(";") if x.strip()]:
            lines.append(f"- {o}")
        lines.append("")
    lines.append("## Actions Taken")
    if actions:
        for a in actions: lines.append(f"- {a}")
    elif raw:
        # fallback: first actions from raw
        lines.append(f"- Executed task: {task}")
        if files: lines.append(f"- Touched {len(files)} file(s)")
    else:
        lines.append(f"- {task}")
    lines.append("")
    lines.append("## Files Changed")
    lines.append("| File | Change | Description |")
    lines.append("|------|--------|-------------|")
    if files:
        for path, ch, desc in files:
            lines.append(f"| `{path}` | {ch} | {desc or '—'} |")
    else:
        lines.append("| — | — | No files listed |")
    lines.append("")
    lines.append("## Verification")
    lines.append(f"- **Checks run:** {verify}")
    lines.append(f"- **Result:** {status}")
    if raw and len(raw)>20:
        lines.append(f"- **Evidence:** {strip_md(raw)[:300]}")
    lines.append("")
    lines.append("## Risks / Blockers")
    lines.append(f"- {risks}")
    lines.append("")
    lines.append("## Next Steps")
    if next_steps:
        for n in next_steps: lines.append(f"- [ ] {n}")
    else:
        lines.append("- [ ] Review report and confirm next action")
    if questions:
        lines.append("")
        lines.append("## Open Questions")
        for q in [x.strip() for x in questions.split(";") if x.strip()]:
            lines.append(f"- {q}")
    lines.append("")
    lines.append("## Appendix")
    if raw and len(raw)<8000:
        lines.append(f"**Raw output (truncated):** {strip_md(raw)[:1500]}")
    lines.append("")
    lines.append("---")
    lines.append(f"**Spoken Summary (last paragraph — spoken aloud):** {spoken}")
    lines.append("")
    text="\n".join(lines)
    return text, spoken

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--task", default="")
    ap.add_argument("--project", default="")
    ap.add_argument("--status", default="completed", choices=["completed","partial","blocked","failed"])
    ap.add_argument("--summary", default="", help="explicit 40w spoken summary (last paragraph)")
    ap.add_argument("--actions", default="", help="; separated")
    ap.add_argument("--files", default="", help="; separated entries path:Change:desc")
    ap.add_argument("--verify", default="")
    ap.add_argument("--risks", default="")
    ap.add_argument("--next", default="", help="; separated")
    ap.add_argument("--questions", default="")
    ap.add_argument("--objectives", default="")
    ap.add_argument("--elapsed", default="")
    ap.add_argument("--raw", default="", help="path to raw output file OR raw text")
    ap.add_argument("--out", default="", help="output path (default stdout + state/reports)")
    ap.add_argument("--state-dir", default="~/.local/state/openclaw-voice-bridge")
    args=ap.parse_args()

    raw=""
    if args.raw:
        rp=pathlib.Path(args.raw).expanduser()
        if rp.exists() and rp.is_file():
            raw=rp.read_text(errors="replace")
        else:
            raw=args.raw

    md, spoken = auto_build(raw, args)

    out_path=None
    if args.out:
        out_path=pathlib.Path(args.out).expanduser()
    else:
        # also persist to state/reports/
        try:
            sd=pathlib.Path(args.state_dir).expanduser()/ "reports"
            sd.mkdir(parents=True, exist_ok=True)
            safe=re.sub(r"[^A-Za-z0-9._-]+","_", (args.task or "report"))[:60].strip("_") or "report"
            ts=datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            out_path=sd/f"{safe}-{ts}.md"
            out_path.write_text(md, errors="replace")
            print(f"[saved to {out_path}]", file=sys.stderr)
        except Exception as e:
            print(f"[warn save failed: {e}]", file=sys.stderr)
            out_path=None

    # main output to stdout (piped to dashboard / spoken)
    sys.stdout.write(md)
    if out_path and str(out_path)!=str(pathlib.Path(args.out).expanduser()) if args.out else False:
        pathlib.Path(args.out).expanduser().write_text(md, errors="replace")

if __name__=="__main__":
    main()
