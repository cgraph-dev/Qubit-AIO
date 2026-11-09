---
name: grill-me
description: "Stateless relentless interview to sharpen a plan or design."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [interview, design, planning, requirements]
    category: software-development
    related_skills: [grilling, grill-with-docs]
---

# Grill Me Skill

A relentless interview that sharpens a plan or design, with **no paper trail**:
nothing is written to disk, no `GLOSSARY.md` is created, no ADRs are offered.
It is `grilling` and nothing else.

## When to Use

- Sharpening a plan, a design, a piece of writing, or a strategy when you are
  **not** in a repo with a place to record the outcome
- The user says "grill me" and there is no working directory to leave notes in

**Don't use for:** work inside a codebase — use `grill-with-docs` there instead,
since it runs the same interview and leaves the reasoning behind. Not for
settling facts; that's `web_search` / `read_file`.

## Prerequisites

None. No files are read or written.

## How to Run

Load the interview primitive and run it as its own body:

```text
skill_view(name="grilling")
```

Follow that skill's procedure exactly — the frontier, one round per set of
settled prerequisites, your recommendation on every question.

## Procedure

1. `skill_view(name="grilling")` and read it in full. Done when the design-tree
   and frontier mechanics are in hand.
2. Run the interview per `grilling`. Done when its frontier is empty.
3. Close with the three-line confirmation and **write nothing to disk**. Done when
   the user confirms and the working tree is untouched.

## Pitfalls

- **Reaching for this inside a repo.** The stateful sibling `grill-with-docs` is
  strictly better there: same interview, plus glossary and ADR capture.
- **Sneaking in file writes.** "Stateless" is the whole difference from
  `grill-with-docs`. If the user asks you to record something, switch skills
  rather than half-writing here.

## Verification

- The interview asked every frontier question once, with a recommendation.
- No file was created or modified by the session.
- The user confirmed the shared understanding before any work began.
