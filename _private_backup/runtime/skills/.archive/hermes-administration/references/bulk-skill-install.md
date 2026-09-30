# Bulk Skill Installation Recipe

Extract every official skill identifier from `hermes skills browse` and install them all non-interactively.

## Problem

`hermes skills browse` renders a Unicode table where the Identifier column (last column) wraps across continuation lines. Simple shell parsing fails, and `hermes skills install` requires a TTY confirmation by default.

## Solution

Use a Python script via `execute_code` to parse the table and batch-install with `--yes`.

```python
import subprocess
import re

all_text = ""
for page in range(1, 6):  # official catalog is 100 skills = 5 pages at 20/page
    result = subprocess.run(
        ["hermes", "skills", "browse", "--source", "official", "--page", str(page)],
        capture_output=True, text=True, timeout=60
    )
    all_text += result.stdout + "\n" + result.stderr + "\n"

lines = all_text.splitlines()
rows = []
current_row = None
current_identifier_parts = []

for line in lines:
    # Detect start of a new table row by the leading number cell
    m = re.match(r'^[\u2502\u2503]\s+(\d+)\s+[\u2502\u2503]', line)
    if m:
        if current_row is not None and current_identifier_parts:
            current_row['identifier'] = ''.join(current_identifier_parts).strip()
            rows.append(current_row)
        current_row = {'num': int(m.group(1))}
        current_identifier_parts = []
        parts = re.split(r'[\u2502\u2503]', line)
        if len(parts) >= 6:
            last = parts[-2].strip()
            if last:
                current_identifier_parts.append(last)
    elif current_row is not None:
        parts = re.split(r'[\u2502\u2503]', line)
        if len(parts) >= 6:
            last = parts[-2].strip()
            if last:
                current_identifier_parts.append(last)

if current_row is not None and current_identifier_parts:
    current_row['identifier'] = ''.join(current_identifier_parts).strip()
    rows.append(current_row)

identifiers = [r['identifier'] for r in rows if r.get('identifier', '').startswith('official/')]
identifiers = sorted(set(identifiers))

# Install loop
success = []
failed = []
for i, skill in enumerate(identifiers, 1):
    print(f"[{i}/{len(identifiers)}] Installing {skill} ...", flush=True)
    result = subprocess.run(
        ["hermes", "skills", "install", "--yes", skill],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode == 0 and "Installed" in result.stdout:
        print("  OK")
        success.append(skill)
    else:
        err = result.stderr.strip() or result.stdout.strip()
        print(f"  FAILED: {err[:200]}")
        failed.append((skill, err))

print(f"\nDone. Success: {len(success)}/{len(identifiers)}, Failed: {len(failed)}")
```

## Known edge cases

- Some identifiers in browse output may have "Identifier" appended if parsed naively. The box-drawing split above handles this correctly.
- A small number of official skills may fail to fetch (often due to identifier mismatches in the hub index). Retry the exact identifier without any suffix, or inspect with `hermes skills inspect <identifier>`.
- After installation, `hermes skills list` shows skills by their frontmatter `name`, which may differ from the hub identifier used for install.
