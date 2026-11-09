---
name: code-review
description: "Two-axis diff review: repo standards plus originating spec."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [code-review, pull-request, diff, standards, spec, subagents]
    category: software-development
    related_skills: [requesting-code-review, pr, implement]
---

# Code Review Skill

Review the diff between `HEAD` and a fixed point the user supplies, along two
independent axes:

- **Standards**: does the code conform to this repo's documented coding standards?
- **Spec**: does the code faithfully implement the originating issue or spec?

Both axes run as parallel subagents so they cannot pollute each other's context,
then this skill aggregates their findings without merging them. It reviews a diff;
it does not fix what it finds.

## When to Use

- The user wants a branch, PR, or work-in-progress diff reviewed
- The user asks to "review since X" or "review against main"
- Before merging, to catch the change that is right by one axis and wrong by the other

**Don't use for:** self-verification before commit when you wrote the code in this
same context — use `requesting-code-review` for the pre-commit pipeline. Not for
reviewing uncommitted scratch work with no fixed point.

## Prerequisites

A git repository and a fixed point (commit SHA, branch, tag, or `HEAD~N`). If the
user gave none, ask before doing anything else.

## How to Run

```text
terminal(command="git rev-parse <fixed-point>", timeout=15)
terminal(command="git diff <fixed-point>...HEAD --stat", timeout=30)
terminal(command="git log <fixed-point>..HEAD --oneline", timeout=15)
```

Then `delegate_task` twice, once per axis.

## Procedure

### 1. Pin the fixed point

Take whatever the user named as the fixed point. Capture the diff command once as
`git diff <fixed-point>...HEAD` — three dots, so the comparison is against the
merge-base — plus the commit list from `git log <fixed-point>..HEAD --oneline`.

Verify `git rev-parse <fixed-point>` resolves and the diff is non-empty. A bad ref
or empty diff fails here, not inside two parallel subagents.

Done when the ref resolves, the diff is non-empty, and you hold both the diff
command and the commit list.

### 2. Identify the spec source

Look, in this order:

1. Issue references in commit messages (`#123`, `Closes #45`, `!67` for GitLab).
2. A path the user passed as an argument.
3. A spec file under `docs/`, `specs/`, or `.scratch/` matching the branch or
   feature name, found with `search_files`.

If nothing turns up, ask the user where the spec is. If they say there isn't one,
the Spec subagent is skipped and the report says "no spec available".

Done when the spec path is known or its absence is confirmed.

### 3. Identify the standards sources

`search_files` for every file documenting how code should be written in this repo.
Any `CODING_STANDARDS.md`, `CONTRIBUTING.md`, or `AGENTS.md` present must be on the
list.

On top of that, the Standards axis always carries the **smell baseline** below — a
fixed set of code smells that applies even when a repo documents nothing. Two rules
bind it:

- **The repo overrides.** A documented repo standard wins; where it endorses
  something the baseline would flag, suppress the smell.
- **Always a judgement call.** Each smell is a labelled heuristic ("possible
  Feature Envy"), never a hard violation. Skip anything tooling already enforces.

Match each against the diff:

- **Mysterious Name**: name doesn't reveal what it does. → rename; if no honest
  name comes, the design is murky.
- **Duplicated Code**: same logic shape in more than one hunk or file. → extract,
  call from both.
- **Feature Envy**: method reaches into another object's data more than its own. →
  move it onto the data it envies.
- **Data Clumps**: the same few fields keep travelling together. → bundle into one
  type.
- **Primitive Obsession**: a primitive standing in for a domain concept. → give the
  concept its own small type.
- **Repeated Switches**: the same `switch`/`if`-cascade on the same type recurs. →
  polymorphism, or one map both sites share.
- **Shotgun Surgery**: one logical change forces scattered edits. → gather what
  changes together into one module.
- **Divergent Change**: one module edited for several unrelated reasons. → split so
  each changes for one reason.
- **Speculative Generality**: abstraction for needs the spec doesn't have. → delete
  it; inline until a real need shows.
- **Message Chains**: long `a.b().c().d()` navigation. → hide the walk behind one
  method.
- **Middle Man**: mostly just delegates onward. → cut it.
- **Refused Bequest**: subclass ignores most of what it inherits. → composition.

Done when you have the standards file list and the baseline in hand.

### 4. Spawn both subagents in parallel

Issue both `delegate_task` calls together and aggregate what they return.

The **Standards** prompt carries: the diff command, the commit list, the standards
file list, **the baseline pasted in full** (the subagent has no other access to it),
and this brief:

> "Report, per file/hunk where relevant, (a) every place the diff violates a
> documented standard: cite the standard (file + the rule); and (b) any baseline
> smell you spot: name it and quote the hunk. Distinguish hard violations from
> judgement calls: documented-standard breaches can be hard, but baseline smells
> are always judgement calls, and a documented repo standard overrides the
> baseline. Skip anything tooling enforces. Under 400 words."

The **Spec** prompt carries: the diff command, the commit list, the spec path or
fetched contents, and this brief:

> "Report: (a) requirements the spec asked for that are missing or partial; (b)
> behaviour in the diff that wasn't asked for (scope creep); (c) requirements that
> look implemented but where the implementation looks wrong. Quote the spec line for
> each finding. Under 400 words."

Done when both reports are back, or the Spec axis is explicitly skipped as absent.

### 5. Aggregate

Present the two reports under `## Standards` and `## Spec`, verbatim or lightly
cleaned. Do **not** merge or rerank findings — the separation is the point.

End with one line: total findings per axis, and the worst issue *within each axis*.
No single cross-axis winner.

Done when both sections are present and the closing line reports per-axis counts.

## Why two axes

A change can pass one and fail the other:

- Follows every standard, implements the wrong thing → **Standards pass, Spec fail.**
- Does exactly what the issue asked, breaks the conventions → **Spec pass,
  Standards fail.**

Reporting separately stops one axis from masking the other.

## Pitfalls

- **Two dots instead of three.** `git diff X..HEAD` compares against `X`'s tip, not
  the merge-base, and pulls in unrelated upstream drift.
- **Reranking across axes.** Picking one "worst overall" finding destroys the
  masking protection.
- **Burying the baseline.** If you summarize the smell list instead of pasting it,
  the Standards subagent cannot apply it.
- **Letting the subagents share context.** Separate `delegate_task` calls, fresh
  context each, or the axes contaminate.

## Verification

- `git rev-parse` succeeded and the diff was non-empty before any subagent ran.
- Both `## Standards` and `## Spec` headings exist (or Spec states why it is absent).
- The baseline was passed to the Standards subagent verbatim, not paraphrased.
- No finding appears reordered, merged, or promoted across axes.
