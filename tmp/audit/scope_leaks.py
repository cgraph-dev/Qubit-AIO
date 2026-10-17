"""Exact line numbers of user-visible 'hermes' strings in the assigned scope."""
import re
import sys
from pathlib import Path

RUNTIME = Path("/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime")
sys.path.insert(0, str(Path("/home/trick/Projects/Repos/qubit-voicebridge/tmp/audit")))
import find_brand_leaks as F  # noqa: E402

SCOPE = re.compile(r"^(tools/|tui_gateway/|gateway/|acp_adapter/|"
                   r"toolsets\.py$|qubit_state_holders\.py$|qubit_logging\.py$|"
                   r"model_tools\.py$|mcp_serve\.py$)")

# file -> line numbers to leave (with reason), keyed to the task's protected list
LEFT = set()
for spec in [
    ("mcp_serve.py", [7, 731]),
]:
    for ln in spec[1]:
        LEFT.add((spec[0], ln))

rows = []
for p in RUNTIME.rglob("*.py"):
    rel = str(p.relative_to(RUNTIME))
    if "__pycache__" in rel or not SCOPE.match(rel):
        continue
    if rel.startswith("tests/") or "/tests/" in rel or Path(rel).name.startswith("test_"):
        continue
    try:
        lines = p.read_text(errors="replace").split("\n")
    except OSError:
        continue
    for i, line in enumerate(lines, 1):
        if not F.HERMES.search(line):
            continue
        if any(pat.search(line) for pat, name in F.LEGIT_PATTERNS if name != "comment"):
            continue
        if line.strip().startswith("#"):
            continue
        if F.is_user_visible(line):
            rows.append((rel, i, line.strip()[:150]))

print(f"total rows: {len(rows)} in {len({r[0] for r in rows})} files")
cur = None
for rel, i, t in rows:
    if rel != cur:
        print(f"\n=== {rel} ===")
        cur = rel
    print(f"  {i}: {t}")
