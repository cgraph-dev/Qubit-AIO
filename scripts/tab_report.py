#!/usr/bin/env python3
"""Agent-friendly one-liner: read a browser tab and produce a professional report.

Usage:
  python tab_report.py <target> [--task "what the agent was asked"] [--project NAME]
                        [--summary "explicit 40w spoken summary"] [--verbose]

`target` is a URL substring or tab id. Reads the real logged-in Chromium tab,
falls back to generate_report.py to emit a full markdown report (which the agent
then summarizes to <=40 words for voice).

The raw tab text is also echoed in the --verbose output so the agent can quote
verbatim excerpts into the report body.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import browser_auto  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("target", help="URL substring or tab id to read")
    ap.add_argument("--task", default="Read browser tab", help="the task being done")
    ap.add_argument("--project", default="", help="project label for the report")
    ap.add_argument("--summary", default="", help="explicit <=40w spoken summary")
    ap.add_argument("--verbose", action="store_true", help="also print the raw tab text")
    ap.add_argument("--out", default="", help="path to write the report; default auto")
    args, _ = ap.parse_known_args()

    result = browser_auto.read_tab(args.target)
    text = result.get("text") or ""
    if result.get("error"):
        print(json.dumps({"error": result["error"], "ok": False}))
        return 1

    if args.verbose:
        print(json.dumps({"tab_text": text[:12000]}, ensure_ascii=False))
        print("\n---\n")

    if not text.strip() or len(text.strip()) < 25:
        print(json.dumps({"error": "tab has too little readable text", "ok": False,
                          "url": result.get("url")}))
        return 1

    # Build the report via generate_report.py (deterministic, idiot-proof).
    cmd = [sys.executable, str(HERE / "generate_report.py"),
           "--task", args.task]
    if args.project:
        cmd += ["--project", args.project]
    if args.summary:
        cmd += ["--summary", args.summary]
    else:
        cmd += ["--summary", f"Read the browser tab at {result.get('url', '')} and produced a summary of what was found."]
    # pass verbatim excerpts as evidence
    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    tmp.write(text[:8000])
    tmp.close()
    cmd += ["--raw", tmp.name]

    done = subprocess.run(cmd, capture_output=True, text=True)
    out = (done.stdout or "").strip()
    report_path = args.out or str(Path.cwd() / f"tab_report-{int(__import__('time').time())}.md")
    if not out:
        out = text  # fall back to raw text if generate_report printed nothing
    try:
        Path(report_path).write_text(out, errors="replace")
    except Exception:
        report_path = "(report not written — generated on stdout)"

    print(json.dumps({"ok": True, "url": result.get("url"), "report": report_path,
                      "chars": len(text), "raw_evidence": tmp.name,
                      "report_body": out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
