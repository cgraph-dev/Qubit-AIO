---
name: pr
description: "Write a PR body: smallest visual, evidence, merge danger."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [pull-request, pr-body, evidence, diagrams, merge-risk]
    category: software-development
    related_skills: [code-review, implement]
---

# PR Skill

Shape a pull-request body around three things a reviewer actually needs: the
**smallest visual** that shows what changed, **before/after evidence** that it
works, and a **merge danger** call. It writes the body; it does not open, review, or
merge the PR. The Summary visual menu is adapted from Dex Horthy's `show-me` skill —
see `references/CREDITS.md`.

## When to Use

- About to open or update a PR and the body needs writing
- The agent finished a change and wants the description to carry its own evidence
- A reviewer will need to judge risk from the body alone

**Don't use for:** reviewing the diff (use `code-review`); writing commit messages.

## Prerequisites

The change must exist and be committed, so evidence can be produced from a real
run. A failed test run or screenshot is worth more than prose.

## How to Run

```text
terminal(command="git diff --stat <base>...HEAD", timeout=30)
terminal(command="git log --oneline <base>..HEAD", timeout=15)
```

## Template

```markdown
## Summary

<diagram, diff-sketch, or tree>

## Evidence

- **Before:** <screenshot/output/failing test run>
  **After:** <screenshot/output/passing test run>

## Merge Danger

**Door:** <one-way or two-way>

<optional: description>

**Blast Radius:** <one-word description>

<optional: potential ramifications of merge>
```

## Procedure

### 1. Pick the Summary visual

Skip all preambles. Keep prose brief and use the project's `GLOSSARY.md` terms.
Match the visual to the change:

- **Pseudocode** for logic or an algorithm.
- **Call tree** for runtime control flow.
- **Component tree** for UI structure, including the state and module boundaries
  that matter.
- **Shallow file tree** for file responsibility or a broad refactor.
- **Mermaid** for component interaction, control flow, or data flow.
- **Diff** when the point is what changes and the surrounding shape already exists.
- **Whole code block** when most of it is new, when omitted context would hide
  ownership or order, or when the reader needs a copyable target shape.

Place each visual next to the short text it supports, and keep only the calls,
files, props, states, and boundaries needed to answer the reader's question. One or
several — all of them would overwhelm.

Done when the Summary contains at least one visual and under four lines of prose.

### 2. Produce evidence

A concrete before and after. **Screenshots** are S-tier when the environment is set
up for it and the change is visual. **Execution-based evidence** is A-tier: test
results, console output, showing the exact test that failed and now passes.

If you cannot produce real evidence, say so in the body rather than describing
evidence you did not gather. Done when the Before line names an observed failure and
the After line names an observed pass.

### 3. Call the merge danger

**Door**: two-way doors can be walked back; one-way doors cannot. Changes involving
destructive actions or hard-to-reverse decisions are one-way.

**Blast radius**: one word for the potential scope of impact — layout shift,
breakage for consumers, mobile responsiveness, and so on. Add the ramifications
only when non-obvious.

Done when both fields are filled and the door call is justified by the change
category, not by optimism.

### 4. Assemble and check

Write the body out. Verify each section is present and no section is prose-only
where it called for a visual. Done when the template's three headings all exist with
their required content.

## Visual examples

```text
on(save)
  if content is unchanged
    return cached result
  write new content
  return fresh result
```

```text
submitForm
  createSession
    persistPrompt
    launchAgent
  navigateToSession
```

```text
src/
├── commands/       # parses user actions
├── sessions/       # owns session state
└── transport/      # sends API requests
```

```diff
 <SessionPage>
   useSessionEvents()
   <SessionToolbar>
+    <RunSkillButton />
   <SessionTimeline>
+    <SkillResultCard />
```

## Pitfalls

- **Prose where a diagram belongs.** A paragraph describing flow reads slower than
  six lines of a call tree.
- **Claiming evidence you didn't run.** A fabricated "After: tests pass" is worse
  than an honest "unverified".
- **Optimistic door call.** Assuming reversibility is how destructive changes get
  merged casually.
- **Summarizing every file.** The Summary answers the reader's question; it is not
  a diff narration.

## Verification

- `## Summary`, `## Evidence`, `## Merge Danger` are all present.
- At least one visual in the Summary, and prose kept to a few lines.
- Before and After both cite real observed output from this session.
- The door call is one-way or two-way, with blast radius filled in.
