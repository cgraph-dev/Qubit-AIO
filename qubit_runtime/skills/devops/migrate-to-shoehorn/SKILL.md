---
name: migrate-to-shoehorn
description: "Replace `as` casts in tests with type-safe helpers."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [typescript, testing, type-assertions, shoehorn, migration]
    category: devops
    related_skills: [implement, code-review, requesting-code-review]
---

# Migrate to Shoehorn Skill

Replace `as` type assertions in **test files** with `@total-typescript/shoehorn`'s
type-safe helpers. `shoehorn` lets you pass partial data in tests while keeping
TypeScript happy, and removes the manual target type and the double-`as` workaround.
It applies to test code only — never to production code.

## When to Use

- The user mentions shoehorn, or wants to replace `as` in tests
- Tests fake large objects but only need a few properties
- Tests need to pass intentionally wrong data to exercise an error path

**Don't use for:** production code (assertions there are a separate design
question); non-TypeScript repos.

## Prerequisites

Node and the repo's package manager. The package is
`@total-typescript/shoehorn`.

## Quick Reference

| Function | Use case |
|---|---|
| `fromPartial()` | Pass partial data that still type-checks |
| `fromAny()` | Pass intentionally wrong data (keeps autocomplete) |
| `fromExact()` | Force a full object (swap to `fromPartial` later) |

## How to Run

Find the assertions first:

```text
search_files(pattern=' as [A-Z]', target='content', file_glob='*.test.ts', limit=200)
search_files(pattern=' as unknown as ', target='content', file_glob='*.spec.ts', limit=200)
terminal(command="npm i -D @total-typescript/shoehorn", timeout=180)
```

## Procedure

### 1. Gather the scope

Ask the user:

- Which test files have `as` assertions causing problems?
- Are they faking large objects where only some properties matter?
- Do they need intentionally wrong data for error-path tests?

Done when the file list and which helper each site needs are decided.

### 2. Install

Add `@total-typescript/shoehorn` with the repo's package manager. Done when it is in
`devDependencies`.

### 3. Migrate the two shapes

- `getUser({ body: { id: "123" } } as Request)` → `getUser(fromPartial({ body: { id: "123" } }))`
- `getUser({ body: { id: 123 } } as unknown as Request)` → `getUser(fromAny({ body: { id: 123 } }))`

Large objects with a few needed properties become `fromPartial({ body: { id: "123" } })`
with the other twenty properties simply omitted. Add the import from
`@total-typescript/shoehorn` in each touched file.

Done when no `as <Type>` remains in the listed test files except where the user kept it
deliberately.

### 4. Verify with the type checker

Run the repo's typecheck across the touched tests. Done when it passes, and the
diff shows fewer assertions than it started with.

## Pitfalls

- **Migrating production code.** `fromPartial` widens the contract; in app code that
  hides real bugs. Test files only.
- **Using `fromAny` where `fromPartial` fits.** `fromAny` turns off checking; reach
  for it only when wrong data is the point.
- **Leaving the old import.** A leftover `as` beside a new `fromPartial` usually means
  the type was genuinely mismatched — fix it or note it, don't paper over it.
- **Counting by eye.** Use the typecheck and a search, not a manual scan, to confirm
  the assertions are gone.

## Verification

- The typecheck passes on every touched test file.
- A `search_files` for ` as [A-Z]` in the migrated files returns only deliberate
  leftovers, each noted.
- No changed file outside a test path.
