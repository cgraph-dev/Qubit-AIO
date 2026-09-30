---
name: codebase-rebranding
description: Safely rebrand, rename, or bulk-replace terms across a codebase without corrupting third-party code, copyright notices, or technical terms that share the same name.
title: Codebase Rebranding & Safe Bulk Text Replacement
trigger:
  - user asks to rebrand, rename, or replace a term across a codebase
  - user says "change all X to Y in my code"
  - user wants to rename a product, library, platform, or internal term project-wide
  - bulk find-and-replace in multiple files while protecting certain matches
---

# Codebase Rebranding & Safe Bulk Text Replacement

## Overview

Rebranding a term across a codebase (e.g., product rename, platform rebrand, internal codename change) is dangerous because simple string replacement will corrupt:
- Third-party dependencies and vendored code
- Copyright and legal notices
- Technical terms that happen to contain the same substring
- URLs, API endpoints, and package names that must remain unchanged

This skill provides a **safe, reviewable workflow** for project-wide text replacement.

## Trigger Conditions

- User says: "rebrand X to Y", "rename X to Y across my project", "change all occurrences of X"
- User explicitly says: "do not touch third-party repos", "only my own files", "skip official repos"
- Any bulk find-and-replace request across multiple source files

## Step-by-Step Workflow

### 1. Discover Scope (Read-Only First)

Search the codebase to understand the blast radius **before making any changes**:

```bash
# Find all files containing the term
search_files(path="/path/to/project", pattern=r"\bOldName\b", target="files", output_mode="files_only")
```

Key questions to answer:
- How many files match? (10 vs 10,000 changes the approach)
- Which directories are third-party? (`node_modules/`, `vendor/`, `third_party/`, official upstream repos in `References/`)
- Are there copyright lines that must be preserved?
- Are there technical terms that share the name? (e.g., "Signal" as a messaging app vs "Signal-to-Noise Ratio")

### 2. Define Protection Rules

Before any replacement, establish skip rules. Common protections:

| Protection Type | Examples | Skip Pattern |
|-----------------|----------|--------------|
| **Third-party directories** | `node_modules/`, `.git/`, `dist/`, `build/`, `venv/`, `vendor/`, `go/pkg/` | Skip dirs by name |
| **Copyright/legal lines** | `Copyright 2023 Signal Messenger, LLC` | Skip lines matching `Copyright.*OldName` |
| **Technical terms** | `Signal-to-Noise Ratio`, `Signal Level`, `Signal Quality`, `highest-signal` | Skip lines matching `Signal-to-Noise\|Signal Level\|Signal Quality` |
| **URLs/endpoints** | `https://api.telegram.org/` | Skip if user says URLs must stay |
| **Package imports** | `import telegram` from PyPI | Skip if user says external API names stay |

**Always ask the user** if URLs, API endpoints, import statements, or package names should be preserved. Default to preserving them.

### 3. Execute Replacement with Whole-Word Regex

Never use simple substring replacement. Use **whole-word regex** (`\b`) to avoid partial matches:

```python
import os, re

# BAD:  "Signal" in "Signal-to-Noise" becomes "Signal-to-Noise"
# GOOD: \bSignal\b only matches the standalone word
new_line, count = re.subn(r'\bSignal\b', 'Signal', line)
```

Always process **line-by-line** so you can apply skip rules per-line:

```python
skip_patterns = [
    re.compile(r"Copyright.*Signal Messenger", re.IGNORECASE),
    re.compile(r"Signal-to-Noise", re.IGNORECASE),
    re.compile(r"Signal Level", re.IGNORECASE),
    re.compile(r"highest-signal", re.IGNORECASE),
]

for line in lines:
    if any(p.search(line) for p in skip_patterns):
        new_lines.append(line)  # preserve untouched
        continue
    # ... apply replacement
```

### 4. Review and Fix False Positives

After running the replacement, **always search for false positives**:

```bash
# Find suspicious replacements
grep -r "Signal-to-Noise\|Signal Level\|Signal Quality" /path/to/project
```

Common false positive categories to check:
- Technical terms that were missed by skip patterns
- Table headers or markdown formatting
- Comments that use the word in a generic sense
- CSS class names, color names, or design tokens

**Fix them immediately** with targeted patches. Do not leave false positives in the codebase.

### 5. Verify with Search

After fixes, run a final verification:

```bash
# Confirm no originals remain in user-owned files
grep -r "\bOldName\b" /path/to/project --include="*.md" --include="*.py" --include="*.ts" --include="*.json"

# Confirm no false positives
grep -ri "newname-to-noise\|newname level\|newname quality" /path/to/project
```

### 6. Consider File/Directory Renames

If the user also wants physical renames:
- Rename directories: `extensions/telegram/` → `extensions/t3g/`
- Rename files: `telegram_adapter.py` → `t3g_adapter.py`
- Update import paths and references to renamed files

**Only do this if explicitly requested.** File renames are more disruptive than text replacement.

## User Preferences to Embed

When a user corrects your approach during rebranding, save the lesson:

| User Says | Lesson |
|-----------|--------|
| "do not rebrand unofficial repos" | Always skip `References/`, `vendor/`, third-party submodules, and upstream forks |
| "only my own files" | Restrict to user's project directories; never touch `~/.hermes/hermes-agent/`, system paths, or installed packages |
| "keep the URLs as-is" | Preserve all URLs, API endpoints, and webhook paths |
| "don't touch copyright lines" | Skip all lines matching `Copyright.*`, `License.*`, legal headers |
| "our Signal is the messaging app, not the technical term" | Build explicit skip list for technical homonyms before starting |

## Pitfalls

1. **Substring replacement corrupts technical terms.** Always use `\bOldName\b` whole-word regex.

2. **Skipping too little touches third-party code.** When in doubt, skip. It's better to manually fix a few missed occurrences than to corrupt 100 vendor files.

3. **False positives are invisible until searched.** Always grep for obvious corruption patterns after replacement (e.g., `NewName-to-Noise`, `NewName Blue` color names).

4. **Case sensitivity matters.** `\bSignal\b` won't match `signal` lowercase. Use `re.IGNORECASE` or handle case variants explicitly if needed.

5. **File extensions matter.** Don't rename `.md` references in third-party docs. Stick to the user's own `.md`, `.py`, `.ts`, `.json`, `.yaml` files.

6. **Hub skills and bundled code are protected.** Never edit skills under `~/.hermes/skills/` that are bundled with Hermes or installed from the hub. Only edit user-created skills.

## Example: Complete Rebranding Session

**User request:** "rebrand Signal to Signal and Telegram to Telegram in my Qubit project. Do not touch official Signal repos."

**Execution:**
1. Search `~/Projects/Repos/Qubit (2)/` for `\bTelegram\b` and `\bSignal\b`
2. Identify `~/Projects/Repos/References/Signal/` as third-party → skip entirely
3. Skip `node_modules/`, `.git/`, `dist/`, `build/`
4. Skip copyright lines: `Copyright.*Signal Messenger, LLC`
5. Skip technical terms: `Signal-to-Noise`, `Signal Level`, `Signal Quality`, `highest-signal`
6. Apply `\bTelegram\b` → `Telegram` and `\bSignal\b` → `Signal` line-by-line
7. Search for false positives: `Signal-to-Noise`, `Signal Level`, `Signal Blue`, `Content Signal Matrix`
8. Fix all false positives with targeted patches
9. Final verification: no `\bTelegram\b` or `\bSignal\b` remain in user-owned files

## Related Skills

- `github-repo-management` — If renames require git operations (branch, commit, PR)
- `simplify-code` — If rebranding reveals dead code or outdated references
- `code-wiki` — If docs need updating alongside code changes
