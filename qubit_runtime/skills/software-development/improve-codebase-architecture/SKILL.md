---
name: improve-codebase-architecture
description: "Find deepening refactors, report them, then grill the pick."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [architecture, refactoring, deep-modules, code-health, html-report]
    category: software-development
    related_skills: [codebase-design, grilling, domain-modeling]
---

# Improve Codebase Architecture Skill

Surface architectural friction and propose **deepening opportunities**: refactors
that turn shallow modules into deep ones, aimed at testability and
agent-navigability. The output is a survey and a decision, not a refactor — it
proposes candidates, renders them as a visual report, then grills the one you pick.

## When to Use

- The user has a spare moment for upkeep rather than feature work
- A module is hard to test, hard to navigate, or hard to understand one concept
  across
- Another skill found no good seam to lock a bug down (from `diagnosing-bugs`)
- The user asks "what should we clean up" or "where is this codebase painful"

**Don't use for:** implementing the refactor — this produces the candidate and the
plan; the build goes through `grilling` then normal implementation. Not for
reviewing a diff (that is `code-review`).

## Prerequisites

- `codebase-design` vocabulary. Load
  `skill_view(name="codebase-design")` and use its terms exactly: **module**,
  **interface**, **depth**, **seam**, **adapter**, **leverage**, **locality**. No
  drifting into component, service, API, or boundary.
- `GLOSSARY.md` for domain names, and ADRs in `docs/adr/` for decisions this must
  not re-litigate.

## How to Run

Scan, report, then grill:

```text
terminal(command="git log --oneline -40", timeout=15)
```

Writes one self-contained HTML report to the OS temp directory; nothing lands in
the repo.

## Procedure

### 1. Scope the scan (YAGNI)

Deepening pays off by making future change easier, so weight the recently changed
code. If the user named a direction (a module, a subsystem, a pain point), take it.
Otherwise walk back the commit history to find hot spots and let those pull your
attention first; if changes are scattered with no hot spot, widen the net.

Read `GLOSSARY.md` and any ADRs in the area first. Done when you can name the
scope in one sentence.

### 2. Explore for friction

Spawn a subagent with a fresh context via `delegate_task` to walk the codebase.
Don't apply rigid heuristics; explore organically and note where understanding
friction appears:

- Where does understanding one concept require bouncing between many small modules?
- Where are modules **shallow** — interface nearly as complex as the implementation?
- Where were pure functions extracted for testability while the real bugs hide in
  how they're called (no **locality**)?
- Where do tightly coupled modules leak across their seams?
- Which parts are untested, or hard to test through the current interface?

Apply the **deletion test** to anything suspected shallow: would deleting it
concentrate complexity, or just move it? "Concentrates" is the signal.

Done when you hold a candidate list, each with a file set and a friction statement.

### 3. Render the report

Write a self-contained HTML file to the OS temp directory so nothing lands in the
repo — resolve `$TMPDIR`, falling back to `/tmp` (`%TEMP%` on Windows), and write
`<tmpdir>/architecture-review-<timestamp>.html` so each run gets a fresh file.
Open it with `terminal` (`xdg-open` on Linux, `open` on macOS, `start` on Windows)
and report the absolute path.

Each card carries: **Files**, **Problem**, **Solution** in plain English,
**Benefits** phrased as locality and leverage and how tests improve, a
**before/after** diagram side by side, and a **Recommendation strength** badge of
`Strong`, `Worth exploring`, or `Speculative`. End with a **Top recommendation**
section. The full scaffold and diagram patterns are in
`references/HTML-REPORT.md`.

Use `GLOSSARY.md` vocabulary for the domain and `codebase-design` vocabulary for the
architecture: "the Order intake module", never "the FooBarHandler" or "the Order
service". If a candidate contradicts an ADR, surface it only when the friction is
real enough to reopen the decision, and say so in an amber callout.

**Do NOT propose interfaces yet.** After writing the file, ask: "Which of these
would you like to explore?" Done when the file exists, its path is reported, and the
question is asked.

### 4. Grill the chosen candidate

Once the user picks, load `skill_view(name="grilling")` and walk the decision tree
with them: constraints, dependencies, the shape of the deepened module, what sits
behind the seam, what tests survive.

Capture side effects inline as decisions crystallise, via
`skill_view(name="domain-modeling")`:

- Naming the deepened module after a concept not in `GLOSSARY.md` → add the term
  (create the file lazily).
- Sharpening a fuzzy term → update `GLOSSARY.md` right there.
- User rejects the candidate with a load-bearing reason → offer an ADR so future
  reviews don't re-suggest it. Skip ephemeral ("not worth it now") or self-evident
  reasons.
- Want alternative interfaces for the deepened module → load
  `skill_view(name="codebase-design", file_path="references/DESIGN-IT-TWICE.md")`,
  the design-it-twice pattern.

Done when the frontier is empty and every side effect is either written or declined.

## Pitfalls

- **Scanning everything.** Without a scope decision the report is a generic lint of
  the whole repo, which nobody acts on.
- **"Service", "component", "boundary".** Drifting off the vocabulary makes the
  report unreadable to the skill that consumes it.
- **Proposing interfaces in step 3.** The report is candidates plus diagrams; the
  interface is the thing step 4 designs.
- **Writing into the repo.** The report is a temp artifact. A report committed to the
  tree becomes stale advice.
- **Re-litigating ADRs wholesale.** Only surface a conflict when the friction is
  real.

## Verification

- Every architecture term in the report appears in `codebase-design`'s glossary.
- Every candidate card has all six parts, including a before/after diagram.
- The report path is absolute, outside the repo, and stated to the user.
- No file under the repo changed during steps 1–3 (`git status`).
