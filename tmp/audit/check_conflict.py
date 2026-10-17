"""Report whether the stash-pop conflict in test_office_invariants_audit.py
spans real code or only comments -- that decides how to resolve it.
"""
from pathlib import Path

p = Path("/home/trick/Projects/Repos/qubit-voicebridge/tests/test_office_invariants_audit.py")
lines = p.read_text().split("\n")

state = None
start = 0
code_conflicts = 0
comment_conflicts = 0
for i, ln in enumerate(lines, 1):
    if ln.startswith("<<<<<<<"):
        state = "ours"
        start = i
    elif ln.startswith("=======") and state:
        state = "theirs"
    elif ln.startswith(">>>>>>>") and state:
        body = lines[start : i - 1]
        real = [l for l in body
                if l.strip() and not l.strip().startswith("#")
                and not l.startswith(("<<<", "===", ">>>"))]
        if real:
            code_conflicts += 1
            print(f"  CODE conflict lines {start}-{i}: {real[:2]}")
        else:
            comment_conflicts += 1
        state = None

print(f"  comment-only conflicts: {comment_conflicts}")
print(f"  conflicts with real code: {code_conflicts}")
