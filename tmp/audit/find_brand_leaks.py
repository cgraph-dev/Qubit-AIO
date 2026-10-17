"""How much 'hermes' is still in USER-VISIBLE strings?

The owner directive (docs/IDENTITY.md, docs/BRAND.md) is zero legacy brand on product
surfaces. Internal identifiers, HERMES_* env vars, frozen wire strings and legal
attribution are permitted exceptions; a name a user READS is not.

This classifies every remaining 'hermes' occurrence so the leak set is measurable rather
than guessed at, and so a fix pass has an explicit allowlist for the legitimate classes.
"""
import re
import subprocess
from collections import Counter
from pathlib import Path

RUNTIME = Path("/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime")

# Legitimate: never a user-visible product name.
LEGIT_PATTERNS = [
    (re.compile(r"HERMES_"), "HERMES_* env var / wire key"),
    (re.compile(r"hermes-gateway-v1"), "frozen WS protocol id"),
    (re.compile(r"hermes-gw-"), "frozen socket dir name"),
    (re.compile(r"x-hermes-"), "frozen HTTP header"),
    (re.compile(r"hermes-setup\.exe|tauri|installer|updater", re.I), "installer/updater binary name"),
    (re.compile(r"com\.nousresearch\.hermes"), "macOS bundle id (frozen)"),
    (re.compile(r"hermes://"), "frozen URL scheme"),
    (re.compile(r"\.hermes"), "path anchor (.hermes dir)"),
    (re.compile(r"NousResearch/hermes-agent|hermes-agent\.git"), "upstream repo (provenance)"),
    (re.compile(r"#\s*\d+|https?://"), "issue ref / URL"),
    (re.compile(r"^\s*#"), "comment"),
    (re.compile(r"get_hermes_|_hermes_home|hermes_home_key|hermes_constants|hermes_cli|hermes_state|hermes_platform|hermes_logging|hermes_yaml|hermes_bootstrap|_hermes_top_level|hermes_owned|hermes_dir|hermes_root"), "internal identifier / compat alias"),
]

# A user-visible string: a Python string literal that looks like prose or a name.
STRING_LIT = re.compile(r"""(['"])(?:(?!\1).){4,}\1""")
HERMES = re.compile(r"hermes|Hermes|HERMES")


def is_user_visible(line: str) -> bool:
    """A quoted string containing hermes that is not an identifier or a path."""
    for m in STRING_LIT.finditer(line):
        lit = m.group(0)
        if not HERMES.search(lit):
            continue
        if any(p.search(lit) for p, _ in LEGIT_PATTERNS):
            continue
        # a bare module path / identifier in quotes is not prose
        if re.fullmatch(r"""['"][\w./:_-]+['"]""", lit) and ("_" in lit or "/" in lit or "." in lit):
            continue
        return True
    return False


hits = []
for p in RUNTIME.rglob("*.py"):
    rel = str(p.relative_to(RUNTIME))
    if "__pycache__" in rel:
        continue
    # TESTS are out of scope for the brand directive: their 'hermes' strings never
    # reach a user, and sweeping them would churn the suite for no product gain.
    if rel.startswith("tests/") or "/tests/" in rel or Path(rel).name.startswith("test_"):
        continue
    try:
        lines = p.read_text(errors="replace").split("\n")
    except OSError:
        continue
    for i, line in enumerate(lines, 1):
        if not HERMES.search(line):
            continue
        if any(pat.search(line) for pat, name in LEGIT_PATTERNS if name != "comment"):
            continue
        if line.strip().startswith("#"):
            continue
        if is_user_visible(line):
            hits.append((rel, i, line.strip()[:130]))

by_file = Counter(h[0] for h in hits)
print(f"  USER-VISIBLE 'hermes' strings in production .py: {len(hits)}")
print(f"  across {len(by_file)} files")
print()
for f, n in by_file.most_common(18):
    print(f"    {n:3d}  {f}")
print()
print("  first 25:")
for f, i, t in hits[:25]:
    print(f"    {f}:{i}")
    print(f"        {t}")
