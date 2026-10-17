"""Group the production string-leak report by area, so agent scopes are sized from data."""
import re
from collections import Counter

REPORT = "/home/trick/Projects/Repos/qubit-voicebridge/tmp/audit/leaks_prod2.txt"

rows = []
started = False
for ln in open(REPORT).read().split("\n"):
    if ln.strip().startswith("first 25:"):
        break
    if ln.startswith("  USER-VISIBLE"):
        started = True
        continue
    if not started:
        continue
    m = re.match(r"^\s+(\d+)\s+(\S+)$", ln)
    if m:
        rows.append((m.group(2), int(m.group(1))))

print(f"  files with production string leaks: {len(rows)}")
print(f"  total occurrences: {sum(n for _, n in rows)}")
print()
area = Counter()
for f, n in rows:
    area[f.split("/")[0]] += n
print("  by top-level area:")
for k, v in area.most_common(25):
    print(f"    {v:5d}  {k}")
print()
print("  worst 30 files:")
for f, n in sorted(rows, key=lambda x: -x[1])[:30]:
    print(f"    {n:4d}  {f}")
