---
name: grilling
description: "Interview the user relentlessly to settle a plan or design."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [interview, design, planning, requirements, decisions]
    category: software-development
    related_skills: [domain-modeling, handoff, to-questionnaire]
---

# Grilling Skill

Interview the user relentlessly until you reach a shared understanding. Map the
subject as a **design tree**: every decision branches into the decisions that hang
off it. This skill settles *what should be true*; it does not write code or docs.

## When to Use

- The user wants to stress-test a plan, architecture, design, or idea
- The user says "grill me", "grill this", "challenge this", or "poke holes in it"
- Another skill needs an interview primitive (`grill-me`, `grill-with-docs`,
  `improve-codebase-architecture` all drive this)
- Before committing to a large build whose shape is still fuzzy

**Don't use for:** settled decisions the user has already made and confirmed;
factual questions you can answer with `web_search` or `read_file`.

## Prerequisites

None. Optional context that sharpens the questions:

- A `GLOSSARY.md` at the repo root (see `domain-modeling`) so you use the project's
  own vocabulary instead of synonyms.
- ADRs under `docs/adr/`, so you don't re-litigate recorded decisions.

## How to Run

You are the interviewer, not the interviewee. The loop is: compute the frontier,
ask the whole frontier in one numbered round with your recommendation attached,
wait, absorb, recompute.

## Procedure

### 1. Build the design tree

Name the root decision and the decisions that hang off it before asking anything.
Each node is a decision with prerequisites. Work through `read_file` and
`search_files` for facts; don't ask the user for what the code already answers.

Done when you can list the root and at least its first layer of child decisions.

### 2. Compute the frontier

The **frontier** is every decision whose prerequisites are already settled: the
questions you can ask *now* without guessing at answers you haven't heard yet.

Done when every question in your round-1 list has all its prerequisites satisfied.

### 3. Ask the whole frontier in one round

Number each question and attach your recommended answer. Word each question so
that "yes" accepts the recommendation.

```
❓ **Q1** - **<question title>**: <question body, may be several paragraphs
or a multiple-choice list>

➡️ <your recommended answer>

---

❓ **Q2** - **<question title>**: <question body>

➡️ <your recommended answer>
```

A question whose answer depends on another question still open **in this round**
belongs to a *later* round. Sending it now is guessing.

Done when every frontier decision has been put to the user exactly once.

### 4. Wait for answers

Do not pre-answer, do not proceed to the next round, do not start building.
The decisions are the user's; your job ended at the recommendation.

Done when the user has responded to the round.

### 5. Recompute and repeat

Each round of answers reshapes the tree: settled decisions push the frontier
outward and unblock the questions that depended on them. Return to step 2.

**Facts are your job, never the user's.** When a frontier question needs a fact
from the environment (filesystem, test run, API docs, a log), dispatch
`delegate_task` with a fresh context to find it. Don't block the whole round on
it: a running exploration is an unsettled prerequisite, so only the questions
*downstream* of it wait. Ask the rest of the frontier now.

Done when the frontier is empty: every branch visited, nothing left silently
assumed.

### 6. Confirm and stop

State in three lines what you believe you agreed on: the decision, the rejected
alternatives, and the remaining risks. Ask the user to confirm you have reached a
shared understanding. Only then hand off to building.

Done when the user confirms, or corrects you and sends you back to step 2.

## Pitfalls

- **Batching one question per message.** A serial interview burns one round-trip
  per decision. Ask the frontier together.
- **Asking what you could look up.** "What does `deploy.sh` do?" is your job.
  "Should deploy be one script or two?" is the user's.
- **Leading questions that hide the trade-off.** Give the recommendation *and* the
  alternative you rejected, so a "yes" is informed.
- **Grinding after agreement.** Once the frontier is empty, extra questions read as
  stalling. Confirm and move.

## Verification

- Every question in the final round had all prerequisites settled when asked.
- No frontier question was answered by you in place of the user.
- The closing three-line summary is confirmed by the user, and it names the
  rejected alternatives, not only the winner.
