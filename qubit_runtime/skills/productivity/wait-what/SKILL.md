---
name: wait-what
description: "Re-pitch the last message in plain language with context."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [communication, plain-language, glossary, clarification, re-pitch]
    category: productivity
    related_skills: [grilling, domain-modeling]
---

# Wait What Skill

A corrective for a message that did not land. The user says "wait, what", and you
re-pitch what you just said — with the context they were missing, in plain
Simplified Technical English, using the project's own vocabulary. It works after the
fact; `grill-with-docs` is the upfront cure.

## When to Use

- Any time the user signals the last message didn't land ("wait, what", "huh", "I
  don't follow", "what do you mean")
- Mid-conversation, inside any other skill
- After a dense or jargon-heavy explanation

**Don't use for:** adding new information. A re-pitch restates what you already
meant; new facts need a new message.

## Prerequisites

A glossary, if the project has one: `GLOSSARY.md` at the root, or `GLOSSARY-MAP.md`
pointing to the right one per context. Without one, use the codebase's own names.

## How to Run

```text
search_files(pattern='GLOSSARY*.md', target='files')
```

## Procedure

### 1. Locate the vocabulary

Read `GLOSSARY.md`, or follow `GLOSSARY-MAP.md` to the context in question. Use the
terms exactly as defined, including the `_Avoid_` rejections.

Done when you know which glossary applies and can name its relevant terms.

### 2. Rebuild the context the user was missing

State, in one or two sentences, where you had got to — the goal and the current
position. The user lost the thread, not the whole conversation.

Done when someone joining cold could follow the next paragraph.

### 3. Re-pitch in Simplified Technical English

Short sentences, active voice, one idea per sentence, one term per concept,
no metaphor. Say what is true, then what it means for the user. No new claims.

Done when the re-pitch could be understood by someone who has not read the
conversation.

### 4. Offer the next move

End with the decision or next action, so the user has something to respond to rather
than just a restatement.

Done when the user can answer yes, no, or a correction.

## Pitfalls

- **Repeating the same message louder.** The words were the problem; restructure them,
  don't re-emit them.
- **Adding jargon to explain jargon.** Every substitute term must be plainer than the
  one it replaces.
- **Drifting off the glossary.** Using a synonym the glossary rejects reintroduces the
  ambiguity it was meant to remove.
- **Hiding new claims in the re-pitch.** If the restatement contains something you
  hadn't said, the user will think they misread. Say it separately.

## Verification

- The re-pitch names where the conversation had got to.
- Every domain term matches `GLOSSARY.md` exactly.
- Sentences are short and active; no sentence carries two ideas.
- No new claim appears that was absent from the original message.
