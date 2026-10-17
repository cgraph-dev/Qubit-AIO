"""Refine the 84 bogus: for each, decide
  (A) basename exists SOMEWHERE upstream at pin (mapper miss -> likely fine)
  (B) basename exists NOWHERE upstream (invented namespace -> FABRICATION)
and record whether the file currently exists on our disk.
"""
import subprocess, os

UP = "/home/trick/upstream/hermes-agent"
PIN = "43faacfb8de7b29548547a7fbdedad0cc6e7391c"
REPO = "/home/trick/Projects/Repos/qubit-voicebridge"


def sh(a):
    return subprocess.run(a, capture_output=True, text=True).stdout


# full upstream file list at pin, plus a basename index
tree = sh(["git", "-C", UP, "ls-tree", "-r", "--name-only", PIN]).split("\n")
up_set = set(tree)
up_base = {}
for f in tree:
    up_base.setdefault(f.rsplit("/", 1)[-1], []).append(f)

bogus = []
for line in open("/home/trick/.local/state/qubit-voicebridge/runtime/cache/scratch/bogus.txt"):
    line = line.strip()
    if line and not line.startswith("added by"):
        bogus.append(line)

cat_a, cat_b = [], []
for p in bogus:
    base = p.rsplit("/", 1)[-1]
    t = p.replace("qubit_runtime/", "", 1)
    hits = [u for u in up_base.get(base, []) if u.startswith(t.split("/")[0] + "/")] or up_base.get(base, [])
    on_disk = os.path.exists(os.path.join(REPO, p))
    rec = (p, on_disk, hits[:3])
    (cat_a if up_base.get(base) else cat_b).append(rec)

print(f"BOGUS total: {len(bogus)}")
print(f"  A) basename EXISTS somewhere upstream at pin : {len(cat_a)}")
print(f"  B) basename NOWHERE upstream (INVENTED)      : {len(cat_b)}")
print("\n=== CATEGORY B (invented basenames) ===")
for p, d, h in cat_b:
    print(f"  on_disk={d!s:5} {p}")
print("\n=== CATEGORY A sample (mapper miss; on_disk / real upstream path) ===")
for p, d, h in cat_a[:60]:
    print(f"  on_disk={d!s:5} {p}   -> {h[0] if h else '?'}")
