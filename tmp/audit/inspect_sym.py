import subprocess, json, re, sys

PIN = "43faacfb8de7b29548547a7fbdedad0cc6e7391c"
UP = "/home/trick/upstream/hermes-agent"
OUR = "/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime"

def up(p):
    r = subprocess.run(["git","-C",UP,"show",f"{PIN}:{p}"],capture_output=True,text=True)
    return r.stdout if r.returncode==0 else None
def our(p):
    try: return open(f"{OUR}/{p}").read()
    except: return None

def def_span(lines, sym):
    """Return (start,end) line indices of the top-level definition of sym."""
    pats = [
        re.compile(rf"^\s*(async\s+)?def\s+{re.escape(sym)}\s*\("),
        re.compile(rf"^class\s+{re.escape(sym)}\b"),
        re.compile(rf"^{re.escape(sym)}\s*[:=]"),
    ]
    for i,l in enumerate(lines):
        if any(p.match(l) for p in pats):
            # find extent by indentation
            indent = len(l)-len(l.lstrip())
            j=i+1
            while j < len(lines):
                lj=lines[j]
                if lj.strip()=="" or lj.strip().startswith("#"):
                    j+=1; continue
                if (len(lj)-len(lj.lstrip()))<=indent:
                    break
                j+=1
            return i,j
    return None

def callers(lines, sym):
    return [i+1 for i,l in enumerate(lines) if re.search(rf"\b{re.escape(sym)}\b", l) and not re.match(rf"^\s*(async\s+)?(def|class)\s+{re.escape(sym)}\b", l) and not re.match(rf"^{re.escape(sym)}\s*[:=]", l)]

if __name__ == "__main__":
    d=json.load(open("/home/trick/Projects/Repos/qubit-voicebridge/tmp/audit/wave3.json"))
    sym=d["symbols"]
    target=sys.argv[1]
    v=sym[target]
    u=up(target); o=our(target)
    ul=u.splitlines()
    print(f"### {target}  up={len(ul)} ours={len(o.splitlines())}")
    for s in v["missing"]:
        sp=def_span(ul,s)
        if sp is None:
            print(f"  [NO DEF MATCH] {s}")
            continue
        print(f"  --- {s} lines {sp[0]+1}-{sp[1]}")
        print("\n".join("    "+x for x in ul[sp[0]:sp[1]]))
        print(f"     callers upstream: {callers(ul,s)}")
        print(f"     present in ours: {bool(re.search(rf'\\b{re.escape(s)}\\b', o))}")
