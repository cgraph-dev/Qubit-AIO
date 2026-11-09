---
name: to-questionnaire
description: "Turn a blocker into a questionnaire for someone else."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [questionnaire, async, stakeholders, requirements, discovery]
    category: productivity
    related_skills: [grilling, handoff]
---

# To Questionnaire Skill

Turn something the user cannot answer alone into a **questionnaire**: a Markdown
document they hand to one person to fill in async, or fill out together in a
meeting. The recipient holds knowledge the user lacks, and the questionnaire pulls
it out of them. This is the inverse of `grilling`: you interview the user about the
**send**, not about the subject.

## When to Use

- The blocking knowledge lives in someone else's head, not in the repo
- The user needs decisions or facts from a stakeholder, vendor, or domain expert
- Async is the only option, so the user gets one pass at asking

**Don't use for:** knowledge you can find with `web_search` or `read_file`;
interviewing the user about their own plan (that's `grilling`).

## Prerequisites

None to start. The questionnaire is written to the current directory.

## How to Run

Two short exchanges, then a file:

```text
write_file(path="to-questionnaire-<slug>.md", content="...")
```

## Procedure

### 1. Establish the recipient

Ask, in **one exchange**, the recipient's role, expertise, and relationship to the
user. This fixes the tone and how much context the document must carry on its own.

Done when you know who the recipient is and what they know that the user doesn't.

### 2. Establish what's needed back

Ask, in **one exchange**, the specific decisions or facts the user cannot resolve
alone. These become the coverage list.

Done when you hold a concrete list of what the user must walk away able to decide.

### 3. Write the questionnaire

Draft questions aimed at the **gap** between steps 1 and 2. Order
most-important-first, since async means you may get one pass, and group under `##`
headings by theme once there are more than a handful.

File name: `to-questionnaire-<slug>.md` in the current directory, slug from the
topic.

Done when the file exists and **every item from step 2 is covered by a question**.

## Document structure

Frame it as a **discovery questionnaire**: the user lacks context, the recipient
holds it.

```markdown
# <Questionnaire title>

**Purpose:** why this questionnaire exists and the decision riding on it.

**From:** <the user>, **To:** <the recipient>, **How your answers will be used:** <where they go>

## Context

One paragraph orienting a recipient who wasn't in the user's head. Enough to answer well, not a page.

## How to answer

Deadline and rough effort. Partial answers and "I don't know" are useful: flag anything you're unsure of rather than skipping it.

## <Theme heading>

### What load is the system expected to handle at launch?

_Why this matters: it decides whether we provision for burst traffic now or defer it._

>
```

Rules for the questions:

- One idea per question, never compound.
- An answer stub (`>`) directly beneath each, so a half-finished reply is still usable.
- A one-line _why this matters_ only where the question could be misread or invite a
  throwaway answer.
- Close with a catch-all: **Anything else?** — what we didn't ask that we should know.

## Pitfalls

- **Grilling the user about the subject.** They can't answer it; that's why the
  questionnaire exists. Interview only the send.
- **Compound questions.** Two decisions in one question means one gets ignored.
- **Burying the ask.** Most-important-first; an async recipient who answers two
  questions should answer the two that matter.
- **No answer stubs.** A blank page invites a blank reply.
- **Context that assumes the user's head.** The recipient wasn't in the
  conversation; a short orientation paragraph is required.

## Verification

- Every item from step 2 maps to at least one question in the file.
- Questions are single-idea, ordered most-important-first, each with an answer stub.
- The file is at `to-questionnaire-<slug>.md` in the current directory and its path
  was reported.
- The Purpose/From/To/How-your-answers-will-be-used block is filled in.
