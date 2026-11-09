---
name: setup-pre-commit
description: "Set up Husky pre-commit: lint-staged, typecheck, tests."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [git-hooks, husky, lint-staged, prettier, pre-commit, javascript]
    category: devops
    related_skills: [git-guardrails, requesting-code-review]
---

# Setup Pre-Commit Skill

Install a commit gate on a JavaScript/TypeScript repo: Husky's pre-commit hook
running lint-staged with Prettier over staged files, then a full typecheck and test
run. It configures an existing repo; it does not fix the failures the hook surfaces.

## When to Use

- The user wants pre-commit hooks or "commit-time formatting"
- Husky, lint-staged, or Prettier needs setting up
- A repo has tests and a typecheck script but nothing enforces them before commit

**Don't use for:** non-JS repos; repos with no test or typecheck script (there is
nothing to gate); CI configuration.

## Prerequisites

A `package.json` at the repo root, and one lockfile so the package manager is
unambiguous: `package-lock.json` (npm), `pnpm-lock.yaml` (pnpm), `yarn.lock` (yarn),
`bun.lockb` (bun). Default to npm when unclear.

Record which of a `typecheck` and a `test` script actually exist — the hook omits
whichever is missing, and the user is told.

## How to Run

```text
terminal(command="ls package-lock.json pnpm-lock.yaml yarn.lock bun.lockb 2>/dev/null; node -p \"Object.keys(require('./package.json').scripts||{}).join(' ')\"", timeout=30)
```

## Procedure

### 1. Detect the package manager

Match the lockfile found in Prerequisites. Done when one package manager is named and
you can say which lockfile justified it.

### 2. Install the dev dependencies

Install `husky`, `lint-staged`, and `prettier` as devDependencies with the detected
package manager. Done when all three appear under `devDependencies`.

### 3. Initialize Husky

Run the package manager's Husky init (`npx husky init` for npm). This creates
`.husky/` and adds a `prepare` script to `package.json`. Done when `.husky/` exists and
`prepare` is `husky`.

### 4. Write `.husky/pre-commit`

```text
npx lint-staged
npm run typecheck
npm run test
```

Replace `npm` with the detected package manager throughout. Husky v9+ needs no
shebang. **Omit** the `typecheck` or `test` line when that script does not exist, and
tell the user which line you dropped. Done when the file matches the detected
scripts exactly.

### 5. Write `.lintstagedrc`

```json
{
  "*": "prettier --ignore-unknown --write"
}
```

Done when the file exists. `--ignore-unknown` skips files Prettier cannot parse, such
as images.

### 6. Write `.prettierrc` only if none exists

Check for any existing Prettier config first; if one exists, leave it alone. Otherwise:

```json
{
  "useTabs": false,
  "tabWidth": 2,
  "printWidth": 80,
  "singleQuote": false,
  "trailingComma": "es5",
  "semi": true,
  "arrowParens": "always"
}
```

Done when exactly one Prettier config governs the repo and you know which.

### 7. Verify

- [ ] `.husky/pre-commit` exists and is executable
- [ ] `.lintstagedrc` exists
- [ ] `prepare` in `package.json` is `"husky"`
- [ ] A Prettier config exists
- [ ] `npx lint-staged` runs cleanly

Done when every box is checked by an actual command, not by inspection.

### 8. Commit

Stage the created files and commit as
`Add pre-commit hooks (husky + lint-staged + prettier)`. The commit runs through the
new hook, which is the smoke test. Done when the commit lands and the hook executed.

## Pitfalls

- **Running lint-staged before typecheck.** Order matters: formatting is fast and
  staged-only, so it runs first and fails early; the full typecheck and test run after.
- **Adding a typecheck line for a script that doesn't exist.** The hook then fails
  every commit with "missing script", which reads as a broken repo.
- **Overwriting an existing Prettier config.** Two configs make formatting
  unpredictable and the user will not find out which one won.
- **Assuming npm.** A pnpm repo gated by `npm run test` fails on a missing lockfile
  namespace at commit time.
- **Hooks that slow commits to minutes.** Full test suites belong in CI when the suite
  is long; say so and let the user choose.

## Verification

- All five boxes in step 7 pass from real command output.
- The `pre-commit` file's commands match the scripts that actually exist in
  `package.json`.
- Only one Prettier config is in effect.
- The smoke-test commit landed with the hook executing.
