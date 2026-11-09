---
name: scaffold-exercises
description: "Create exercise dirs that pass the course lint."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [exercises, scaffolding, course, lint, directories]
    category: devops
    related_skills: [teach]
---

# Scaffold Exercises Skill

Create exercise directory structures that pass the course linter, then commit them.
Sections hold exercises; each exercise holds one or more variants (`explainer/`,
`problem/`, `solution/`), each with a non-empty `readme.md`. It scaffolds structure
and stubs only — it does not write exercise content.

## When to Use

- The user wants to scaffold exercises or create exercise stubs
- Setting up a new course section from a plan
- Renumbering or moving existing exercises

**Don't use for:** writing the exercise material itself; repos with no course linter
(the directory convention below is then just a suggestion).

## Prerequisites

- A plan naming sections, exercises, and which variants each needs.
- The linter from the repo's task runner (upstream used `pnpm ai-hero-cli internal
  lint`; substitute the repo's own command).

## How to Run

```text
terminal(command="mkdir -p exercises/05-topic/05.01-name/explainer", timeout=20)
terminal(command="<repo lint command>", timeout=300)
```

## Procedure

### 1. Parse the plan

Extract section names, exercise names, and variant types. Done when every planned
exercise has a name and at least one variant.

### 2. Create the directories

- **Sections**: `XX-section-name/` inside `exercises/`, e.g. `01-retrieval-skill-building`.
- **Exercises**: `XX.YY-exercise-name/` inside a section, e.g. `01.03-retrieval-with-bm25`.
- Section number is `XX`, exercise number is `XX.YY`, names are dash-case.

Default a stub with no stated variant to `explainer/`. Done when `mkdir -p` has run
for every path in the plan.

### 3. Create the readmes

One `readme.md` per variant folder, never empty:

```md
# Exercise Title

Description here
```

Done when every variant folder has a `readme.md` with at least a title.

### 4. Lint

Run the repo's lint command. Fix every error and re-run. Done when lint passes with no
errors.

### 5. Commit

Stage the new tree and commit. Done when the commit lands and `git status` is clean.

## Lint rules the structure must satisfy

- Each exercise has subfolders from `problem/`, `solution/`, `explainer/`
- At least one of `problem/`, `explainer/` exists per exercise
- `readme.md` exists and is non-empty in the primary subfolder
- No `.gitkeep` files, no `speaker-notes.md` files
- No broken links in readmes
- No `pnpm run exercise` commands inside readmes
- `main.ts` (>1 line) required per subfolder that has code; readme-only is fine

## Moving or renaming

Use `git mv`, never `mv`, so history follows. Update the numeric prefix to keep order,
then re-run lint.

```text
terminal(command="git mv exercises/01-retrieval/01.03-embeddings exercises/01-retrieval/01.04-embeddings", timeout=20)
```

## Pitfalls

- **Empty readmes.** The linter treats an empty file as missing; a bare title is
  enough but zero bytes is not.
- **`mv` instead of `git mv`.** The rename becomes a delete plus an add, and the
  exercise's history is lost.
- **Renumbering without re-linting.** Collisions and gaps both pass `mkdir` and fail
  lint.
- **`.gitkeep` to force an empty dir.** The linter rejects it; use a stub readme.
- **Guessing the lint command.** Use the repo's own task; upstream's name will not
  exist here.

## Verification

- The lint command exits clean on the full tree.
- Every planned exercise path exists with its variants.
- No `.gitkeep` or `speaker-notes.md` anywhere in the new tree.
- Renames used `git mv` and lint still passes.
