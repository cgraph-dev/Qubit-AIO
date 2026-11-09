---
name: ask-matt
description: "Pick the right workflow skill for the situation at hand."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [router, workflow, orientation, skill-selection]
    category: software-development
    related_skills: [grilling, grill-with-docs, diagnosing-bugs, implement, pr]
---

# Ask Matt Skill

You don't remember every workflow skill, so ask which one fits. This is a router:
it names the skills that exist and the situation each belongs to. Before stating
what a skill does or recommending a step be skipped, `skill_view` that skill — the
summaries here are orientation only.

## When to Use

- The user asks "which skill should I use", "how do I start this", or "what's the
  flow here"
- You are unsure whether a request is grilling, diagnosis, design, or delivery
- The user wants to understand how these workflow skills fit together

**Don't use for:** doing the work itself — load the skill this routes you to. The
upstream "main flow" also referenced skills not present here (`to-spec`, `to-tickets`,
`tdd`, `triage`, `wayfinder`, `retro`, `wizard`); this version routes only among
skills that exist in this install.

## Prerequisites

Read the target skill before recommending it. Every claim about a skill's behaviour
here is a pointer, not a substitute for its text.

## Quick Reference

| Situation | Skill |
|---|---|
| An idea needs sharpening, inside a repo | `grill-with-docs` |
| An idea needs sharpening, no repo | `grill-me` |
| The interview primitive, unwrapped | `grilling` |
| The project's words are the problem | `domain-modeling` |
| Designing a module's shape | `codebase-design` |
| Keeping a codebase good for agents | `improve-codebase-architecture` |
| Something is broken, hard to reproduce | `diagnosing-bugs` |
| Building one ticket, test-first | `implement` |
| Building a whole spec of tickets | `implement-spec` |
| Reviewing a branch or PR diff | `code-review` |
| Writing the PR body | `pr` |
| Settling a design question with throwaway code | `prototype` |
| Context is full, work must continue elsewhere | `handoff` |
| The blocker is someone else's knowledge | `to-questionnaire` |
| A message didn't land | `wait-what` |
| Writing a document an agent will consume | `writing-for-agents` |

## Procedure

### 1. Classify the request

Route by what the user actually needs, not by the words they used:

- **The thinking isn't settled** → an interview: `grill-with-docs` in a repo,
  `grill-me` outside one. Both drive `grilling`.
- **The language is the problem** (one word, three meanings) → `domain-modeling`.
- **A module's shape is wrong** → `codebase-design` for the vocabulary, then
  `grilling` to settle it. To find *which* module, start at
  `improve-codebase-architecture`.
- **Something is broken** → `diagnosing-bugs`. Its loop-first rule is the whole
  value; don't route around it.
- **The work is decided and needs building** → `implement` for one ticket,
  `implement-spec` for a ticket graph.
- **The change is built** → `code-review` on the diff, then `pr` for the body.
- **A decision needs a runnable answer** → `prototype`.

Done when one skill name is chosen and you can say why in a sentence.

### 2. Verify before recommending

`skill_view` the chosen skill and confirm its `When to Use` matches. If it doesn't,
return to step 1 rather than stretching it.

Done when the loaded skill's own triggers cover the request.

### 3. Hand over with the right context

Tell the user which skill you're loading and what you'll do first. If the handoff is
mid-task, `handoff` writes a portable file rather than you pasting context.

Done when the user knows the path and the skill is loaded.

## Phase boundaries

When you finish a phase of work and must decide what to do with the context, the
five options are in `references/PHASE-BOUNDARIES.md`: continue, clear, hand off,
dispatch a subagent, or compact — and the order in which to ask.

## Pitfalls

- **Recommending from the summary table.** Summaries are orientation; the skill's own
  triggers decide, and a summary that stretches a skill is how the wrong one loads.
- **Routing to a skill this install doesn't have.** The ported set is the table
  above. `to-tickets` and `tdd` do not exist here; `implement` carries the test-first
  loop itself.
- **Loading three skills "just in case".** Each costs context; pick one and let it
  pull in its own dependencies.
- **Skipping diagnosis because the fix looks obvious.** Route by situation, not by
  confidence.

## Verification

- Exactly one workflow skill was chosen, and its own `When to Use` covers the
  request.
- Every skill named in the recommendation exists (`skills_list`).
- The user was told the chosen skill and the first step.
