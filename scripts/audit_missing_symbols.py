
import ast, json, re, subprocess, sys
from pathlib import Path

# Derive from __file__, NOT a hardcoded path. Hardcoding the main checkout made this
# audit grade MAIN's symbols when run from a worktree: it reported 16 "GENUINELY ABSENT"
# symbols and all 16 were false positives (each existed as a real `def` in the flagged
# file). A porter acting on that list would create duplicate objects (hazard #1).
# Same bug class as sync_driver.py line 29, fixed the same way.
REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "qubit_runtime"
def _upstream_clone() -> Path:
    """Locate the upstream clone, tolerating the hermes home being retired.

    The clone lived at ~/.hermes/hermes-agent; the hermes home has since been moved aside as part
    of retirement, so the same tree now sits at ~/.hermes.pre-retirement/hermes-agent. Prefer the
    live location, fall back to the retired one, and fail LOUDLY rather than silently measuring an
    empty directory -- a silent miss here would make every audit grade the wrong thing.
    """
    for candidate in (
        Path.home() / "upstream" / "hermes-agent",
        Path.home() / ".hermes" / "hermes-agent",
        Path.home() / ".hermes.pre-retirement" / "hermes-agent",
    ):
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit(
        "cannot locate the upstream clone (tried ~/.hermes/hermes-agent and "
        "~/.hermes.pre-retirement/hermes-agent); every audit would measure nothing"
    )


UP = _upstream_clone()

# DRIFT-MEASURING: which upstream revision to compare our symbols against. This
# was a hardcoded `a036b13793` (the 2026-10-06 sync waypoint). Measured against a
# stale base, a symbol upstream added SINCE that waypoint is invisible -- the
# audit under-reports the gap. Read the frozen pin (our vendoring revision) from
# its single declared home so the comparison base agrees with the burndown
# instrument instead of drifting on its own.
def _frozen_rev():
    src = Path(__file__).resolve().parent / "frozen_target_progress.py"
    if src.is_file():
        for line in src.read_text().splitlines():
            if line.startswith("FROZEN_REV"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"cannot read the frozen pin from {src}; refusing to compare "
                     "symbols against an unknown revision.")

BASE = _frozen_rev()
ROUTES = {"hermes_cli":"qubit_cli","hermes_platform":"qubit_platform","hermes_state":"qubit_state"}

def route(path):
    for prefix in ("tests/",""):
        if prefix and not path.startswith(prefix): continue
        rest = path[len(prefix):] if prefix else path
        segs = rest.split("/")
        if segs and segs[0] in ROUTES: segs[0] = ROUTES[segs[0]]
        if segs and segs[-1].startswith("hermes"): segs[-1] = "qubit"+segs[-1][6:]
        return prefix + "/".join(segs)
    return path

def rebrand(sym):
    for up,qb in (("_HERMES_","_QUBIT_"),("HERMES_","QUBIT_"),("_hermes_","_qubit_"),("hermes_","qubit_")):
        if sym.startswith(up): return qb+sym[len(up):]
    return sym.replace("hermes","qubit") if "hermes" in sym else sym

def defs_of_file(p):
    try: src = p.read_text(errors="replace")
    except OSError: return set()
    try: tree = ast.parse(src)
    except SyntaxError: return set()
    out=set()
    for n in tree.body:
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): out.add(n.name)
        elif isinstance(n,ast.Assign):
            for t in n.targets:
                if isinstance(t,ast.Name): out.add(t.id)
        elif isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name): out.add(n.target.id)
    return out

# ONE global index of every top-level name defined anywhere in our tree
ALL = set()
for p in RUNTIME.rglob("*.py"):
    if "__pycache__" in p.parts: continue
    ALL |= defs_of_file(p)
# plus root-level modules (voicebridge, cli.py etc)
for p in REPO.glob("*.py"):
    ALL |= defs_of_file(p)

d = json.load(open(sys.argv[1]))
prod = [x for x in d["worst_short_ports"] if not x["path"].startswith("tests/")]
real=[]; fp=0; checked=0
for item in prod:
    up_path=item["path"]
    our = RUNTIME / route(up_path)
    if not our.exists(): continue
    try: head = ast.parse(subprocess.run(["git","-C",str(UP),"show",f"{BASE}:{up_path}"],capture_output=True,text=True,timeout=120).stdout)
    except SyntaxError: continue
    ours = defs_of_file(our)
    for n in head.body:
        if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): continue
        if n.name in ours: continue
        checked+=1
        alt = rebrand(n.name)
        if alt in ALL or n.name in ALL:
            fp+=1
        else:
            real.append((up_path,n.name))
print(f"checked {checked}  present-elsewhere (FP) {fp}  GENUINELY ABSENT {len(real)}")
print()
for p,s in real[:80]: print(f"  {s:48} {p}")
