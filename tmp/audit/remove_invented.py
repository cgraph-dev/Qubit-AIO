"""Surgically remove the 9 invented symbols the verifier found, plus their
self-wired call sites, without disturbing the genuine ports around them.

Verified before removal:
  * none of the 9 exists anywhere in upstream at the pin (grep of the pin's tree);
  * the issue numbers they cite (#126351, #140183) appear in ZERO upstream files;
  * their own dependencies (SPILLOVER_SQL_FILTER, spillover_names_in_text,
    referenced_spillover_names) do not exist in our tree either, so the code could
    never have run -- dead AND broken;
  * 5 of the 9 have zero in-repo callers; the other 4 are called only from
    agent-authored lines in the same batch (a caller+callee invented together).
"""
from pathlib import Path

RUNTIME = Path("/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime")

# file -> list of (symbol, kind) where kind is "def" (remove the def block) or
# "line" (remove that exact call line).
REMOVALS = {
    "qubit_state_media.py": ["retire_spillover", "collect_retired_spillover"],
    "qubit_state_local_migration.py": ["login_owned_by"],
    "qubit_state_mutation_prepared.py": ["picks_on", "policy_picks", "commit_refreshed_policy"],
}

CALL_LINES = [
    # (file, exact substring of the line to drop)
    ("qubit_state_mutation_retirement.py", "from qubit_state_media import retire_spillover"),
    ("qubit_state_mutation_retirement.py", "retire_spillover(conn, session_ids)"),
    ("qubit_state_mutation_branch.py", "from qubit_state_mutation_prepared import picks_on"),
    ("qubit_state_mutation_branch.py", "receipt['picks'] = sorted(picks_on(conn, session_id))"),
]


def strip_def(src: str, name: str) -> str:
    """Drop the top-level `def name(...)` block (def line through last body line)."""
    lines = src.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(f"def {name}("):
            start = i
            break
    if start is None:
        return src
    # block ends at the next top-level statement
    end = len(lines)
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if ln and not ln[0].isspace() and not ln.startswith((")", "]", "}")):
            end = j
            break
    # keep any decorator immediately above
    while start > 0 and lines[start - 1].startswith("@"):
        start -= 1
    # trim trailing blank lines that preceded the next def
    while end > start and lines[end - 1].strip() == "":
        end -= 1
    del lines[start:end]
    # collapse a resulting triple blank line
    out = "\n".join(lines)
    while "\n\n\n\n" in out:
        out = out.replace("\n\n\n\n", "\n\n\n")
    return out


for fname, names in REMOVALS.items():
    p = RUNTIME / fname
    src = p.read_text()
    before = src.count("\ndef ")
    for n in names:
        src = strip_def(src, n)
    p.write_text(src)
    print(f"  {fname}: removed {len(names)} invented def(s), {before} -> {src.count(chr(10)+'def ')} top-level defs")

for fname, needle in CALL_LINES:
    p = RUNTIME / fname
    lines = p.read_text().split("\n")
    kept = [ln for ln in lines if needle not in ln]
    if len(kept) != len(lines):
        p.write_text("\n".join(kept))
        print(f"  {fname}: dropped call line -> {needle[:52]}")
    else:
        print(f"  {fname}: NOT FOUND (left as-is) -> {needle[:52]}")
