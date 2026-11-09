---
name: domain-modeling
description: "Sharpen domain language; write glossary and ADRs."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [domain-modeling, glossary, adr, vocabulary, ddd]
    category: software-development
    related_skills: [grilling, grill-with-docs, codebase-design]
---

# Domain Modeling Skill

Actively build and sharpen the project's domain model while you design: challenge
fuzzy terms, resolve overloaded words, record hard-to-reverse decisions. This is
the *active* discipline — you are changing the model, not merely reading it.
Merely looking up a term in `GLOSSARY.md` is a habit any skill can do.

## When to Use

- The user uses a term that means something different than the code or the
  glossary does
- One word is doing several jobs ("account" = customer, user, and billing entity)
- About to record a decision that is expensive to reverse
- Writing or editing `GLOSSARY.md`, `GLOSSARY-MAP.md`, or an ADR
- Driving the capture half of `grill-with-docs`

**Don't use for:** general programming concepts — timeouts, retries, error types
belong nowhere in a domain glossary however heavily the project uses them.

## Prerequisites

Nothing pre-exists. Create files lazily:

- Single-context repo: `GLOSSARY.md` at the root, `docs/adr/` beside it.
- Multi-context repo: `GLOSSARY-MAP.md` at the root pointing at each context's own
  `GLOSSARY.md` and `docs/adr/`.

## How to Run

Detect the layout first, then work inline as terms crystallise:

```text
search_files(pattern='GLOSSARY-MAP.md', target='files')  # multi-context?
search_files(pattern='GLOSSARY.md', target='files')      # where?
search_files(pattern='*.md', path='docs/adr', target='files')  # existing ADRs
```

Formats live beside this skill, loaded with
`skill_view(name="domain-modeling", file_path="references/<name>.md")`:
`references/GLOSSARY-FORMAT.md` and `references/ADR-FORMAT.md`.

## Procedure

1. Resolve the layout. `GLOSSARY-MAP.md` present → multi-context, read it and pick
   the context the topic belongs to (ask if genuinely unclear). Root
   `GLOSSARY.md` only → single context. Neither → create lazily on first term.
   Done when you can name the glossary path you will write to.
2. Challenge against the existing glossary. When the user's usage conflicts with a
   recorded term, say so and ask which is right: "Your glossary defines
   'cancellation' as X, but you seem to mean Y. Which is it?" Done when the user
   has chosen.
3. Sharpen fuzzy or overloaded language into one canonical term. Done when each
   fuzzy word maps to a single precise term with its rejects listed.
4. Stress-test with concrete scenarios. Invent an edge case that forces precision
   about where two concepts divide. Done when a scenario has exposed either a
   boundary or an ambiguity the user resolved.
5. Cross-check against the code with `search_files`. If the code contradicts what
   the user just said, surface it rather than recording the claim. Done when code
   and statement agree, or the contradiction is resolved.
6. Write the term into `GLOSSARY.md` right there, per
   `references/GLOSSARY-FORMAT.md`: definition of *what it is*, plus an `_Avoid_`
   line of rejected synonyms. Done when the file reads as a glossary and carries
   no implementation detail.
7. Offer an ADR **only** when all three hold: hard to reverse, surprising without
   context, the result of a real trade-off. Missing any one → skip it. Done when
   each qualifying decision is recorded in `docs/adr/NNNN-slug.md` with the next
   number, or the user declined.

## Pitfalls

- **`GLOSSARY.md` drifting into a spec.** Home for definitions only. Implementation
  choices go in an ADR, scratch reasoning goes nowhere.
- **Recording instead of challenging.** If you write down whatever the user said,
  the glossary encodes the ambiguity you were hired to remove.
- **ADR inflation.** A reversible, obvious, or no-alternative decision is not an
  ADR; recording it adds sediment that future readers must dig through.
- **Multi-context flat file.** In a repo with `GLOSSARY-MAP.md`, a root glossary
  is wrong — write to the context's own file.

## Verification

- Each new term has a definition and an `_Avoid_` line.
- No implementation detail in `GLOSSARY.md`.
- Each ADR passes all three tests, has the next sequential number, and names the
  alternative it rejected.
- File layout matches what step 1 detected (root vs per-context).
