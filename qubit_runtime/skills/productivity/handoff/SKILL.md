---
name: handoff
description: "Compact this conversation into a portable handoff document."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [handoff, context, session, portability, continuity]
    category: productivity
    related_skills: [grilling, writing-for-agents]
---

# Handoff Skill

Compact the current conversation into a handoff document a fresh agent can pick up
from. It buys **portability**: a file that travels to a new session, a new directory,
a new harness, or a colleague. It never restates what other artifacts already hold.

## When to Use

- Context is nearly full and the work must continue in a fresh session
- Moving to a different directory, repo, or agent
- Handing work to a colleague
- Forking a side task mid-phase without derailing the current one

**Don't use for:** continuing in the same session with budget left — continuing costs
nothing and loses nothing. Not for compressing context in place; that's the built-in
compression.

## Prerequisites

None. If the user passed arguments, treat them as what the next session will focus
on and tailor the document to that.

## How to Run

Write to the OS temp directory — not the workspace. Resolve `$TMPDIR`, falling back
to `/tmp` (`%TEMP%` on Windows):

```text
terminal(command="echo ${TMPDIR:-/tmp}", timeout=10)
```

Then `write_file` to `<tmpdir>/handoff-<slug>.md` and report the absolute path.

## Procedure

### 1. Fix the focus

Read the user's arguments as the next session's job. If none were given, infer the
next phase from where this one stopped, and say what you assumed.

Done when the document's target session has one stated purpose.

### 2. Write the document

Include, in this order:

- **Goal**: what the next session is for, in one or two sentences.
- **State**: what is done, what is in flight, and what is blocked, with the exact
  branch, commit, and file paths.
- **Decisions**: what was settled and why, so the next agent doesn't reopen it.
- **Next steps**: ordered, first step actionable without asking anyone.
- **Suggested skills**: name the skills the next agent should `skill_view`, and why
  each. This section is required.
- **Open questions**: what you would have asked if you had more turns.

**Reference, don't duplicate.** Specs, plans, ADRs, issues, commits, and diffs are
already artifacts: cite them by path or URL rather than pasting their content.

**Redact** API keys, passwords, tokens, and personally identifiable information.

Done when all seven sections exist and no section pastes content a pointer could
carry.

### 3. Hand over the path

Report the absolute path and state that it lives outside the workspace, so the user
knows it is not committed.

Done when the path has been stated and the user can open it.

## Pitfalls

- **Writing into the workspace.** A handoff file in the repo gets committed by
  accident and goes stale in place; the temp directory is the destination.
- **Pasting the spec.** A handoff that duplicates the spec drifts from it; a path
  does not.
- **Leaking secrets.** A conversation about an API almost always contains a token
  somewhere; redact while writing, not afterwards.
- **Omitting suggested skills.** Without it the next agent re-derives which discipline
  applies.
- **Losing the "why".** A list of next steps without the decisions behind them makes
  the next session confidently re-litigate settled ground.

## Verification

- The file exists under the temp directory, outside the workspace.
- All seven sections are present, including suggested skills.
- No secret or PII appears; no spec or diff content is pasted where a path would do.
- The reported path matches the file actually written.
