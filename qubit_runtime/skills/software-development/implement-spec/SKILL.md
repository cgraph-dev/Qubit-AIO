---
name: implement-spec
description: "Orchestrate a whole spec's ticket graph onto one branch."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [implementation, orchestration, task-graph, subagents, worktrees, spec]
    category: software-development
    related_skills: [implement, code-review]
---

# Implement Spec Skill

Build an entire spec on a single integration branch by treating its tickets as a
**task graph**, not a checklist: run implementer subagents against the ready
**frontier**, merge each through a merger subagent, and close with one review. Use
it when you would rather orchestrate the build than drive each ticket by hand.

## When to Use

- The user supplies a spec whose tickets describe the implementation
- The build is too large for one context window but is already decomposed
- The tickets declare blocking relationships, so some are ready now and others wait

**Don't use for:** a single ticket or small change (use `implement`); a spec with no
tickets (decompose it first, or run `grilling` to shape it).

## Prerequisites

- A spec and its associated tickets in the issue tracker.
- Each ticket declares its **blocking edges** — which tickets must land first.
- Git worktree support, since each implementer works in its own worktree.
- `subagent-report-verification` on hand: subagents claim completion, and only
  bytes prove it.

## How to Run

```text
terminal(command="git worktree list", timeout=15)
terminal(command="git branch --show-current", timeout=15)
```

Then drive implementers with `delegate_task`, one per ticket.

## Procedure

### 1. Read the graph

Read the spec and every ticket, and derive the blocking edges. The tickets are a
task graph with blocking relationships, so there is always a **frontier** of
tickets ready to be grabbed.

Done when you can name the frontier for round one and the edges behind it.

### 2. Explore once, up front (optional)

Use an exploration subagent via `delegate_task` for any exploration the tickets
need: relevant codebase files, external documentation. It **must be able to save
files** — have it write its markdown notes to a directory **outside the repo**,
readable by every future subagent, so implementers spend their context on
implementation instead of rediscovery.

Done when the notes path exists and is named in the brief you will give implementers.

### 3. Create the integration branch

Branch from the current tip. If the tracker closes work through PRs, or the user
asked for one, open a **draft** PR after the first merge in step 5 — a branch with no
commits ahead of the base cannot open one — marked as closing the spec and its
tickets.

Done when the integration branch exists and any draft PR is open.

### 4. Dispatch implementers, one per ready ticket

Every subagent starts with a **fresh context window**. Communication with subagents
is sparse and goes through **context pointers**: the spec, the tickets, the
exploration notes, previous commits. Never duplicate what a pointer already carries.

Run each implementer in the background where possible for concurrency. Each one:

- confirms its worktree is based on the **integration branch** before starting, and
  resets onto it if not;
- builds its ticket test-first at agreed seams;
- merges the integration branch tip into its own branch before reporting done.

Done when every frontier ticket has a running implementer.

### 5. Merge through a merger subagent

When an implementer completes, **verify its claim against the bytes** before
trusting it: the branch exists, the commits are on it, the tests actually ran. Then
merge its work to the integration branch with a merger subagent, one ticket per
merge so a conflict names its owner.

Done when the ticket is on the integration branch and its worktree is recorded for
cleanup.

### 6. Recompute the frontier

If the merge changed which tickets are ready, dispatch implementers for the newly
unblocked set. Repeat steps 4–6 until nothing is blocked.

Done when the frontier is empty and every ticket has landed.

### 7. Review once, fix in one subagent

Load `skill_view(name="code-review")` against the integration branch. Fix what it
raises in a **single fresh implementer subagent**, not by hand-editing a branch nine
subagents touched.

Done when the review's findings are resolved and the branch is green.

### 8. Close out

If a draft PR exists, mark it ready for review. Otherwise resolve each ticket the
way the tracker closes work and report the integration branch.

Done when the PR is ready, or every ticket is closed and the branch named.

### 9. Clean up worktrees

Remove every implementer worktree. Done when `git worktree list` shows only the main
checkout and the integration branch.

## Pitfalls

- **Treating tickets as a list.** Sequential execution of a graph-worthy spec wastes
  the parallelism that makes this skill worth loading.
- **Sharing rich context between subagents.** Pointers, not pastes: pasted context
  goes stale the moment a neighbour merges.
- **Trusting "done" reports.** Verify commits and test output; see
  `subagent-report-verification`.
- **Implementers branching from main.** Each must reset onto the integration branch
  or the merge becomes archaeology.
- **Fixing review findings by hand.** One fresh subagent, so the fix gets a clean
  context and reviewable diff.

## Verification

- Every ticket in the spec has a commit on the integration branch, or a stated
  blocker.
- `git worktree list` shows no leftover implementer worktrees.
- The code review ran against the integration branch and its findings are resolved.
- The final state is a ready PR or closed tickets — never a branch of unknown status.
