"""Independent check: how many upstream production .py files are absent from our tree?

Deliberately re-implements the routing rather than calling the audit, so a bug in
the audit cannot validate itself.
"""
import subprocess
from pathlib import Path

UP = "/home/trick/upstream/hermes-agent"
REV = "43faacfb8de7b29548547a7fbdedad0cc6e7391c"
RUNTIME = Path("/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime")

files = [p for p in subprocess.run(
    ["git", "-C", UP, "ls-tree", "-r", "--name-only", REV],
    capture_output=True, text=True).stdout.split("\n") if p.endswith(".py")]


def route(p: str) -> str:
    segs = p.split("/")
    h = segs[0]
    if h == "hermes_cli":
        segs[0] = "qubit_cli"
    elif h == "hermes_platform":
        segs[0] = "qubit_platform"
    elif h.startswith("hermes_state"):
        segs[0] = "qubit_state" + h[len("hermes_state"):]
    elif h.startswith("hermes"):
        segs[0] = "qubit" + h[len("hermes"):]
    return "/".join(segs)


def is_test(p: str) -> bool:
    return p.startswith("tests/") or "/tests/" in p


miss_prod, miss_test = [], []
for p in files:
    if (RUNTIME / route(p)).exists():
        continue
    (miss_test if is_test(p) else miss_prod).append(p)

print(f"  upstream .py at pin : {len(files)}")
print(f"  ABSENT production   : {len(miss_prod)}")
print(f"  ABSENT tests        : {len(miss_test)}")
for p in miss_prod[:20]:
    print("     ", p)
