import json, subprocess, re, os, ast

OUR = "/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime"
d = json.load(open("/home/trick/Projects/Repos/qubit-voicebridge/tmp/audit/wave3.json"))
sym = d["symbols"]
targets = {k: v["missing"] for k, v in sym.items() if k.startswith("tools/")}

# module import path for each file: tools/foo.py -> tools.foo ; tools/a/b.py -> tools.a.b
def modpath(p):
    assert p.endswith(".py")
    return p[:-3].replace("/", ".")

# Collect all python files
allpy = []
for root, dirs, files in os.walk(OUR):
    if "__pycache__" in root: continue
    for f in files:
        if f.endswith(".py"):
            allpy.append(os.path.join(root, f))

for path, syms in sorted(targets.items()):
    mp = modpath(path)
    for s in syms:
        users = []
        for f in allpy:
            try:
                src = open(f).read()
            except: continue
            # literal import of this module
            if mp not in src and os.path.relpath(f, OUR) == path: continue
            # find "from <mp> import ... s ..."
            for m in re.finditer(rf"from\s+{re.escape(mp)}\s+import\s+([^\n(]+|\([^)]*\))", src):
                if re.search(rf"\b{re.escape(s)}\b", m.group(1)):
                    users.append(os.path.relpath(f, OUR) + " [import]")
            # attribute access  tools.foo.s  or alias .s
            if re.search(rf"\b{re.escape(s)}\b", src) and os.path.relpath(f, OUR) != path:
                # only count if module is referenced by attribute form
                if re.search(rf"\b{re.escape(s)}\b", src):
                    users.append(os.path.relpath(f, OUR) + " [name]")
        # dedupe
        if users:
            print(f"{path}::{s}")
            for u in sorted(set(users))[:8]:
                print("    ", u)
