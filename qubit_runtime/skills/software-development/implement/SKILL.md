---
name: implement
description: "Build one ticket or spec item end to end, test-first."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [implementation, tickets, tdd, code-review, delivery]
    category: software-development
    related_skills: [implement-spec, code-review, requesting-code-review]
---

# Implement Skill

Take one piece of work described by a spec or a ticket and build it end to end in
the current context window: fetch it, build it test-first at pre-agreed seams,
review the result, commit. It handles one ticket; a whole multi-ticket spec belongs
to `implement-spec`.

## When to Use

- The user hands you a ticket reference, an issue, or a small spec item to build
- The work fits in one context window and one branch
- A ticket arrived from triage already agent-ready

**Don't use for:** orchestrating a full multi-ticket spec across subagents (use
`implement-spec`); reviewing someone else's diff (use `code-review`).

## Prerequisites

- A spec or ticket body. If the user passes a ticket reference, fetch it from the
  issue tracker first.
- A branch you may commit to. Never commit onto a branch another writer is using.

## How to Run

```text
terminal(command="git status --short --branch", timeout=15)
terminal(command="git log --oneline -5", timeout=15)
```

Read the spec, then work the red-green loop with `terminal`, `patch`, and
`write_file`.

## Procedure

### 1. Fetch and state the ticket

If the user passed a ticket reference, fetch it and **state its title** before
starting. If the reference is ambiguous, ask rather than guessing.

Done when the ticket title and acceptance criteria are quoted back.

### 2. Confirm the seams

Identify the seams the ticket's behaviour will be tested at, and agree them with
the user before writing code. A seam is where you can alter behaviour without
editing in that place. Done when each behaviour in the ticket maps to a named seam.

### 3. Build test-first

For each behaviour, one red-green slice at a time:

1. Write a failing test at the agreed seam.
2. Run it and watch it fail for the right reason.
3. Write the minimum implementation that makes it pass.
4. Run it and watch it pass.

Skip the test only where **no correct seam exists**, and say so explicitly — that
absence is a finding, not a shortcut. Done when every ticket behaviour has a test
that went red then green.

### 4. Check continuously, verify once

Run typechecking and single test files regularly while building. Run the **full**
suite once, at the end. Done when the full suite is green and typecheck is clean.

### 5. Review the work

Load `skill_view(name="code-review")` and review the diff against the ticket as the
spec source. Address what it reports before committing. Done when the review's
findings are either fixed or explicitly noted as out of scope.

### 6. Commit

Commit the work to the current branch with a message naming the ticket. Commit as
you finish each coherent unit rather than one blob at the end. Done when
`git status` is clean and the log shows the commit.

## Pitfalls

- **Starting before the title is stated.** A misread ticket reference produces a
  correct implementation of the wrong work.
- **Green-before-red.** A test that never went red proves nothing; watch it fail
  first.
- **Running the full suite in a loop.** Slow feedback while building, one final run
  at the end.
- **Skipping the review because the tests pass.** Tests prove the behaviour; the
  review catches standards and spec drift.

## Verification

- The ticket title and acceptance criteria were quoted before any edit.
- Each behaviour has a test that was seen red, then green.
- The full suite and typecheck pass in their final run.
- `code-review` ran against this diff, and every finding is fixed or noted.
- `git status` is clean; the work is committed.
