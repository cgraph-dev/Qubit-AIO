---
name: writing-for-agents
description: "Write docs an agent consumes: skills, AGENTS.md, pointers."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [writing, agents-md, skills, documentation, context-pointers, prompting]
    category: productivity
    related_skills: [grilling, handoff]
---

# Writing For Agents Skill

Reference for writing any document an agent consumes: a skill, an `AGENTS.md`, or a
doc reached by a pointer. The packaging differs; the writing does not. The aim is a
document that makes the agent take the same *process* every run, not one that
produces the same output. For a skill specifically, also read
`references/SKILL-MECHANICS.md`.

## When to Use

- Authoring or editing a `SKILL.md`
- Writing or trimming an `AGENTS.md` / project instruction file
- A doc keeps getting missed by the agent, or keeps being half-read
- A rules document has grown and needs pruning

**Don't use for:** end-user documentation, READMEs, or marketing copy. For Qubit's
frontmatter and validation specifics, `qubit-agent-skill-authoring` is authoritative
— this skill is about the writing.

## Prerequisites

None. Read the document in question fully before editing it.

## Quick Reference

| Lever | What it does |
|---|---|
| Context pointer | Names out-of-context material and the condition for reaching it |
| Context load | Cost of always-loaded text on every turn |
| Cognitive load | Cost on the human of remembering what exists |
| Information hierarchy | In-file step → in-file reference → disclosed reference |
| Progressive disclosure | Pushing material behind a pointer |
| Co-location | Keeping a concept's parts under one heading |
| Completion criterion | Checkable, and where it matters exhaustive, end of each step |
| Leading word | One pretrained token standing for a whole region of behaviour |
| Sprawl / sediment | Too long; or stale layers nobody dares remove |

## Procedure

### 1. Choose the load for every piece of material

Always-loaded text spends **context load** every turn; text reached only through a
pointer spends one line instead; text with no pointer at all rides entirely on the
user's **cognitive load**. Decide per section which of the three it gets.

Done when every section of the document has an assigned load.

### 2. Place each piece on the hierarchy

1. **In-file step** — what the agent does, in order. The primary tier.
2. **In-file reference** — consulted on demand. A flat peer-set (every rule of a
   review) is fine, not a smell.
3. **Disclosed reference** — pushed to a separate file behind a pointer.

Inline what every branch needs; disclose what only some branches reach. Branching is
the cleanest disclosure test. Keep steps in view of each other too: a step whose later
steps are visible invites rushing it.

Done when every reference section is either justified in-file or disclosed.

### 3. Write a pointer that fires

A pointer does two jobs: state what the material is, and list the **branches** that
should trigger reaching it. Front-load the leading word, one trigger per genuinely
distinct branch (synonyms that rename one branch are one branch written twice), and
cut any identity the body already carries.

Done when each pointer names its branches in the first few words, and no two
trigger phrases mean the same thing.

### 4. End every step on a completion criterion

Two properties make it a lever: **clarity** (can the agent tell done from not-done?)
and **demand** (how much it requires). Vague bounds invite **premature completion**.
Defend in order: sharpen the bound first; only if irreducibly fuzzy *and* you observe
the rush, split the sequence so later steps are out of view. Splitting works only
across a real context boundary — a handoff or subagent dispatch.

The strongest criteria are both checkable and exhaustive: "every modified model
accounted for" over "produce a change list".

Done when each step has a criterion you could test, not a restatement of the step.

### 5. Refactor with leading words

A **leading word** is a compact pretrained concept the agent thinks with (_lesson_,
_tracer bullet_, _tight loop_, _red_). Repeat it as a token, never as a sentence.
Hunt for passages that spell out a concept: each is begging to collapse.

**Negation is the failure mode.** Steering by prohibition drags the forbidden thing
into context and makes it more available. Prompt the positive instead; keep a
prohibition only as a hard guardrail you cannot phrase positively, and pair it with
the target behaviour.

Done when no passage states a concept the document already has a word for, and no
instruction is a bare "don't".

### 6. Prune

- **Single source of truth** per meaning. Duplication costs maintenance, tokens, and
  inflates a meaning's prominence past its rank.
- **The environment is a source of truth** too — scripts, config, directory layout,
  `--help`. A document restating it is a cache; keep only what cannot be looked up
  (unwritten conventions, reasons, gotchas).
- **Check relevance** per line. Lines go irrelevant by never bearing on the task, or
  by going stale.
- **Cut no-ops** sentence by sentence: an instruction the model already obeys pays
  load for nothing. Delete the whole sentence, not words from it. Model-relative, not
  reader-relative — settle disagreements by running the document.

Done when removing any remaining sentence would change behaviour, or would lose
knowledge that cannot be looked up.

## Pitfalls

- **Summarizing a reference instead of disclosing it.** A summary of a rules doc is
  worse than a pointer to it, because the agent acts on the summary.
- **Pointer wording that names the topic but not the trigger.** A must-have target
  behind weak wording is a variance bug: sharpen the pointer before inlining.
- **Splitting for its own sake.** Each split spends a load; only a cut by sequence or
  by invocation earns it.
- **Sediment.** Adding feels safe, removing feels risky, and stale layers accumulate
  until you must core down to find what is live.
- **A leading word too weak to beat the default.** "Be thorough" is a no-op; a
  stronger word is the fix, not a different technique.

## Verification

- Every pointer states its triggering branches in its opening words.
- Every step ends on a criterion that is checkable, and exhaustive where the work is.
- No meaning appears in two places; no line restates what the environment encodes.
- Every sentence either changes behaviour or carries knowledge that cannot be looked
  up.
- For a skill document, `references/SKILL-MECHANICS.md` was applied.
