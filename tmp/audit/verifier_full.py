"""VERIFIER: independent path-existence audit over EVERY commit in the window.

Does not trust any agent's commit-message summary. Walks `git log --since`
for real, maps each added .py to its upstream path, and asserts existence at
the frozen pin. Reports the ADDING COMMIT for every failure.
"""
import subprocess, sys

UP = "/home/trick/upstream/hermes-agent"
PIN = "43faacfb8de7b29548547a7fbdedad0cc6e7391c"
REPO = "/home/trick/Projects/Repos/qubit-voicebridge"


def sh(args):
    return subprocess.run(args, capture_output=True, text=True).stdout


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


# every commit in window, in order, with the file list each ADDED
raw = sh(["git", "-C", REPO, "log", "--since=2 hours ago",
          "--diff-filter=A", "--name-only", "--format=@@@%H %ad %s", "--date=format:%H:%M"])
commit = None
added = {}          # path -> [commits that added it]
order = []          # commit hashes in order
head = {}           # commit -> summary line
for line in raw.split("\n"):
    if line.startswith("@@@"):
        commit = line[3:].split(" ")[0]
        order.append(commit)
        head[commit] = line[3:]
    elif line.strip().endswith(".py"):
        added.setdefault(line.strip(), []).append(commit)

bogus = {}
for p, cs in sorted(added.items()):
    if not (exists(up_path(p)) or exists(p.replace("qubit_runtime/", ""))):
        bogus[p] = cs

print(f"window commits (newest first): {len(order)}")
for c in order:
    print("   ", head[c])
print(f"distinct .py added: {len(added)}")
print(f"BOGUS (path absent upstream at pin): {len(bogus)}")
for p, cs in sorted(bogus.items()):
    print(f"  {p}")
    for c in cs:
        print(f"        added by {c}  {head[c]}")
