"""Triage the production short-port symbols: real gap, or audit artifact?

Three agents independently reported the completeness audit OVER-REPORTS. This script
establishes how much of the remaining production gap is real, because the count drives
every work wave and an inflated count wastes agents on symbols upstream does not have.

For each production file in the audit's worst_short_ports, take the missing symbol names
(only available when the audit ran with --show-symbols) and classify each:

  REAL        the symbol is defined in upstream's copy of THAT file at the pin
  ARTIFACT    the symbol is not defined anywhere in upstream at the pin
  RENAMED     it IS in upstream, but under a hermes*/qubit* spelling of the name

Output: a JSON report plus counts, so a work wave can be scoped to REAL only.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

UP = "/home/trick/upstream/hermes-agent"
PIN = "43faacfb8de7b29548547a7fbdedad0cc6e7391c"
REPO = Path("/home/trick/Projects/Repos/qubit-voicebridge")
AUDIT = REPO / "tmp/audit/wave4.json"
OUT = REPO / "tmp/audit/triage.json"

data = json.loads(AUDIT.read_text())
symbols = data.get("symbols") or {}


def is_test(p: str) -> bool:
    return p.startswith("tests/") or "/tests/" in p or p.split("/")[-1].startswith("test_")


def up_path(p: str) -> str:
    """Our path -> upstream path (segment-wise, incl. basenames)."""
    segs = p.split("/")
    out = []
    for s in segs:
        if s.startswith("qubit_cli"):
            out.append("hermes_cli" + s[len("qubit_cli"):])
        elif s.startswith("qubit_platform"):
            out.append("hermes_platform" + s[len("qubit_platform"):])
        elif s.startswith("qubit_state"):
            out.append("hermes_state" + s[len("qubit_state"):])
        elif s.startswith("qubit"):
            out.append("hermes" + s[len("qubit") :])
        elif s.lstrip("_").startswith("qubit"):
            pre = s[: len(s) - len(s.lstrip("_"))]
            out.append(pre + "hermes" + s.lstrip("_")[len("qubit") :])
        else:
            out.append(s)
    return "/".join(out)


def upstream_src(path: str) -> str:
    r = subprocess.run(["git", "-C", UP, "show", f"{PIN}:{path}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def defs_in(src: str) -> set[str]:
    found = set()
    for m in re.finditer(r"^(?:async\s+)?def\s+(\w+)", src, re.M):
        found.add(m.group(1))
    for m in re.finditer(r"^class\s+(\w+)", src, re.M):
        found.add(m.group(1))
    for m in re.finditer(r"^([A-Z_][A-Z0-9_]*)\s*[:=]", src, re.M):
        found.add(m.group(1))
    return found


# All upstream defs at the pin, for the ARTIFACT test (is it ANYWHERE upstream?)
whole_tree = subprocess.run(
    ["git", "-C", UP, "grep", "-hoE", r"^(async )?def [a-zA-Z_]+|^class [a-zA-Z_]+", PIN, "--", "*.py"],
    capture_output=True, text=True).stdout
upstream_all = set()
for line in whole_tree.split("\n"):
    line = line.strip()
    if line.startswith("def "):
        upstream_all.add(line[4:].split("(")[0])
    elif line.startswith("async def "):
        upstream_all.add(line[10:].split("(")[0])
    elif line.startswith("class "):
        upstream_all.add(line[6:].split("(")[0].split(":")[0])

report = {"real": [], "artifact": [], "renamed": [], "by_file": {}}
for path, entry in symbols.items():
    if is_test(path):
        continue
    # `symbols[path]` is {"missing": [...names...], "branch": "..."} -- take the names.
    syms = entry.get("missing") if isinstance(entry, dict) else entry
    if not syms:
        continue
    up = up_path(path)
    src = upstream_src(up)
    in_file = defs_in(src) if src else set()
    real, artifact, renamed = [], [], []
    for s in syms:
        if s in in_file:
            real.append(s)
        elif s in upstream_all:
            renamed.append(s)          # exists upstream, different file/spelling
        else:
            artifact.append(s)         # nowhere upstream at the pin
    report["by_file"][path] = {"real": real, "artifact": artifact, "renamed": renamed,
                               "upstream_path": up, "upstream_exists": bool(src)}
    report["real"] += [f"{path}::{s}" for s in real]
    report["artifact"] += [f"{path}::{s}" for s in artifact]
    report["renamed"] += [f"{path}::{s}" for s in renamed]

OUT.write_text(json.dumps(report, indent=2))
tr, ta, trn = len(report["real"]), len(report["artifact"]), len(report["renamed"])
tot = tr + ta + trn
print(f"  production symbols triaged : {tot}")
print(f"    REAL (in upstream file)  : {tr}   ({100*tr//max(tot,1)}%)")
print(f"    RENAMED (elsewhere up)   : {trn}   ({100*trn//max(tot,1)}%)")
print(f"    ARTIFACT (nowhere up)    : {ta}   ({100*ta//max(tot,1)}%)")
print(f"  report: {OUT}")
print()
print("  top files by REAL symbol count:")
rows = sorted(report["by_file"].items(), key=lambda kv: -len(kv[1]["real"]))
for p, v in rows[:22]:
    if v["real"]:
        print(f"    {len(v['real']):4d} real / {len(v['artifact']):3d} artifact   {p}")
