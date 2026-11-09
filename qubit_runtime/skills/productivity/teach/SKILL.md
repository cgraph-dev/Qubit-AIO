---
name: teach
description: "Teach a concept across sessions in a stateful workspace."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [teaching, learning, lessons, spaced-repetition, curriculum]
    category: productivity
    related_skills: [writing-for-agents, wait-what]
---

# Teach Skill

Teach the user a topic across multiple sessions, using the current directory as a
stateful teaching workspace. It produces self-contained HTML lessons aimed at one
mission, backed by curated resources, reference material, and learning records. It
does not deliver a lecture in chat; the artifacts are the teaching.

## When to Use

- The user says "teach me <topic>" or "I want to learn <topic>"
- They intend to learn over several sessions, not get one explainer
- A topic has its own vocabulary, syntax, or physical practice worth drilling

**Don't use for:** one-off factual questions (answer them); exam cramming with no
mission; a topic with no way to practise it.

## Prerequisites

- A working directory to serve as the lesson workspace.
- `web_search` / `web_extract` for resources. **Never trust parametric knowledge**
  for the knowledge base; find and cite real sources.

## Workspace layout

Paths resolve from the directory the skill was invoked in. Only the format
references below resolve from this skill's folder.

- `MISSION.md` — why the user is learning this. Grounds every teaching decision.
  Format: `references/MISSION-FORMAT.md`.
- `RESOURCES.md` — curated, annotated sources for knowledge and communities.
  Format: `references/RESOURCES-FORMAT.md`.
- `learning-records/*.md` — decision-grade insights, numbered `0001-<slug>.md`.
  Format: `references/LEARNING-RECORD-FORMAT.md`.
- `lessons/*.html` — the primary unit of teaching, `0001-<slug>.html`.
- `reference/*.html` — compressed reference: cheat sheets, syntax, algorithms,
  glossaries. Designed to print and be revisited.
- `assets/*` — reusable components shared across lessons.
- `NOTES.md` — user preferences and working notes.

## How to Run

```text
skill_view(name="teach", file_path="references/MISSION-FORMAT.md")
skill_view(name="teach", file_path="references/RESOURCES-FORMAT.md")
skill_view(name="teach", file_path="references/LEARNING-RECORD-FORMAT.md")
```

## Procedure

### 1. Establish or confirm the mission

If `MISSION.md` is missing or unpopulated, question the user on *why* they want to
learn this before anything else. Push for a concrete outcome ("run a half marathon
by October", not "get fitter"). Missions change as skills develop: when they do,
update the file **and** write a learning record, after confirming with the user.

Done when `MISSION.md` states a concrete goal, observable success criteria, and what
is out of scope.

### 2. Populate resources before teaching

Gather high-trust sources: primary sources, recognised experts, peer-reviewed work,
well-moderated communities. Annotate every entry with what it covers and when to
reach for it. Record gaps explicitly rather than filling them from memory.

Done when `RESOURCES.md` has enough grounding to author one lesson without guessing.

### 3. Compute the zone of proximal development

Read the learning records, then pick the thing most relevant to the mission that sits
just beyond what the user already demonstrably knows. If the user names an exact
thing to learn, take it.

Done when the lesson's target skill is named and justified by a learning record or
the mission.

### 4. Author one lesson

One self-contained HTML file at `lessons/NNNN-<slug>.html`. Requirements:

- **Short and completable quickly.** Working memory is small; each lesson is one
  tangible win.
- **Beautiful and readable** — clean typography and layout, since it will be revisited.
- **Knowledge first, then skill.** Teach only the knowledge required to acquire the
  pick-up-and-do skill, grounded in effective learning science.
- **A tight feedback loop** for the skill phase: quizzes or guided real-world steps
  that report performance immediately.
- **Citations** to sources from `RESOURCES.md` for every claim.
- **Links** to other lessons and reference documents, plus one recommended primary
  source to read or watch.
- **A reminder** to ask follow-up questions, since you are their teacher.

For quizzes: every answer the same length in words and characters, varied answer
position, no formatting or ordering clue.

Done when the file opens standalone and every requirement above is satisfied.

### 5. Build reference material as it accumulates

Lessons are rarely revisited; reference documents are. Distil each lesson into
`reference/*.html`: syntax, algorithms, poses, routines, glossaries. Once a glossary
exists, every later lesson must use it.

Done when the lesson's reusable essence exists as a reference document, and shared
styles/widgets live in `assets/` rather than being inlined.

### 6. Record what was actually learned

Write a learning record only when one of these is true: genuine demonstrated
understanding of something non-trivial; disclosed prior knowledge (record the depth
claimed); a corrected misconception; or a mission shift. Coverage alone does not
qualify, and neither does a session log.

Done when the record is numbered, one paragraph, and states why it changes what to
teach next.

### 7. Point at wisdom

When a question needs real-world judgement, answer what you can, then route to a
**community**: a forum, a subreddit, a class, a local group. Prefer high-reputation
ones. If the user has declined communities, record that preference in
`RESOURCES.md` and stop proposing them.

Done when the reply either names a community or honours a recorded refusal.

## Fluency vs storage strength

Fluency is in-the-moment retrieval; storage strength is long-term retention. Fluency
gives an illusory sense of mastery. Design lessons for storage strength using
desirable difficulty: retrieval practice (recall from memory), spacing (distribute
practice over time), and interleaving (mix related topics — for skills practice
only). For acquiring knowledge, difficulty is the enemy; for building a skill,
difficulty is the tool.

## Pitfalls

- **Teaching without a mission.** Lessons feel abstract and you cannot judge what
  comes next.
- **Parametric knowledge as curriculum.** Anything you "remember" about the topic may
  be wrong; cite or omit.
- **Long lessons.** Working memory is tiny; a lesson that exhausts it teaches nothing.
- **Inlining what `assets/` already has.** Without reuse each lesson is a one-off and
  the course stops looking like a course.
- **Logging instead of recording.** "Covered chapter 3" is not a learning record;
  evidence of understanding is.
- **Quiz answer-length clues.** Equal-length answers and shuffled positions, or the
  quiz measures pattern-spotting.

## Verification

- `MISSION.md` states a concrete goal and what is out of scope.
- The lesson cites `RESOURCES.md` sources for each claim and recommends one primary
  source.
- The lesson opens standalone from `lessons/` and links to its references.
- Any new learning record states why it changes future teaching.
- No preference or refusal recorded in `NOTES.md` / `RESOURCES.md` is contradicted.
