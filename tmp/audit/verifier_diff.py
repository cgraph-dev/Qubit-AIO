"""Path-existence check for ADDED **and MODIFIED** files post-revert.

The ready-made verify_ports.py only looks at --diff-filter=A. Most post-revert
work is amendments to existing files, so a pure-add check is blind to it.
"""
import subprocess, sys

UP = "/home/trick/upstream/hermes-agent"
PIN = "43faacfb8de7b29548547a7fbdedad0cc6e7391c"
REPO = "/home/trick/Projects/Repos/qubit-voicebridge"
BASE = sys.argv[1] if len(sys.argv) > 1 else "715693a58"


def sh(a):
    return subprocess.run(a, capture_output=True, text=True).stdout


def up_path(p):
    segs = p.split("/")
    if segs and segs[0] == "qubit_runtime":
        segs = segs[1:]
    if not segs:
        return p
    if segs[0] == "qubit_cli":
        segs[0] = "hermes_cli"
    elif segs[0] == "qubit_platform":
        segs[0] = "hermes_platform"
    elif segs[0].startswith("qubit_state"):
        segs[0] = "hermes_state" + segs[0][len("qubit_state"):]
    elif segs[0].startswith("qubit"):
        segs[0] = "hermes" + segs[0][len("qubit"):]
    fixed = []
    for s in segs:
        if s.startswith("qubit"):
            fixed.append("hermes" + s[len("qubit"):])
        elif s.lstrip("_").startswith("qubit"):
            pre = s[: len(s) - len(s.lstrip("_"))]
            fixed.append(pre + "hermes" + s.lstrip("_")[len("qubit"):])
        else:
            fixed.append(s)
    return "/".join(fixed)


def exists(up):
    return subprocess.run(["git", "-C", UP, "cat-file", "-e", f"{PIN}:{up}"],
                          capture_output=True).returncode == 0


files = [p for p in sh(["git", "-C", REPO, "diff", "--name-only",
                        f"{BASE}..HEAD"]).split("\n") if p.endswith(".py")]
print(f"base = {BASE}")
print(f".py changed {BASE}..HEAD : {len(files)}")
miss = []
for p in sorted(files):
    cands = [up_path(p), p.replace("qubit_runtime/", "", 1)]
    if not any(exists(c) for c in cands):
        miss.append(p)
print(f"not present upstream at pin: {len(miss)}")
for p in miss:
    print("   ", p)
