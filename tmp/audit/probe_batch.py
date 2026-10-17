"""Probe: does blob_many serve EVERY file the audit will ask for?

If some paths come back missing, the audit falls back to per-file `git show`,
which in this PARTIAL clone is a network fetch per file -- the real reason the
walk is slow even after batching.
"""
import importlib.util
import sys
import time

spec = importlib.util.spec_from_file_location("aud", "scripts/audit_sync_completeness.py")
m = importlib.util.module_from_spec(spec)
sys.argv = ["aud"]
try:
    spec.loader.exec_module(m)
except SystemExit:
    pass

REV = "43faacfb8de7b29548547a7fbdedad0cc6e7391c"
files = [p for p in m.sh(["git", "-C", str(m.UP), "ls-tree", "-r", "--name-only", REV]).split("\n")
         if p.endswith(".py")]
print(f"  total .py files : {len(files)}")

t0 = time.time()
blobs = m.blob_many(REV, files)
dt = time.time() - t0
print(f"  blob_many served: {len(blobs)}/{len(files)} in {dt:.1f}s")

miss = [p for p in files if p not in blobs]
print(f"  MISSING         : {len(miss)}")
for p in miss[:15]:
    print("     ", p)
