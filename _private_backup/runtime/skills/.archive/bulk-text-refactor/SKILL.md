---
name: bulk-text-refactor
title: Bulk Text Refactor — Mass Find-and-Replace Across Codebases
description: |
  Rename, rebrand, or replace text across thousands of files safely.
  Covers whole-word regex matching, scope discipline, skip patterns for
  technical terms, and copyright/legal line handling.
triggers:
  - User asks to rename, rebrand, or replace a word/phrase across many files
  - User says "change all occurrences of X to Y"
  - User wants to bulk-update identifiers, brand names, or terminology
  - Mass string replacement in a project or reference directory
---

# Bulk Text Refactor

## 1. Lock the Scope — Do NOT Broaden Without Permission

**CRITICAL:** Operate ONLY on the directories or file globs the user explicitly named.

- If user says "in `/home/trick/Projects/Repos/References`" → ONLY scan that path.
- Do NOT automatically include `~/.hermes/skills/`, `~/Projects/Qubit/`, or other nearby directories because they "seem related."
- If you are unsure whether a subdirectory is in scope, ask — do not assume.

> **Pitfall:** The #1 cause of user frustration in this workflow is scope creep. Users will use strong language if you touch files they did not name.

## 2. Identify What to Replace

Ask or infer:
- **Target word(s):** What exact string(s) should be replaced?
- **Replacement:** What should they become?
- **Case sensitivity:** Preserve original casing? (e.g., `Telegram` → `T3G`, `telegram` → `t3g`)
- **Whole-word only?** Almost always yes — use `\bWord\b` regex to avoid partial matches inside other identifiers.

## 3. Scan First, Report, Then Confirm

Before writing any files:

```bash
rg -l '\bOldWord\b' /target/path | wc -l          # count matches
rg -l '\bOldWord\b' /target/path | head -20       # sample files
```

Present the user with:
- Approximate file count
- A few sample paths
- Ask: "Proceed with replacement in these files only?"

If the user has already given clear go-ahead, skip the confirm step.

## 4. Define Skip Patterns for Technical Terms

Some words are **brand names AND technical terms**. You MUST protect the technical uses:

| Word | Technical Usage to Protect |
|------|---------------------------|
| `Signal` | `Signal-to-Noise Ratio`, `signal level`, `signal quality`, `P_signal`, `highest-signal`, traffic `signal` states |
| `Telegram` | (less overlap, but check for `telegram` as variable name in bots) |
| `Port` | `portaudio`, `port number` — do not confuse with brand |

Use regex that skips lines containing protected phrases, OR revert false positives after the first pass.

> **Pitfall:** `Signal` → `S1G` will corrupt "Signal-to-Noise Ratio" into "S1G-to-Noise Ratio". Always scan for and revert these.

## 5. Copyright / Legal Lines — Ask, Then Comply

If the target files contain copyright headers (`Copyright © Signal Messenger, LLC`, `Telegram FZ-LLC`):

1. **Default:** Preserve copyright lines untouched (safer default).
2. **If user says "local copies only / learning / never online"** → user is explicitly overriding caution. Replace everything including copyrights.
3. **If user says "do NOT touch copyrights"** → skip lines matching copyright patterns.

Do not argue with the user's legal risk tolerance for their own local copies.

## 6. Execute the Replacement

Use Python or `sd`/`sed` with whole-word regex:

```python
import re
new_content = re.sub(r'\bOldWord\b', 'NewWord', content)
```

For case-preserving replacement (if needed):
```python
def replace_case(match):
    word = match.group(0)
    if word.isupper(): return 'NEWWORD'
    if word[0].isupper(): return 'Newword'
    return 'newword'
new_content = re.sub(r'\bOldWord\b', replace_case, content, flags=re.IGNORECASE)
```

## 7. Verify and Report

After writing:
- Count files changed
- Show first few changed paths
- **Run a second pass** to confirm no false positives remain (grep for corrupted technical terms)
- Report any files that could not be written (encoding errors, permissions)

## 8. Revert on Demand

If the user is angry about scope or false positives, be ready to revert:
- Keep the original→new mapping clear
- Reverse the replacement exactly

---

## Workflow Summary

1. ✅ Lock scope — ONLY user-named paths
2. ✅ Scan and count
3. ✅ Define skip patterns (technical terms)
4. ✅ Clarify copyright/legal handling
5. ✅ Execute whole-word regex replacement
6. ✅ Verify — grep for false positives
7. ✅ Report counts and samples
