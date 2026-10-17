"""Diagnose blob_many desync: find the FIRST path that goes wrong.

`cat-file --batch` is a strict request/response stream. If any response is
mis-consumed (short read, missed trailing newline, wrong header parse), every
later read is garbage -- which is exactly the "2,808 missing" symptom.
"""
import subprocess
import os

UP = "/home/trick/upstream/hermes-agent"
REV = "43faacfb8de7b29548547a7fbdedad0cc6e7391c"

files = [p for p in subprocess.run(
    ["git", "-C", UP, "ls-tree", "-r", "--name-only", REV],
    capture_output=True, text=True).stdout.split("\n") if p.endswith(".py")]

env = dict(os.environ, GIT_NO_LAZY_FETCH="1")
proc = subprocess.Popen(["git", "-C", UP, "cat-file", "--batch"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, env=env)
stdin, stdout = proc.stdin, proc.stdout

bad = 0
first_bad = None
for i, path in enumerate(files):
    spec = f"{REV}:{path}"
    stdin.write((spec + "\n").encode())
    stdin.flush()
    header = stdout.readline().decode("utf-8", "replace").rstrip("\n")
    parts = header.rsplit(" ", 2)
    if len(parts) != 3 or not parts[2].isdigit():
        bad += 1
        if first_bad is None:
            first_bad = (i, path, header)
            print(f"FIRST BAD at index {i}")
            print(f"  path   : {path}")
            print(f"  header : {header!r}")
            print(f"  prev   : {files[i-1]!r}")
            print(f"  next 3 headers:")
            for j in range(i + 1, min(i + 4, len(files))):
                stdin.write((f"{REV}:{files[j]}\n").encode())
                stdin.flush()
                h = stdout.readline().decode("utf-8", "replace").rstrip("\n")
                print(f"    [{j}] {h!r}")
                pj = h.rsplit(" ", 2)
                if len(pj) == 3 and pj[2].isdigit():
                    stdout.read(int(pj[2]))
                    stdout.read(1)
            break
        continue
    size = int(parts[2])
    stdout.read(size)
    stdout.read(1)

print(f"\ntotal bad: {bad}  (checked {i+1} of {len(files)})")
print(f"expected order position: file[{i}] should be {files[i][:60]}")
print(f"\nNote: cat-file --batch resolves '<rev>:<path>' by walking the tree each")
print(f"time; a path whose blob is absent locally makes git print 'missing' AND")
print(f"may emit a lazy-fetch error on stderr.")
stdin.close(); stdout.close(); proc.kill()
