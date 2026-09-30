# Rebranding Script Template

Copy and adapt this Python script for any codebase-wide rebranding task.

```python
import os
import re

# Configuration
base_dir = "/path/to/project"
extensions = {".md", ".yaml", ".yml", ".json", ".txt", ".toml", ".cfg", ".ini", ".py", ".ts", ".tsx", ".js", ".jsx"}
skip_dirs = {"node_modules", ".git", "dist", "build", "venv", ".venv", "__pycache__", ".next", "coverage", ".turbo"}

# Terms to rebrand: [(old, new), ...]
rebrands = [("Telegram", "Telegram"), ("Signal", "Signal")]

# Lines matching these patterns are preserved untouched
skip_line_patterns = [
    re.compile(r"Copyright.*Signal Messenger", re.IGNORECASE),
    re.compile(r"Signal Messenger, LLC", re.IGNORECASE),
    # Technical terms that share the name
    re.compile(r"Signal-to-Noise", re.IGNORECASE),
    re.compile(r"Signal Level", re.IGNORECASE),
    re.compile(r"Signal Quality", re.IGNORECASE),
    re.compile(r"Signal Acquisition", re.IGNORECASE),
    re.compile(r"P_signal", re.IGNORECASE),
    re.compile(r"highest-signal", re.IGNORECASE),
    re.compile(r"Signal to capture", re.IGNORECASE),  # e.g. "// Signal to capture script"
    re.compile(r"Content Signal Matrix", re.IGNORECASE),
    re.compile(r"Traffic Light with Signal States", re.IGNORECASE),
]

changes = []

for root, dirs, files in os.walk(base_dir):
    dirs[:] = [d for d in dirs if d not in skip_dirs]
    for fname in files:
        if not any(fname.endswith(ext) for ext in extensions):
            continue
        fpath = os.path.join(root, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
        except (UnicodeDecodeError, OSError):
            continue

        lines = content.splitlines(keepends=True)
        new_lines = []
        file_changes = []

        for line in lines:
            # Skip protected lines
            if any(p.search(line) for p in skip_line_patterns):
                new_lines.append(line)
                continue

            new_line = line
            for old, new in rebrands:
                new_line, count = re.subn(rf'\\b{re.escape(old)}\\b', new, new_line)
                if count:
                    file_changes.append((old, line.rstrip('\\n'), new_line.rstrip('\\n')))
            new_lines.append(new_line)

        if file_changes:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write("".join(new_lines))
            rel = os.path.relpath(fpath, base_dir)
            changes.append((rel, file_changes))

# Report
print(f"Rebranded {len(changes)} files.\\n")
for rel, file_changes in changes:
    print(f"📄 {rel}: {len(file_changes)} changes")
```

## Post-Replacement False-Positive Checklist

After running the script, grep for these common corruption patterns:

```bash
# Technical terms corrupted by Signal → Signal
grep -ri "Signal-to-Noise\|Signal Level\|Signal Quality\|Signal Acquisition\|P_S1G\|highest-s1g" "$base_dir"

# Generic terms corrupted by Signal → Signal
grep -ri "Content Signal Matrix\|Signal to capture\|Signal States\|Emerald Signal Green\|Signal Blue" "$base_dir"

# Technical terms corrupted by Telegram → Telegram (rare, but check)
grep -ri "Telegram-to-" "$base_dir"  # unlikely but verify

# Table headers that might have been caught
grep -ri "| Signal |" "$base_dir"
```

Fix any matches with targeted `patch` operations.

## Hermes-Specific Extensions

When rebranding inside `~/.hermes/skills/`:
- Skip bundled skills (shipped with Hermes) — check if the skill is user-created
- Skip hub-installed skills (installed via `hermes skills install`)
- Only edit skills the user created manually or that are in their personal skills directory
- Never edit `~/.hermes/hermes-agent/` (official Hermes source code)
