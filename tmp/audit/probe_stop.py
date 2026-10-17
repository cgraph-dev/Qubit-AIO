"""Isolate the batch failure: does git die at a specific INDEX, or a specific file?

Runs the same 9,503 specs through one `cat-file --batch`, printing git's stderr
and exit status the moment the stream ends early -- so we learn WHY it stops
rather than only that it does.
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

ok = 0
for i, path in enumerate(files):
    try:
        stdin.write((f"{REV}:{path}\n").encode())
        stdin.flush()
    except BrokenPipeError:
        print(f"BROKEN PIPE writing index {i} ({path})")
        break
    header = stdout.readline().decode("utf-8", "replace").rstrip("\n")
    if not header:
        print(f"EMPTY HEADER at index {i} ({path}) -- git stdout closed")
        break
    parts = header.rsplit(" ", 2)
    if len(parts) == 3 and parts[2].isdigit():
        stdout.read(int(parts[2]))
        stdout.read(1)
        ok += 1
    else:
        print(f"ODD HEADER at index {i} ({path}): {header!r}")
        break

print(f"served {ok} blobs, stopped at index {i} of {len(files)}")
print(f"  file at stop: {files[i]}")
rc = proc.poll()
print(f"  git process exited? {rc is not None}  rc={rc}")
try:
    err = proc.stderr.read().decode("utf-8", "replace") if proc.poll() is not None else "(still running)"
except Exception as e:
    err = f"(err read failed: {e})"
print(f"  git stderr: {err[:400]!r}")
if proc.poll() is None:
    proc.kill()
print(f"\n  RSS of git at stop (if alive): n/a")
print(f"  index {i} as a fraction: {i}/{len(files)} = {100*i/len(files):.1f}%")
