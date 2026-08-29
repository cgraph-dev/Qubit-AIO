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
import argparse, re, sys, pathlib, datetime

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
    candidates=re.findall(r"(?:^|\s)([A-Za-z0-9._/\-]+\.(?:py|ts|tsx|js|md|yaml|yml|json|html|css|sh|rs|go|java|sql|toml))", raw or "")
    seen=[]
    for c in candidates:
        if c not in seen:
            seen.append(c)
    return seen[:14]

def extract_commands(raw):
    # shell commands (lines starting with $ or `...`)
    cmds=re.findall(r"(?:^|\n)\s*[\$>]\s*(.+?)(?:\n|$)", raw or "")
    cmds+=[c for c in re.findall(r"`([^`\n]{4,120})`", raw or "") if any(k in c.lower() for k in ("pip ","pytest","npm ","git ","yarn ","curl ","python","docker","node ","cargo ","go "))]
    seen=[]
    for c in cmds:
        c=c.strip()
        if c and c not in seen and len(c)>=4:
            seen.append(c)
    return seen[:12]

def extract_references(raw):
    # urls
    urls=re.findall(r"https?://[^\s)\]\"']+", raw or "")
    seen=[]
    for u in urls:
        u=u.rstrip('.,;)')
        if u and u not in seen:
            seen.append(u)
    return seen[:12]

def extract_screenshots(raw):
    refs=re.findall(r"[\w./\-]*\.(?:png|jpg|jpeg|gif|webp)", raw or "")
    seen=[]
    for s in refs:
        if s and s not in seen:
            seen.append(s)
    return seen[:8]

def extract_test_results(raw):
    # pytest / test summary lines
    out=[]
    for line in (raw or "").splitlines():
        lo=line.lower().strip()
        if re.search(r"(passed|failed|error|ok|✓|✗)", lo) and re.search(r"\d+", lo):
            out.append(line.strip()[:120])
    return out[:8]

def infer_status(raw):
    lo=(raw or "").lower()
    # True failures: crash-y signatures, or a real (non-zero) failing count.
    crash=bool(re.search(r"(traceback|exception|assertionerror|segfault|fatal|killed|syntaxerror|importerror)", lo))
    fail_count=re.search(r"(\d+)\s+(?:tests?\s+)?failed", lo)
    real_fail=bool(fail_count and int(fail_count.group(1))>0)
    generic_fail=bool(re.search(r"\bfailed\b", lo)) and not re.search(r"(\bno\b|\b0\b|not failed|never)", lo)
    if crash or real_fail or generic_fail:
        return "partial"
    return "completed"

def auto_build(raw, args):
    raw=(raw or "").strip()
    task=args.task or "Task"
    project=args.project or ""
    status=args.status or infer_status(raw)
    elapsed=args.elapsed or ""
    agent=args.agent or ""
    route=args.route or ""
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
    elif raw and len(raw)>40:
        # auto: pick the first few tool/terminal lines as actions
        for line in (raw or "").splitlines():
            l=line.strip().lstrip("-•*").strip()
            if l and len(l)>12 and not l.startswith(("#","```","|","---","**")):
                actions.append(l[:140])
            if len(actions)>=8: break
    # spoken summary: prefer explicit --summary, else last 40w of raw
    spoken=args.summary or ""
    if not spoken:
        spoken=clamp_words(strip_md(raw) or task, 40) if raw else task
    else:
        spoken=clamp_words(spoken.strip(), 40)

    commands=extract_commands(raw)
    references=extract_references(raw)
    screenshots=extract_screenshots(raw)
    tests=extract_test_results(raw)
    gen_refs=args.references or ""
    if gen_refs:
        references+=[r for r in gen_refs.split(";") if r.strip()]

    date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    verify=args.verify or ("; ".join(tests[:4]) if tests else "—")
    risks=args.risks or "None"
    next_steps=[s.strip() for s in (args.next or "").split(";") if s.strip()]
    questions=args.questions or ""

    lines=[]
    lines.append(f"# Task Execution Report — {task}")
    lines.append("")
    lines.append(f"**Task:** {task}  ")
    if agent: lines.append(f"**Agent:** {agent}  ")
    if route: lines.append(f"**Route:** {route}  ")
    lines.append(f"**Date:** {date}  ")
    if project: lines.append(f"**Project:** {project}  ")
    lines.append(f"**Status:** {status}  ")
    if elapsed: lines.append(f"**Elapsed:** {elapsed}  ")
    lines.append("")
    lines.append("## Executive Summary")
    if args.objectives or (raw and len(raw)>60):
        sents=re.split(r"(?<=[.!?])\s+", strip_md(raw))
        for s in sents[:4]:
            if s.strip(): lines.append(f"- {s.strip()}")
        if not sents or not any(s.strip() for s in sents):
            lines.append(f"- {spoken}")
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
        lines.append(f"- Executed task: {task}")
        if files: lines.append(f"- Touched {len(files)} file(s)")
        if tests: lines.append(f"- Ran checks: {len(tests)}")
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
    if tests:
        lines.append("- **Test output:**")
        for t in tests[:8]:
            lines.append(f"  - {t}")
    if raw and len(raw)>20:
        lines.append(f"- **Evidence:** {strip_md(raw)[:400]}")
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
    if commands:
        lines.append("### Commands Run")
        for c in commands[:12]:
            lines.append(f"- `{c}`")
        lines.append("")
    if references:
        lines.append("### References / Sources")
        for u in references[:12]:
            lines.append(f"- [{u}]({u})")
        lines.append("")
    if screenshots:
        lines.append("### Screenshots / Evidence")
        for s in screenshots[:8]:
            lines.append(f"- `{s}`")
        lines.append("")
    if raw:
        lines.append("### Raw output excerpt")
        lines.append(f"```")
        lines.append(strip_md(raw)[:1500])
        lines.append("```")
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
    ap.add_argument("--status", default="", choices=["completed","partial","blocked","failed"], help="default: auto-inferred from raw")
    ap.add_argument("--agent", default="", help="backend/model: openclaw | hermes | local")
    ap.add_argument("--route", default="", help="route: openclaw | hermes | local-script | web")
    ap.add_argument("--summary", default="", help="explicit 40w spoken summary (last paragraph)")
    ap.add_argument("--actions", default="", help="; separated")
    ap.add_argument("--files", default="", help="; separated entries path:Change:desc")
    ap.add_argument("--verify", default="")
    ap.add_argument("--risks", default="")
    ap.add_argument("--next", default="", help="; separated")
    ap.add_argument("--questions", default="")
    ap.add_argument("--objectives", default="")
    ap.add_argument("--references", default="", help="; separated extra source links")
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
