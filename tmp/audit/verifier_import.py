"""Import-check touched modules the way the runtime actually imports them:
`qubit_runtime/` is itself the source root, so modules are bare (tools.x, agent.x).
"""
import subprocess

PY = "/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python"
R = "/home/trick/Projects/Repos/qubit-voicebridge"
SRC = R + "/qubit_runtime"

base = subprocess.run(["git", "-C", R, "log", "--since=3 hours ago", "--format=%H"],
                      capture_output=True, text=True).stdout.split()[-1]
files = subprocess.run(["git", "-C", R, "diff", "--name-only", f"{base}..HEAD"],
                       capture_output=True, text=True).stdout.split("\n")
files = [f for f in files if f.endswith(".py") and f.startswith("qubit_runtime/")]

PREAMBLE = (
    "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); "
    "import importlib; importlib.import_module(%r)"
)

fails, ok = [], 0
for f in sorted(set(files)):
    mod = f[len("qubit_runtime/"):-3].replace("/", ".")
    if mod.endswith(".__init__"):
        mod = mod[: -len(".__init__")]
    code = PREAMBLE % (SRC, R, mod)
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, cwd=SRC, timeout=60)
    if r.returncode != 0:
        lines = [l for l in r.stderr.strip().split("\n") if l.strip()]
        err = lines[-1] if lines else "unknown"
        fails.append((mod, err))
    else:
        ok += 1

print(f"touched qubit_runtime modules: {len(set(files))}")
print(f"import OK: {ok}   FAIL: {len(fails)}")
for m, e in fails:
    print(f"  {m}\n      {e}")
