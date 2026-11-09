---
name: prototype
description: "Throwaway code that answers one design question."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [prototype, design, exploration, state-models, ui-variants]
    category: software-development
    related_skills: [grilling, handoff, codebase-design]
---

# Prototype Skill

A prototype is **throwaway code that answers a question**. The question decides the
shape, so branch on the question before writing anything. This skill builds the
artifact and captures the answer; it does not polish the result into production code.

## When to Use

- "Does this state model or logic feel right?" — you can't settle it on paper
- "What should this look like?" — you need several real UI variants, not mockups
- The user wants to sanity-check a design before committing to it

**Don't use for:** production features (throwaway is a constraint, not a promise to
destroy); answering a question a `read_file` or a test run settles.

## Prerequisites

- The question, stated in one paragraph before any code is written.
- For the UI branch, the project's existing page or route convention — never invent
  a new top-level structure.

## How to Run

Branch first:

```text
skill_view(name="prototype", file_path="references/LOGIC.md")
skill_view(name="prototype", file_path="references/UI.md")
```

## Procedure

### 1. Pick the branch

From the user's prompt, the surrounding code, or by asking:

- **"Does this logic / state model feel right?"** →
  `references/LOGIC.md`: one self-contained HTML file with free-play buttons and
  tabbed guided walkthroughs, drivable by a non-developer.
- **"What should this look like?"** → `references/UI.md`: several radically different
  variants on one route, switchable by URL search param and a floating bottom bar.

If genuinely ambiguous and the user isn't reachable, default to whichever matches the
surrounding code (a backend module → logic; a page or component → UI) and state the
assumption at the top of the prototype.

Done when you can name the branch and the question in one sentence.

### 2. Apply the rules for both branches

1. **Throwaway from day one, clearly marked.** Locate it close to where it will be
   used so context is obvious, but name it so a casual reader sees it's a prototype.
   For throwaway UI routes, obey the project's existing routing convention.
2. **Trivial to run.** One command from the project's task runner, or a single HTML
   file the user double-clicks. No thinking required to start.
3. **No persistence by default.** State lives in memory; persistence is what the
   prototype is *checking*, not what it depends on. If the question is about a
   database, hit a scratch DB or a file named "PROTOTYPE, wipe me".
4. **Skip the polish.** No tests, no error handling beyond what makes it runnable,
   no abstractions. The point is to learn fast.
5. **Surface the state.** After every action, or every variant switch, render the
   full relevant state so the user sees what changed.
6. **Capture it when done** — see step 4.

Done when each rule is either satisfied or explicitly waived with a reason.

### 3. Build and hand over

Follow the branch reference to build the artifact, then give the user the path or
URL. Done when they can open it with the instruction you gave them.

### 4. Capture the answer and the prototype

Fold the validated decision into the real code. Then capture the prototype itself as
a **primary source**: commit it to a throwaway branch out of main, and leave a
context pointer to that branch on the implementation issue. Record the verdict and
the question it settled there too.

Done when main contains only the validated decision, the throwaway branch holds the
prototype, and the issue carries the pointer.

## Pitfalls

- **Picking the wrong branch.** The two branches produce very different artifacts, so
  getting it wrong wastes the whole prototype. A logic prototype built as live code,
  or three UI variants that differ by colour, are both misses.
- **Adding tests or abstractions.** A prototype that needs tests is no longer one.
- **Wiring it to the real database.** Persistence you didn't intend to build hides
  the thing you were testing.
- **Leaving it in main.** It rots fast and confuses the next reader; the branch is
  where it belongs.
- **Shipping the shell.** The validated decision folds in; the HTML shell or variant
  switcher does not.

## Verification

- The question appears in a visible intro of the artifact, not just a comment.
- The artifact runs with the single command or double-click you gave the user.
- The throwaway branch exists and main holds only the validated decision.
- The implementation issue carries the pointer and the verdict.
