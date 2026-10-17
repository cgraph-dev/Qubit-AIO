"""Brand the model-facing tool strings: 'Hermes desktop app' -> 'Qubit desktop app'.

These are docstrings and tool_error strings the MODEL reads and can repeat back to the
user, so they are product surface even though they are not printed by the CLI directly.

Deliberately narrow replacements -- only the human-readable product phrasing, never an
identifier, an env var, or a wire string.
"""
from pathlib import Path

RUNTIME = Path("/home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime")

REPLACEMENTS = [
    ("the Hermes desktop app", "the Qubit desktop app"),
    ("The Hermes desktop app", "The Qubit desktop app"),
    ("Hermes desktop app's", "Qubit desktop app's"),
    ("in the Hermes desktop app", "in the Qubit desktop app"),
    ("the Hermes desktop window", "the Qubit desktop window"),
    ("Hermes desktop window", "Qubit desktop window"),
    ("the Hermes window", "the Qubit window"),
    ("Hermes's root", "Qubit's root"),
    ("Hermes's Python env", "Qubit's Python env"),
    ("Hermes's interpreter", "Qubit's interpreter"),
    ("Hermes's own venv", "Qubit's own venv"),
    ("Hermes')", "Qubit')"),
    ("Hermes' own", "Qubit's own"),
    ("Hermes is typically maximised", "Qubit is typically maximised"),
]

FILES = [
    "tools/set_theme_tool.py",
    "tools/tool_search.py",
    "tools/setup_beat_tool.py",
    "tools/environments/base.py",
    "tools/terminal_tool_macos_open.py",
    "tools/code_execution_env.py",
    "tools/browser_use_cli.py",
]

for rel in FILES:
    p = RUNTIME / rel
    if not p.is_file():
        print(f"  MISSING {rel}")
        continue
    src = p.read_text()
    out = src
    count = 0
    for old, new in REPLACEMENTS:
        n = out.count(old)
        if n:
            out = out.replace(old, new)
            count += n
    if out != src:
        p.write_text(out)
        print(f"  {rel}: {count} replacement(s)")
    else:
        print(f"  {rel}: unchanged")
