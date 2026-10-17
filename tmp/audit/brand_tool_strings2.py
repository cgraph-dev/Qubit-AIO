"""Second pass: multi-line comment cases the first pass missed.

Comments where the brand word wraps to the next line ('...the Hermes\n    desktop
window') cannot be caught by a single-line replace. These are developer comments, not
user-visible strings, but they are the same leak class and cheap to fix.
"""
from pathlib import Path

RUNTIME = Path("/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime")

REPLACEMENTS = [
    ("BEHIND the Hermes\n    desktop window", "BEHIND the Qubit\n    desktop window"),
    ("exposing Hermes's site-packages", "exposing Qubit's site-packages"),
    ("Inherited Hermes-owned entries", "Inherited Qubit-owned entries"),
    ("strip Hermes-owned entries", "strip Qubit-owned entries"),
    ("must not inherit Hermes dependencies", "must not inherit Qubit dependencies"),
    ("runs on Hermes's own interpreter", "runs on Qubit's own interpreter"),
    ("The flag tells Hermes' own deadline", "The flag tells Qubit's own deadline"),
]

FILES = [
    "tools/terminal_tool_macos_open.py",
    "tools/code_execution_env.py",
    "tools/browser_use_cli.py",
    "tools/environments/base.py",
]

for rel in FILES:
    p = RUNTIME / rel
    src = p.read_text()
    out = src
    n = 0
    for old, new in REPLACEMENTS:
        c = out.count(old)
        if c:
            out = out.replace(old, new)
            n += c
    if out != src:
        p.write_text(out)
        print(f"  {rel}: {n} replacement(s)")
    else:
        print(f"  {rel}: unchanged")
