import json, subprocess, re, sys

PIN = "43faacfb8de7b29548547a7fbdedad0cc6e7391c"
UP = "/home/trick/upstream/hermes-agent"
OUR = "/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime"

d = json.load(open("/home/trick/Projects/Repos/qubit-voicebridge/tmp/audit/wave3.json"))
sym = d["symbols"]

def upstream_text(path):
    r = subprocess.run(["git","-C",UP,"show",f"{PIN}:{path}"],capture_output=True,text=True)
    if r.returncode != 0:
        return None
    return r.stdout

def our_text(path):
    try:
        return open(f"{OUR}/{path}").read()
    except FileNotFoundError:
        return None

def has_def(text, s):
    # match def s( / class s( / s = / async def s(
    pats = [
        rf"^\s*(async\s+)?def\s+{re.escape(s)}\s*\(",
        rf"^\s*class\s+{re.escape(s)}\b",
        rf"^{re.escape(s)}\s*[:=]",
        rf"^\s*{re.escape(s)}\s*:\s*[A-Za-z]",
        rf"^\s*{re.escape(s)}\s*=",
    ]
    return any(re.search(p, text, re.M) for p in pats)

rows = []
for path, v in sym.items():
    if not path.startswith("tools/"):
        continue
    up = upstream_text(path)
    ou = our_text(path)
    for s in v.get("missing", []):
        up_has = has_def(up, s) if up is not None else None
        our_has = has_def(ou, s) if ou is not None else None
        rows.append((path, s, up is not None, up_has, our_has))

# report
missing_upstream = [r for r in rows if r[2] and not r[3]]
not_upstream_file = [r for r in rows if not r[2]]
real = [r for r in rows if r[2] and r[3] and not r[4]]
already = [r for r in rows if r[2] and r[3] and r[4]]

print("TOTAL symbols:", len(rows))
print("FILE ABSENT UPSTREAM:", len(not_upstream_file))
for r in not_upstream_file: print("   ", r[0], r[1])
print("SYMBOL NOT FOUND UPSTREAM (over-report):", len(missing_upstream))
for r in missing_upstream: print("   ", r[0], r[1])
print("ALREADY IN OURS:", len(already))
print("REAL GAP:", len(real))
for r in real: print("   ", r[0], r[1])
json.dump({"real":real,"over":missing_upstream,"absent_file":not_upstream_file,"already":already}, open("/home/trick/Projects/Repos/qubit-voicebridge/tmp/audit/tools_verify.json","w"), indent=1)
