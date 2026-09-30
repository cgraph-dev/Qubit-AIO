#!/usr/bin/env python3
"""Packet 8T-F — dump the pinned upstream CLI surface from a real binary.

Runs `<binary> <cmd> --help` per subcommand against an ISOLATED install
(never the operator binary) and records usage + options. Output feeds
docs/archive/2026-09-21/PARITY_HERMES.json verdicts. Read-only; no model calls (help only).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

COMMANDS = """acp approvals auth backup browser bundles chat checkpoints claw
completion computer-use config console cron curator dashboard debug desktop
doctor dump egress fallback gateway hooks import import-agent insights journey
kanban login logout logs lsp mcp memory migrate moa model monitoring pairing
pause peer pets plugins portal profile project prompt-size proxy resume
secrets security send serve sessions setup skills skin slack status sync
tools uninstall update verify webhook whatsapp whatsapp-cloud worktree""".split()


def main() -> int:
    binary = sys.argv[1]
    home = sys.argv[2]
    out = sys.argv[3]
    env = {"PATH": os.environ.get("PATH", ""), "HOME": home,
           "QUBIT_HOME": home}
    rows = []
    for cmd in COMMANDS:
        try:
            proc = subprocess.run(
                [binary, cmd, "--help"], capture_output=True, text=True,
                timeout=120, env=env)
            text = proc.stdout or proc.stderr or ""
            first = next((ln.strip() for ln in text.splitlines()
                          if ln.strip() and "usage:" not in ln.lower()), "")
            opts = sorted({tok for tok in __import__("re").findall(
                r"(?:^|\s)(-\w|--[\w-]+)", text)})
            rows.append({"command": cmd, "rc": proc.returncode,
                         "usage": text.splitlines()[0][:160] if text else "",
                         "blurb": first[:200], "options": opts[:40]})
        except Exception as exc:
            rows.append({"command": cmd, "rc": -1, "error": str(exc)[:160]})
    json.dump({"binary": binary, "commands": rows},
              open(out, "w"), indent=1)
    print(f"wrote {out} ({len(rows)} commands)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
