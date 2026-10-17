import json, subprocess, re, os

OUR = "/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime"
d = json.load(open("/home/trick/Projects/Repos/qubit-voicebridge/tmp/audit/wave3.json"))
sym = d["symbols"]

targets = {k: v["missing"] for k, v in sym.items() if k.startswith("tools/")}

# build the set of (file, symbol) to search for
all_syms = set()
for k, v in targets.items():
    for s in v:
        all_syms.add(s)

# grep repo-wide for each symbol usage outside its own file
for path, syms in targets.items():
    base = os.path.basename(path)
    hits = {}
    for s in syms:
        r = subprocess.run(["grep", "-rn", "--include=*.py", r"\b" + re.escape(s) + r"\b", "."],
                           capture_output=True, text=True, cwd=OUR)
        files = set()
        for line in r.stdout.splitlines():
            f = line.split(":")[0].lstrip("./")
            if f == path:
                continue
            files.add(f)
        if files:
            hits[s] = sorted(files)[:6]
    if hits:
        print(f"### {path}")
        for s, fs in hits.items():
            print(f"   {s}: {fs}")
