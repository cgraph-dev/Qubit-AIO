"""Verify every file added by a set of commits exists upstream at the pin.

This is the check that caught the earlier fabrication (75 files written at paths
upstream does not have). Run it on ANY delegated batch before trusting its summary.
"""
import subprocess
import sys

UP = "/home/trick/upstream/hermes-agent"
PIN = "43faacfb8de7b29548547a7fbdedad0cc6e7391c"
REPO = "/home/trick/Projects/Repos/qubit-voicebridge"

COMMITS = sys.argv[1:] or [
    "f9c018371",   # task 1: 36 gateway
    "a75ad2173", "f7ccefa60", "064272b72", "ec3d7b160", "9c55ac7bc", "46a9ee23a",  # task 2: 21 qubit_cli
    "9e60cb9d5", "520ea1191", "c490f44e8",   # task 3: short-ports
    "e271d3804", "34f0491f8",  # task 4: remaining absent
]


def sh(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout


added: set[str] = set()
for c in COMMITS:
    out = sh(["git", "-C", REPO, "show", "--diff-filter=A", "--name-only", "--format=", c])
    added |= {p for p in out.split("\n") if p.endswith(".py")}

# Files that legitimately differ in basename: skip the rename and test raw too.
def up_path(p: str) -> str:
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
    # segment-wise rename (basenames too), mirroring the audit's route()
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


ok, bogus = [], []
for p in sorted(added):
    for cand in (up_path(p), p.replace("qubit_runtime/", "")):
        if subprocess.run(["git", "-C", UP, "cat-file", "-e", f"{PIN}:{cand}"],
                          capture_output=True).returncode == 0:
            ok.append(p)
            break
    else:
        bogus.append(p)

print(f"  commits checked     : {len(COMMITS)}")
print(f"  files added (.py)   : {len(added)}")
print(f"  EXIST at pin        : {len(ok)}")
print(f"  NOT at pin (BOGUS)  : {len(bogus)}")
for p in bogus:
    print("     ", p)
