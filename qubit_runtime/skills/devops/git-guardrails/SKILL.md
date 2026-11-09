---
name: git-guardrails
description: "Block destructive git commands before they run."
version: 1.1.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos]
metadata:
  qubit:
    tags: [git, guardrails, safety, hooks, destructive-commands]
    category: devops
    related_skills: []
---

# Git Guardrails Skill

Install a guard that intercepts and refuses destructive git commands before they
execute: force-push, hard reset, destructive clean, branch deletion, and wholesale
working-tree checkout. The command is stopped and the agent is told it lacks
authority for it.

Upstream shipped this as a hook for a different agent's tool-gate. Qubit's
equivalent is a **shell hook** on `pre_tool_call`, declared in a profile
`config.yaml`, where **exit code 2 blocks the tool call**. The same script also works
as a standalone `git` wrapper when no hook is configured.

## When to Use

- The user wants to prevent destructive git operations in an agent session
- A past session lost work to `push --force` or `reset --hard`
- Onboarding a repo where the agent runs unattended

**Don't use for:** blocking ordinary `git add` / `commit` / `branch`; reviewing
history policy; preventing pushes in the first place (use a protected branch).

## What gets blocked

- `git push` (all variants, including `--force`)
- `git reset --hard`
- `git clean -f` / `git clean -fd`
- `git branch -D`
- `git checkout .` / `git restore .`

## Prerequisites

- `bash`, and `jq` to read the hook payload's `tool_input.command`. Substitute Python
  if `jq` is unavailable.
- A profile you may edit: `~/.qubit/config.yaml`, or
  `$QUBIT_HOME/profiles/<name>/config.yaml`.
- The script uses bash arrays, so `platforms:` is `[linux, macos]`.

## How to Run

Copy the bundled script out of the skill and make it executable:

```text
terminal(command="mkdir -p ~/.qubit/agent-hooks && cp <skill-dir>/scripts/block-dangerous-git.sh ~/.qubit/agent-hooks/ && chmod +x ~/.qubit/agent-hooks/block-dangerous-git.sh", timeout=30)
```

## Procedure

### 1. Ask the scope

Ask the user: this profile only, or every profile? A per-profile script lives under
that profile's directory; a shared one under `~/.qubit/agent-hooks/` referenced from
each `config.yaml`. Done when the script path is chosen.

### 2. Install the script

Copy `scripts/block-dangerous-git.sh` to that path and `chmod +x` it. The hook reads
`tool_input.command` from stdin JSON, so no adaptation is needed for the `terminal`
tool's payload. Done when the path exists and is executable.

### 3. Declare the hook

Merge into the profile `config.yaml` — never overwrite other keys:

```yaml
hooks:
  pre_tool_call:
    - matcher: "terminal"
      command: "~/.qubit/agent-hooks/block-dangerous-git.sh"
      timeout: 10
      fail_closed: true
```

`fail_closed: true` matters: if the script crashes, the destructive command is
blocked rather than allowed. Shell hooks need first-use consent per `(event,
command)` — approve at the CLI prompt, or set `HERMES_ACCEPT_HOOKS=1` /
`hooks_auto_accept: true`. Done when the block is present and consent is resolved.

### 4. Offer customization

Ask whether to add or remove patterns, and edit the copied script accordingly. Done
when the pattern list matches what the user asked for.

### 5. Verify

```text
terminal(command="echo '{\"tool_input\":{\"command\":\"git push origin main\"}}' | ~/.qubit/agent-hooks/block-dangerous-git.sh; echo \"exit=$?\"", timeout=20)
```

Expected: a `BLOCKED:` line on stderr and exit code 2. Then confirm a benign command
exits 0, and that a real `git status` through the agent still runs.

## Pitfalls

- **Blocking the agent's own commits.** The guardrail blocks `push`, not `commit`;
  the commit-as-you-go rule still applies and must keep working.
- **`fail_closed: false` on a safety hook.** The guard fails open exactly when it is
  broken, which is when you need it.
- **Expecting blocking from a non-`pre_tool_call` event.** Only `pre_tool_call`
  honors exit-2 blocking and `fail_closed`.
- **Overwriting `config.yaml`.** Merge into the existing `hooks.pre_tool_call` list.
- **Bare pattern regexes.** `git clean -f` unanchored also matches a commit message
  quoting it; anchor the patterns.
- **Windows.** Bash arrays and `printf -v` do not port, hence the gated `platforms:`.

## Verification

- The script exits 2 on a destructive command and 0 on a benign one.
- `config.yaml` still holds its previous keys after the merge.
- Consent is recorded, so the hook is not silently skipped at startup.
- A benign `terminal` git command still runs through the agent.
