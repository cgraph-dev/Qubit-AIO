---
name: grill-with-docs
description: "Stateful interview that also writes glossary and ADRs."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [interview, design, glossary, adr, documentation]
    category: software-development
    related_skills: [grilling, domain-modeling, grill-me]
---

# Grill With Docs Skill

The relentless interview, plus a paper trail: while you grill, resolved terms land
in `GLOSSARY.md` and hard-to-reverse decisions land in `docs/adr/`. This is the
default way to sharpen an idea when a repo exists to leave notes in.

## When to Use

- Sharpening an idea, feature, or architecture **inside a working directory**
- The user says "grill me" while in a repo, or "let's think this through properly"
- Before a multi-session build, so the reasoning is recoverable later

**Don't use for:** out-of-repo thinking (use `grill-me`); recording decisions
without an interview (use `domain-modeling` directly).

## Prerequisites

None required. `domain-modeling` creates `GLOSSARY.md` and `docs/adr/` lazily, so
nothing needs to pre-exist.

## How to Run

This skill is two loaded skills run as one loop:

```text
skill_view(name="grilling")        # the interview
skill_view(name="domain-modeling") # the capture discipline
```

## Procedure

1. Load both skills above and read them fully. Done when you hold the frontier
   mechanics and the glossary/ADR formats.
2. Run the `grilling` loop. As each round settles terms, apply
   `domain-modeling`: add or sharpen the term in `GLOSSARY.md` **immediately**,
   not batched at the end. Done when every settled term is in the glossary.
3. When a decision meets all three ADR tests — hard to reverse, surprising
   without context, a real trade-off — offer an ADR via `domain-modeling` and
   write it to `docs/adr/`. Done when every qualifying decision is either recorded
   or explicitly declined by the user.
4. When the frontier is empty, close with the three-line confirmation and report
   the paths you touched. Done when the user confirms and every written file is
   listed.

## Pitfalls

- **Batching glossary edits to the end.** Half the value is that the interview
  uses the vocabulary it just settled; write it inline or the next round drifts.
- **Turning `GLOSSARY.md` into a spec.** It is a glossary only — definitions, not
  implementation decisions. Those go in an ADR.
- **An ADR for every choice.** If it is reversible or obvious, it doesn't qualify
  and an ADR just adds sediment.

## Verification

- Every term the interview settled appears in `GLOSSARY.md` with an `_Avoid_` line.
- Every ADR written passes the three tests in `domain-modeling`.
- The reported file list matches what actually changed (`git status`).
