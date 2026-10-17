"""Plan the FILENAME rebrand: 698 tracked files have 'hermes' in the name.

Renaming a file breaks every importer, so this does NOT rename anything -- it classifies
the set so a rename wave can be scoped and ordered, and so the genuinely risky ones
(shipped assets, plugin ids users reference) are separated from the cheap ones (tests).
"""
import subprocess
from collections import Counter
from pathlib import Path

REPO = "/home/trick/Projects/Repos/qubit-voicebridge"

files = subprocess.run(
    ["git", "-C", REPO, "ls-files"], capture_output=True, text=True
).stdout.split("\n")
branded = [f for f in files if f and "hermes" in f.lower()]

def classify(path: str) -> str:
    p = path.lower()
    if p.startswith("qubit_runtime/tests/") or "/tests/" in p or Path(p).name.startswith("test_"):
        return "test"
    if p.endswith(".py"):
        return "python"
    if p.endswith((".ts", ".tsx", ".js", ".jsx")):
        return "typescript"
    if p.endswith((".png", ".jpg", ".svg", ".ico", ".gif", ".webp")):
        return "ASSET (shipped image)"
    if p.startswith("qubit_runtime/plugin-catalog/"):
        return "PLUGIN CATALOG (user-facing ids)"
    if p.startswith("qubit_runtime/apps/desktop/src/plugins/"):
        return "DESKTOP PLUGIN (shipped)"
    if p.startswith("qubit_runtime/contributors/"):
        return "contributor data (provenance)"
    if p.startswith("qubit_runtime/website/"):
        return "docs (needs history check)"
    return "other"

buckets = Counter(classify(f) for f in branded)
print(f"  tracked files with 'hermes' in the name: {len(branded)}")
print()
for k, v in buckets.most_common():
    print(f"    {v:5d}  {k}")
print()

# Of the non-test, non-asset Python files, how many are IMPORTED by something?
risky = [f for f in branded if classify(f) == "python" and not f.startswith("qubit_runtime/tests/")]
print(f"  non-test Python files with a branded PATH: {len(risky)}")
importers = 0
for f in risky[:80]:
    stem = Path(f).stem
    # is the module imported anywhere? (the basename without .py)
    hit = subprocess.run(
        ["grep", "-rl", "--include=*.py", f"\\b{stem}\\b", f"{REPO}/qubit_runtime"],
        capture_output=True, text=True).stdout.strip()
    n = len([l for l in hit.split("\n") if l])
    if n > 1:
        importers += 1
print(f"  of the first {min(80, len(risky))} checked, {importers} are imported elsewhere")
print()
print("  sample of the non-test python ones (path, then whether imported):")
for f in risky[:15]:
    stem = Path(f).stem
    hit = subprocess.run(
        ["grep", "-rl", "--include=*.py", f"\\b{stem}\\b", f"{REPO}/qubit_runtime"],
        capture_output=True, text=True).stdout.strip()
    n = len([l for l in hit.split("\n") if l])
    print(f"    importers={n:3d}  {f}")
