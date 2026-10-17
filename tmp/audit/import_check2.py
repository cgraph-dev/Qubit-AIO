import json, subprocess, re, os

OUR = "/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime"
d = json.load(open("/home/trick/Projects/Repos/qubit-voicebridge/tmp/audit/wave3.json"))
sym = d["symbols"]
targets = {k: v["missing"] for k, v in sym.items() if k.startswith("tools/")}

def modpath(p): return p[:-3].replace("/", ".")

SRC = ["tools", "gateway", "tui_gateway", "qubit_cli", "agent", "cron", "utils",
       "toolsets.py", "qubit_constants.py", "qubit_state_runtime.py", "qubit_state_errors.py",
       "qubit_yaml.py", "hermes_yaml.py", "qubit_gateway", "hermes_gateway", "acp", "sandbox"]

for path, syms in sorted(targets.items()):
    mp = modpath(path)
    found = {}
    for s in syms:
        r = subprocess.run(["grep", "-rn", "--include=*.py",
                            r"from\s*" + re.escape(mp) + r"\s*import", "."],
                           capture_output=True, text=True, cwd=OUR)
        users = set()
        for line in r.stdout.splitlines():
            f, _, rest = line.partition(":")
            f = f.lstrip("./")
            if f == path: continue
            if re.search(rf"\b{re.escape(s)}\b", rest):
                users.add(f)
        if users:
            found[s] = sorted(users)
    if found:
        print(f"### {path}")
        for s, fs in found.items():
            print(f"   {s} <- {fs}")

# also module-alias attribute usage for a few
