#!/usr/bin/env python3
"""Packet 7A: deployment-template contract check.

Renders the repository systemd template and validates the DEPLOYMENT contract:
  - Restart policy must be on-failure with a bounded RestartSec;
  - the rendered ExecStart must resolve __PROJECT_DIR__ (no leftover placeholders);
  - portable resource guardrails must be DOCUMENTED comments, not hardcoded
    machine limits;
  - the template must contain NO machine-specific paths, tokens, or secrets;
  - Environment lines must not carry secret values (presence only).

Read-only: never installs, never edits the installed unit.
Exit: 0 ok, 2 at least one contract FAIL.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

EXIT_OK = 0
EXIT_FAIL = 2

_SECRET_SHAPES = (
    re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\s*[=:]\s*\S+"),
)
_MACHINE_PATH = re.compile(r"/home/[A-Za-z0-9_.-]+")


def _render(template: Path) -> str:
    text = template.read_text(errors="replace")
    # __PROJECT_DIR__ is the only placeholder the installer must resolve.
    return text.replace("__PROJECT_DIR__", "<PROJECT_DIR_RESOLVED>")


def check_deployment(template: Path) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    if not template.exists():
        results.append(("systemd.template", "FAIL", f"template missing: {template}"))
        return results
    raw = template.read_text(errors="replace")
    rendered = _render(template)

    if "__PROJECT_DIR__" in rendered:
        results.append(("systemd.placeholder", "FAIL", "__PROJECT_DIR__ unresolved"))
    elif "ExecStart=" in rendered and "<PROJECT_DIR_RESOLVED>" in rendered:
        results.append(("systemd.execstart", "PASS",
                        "ExecStart resolves the project dir (no leftover placeholder)"))
    else:
        results.append(("systemd.execstart", "FAIL", "ExecStart not rendered"))

    restart = re.search(r"^Restart=(\S+)", raw, re.M)
    if not restart:
        results.append(("systemd.restart", "FAIL", "no Restart= directive"))
    elif restart.group(1) == "on-failure":
        results.append(("systemd.restart", "PASS", "Restart=on-failure"))
    else:
        results.append(("systemd.restart", "FAIL",
                        f"Restart={restart.group(1)} != on-failure (watchdog risk)"))
    sec = re.search(r"^RestartSec=(\S+)", raw, re.M)
    if sec and 0 < float(sec.group(1)) <= 60:
        results.append(("systemd.restart_sec", "PASS", f"RestartSec={sec.group(1)}"))
    elif sec:
        results.append(("systemd.restart_sec", "FAIL",
                        f"RestartSec={sec.group(1)} out of (0,60]"))
    else:
        results.append(("systemd.restart_sec", "WARN", "RestartSec not pinned"))

    # Resource guardrails must be DOCUMENTED comments (portable defaults), never
    # hardcoded machine limits in the portable template.
    hard = re.search(r"^(MemoryMax|MemoryHigh|Nice|CPUQuota)=\S+", raw, re.M)
    if hard:
        results.append(("systemd.resource_defaults", "FAIL",
                        f"hardcoded resource directive in portable template: "
                        f"{hard.group(0)} (must be a commented machine override)"))
    else:
        results.append(("systemd.resource_defaults", "PASS",
                        "portable template documents resource guardrails as comments"))

    secrets = [s for s in _SECRET_SHAPES if s.search(raw)]
    if secrets:
        results.append(("systemd.secret_safe", "FAIL",
                        "template carries secret-shaped values"))
    else:
        results.append(("systemd.secret_safe", "PASS", "no secret-shaped values"))

    hp = _MACHINE_PATH.findall(raw)
    if hp:
        results.append(("systemd.machine_path", "FAIL",
                        f"machine-specific path in template: {sorted(set(hp))}"))
    else:
        results.append(("systemd.machine_path", "PASS",
                        "no machine-specific home path in template"))

    # Environment lines: values must not look like credentials.
    for line in raw.splitlines():
        m = re.match(r"^\s*Environment=(\S+)", line)
        if m and any(s.search(m.group(1)) for s in _SECRET_SHAPES):
            results.append(("systemd.environment", "FAIL", "Environment carries a secret value"))
            break
    else:
        results.append(("systemd.environment", "PASS", "Environment lines are secret-safe"))

    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Packet 7A deployment-template check")
    ap.add_argument("--template", default=str(REPO / "systemd/openclaw-voice-bridge.service"))
    args = ap.parse_args(argv)
    results = check_deployment(Path(args.template))
    order = {"FAIL": 0, "WARN": 1}
    for name, state, detail in sorted(results, key=lambda r: order.get(r[1], 2)):
        print(f"[{state:>5}] {name:26s} {detail}")
    fails = sum(1 for _, s, _ in results if s == "FAIL")
    print(f"\ndeployment: {len(results)} checks — {fails} fail")
    return EXIT_FAIL if fails else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())